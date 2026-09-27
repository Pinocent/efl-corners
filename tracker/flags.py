"""
Warnings: things in a team's recent matches that the ratings can't see, or
that may have pushed them the wrong way. Shown beside the prediction so you
can decide how far to trust it. They don't change the numbers.

  Red card        a sending-off (either side) in their last match - corners
                  go lopsided once a side is down to ten
  Freak count     last match's corners won or conceded were a 1-in-33 (or
                  rarer) result given what was expected
  Lopsided score  won or lost by 4+ goals last time - game state drives
                  corners, so that match says less than usual
  Trend           corners won or conceded over the last 3 matches are well
                  away from expectation (roughly a 1-in-20 run)

Rest days aren't flagged: two games in four days is routine in the EFL.
"""

import math

from .markets import nb_pmf

TAIL = 0.03       # a single match this unlikely (about 1 in 33) gets flagged
BIG_MARGIN = 4    # goals
TREND_Z = 2.0     # three-match run this many standard deviations out
TREND_N = 3


def _tail(pmf, k):
    """(P(X >= k), P(X <= k))"""
    k = min(int(k), len(pmf) - 1)
    return sum(pmf[k:]), sum(pmf[:k + 1])


def _side(m, team):
    home = m["home"] == team
    return {"home": home, "opp": m["away"] if home else m["home"],
            "cf": m["hc"] if home else m["ac"], "ca": m["ac"] if home else m["hc"],
            "gf": m["hg"] if home else m["ag"], "ga": m["ag"] if home else m["hg"],
            "rf": m.get("hr") if home else m.get("ar"),
            "ra": m.get("ar") if home else m.get("hr")}


def team_flags(team, before, results, model, r, cache):
    """Warnings for one team, using only matches played before `before`."""
    past = [m for m in results if team in (m["home"], m["away"])
            and m["date"] < before and m.get("hc") is not None]
    past.sort(key=lambda m: m["date"])
    if not past:
        return []
    out = []

    def expected(m):
        k = (m["home"], m["away"], m["date"])
        if k not in cache:
            cache[k] = model.expect_corners(m)
        eh, ea = cache[k]
        if eh is None:
            return None, None
        return (eh, ea) if m["home"] == team else (ea, eh)

    last = past[-1]
    s = _side(last, team)
    when = last["date"].strftime("%-d %b")
    vs = f"{'v' if s['home'] else 'at'} {s['opp']} ({when})"

    if s["rf"]:
        n = int(s["rf"])
        out.append({"kind": "Red card", "text": f"Had {'a player' if n == 1 else f'{n} players'} sent off {vs}. "
                    "Corner counts from a match with ten men are less reliable."})
    if s["ra"]:
        n = int(s["ra"])
        out.append({"kind": "Red card", "text": f"Played against ten men {vs}"
                    f"{'' if n == 1 else f' ({n} sent off)'}. Corner counts from that match are less reliable."})

    ef, ea = expected(last)
    if ef is not None:
        for label, obs, mu, word in (("won", s["cf"], ef, "won"), ("conceded", s["ca"], ea, "conceded")):
            hi, lo = _tail(nb_pmf(mu, r), obs)
            p = min(hi, lo)
            if p < TAIL:
                odds = max(2, round(1 / max(p, 1e-4)))
                direction = "far more" if hi < lo else "far fewer"
                out.append({"kind": "Freak count",
                            "text": f"{word.capitalize()} {int(obs)} corners {vs}, {direction} than the "
                                    f"{mu:.1f} expected (about a 1-in-{odds} result)."})

    if s["gf"] is not None and s["ga"] is not None and abs(s["gf"] - s["ga"]) >= BIG_MARGIN:
        res = "Won" if s["gf"] > s["ga"] else "Lost"
        out.append({"kind": "Lopsided score",
                    "text": f"{res} {int(s['gf'])}–{int(s['ga'])} {vs}. Corners follow the "
                            "scoreline, so that match says less than usual."})

    if len(past) >= TREND_N:
        run = past[-TREND_N:]
        sums = {"won": [0, 0.0, 0.0], "conceded": [0, 0.0, 0.0]}
        ok = True
        for m in run:
            sd = _side(m, team)
            e1, e2 = expected(m)
            if e1 is None:
                ok = False
                break
            for key, obs, mu in (("won", sd["cf"], e1), ("conceded", sd["ca"], e2)):
                v = sums[key]
                v[0] += obs; v[1] += mu; v[2] += mu + mu * mu / r
        if ok:
            for key, (o, e, var) in sums.items():
                z = (o - e) / math.sqrt(var)
                if abs(z) >= TREND_Z:
                    word = "risen" if z > 0 else "fallen"
                    out.append({"kind": "Trend",
                                "text": f"Corners {key} have {word} sharply over the last {TREND_N}: "
                                        f"{o / TREND_N:.1f} a match against {e / TREND_N:.1f} expected. "
                                        "Could be a change of manager, system or injuries."})
    return out


def fixture_flags(fx, results, model, r, cache):
    out = []
    for team in (fx["home"], fx["away"]):
        for f_ in team_flags(team, fx["date"], results, model, r, cache):
            f_["team"] = team
            out.append(f_)
    return out
