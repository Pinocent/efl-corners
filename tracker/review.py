"""
The weekly self-review, and the cautious corrections it's allowed to make.

After each gameweek is fully marked, the review checks every market the board
predicts, over its most recent few hundred predictions:

  * does it happen as often as the model says? (with the spread you'd expect
    from luck allowed for)
  * are the confident predictions too confident, or too timid? (the
    calibration slope from a logistic fit: 1.0 is right)
  * are the expected corners, cards and goals running high or low in any
    division?
  * are the calls landing at the rate the model gave them?

One gameweek is 30-odd matches, far too few to judge anything by, so nothing
is flagged unless it's well beyond chance (2.5 standard errors) over at
least 100 predictions.

The review may make two kinds of correction, to future predictions only;
predictions frozen at kick-off are never rewritten.

  level   a division's expected corners, cards or goals are raised or lowered
          when that division has run high or low over at least 150 matches.
          Capped at +/-10%.
  spread  the gap between each match's expectation and the division average
          is stretched (model too timid) or squeezed (too confident) when
          that's clear over at least 300 matches. Capped at 0.7x-1.3x.

Before either is applied, the older and newer halves of the evidence must
point the same way. Corrections are sized cautiously (part of the way to what
the data says, less when the evidence is thinner). Every 100 matches played
with a correction in force, it's checked: if the corrected expectations did
worse than the uncorrected ones would have, it's rolled back and not tried
again for eight weeks. A correction also lapses when the evidence for it
fades.

Corrections change the expected counts, not individual lines, so every line,
chance and call for a match stays consistent with the rest.

State lives in data/self_review.json: corrections in force, their history,
one snapshot per reviewed gameweek, and the date of the last human check-in.
"""

import json
import math
import os
from datetime import date, timedelta

WINDOW = 400            # predictions per market looked at
MIN_REVIEW = 100        # fewer than this: "not enough results yet"
Z_FLAG = 2.5            # beyond chance
Z_WATCH = 2.0
Z_KEEP = 1.5            # a correction in force stays while the evidence is at least this strong
LEVEL_MIN_N, LEVEL_CAP, LEVEL_MIN_GAP = 150, 0.10, 0.03
SPREAD_MIN_N, SPREAD_CAP, SPREAD_MIN_GAP = 300, 0.30, 0.10
RECHECK_N = 100         # matches played with a correction before it's re-tested
COOLDOWN_DAYS = 56
CHECKIN_WEEKS = 7
LEAGUES = ("Championship", "League 1", "League 2")
# component -> (key in the row's "raw" field, expected home/away fields, actual home/away fields, label)
COMPONENTS = {"corners": ("c", "eh", "ea", "hc", "ac", "corners"),
              "cards": ("k", "kh", "ka", "hk", "ak", "cards"),
              "goals": ("g", "lh", "la", "hg", "ag", "goals")}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- state

def load(path, today):
    if os.path.exists(path):
        with open(path) as fh:
            s = json.load(fh)
    else:
        s = {}
    s.setdefault("active", {})
    s.setdefault("history", [])
    s.setdefault("reviews", [])
    s.setdefault("reviewed", [])
    s.setdefault("cooldown", {})
    s.setdefault("last_checkin", today.isoformat())
    s.setdefault("version", "")
    return s


def save(path, state):
    state["reviews"] = state["reviews"][-60:]
    state["history"] = state["history"][-200:]
    with open(path, "w") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)


# ---------------------------------------------------------------- corrections

class Adjuster:
    """Applies the corrections in force to a match's expected (home, away) counts."""

    def __init__(self, state):
        self.active = state.get("active", {})
        self.version = state.get("version", "")

    def __call__(self, comp, league, a, b):
        return transform(self.active.get(comp), league, a, b)

    def any(self):
        return any(_in_force(v) for v in self.active.values())


def _in_force(act):
    if not act:
        return False
    return (any(abs(x - 1) > 1e-9 for x in act.get("level", {}).values())
            or abs(act.get("spread", 1.0) - 1) > 1e-9)


def transform(act, league, a, b):
    if not act or a is None or b is None:
        return a, b
    t = a + b
    if t <= 0:
        return a, b
    share = a / t
    s, c = act.get("spread", 1.0), act.get("centre", {}).get(league)
    if abs(s - 1) > 1e-9 and c:
        t = c + s * (t - c)
    t *= act.get("level", {}).get(league, 1.0)
    return max(t * share, 0.05), max(t * (1 - share), 0.05)


def _pairs(rows, comp):
    """(date, league, raw total, shown total, actual total) for every marked match."""
    key, eh, ea, ah, aa, _ = COMPONENTS[comp]
    out = []
    for r in rows:
        act_h, act_a = _f(r.get(ah)), _f(r.get(aa))
        if act_h is None or act_a is None:
            continue
        shown_h, shown_a = _f(r.get(eh)), _f(r.get(ea))
        if shown_h is None or shown_a is None:
            continue
        raw = r.get("raw")
        try:
            rh, ra = json.loads(raw)[key] if raw else (shown_h, shown_a)
        except (ValueError, KeyError, TypeError):
            rh, ra = shown_h, shown_a
        out.append((date.fromisoformat(r["date"]), r["league"], rh + ra, shown_h + shown_a, act_h + act_a))
    out.sort(key=lambda x: x[0])
    return out


def _level_stats(d):
    """ratio actual/expected (raw), z, and whether both halves agree."""
    if not d:
        return None
    e = sum(x[2] for x in d); a = sum(x[4] for x in d)
    var = sum((x[4] - x[2]) ** 2 for x in d)
    z = (a - e) / math.sqrt(var) if var > 0 else 0.0
    h = len(d) // 2
    r1 = sum(x[4] for x in d[:h]) / max(sum(x[2] for x in d[:h]), 1e-9)
    r2 = sum(x[4] for x in d[h:]) / max(sum(x[2] for x in d[h:]), 1e-9)
    ratio = a / e if e else 1.0
    agree = (r1 - 1) * (ratio - 1) > 0 and (r2 - 1) * (ratio - 1) > 0
    return {"n": len(d), "ratio": ratio, "z": z, "agree": agree}


def _slope(xs, ys):
    """OLS slope, its standard error."""
    n = len(xs)
    if n < 3:
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    res = sum((y - my - b * (x - mx)) ** 2 for x, y in zip(xs, ys)) / (n - 2)
    return b, math.sqrt(res / sxx)


def _spread_stats(d):
    """Does the actual total follow the expected one as closely as it should?"""
    if len(d) < 3:
        return None
    centre = {}
    for lg in {x[1] for x in d}:
        v = [x[2] for x in d if x[1] == lg]
        centre[lg] = sum(v) / len(v)
    act_c = {}
    for lg in centre:
        v = [x[4] for x in d if x[1] == lg]
        act_c[lg] = sum(v) / len(v)

    def fit(part):
        return _slope([x[2] - centre[x[1]] for x in part], [x[4] - act_c[x[1]] for x in part])
    b, se = fit(d)
    if b is None:
        return None
    h = len(d) // 2
    b1, _ = fit(d[:h]); b2, _ = fit(d[h:])
    agree = b1 is not None and b2 is not None and (b1 - 1) * (b - 1) > 0 and (b2 - 1) * (b - 1) > 0
    return {"n": len(d), "slope": b, "se": se, "z": (b - 1) / se if se else 0.0, "agree": agree,
            "centre": {k: round(v, 3) for k, v in centre.items()}}


def _log(state, today, comp, kind, league, old, new, why, stats):
    state["history"].append({"date": today.isoformat(), "component": comp, "kind": kind,
                             "league": league or "all", "from": round(old, 3), "to": round(new, 3),
                             "why": why, "n": stats.get("n") if stats else None})
    state["version"] = today.isoformat()


def _cooling(state, key, today):
    until = state["cooldown"].get(key)
    return bool(until) and date.fromisoformat(until) > today


def _recheck(state, rows, today):
    """Roll back any correction that made the matches played with it worse."""
    changes = []
    for comp, act in state["active"].items():
        if not _in_force(act):
            continue
        since = date.fromisoformat(act.get("since", today.isoformat()))
        played = [x for x in _pairs(rows, comp) if x[0] >= since]
        done = act.get("checked_n", 0)
        if len(played) - done < RECHECK_N:
            continue
        act["checked_n"] = len(played)
        sse_raw = sum((x[4] - x[2]) ** 2 for x in played)
        sse_used = sum((x[4] - x[3]) ** 2 for x in played)
        if sse_used > sse_raw:
            for lg, v in list(act.get("level", {}).items()):
                if abs(v - 1) > 1e-9:
                    _log(state, today, comp, "level", lg, v, 1.0,
                         f"rolled back: the {len(played)} matches since {since:%-d %b} came out better without it", None)
                    state["cooldown"][f"{comp}:level:{lg}"] = (today + timedelta(days=COOLDOWN_DAYS)).isoformat()
            if abs(act.get("spread", 1.0) - 1) > 1e-9:
                _log(state, today, comp, "spread", None, act["spread"], 1.0,
                     f"rolled back: the {len(played)} matches since {since:%-d %b} came out better without it", None)
                state["cooldown"][f"{comp}:spread"] = (today + timedelta(days=COOLDOWN_DAYS)).isoformat()
            state["active"][comp] = {"level": {}, "spread": 1.0, "centre": {}}
            changes.append(f"Rolled back the {COMPONENTS[comp][5]} correction: the {len(played)} matches played "
                           "with it would have been predicted better without it.")
    return changes


def decide(state, rows, today):
    """The weekly decisions. Returns plain-English notes on anything changed."""
    changes = _recheck(state, rows, today)
    for comp in COMPONENTS:
        label = COMPONENTS[comp][5]
        data = _pairs(rows, comp)
        act = state["active"].setdefault(comp, {"level": {}, "spread": 1.0, "centre": {}})
        before = _in_force(act)

        # level, per division
        for lg in LEAGUES:
            d = [x for x in data if x[1] == lg][-(2 * WINDOW):]
            st = _level_stats(d)
            cur = act["level"].get(lg, 1.0)
            key = f"{comp}:level:{lg}"
            if not st or st["n"] < LEVEL_MIN_N:
                continue
            strong = abs(st["z"]) >= Z_FLAG and abs(st["ratio"] - 1) >= LEVEL_MIN_GAP and st["agree"]
            target = 1 + (st["ratio"] - 1) * st["n"] / (st["n"] + LEVEL_MIN_N)
            target = min(max(target, 1 - LEVEL_CAP), 1 + LEVEL_CAP)
            if abs(cur - 1) < 1e-9:
                if strong and not _cooling(state, key, today):
                    act["level"][lg] = round(target, 3)
                    word = "raised" if target > 1 else "lowered"
                    _log(state, today, comp, "level", lg, cur, target,
                         f"{lg} {label} ran {abs(st['ratio'] - 1) * 100:.0f}% {'above' if st['ratio'] > 1 else 'below'} "
                         f"expectation over {st['n']} matches", st)
                    changes.append(f"{lg}: expected {label} {word} {abs(target - 1) * 100:.0f}%. Over the last "
                                   f"{st['n']} matches they came in {abs(st['ratio'] - 1) * 100:.0f}% "
                                   f"{'above' if st['ratio'] > 1 else 'below'} what the model expected.")
            elif abs(st["z"]) < Z_KEEP or not st["agree"]:
                act["level"][lg] = 1.0
                _log(state, today, comp, "level", lg, cur, 1.0, "lapsed: the evidence for it has faded", st)
                changes.append(f"{lg}: the {label} correction lapsed; recent matches no longer show a clear gap.")
            elif abs(target - cur) >= 0.02:
                act["level"][lg] = round(target, 3)
                _log(state, today, comp, "level", lg, cur, target, "resized to the latest evidence", st)

        # spread, all divisions together (each measured from its own average)
        st = _spread_stats(data[-(2 * WINDOW):])
        cur = act.get("spread", 1.0)
        key = f"{comp}:spread"
        if st and st["n"] >= SPREAD_MIN_N:
            strong = abs(st["z"]) >= Z_FLAG and abs(st["slope"] - 1) >= SPREAD_MIN_GAP and st["agree"]
            target = 1 + (st["slope"] - 1) * st["n"] / (st["n"] + SPREAD_MIN_N)
            target = min(max(target, 1 - SPREAD_CAP), 1 + SPREAD_CAP)
            if abs(cur - 1) < 1e-9:
                if strong and not _cooling(state, key, today):
                    act["spread"], act["centre"] = round(target, 3), st["centre"]
                    how = "bolder" if target > 1 else "more cautious"
                    _log(state, today, comp, "spread", None, cur, target,
                         f"expected {label} {'under' if st['slope'] > 1 else 'over'}stated the differences between matches "
                         f"(slope {st['slope']:.2f}, {st['n']} matches)", st)
                    changes.append(f"Expected {label} made {how} ({target:.2f}x the gap from each division's average): "
                                   f"over {st['n']} matches the model {'under' if st['slope'] > 1 else 'over'}stated "
                                   "how different matches would be.")
            elif abs(st["z"]) < Z_KEEP or not st["agree"]:
                act["spread"], act["centre"] = 1.0, {}
                _log(state, today, comp, "spread", None, cur, 1.0, "lapsed: the evidence for it has faded", st)
                changes.append(f"The {label} spread correction lapsed; recent matches no longer show a clear pattern.")
            elif abs(target - cur) >= 0.05:
                act["spread"], act["centre"] = round(target, 3), st["centre"]
                _log(state, today, comp, "spread", None, cur, target, "resized to the latest evidence", st)

        if _in_force(act) and not before:
            act["since"], act["checked_n"] = today.isoformat(), 0
    return changes


# ---------------------------------------------------------------- health

def _prob_markets(r):
    """[(market, chance, happened)] for one marked prediction row."""
    g = lambda k: _f(r.get(k))
    hc, ac, hg, ag, hk, ak = (g(k) for k in ("hc", "ac", "hg", "ag", "hk", "ak"))
    out = []
    if hc is not None and ac is not None:
        t = hc + ac
        out += [("Corners over 9.5", g("o95"), t > 9.5), ("Corners over 10.5", g("o105"), t > 10.5),
                ("Both teams 4+ corners", g("both4"), hc >= 4 and ac >= 4),
                ("Corner dominance", g("dom_home"), hc > ac), ("Corner dominance", g("dom_away"), ac > hc)]
    if hg is not None and ag is not None:
        out += [("Both teams to score", g("btts"), hg > 0 and ag > 0), ("Over 2.5 goals", g("o25"), hg + ag > 2),
                ("Clean sheets", g("cs_home"), ag == 0), ("Clean sheets", g("cs_away"), hg == 0)]
    if hk is not None and ak is not None:
        out += [("Cards over 3.5", g("k35"), hk + ak > 3.5), ("Cards over 4.5", g("k45"), hk + ak > 4.5),
                ("Each side 2+ cards", g("khome2"), hk >= 2), ("Each side 2+ cards", g("kaway2"), ak >= 2),
                ("A red card", g("red"), (g("hred") or 0) > 0)]
    return [(m, p, bool(o)) for m, p, o in out if p is not None]


def _logit(p):
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


def calibration_slope(ps, os_):
    """Logistic fit of outcome on logit(chance): (slope, standard error)."""
    xs = [_logit(p) for p in ps]
    a, b = 0.0, 1.0
    for _ in range(30):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, o in zip(xs, os_):
            q = 1 / (1 + math.exp(-(a + b * x)))
            w = q * (1 - q)
            g0 += o - q; g1 += (o - q) * x
            h00 += w; h01 += w * x; h11 += w * x * x
        det = h00 * h11 - h01 * h01
        if det <= 1e-12:
            return None, None
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a, b = a + da, b + db
        if abs(da) < 1e-8 and abs(db) < 1e-8:
            break
    return b, math.sqrt(h00 / det)


def health(rows, marked):
    """
    rows: marked prediction rows (v3), any order. marked: the same with their
    calls marked. Returns the review tables.
    """
    rows = sorted(rows, key=lambda r: (r["date"], r.get("time") or ""))
    by = {}
    for r in rows:
        for m, p, o in _prob_markets(r):
            by.setdefault(m, []).append((p, o))
    markets = []
    for m, xs in by.items():
        xs = xs[-WINDOW:]
        n = len(xs)
        said = sum(p for p, _ in xs) / n
        hap = sum(o for _, o in xs) / n
        var = sum(p * (1 - p) for p, _ in xs)
        z = (sum(o for _, o in xs) - sum(p for p, _ in xs)) / math.sqrt(var) if var > 0 else 0.0
        slope = se = None
        if n >= 200:
            slope, se = calibration_slope([p for p, _ in xs], [o for _, o in xs])
        zc = (slope - 1) / se if slope is not None and se else 0.0
        if n < MIN_REVIEW:
            verdict, cls = "Not enough results yet", "meh"
        elif abs(z) >= Z_FLAG:
            verdict, cls = ("Running low" if z > 0 else "Running high"), "bad"
        elif n >= 200 and abs(zc) >= Z_FLAG:
            verdict, cls = ("Too cautious" if zc > 0 else "Too confident"), "bad"
        elif abs(z) >= Z_WATCH or (n >= 200 and abs(zc) >= Z_WATCH):
            verdict, cls = "Worth watching", "meh"
        else:
            verdict, cls = "On target", "good"
        markets.append({"market": m, "n": n, "said": round(said, 3), "happened": round(hap, 3), "z": round(z, 2),
                        "slope": round(slope, 2) if slope is not None else None, "zc": round(zc, 2),
                        "verdict": verdict, "cls": cls})
    order = ["Corners over 9.5", "Corners over 10.5", "Corner dominance", "Both teams 4+ corners",
             "Cards over 3.5", "Cards over 4.5", "Each side 2+ cards", "A red card",
             "Both teams to score", "Over 2.5 goals", "Clean sheets"]
    markets.sort(key=lambda x: order.index(x["market"]) if x["market"] in order else 99)

    counts = []
    for comp in COMPONENTS:
        data = _pairs(rows, comp)
        for lg in LEAGUES:
            d = [x for x in data if x[1] == lg][-WINDOW:]
            if not d:
                continue
            e = sum(x[3] for x in d); a = sum(x[4] for x in d)
            var = sum((x[4] - x[3]) ** 2 for x in d)
            z = (a - e) / math.sqrt(var) if var > 0 else 0.0
            n = len(d)
            verdict, cls = (("Not enough results yet", "meh") if n < MIN_REVIEW else
                            (("Running low" if z > 0 else "Running high"), "bad") if abs(z) >= Z_FLAG else
                            ("Worth watching", "meh") if abs(z) >= Z_WATCH else ("On target", "good"))
            counts.append({"what": COMPONENTS[comp][5], "league": lg, "n": n, "expected": round(e / n, 2),
                           "actual": round(a / n, 2), "z": round(z, 2), "verdict": verdict, "cls": cls})

    calls = {}
    for r in marked:
        for c in r.get("calls") or []:
            if c.get("hit") is None:
                continue
            v = calls.setdefault(c["market"], [0, 0, 0.0, 0.0])
            v[0] += 1; v[1] += c["hit"]; v[2] += c["p"]; v[3] += c["p"] * (1 - c["p"])
    call_rows = []
    for m, (n, h, sp, var) in sorted(calls.items(), key=lambda kv: -kv[1][0]):
        z = (h - sp) / math.sqrt(var) if var > 0 else 0.0
        verdict, cls = (("Not enough calls yet", "meh") if n < 30 else
                        ("Landing below what it said", "bad") if z <= -Z_FLAG else
                        ("Landing above what it said", "good") if z >= Z_FLAG else
                        ("Worth watching", "meh") if z <= -Z_WATCH else ("As expected", "good"))
        call_rows.append({"market": m, "n": n, "hits": h, "said": round(sp / n, 3), "rate": round(h / n, 3),
                          "z": round(z, 2), "verdict": verdict, "cls": cls})
    return {"markets": markets, "counts": counts, "calls": call_rows}


CALL_NAME = {"Corners total": "Corner total", "Corner dominance": "Corner dominance", "Cards total": "Card total",
             "Both 4+ corners": "Both teams 4+ corners", "BTTS": "Both teams to score", "Goals 2.5": "Over/under 2.5 goals"}


def flags(h):
    """Plain-English notes for anything beyond chance."""
    out = []
    for m in h["markets"]:
        if m["verdict"] in ("Running low", "Running high"):
            out.append(f"{m['market']}: happened {m['happened'] * 100:.0f}% of the time when the model said "
                       f"{m['said'] * 100:.0f}%, over {m['n']} predictions. The model is {'too low' if m['verdict'] == 'Running low' else 'too high'} here.")
        elif m["verdict"] in ("Too confident", "Too cautious"):
            out.append(f"{m['market']}: the model's strongest predictions have been "
                       f"{'too confident' if m['verdict'] == 'Too confident' else 'too cautious'} over {m['n']} predictions "
                       f"(calibration slope {m['slope']:.2f}; 1.00 is right).")
    for c in h["counts"]:
        if c["verdict"] in ("Running low", "Running high"):
            gap = (c["actual"] / c["expected"] - 1) * 100 if c["expected"] else 0
            out.append(f"{c['league']} {c['what']}: {c['actual']:.2f} a match against {c['expected']:.2f} expected "
                       f"({gap:+.0f}%) over {c['n']} matches.")
    for c in h["calls"]:
        if c["verdict"] == "Landing below what it said":
            out.append(f"{CALL_NAME.get(c['market'], c['market'])} calls: {c['hits']} of {c['n']} landed "
                       f"({c['rate'] * 100:.0f}%) against the {c['said'] * 100:.0f}% the model gave them.")
    return out


# ---------------------------------------------------------------- the weekly run

def weekly(state, rows, marked, rounds, today):
    """
    Review the latest fully-marked gameweek not yet reviewed (at most one a
    run). Returns the new snapshot, or None when there's nothing new.
    """
    done_ids = []
    for rid, r in rounds.items():
        if r["end"] >= today:
            continue
        rs = [x for x in rows if x.get("round") == rid]
        marked_n = sum(_f(x.get("hc")) is not None and _f(x.get("hg")) is not None for x in rs)
        # a gameweek counts as done once every match is marked, or four days on
        # with at least three quarters marked (postponements shouldn't stall the
        # review; late results are picked up by the next one)
        if rs and (marked_n == len(rs) or ((today - r["end"]).days >= 4 and marked_n >= 0.75 * len(rs))):
            done_ids.append(rid)
    todo = [rid for rid in sorted(done_ids) if rid not in state["reviewed"]]
    if not todo:
        return None
    rid = todo[-1]                                   # catch up in one go
    upto = [x for x in rows if x.get("round") and x["round"] <= rid]
    upto_marked = [x for x in marked if x.get("round") and x["round"] <= rid]
    changes = decide(state, upto, today)
    h = health(upto, upto_marked)
    snap = {"date": today.isoformat(), "round": rid, "gameweek": rounds[rid].get("n"),
            "label": rounds[rid].get("label"), "flags": flags(h), "changes": changes,
            "matches": len([x for x in upto if _f(x.get("hc")) is not None])}
    state["reviews"].append(snap)
    state["reviewed"] = sorted(set(state["reviewed"]) | set(todo))
    return snap


def checkin_due(state, today):
    last = date.fromisoformat(state.get("last_checkin") or today.isoformat())
    due = last + timedelta(weeks=CHECKIN_WEEKS)
    return due, today >= due


def summary(state, today):
    """What the page and the nightly report show."""
    due, is_due = checkin_due(state, today)
    active = []
    for comp, act in state["active"].items():
        for lg, v in act.get("level", {}).items():
            if abs(v - 1) > 1e-9:
                active.append({"what": COMPONENTS[comp][5], "league": lg, "kind": "level", "factor": v,
                               "text": f"{lg}: expected {COMPONENTS[comp][5]} {'raised' if v > 1 else 'lowered'} {abs(v - 1) * 100:.0f}%",
                               "since": act.get("since")})
        if abs(act.get("spread", 1.0) - 1) > 1e-9:
            s = act["spread"]
            active.append({"what": COMPONENTS[comp][5], "league": "all", "kind": "spread", "factor": s,
                           "text": f"Expected {COMPONENTS[comp][5]} made {'bolder' if s > 1 else 'more cautious'} ({s:.2f}x the gap from each division's average)",
                           "since": act.get("since")})
    return {"latest": state["reviews"][-1] if state["reviews"] else None,
            "reviews": state["reviews"][-8:], "active": active, "history": state["history"][-12:],
            "last_checkin": state.get("last_checkin"), "next_checkin": due.isoformat(), "checkin_due": is_due}
