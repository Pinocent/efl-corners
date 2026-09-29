"""
The prediction log, and marking it against what happened.

Predictions live in data/predictions.csv, one row per fixture. A row is
refreshed on every run until kick-off, then frozen. The calls are worked out
and stored in the row at the same moment, together with the corners line
they were made against, so changing a threshold or the league's usual line
later can never rewrite what was called in the past.

A "call" is a prediction confident enough to act on. Only calls are counted
in hit rates; every probability is also scored with a Brier score against
the league-average rate as it stood before that gameweek, which says whether
the model knows anything beyond "corners in League 2 average about 10".
"""

import json
import math
from datetime import date, datetime, time as dtime

from .markets import TOTAL_LINES

try:
    from zoneinfo import ZoneInfo
    UK = ZoneInfo("Europe/London")
except Exception:                        # very old Python: treat times as UTC
    UK = None

# how sure the model must be before a prediction becomes a call
# "dom" is a side taking more corners than the other (a level count is a miss).
# Chosen on 2018-19 to 2023-24: at 65% about 12% of matches qualified and the
# favoured side won more corners 72% of the time; on 2024-25 and 2025-26,
# which it hadn't seen, 17% qualified and 75% landed.
CALL = {"total": 0.58, "both4": 0.60, "btts": 0.60, "o25": 0.60, "cards": 0.60, "dom": 0.65}

# Prediction tags - not calls. Each marks where the model says something
# clearly different from the norm (rates from replaying 2024-25 and 2025-26).
TAG = {
    "big_gap": 2.0,   # expected corners differ by this much
    "low_u3": 0.30,   # side's chance of 0-2 corners
    "cs": 0.35,       # clean sheet chance
}

FIELDS = (["model", "made", "round", "date", "time", "league", "home", "away",
           "eh", "ea", "et", "main_line"] +
          [f"o{str(l).replace('.', '')}" for l in TOTAL_LINES] +
          ["home4", "away4", "both4", "home_u3", "away_u3", "home_more", "dom_home", "dom_away",
           "ph", "pa", "lh", "la", "p_home", "p_draw", "p_away", "btts", "o25",
           "cs_home", "cs_away", "sample",
           "ref", "ref_f", "kh", "ka", "kt", "k25", "k35", "k45", "k55", "k65",
           "khome2", "kaway2", "red", "kph", "kpa", "kline",
           "line", "calls", "hc", "ac", "hg", "ag", "hk", "ak", "hred"])
CARD_LINES = (2.5, 3.5, 4.5, 5.5, 6.5)


def _k(line):
    return f"o{str(line).replace('.', '')}"


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kickoff(row):
    """Kick-off as an aware datetime (UK time; fixture lists use UK time)."""
    d = date.fromisoformat(row["date"])
    try:
        hh, mm = (int(x) for x in (row.get("time") or "12:00").split(":")[:2])
    except ValueError:
        hh, mm = 12, 0
    dt = datetime.combine(d, dtime(hh, mm))
    return dt.replace(tzinfo=UK) if UK else dt


def started(row, now):
    ko = kickoff(row)
    return (now if ko.tzinfo else now.replace(tzinfo=None)) >= ko


# Bookmakers set a corners main line near the league's typical total, so a
# call is only interesting against that line - "over 7.5" lands most weeks
# and pays next to nothing. run.py sets these from this season's matches.
BOOK_LINE = {"Championship": 10.5, "League 1": 10.5, "League 2": 10.5}
CARD_LINE = {"Championship": 3.5, "League 1": 3.5, "League 2": 3.5}


def book_lines(rates):
    for lg, r in rates.items():
        BOOK_LINE[lg] = math.floor(r["avg_corners"]) + 0.5
        # the cards line bookmakers lead with sits where matches go over about
        # half the time: 3.5 or 4.5, whichever is nearer a coin flip this season
        if r.get("k35") is not None and r.get("k45") is not None:
            CARD_LINE[lg] = 3.5 if abs(r["k35"] - 0.5) <= abs(r["k45"] - 0.5) else 4.5


def to_row(fx, pred, made, model="v3", cards=None):
    c, g = pred["corners"], pred["goals"]
    row = {"model": model, "made": made.isoformat(), "round": fx.get("round", ""),
           "date": fx["date"].isoformat(), "time": fx.get("time", ""),
           "league": fx["league"], "home": fx["home"], "away": fx["away"],
           "eh": c["eh"], "ea": c["ea"], "et": c["et"], "main_line": c["main_line"],
           "home4": c["home4"], "away4": c["away4"], "both4": c["both4"],
           "home_u3": c["home_u3"], "away_u3": c["away_u3"], "home_more": c["home_more"],
           "dom_home": c["dom_home"], "dom_away": c["dom_away"],
           "ph": ";".join(f"{x:.4f}" for x in c["ph"]),
           "pa": ";".join(f"{x:.4f}" for x in c["pa"]),
           "lh": g["lh"], "la": g["la"], "p_home": g["home"], "p_draw": g["draw"],
           "p_away": g["away"], "btts": g["btts"], "o25": g["o25"],
           "cs_home": g["cs_home"], "cs_away": g["cs_away"],
           "sample": pred.get("sample", "")}
    for l in TOTAL_LINES:
        row[_k(l)] = c["totals"][l]
    if cards:
        row.update(ref=cards["ref"], ref_f=cards["ref_factor"], kh=cards["kh"], ka=cards["ka"],
                   kt=cards["kt"], khome2=cards["home2"], kaway2=cards["away2"], red=cards["red"],
                   kph=";".join(f"{x:.4f}" for x in cards["kph"]),
                   kpa=";".join(f"{x:.4f}" for x in cards["kpa"]))
        for l in CARD_LINES:
            row[_kk(l)] = cards["ktotals"][l]
    freeze_calls(row)
    return row


def _kk(line):
    return f"k{str(line).replace('.', '')}"


def freeze_calls(row):
    """Work out the row's calls now and store them with the line used."""
    row["line"] = BOOK_LINE.get(row["league"], 10.5)
    row["kline"] = CARD_LINE.get(row["league"], 3.5) if row.get("kt") not in (None, "") else ""
    row["calls"] = json.dumps(calls_for(row))


def calls_for(p):
    """The confident calls in one prediction: [{market, pick, p}]."""
    out = []
    lg = p["league"]

    # corner totals, against the league's usual line; unders only where offered
    l = f(p.get("line")) or BOOK_LINE.get(lg, 10.5)
    q = f(p.get(_k(l)))
    if q is not None and q >= CALL["total"]:
        out.append(("Corners total", f"Over {l}", q))
    elif q is not None and lg != "League 2" and 1 - q >= CALL["total"]:
        out.append(("Corners total", f"Under {l}", 1 - q))

    # cards, against the league's usual cards line (over and under both offered)
    kl = f(p.get("kline"))
    kq = f(p.get(_kk(kl))) if kl else None
    if kq is not None and kq >= CALL["cards"]:
        out.append(("Cards total", f"Over {kl}", kq))
    elif kq is not None and 1 - kq >= CALL["cards"]:
        out.append(("Cards total", f"Under {kl}", 1 - kq))

    # corner dominance: which side takes more corners than the other
    d = dominance_call(p)
    if d:
        out.append(d)

    b4 = f(p.get("both4"))
    if b4 is not None and b4 >= CALL["both4"]:
        out.append(("Both 4+ corners", "Yes", b4))

    bt = f(p.get("btts"))
    if bt is not None:
        if bt >= CALL["btts"]:
            out.append(("BTTS", "Yes", bt))
        elif 1 - bt >= CALL["btts"]:
            out.append(("BTTS", "No", 1 - bt))
    o = f(p.get("o25"))
    if o is not None:
        if o >= CALL["o25"]:
            out.append(("Goals 2.5", "Over", o))
        elif 1 - o >= CALL["o25"]:
            out.append(("Goals 2.5", "Under", 1 - o))
    return [{"market": m, "pick": k, "p": round(v, 3)} for m, k, v in out]


def dominance_call(p):
    """(market, pick, chance) if one side is confidently expected to win more corners."""
    dh, da = f(p.get("dom_home")), f(p.get("dom_away"))
    if dh is not None and dh >= CALL["dom"]:
        return ("Corner dominance", "Home", dh)
    if da is not None and da >= CALL["dom"]:
        return ("Corner dominance", "Away", da)
    return None


def add_call(row, call):
    """Add one (market, pick, chance) call to a stored row, once."""
    calls = json.loads(row["calls"]) if row.get("calls") else []
    if call and not any(c["market"] == call[0] for c in calls):
        calls.append({"market": call[0], "pick": call[1], "p": round(call[2], 3)})
        row["calls"] = json.dumps(calls)


def landed(call, hc, ac, hg, ag, hk=None, ak=None):
    """Did a stored call come off? (None if it can't be marked yet)"""
    m, pick = call["market"], call["pick"]
    if m == "Cards total":
        if hk is None or ak is None:
            return None
        side, line = pick.split()
        return hk + ak > float(line) if side == "Over" else hk + ak < float(line)
    if m == "Corners total":
        side, line = pick.split()
        return hc + ac > float(line) if side == "Over" else hc + ac < float(line)
    if m == "Corner dominance":
        return hc > ac if pick == "Home" else ac > hc
    if m == "Both 4+ corners":
        return hc >= 4 and ac >= 4
    if m == "BTTS":
        return (hg > 0 and ag > 0) == (pick == "Yes")
    if m == "Goals 2.5":
        return hg + ag > 2 if pick == "Over" else hg + ag < 3
    raise ValueError(f"unknown market {m}")


def upsert(store, rows, now):
    """
    store: {(model, home, away, date): row}. A stored row is replaced only
    while its match hasn't kicked off.
    """
    n = 0
    for r in rows:
        key = (r["model"], r["home"], r["away"], r["date"])
        old = store.get(key)
        if old and started(old, now):
            continue                      # frozen at kick-off
        store[key] = r
        n += 1
    return n


def settle(store, results):
    """
    Copy each finished match's result into its prediction row, so the log
    keeps working after the results feed rolls over to a new season.
    """
    idx = {}
    for m in results:
        idx.setdefault((m["home"], m["away"]), []).append(m)
    for p in store.values():
        d = date.fromisoformat(p["date"])
        m = next((m for m in idx.get((p["home"], p["away"]), [])
                  if abs((m["date"] - d).days) <= 7), None)
        if m and m.get("hc") is not None and m.get("hg") is not None:
            p.update(hc=int(m["hc"]), ac=int(m["ac"]), hg=int(m["hg"]), ag=int(m["ag"]))
            if m.get("hy") is not None and m.get("ay") is not None:
                p.update(hk=int(m["hy"] + (m.get("hr") or 0)), ak=int(m["ay"] + (m.get("ar") or 0)),
                         hred=int(((m.get("hr") or 0) + (m.get("ar") or 0)) > 0))


def result(p):
    """(hc, ac, hg, ag) as ints, or None if not played / not known."""
    v = [f(p.get(k)) for k in ("hc", "ac", "hg", "ag")]
    return None if None in v else tuple(int(x) for x in v)


def cards_result(p):
    v = [f(p.get(k)) for k in ("hk", "ak")]
    return None if None in v else tuple(int(x) for x in v)


def mark(p):
    """Stored calls, each with hit/miss once the result is in."""
    calls = json.loads(p["calls"]) if p.get("calls") else []
    res = result(p)
    if res:
        kr = cards_result(p) or (None, None)
        for c in calls:
            hit = landed(c, *res, *kr)
            if hit is not None:
                c["hit"] = bool(hit)
    return calls


def brier_block(rows, rates_for):
    """
    Brier score per market for the model vs the league-average rate as it
    stood before each match's gameweek. Skill > 0 means the model beats
    simply quoting that rate for every match.
    """
    acc = {}

    def add(name, p, base, outcome):
        if p is None or base is None:
            return
        a = acc.setdefault(name, [0, 0.0, 0.0])
        a[0] += 1
        a[1] += (p - outcome) ** 2
        a[2] += (base - outcome) ** 2

    for r in rows:
        res = result(r)
        if not res:
            continue
        hc, ac, hg, ag = res
        lr = rates_for(r).get(r["league"], {})
        tot = hc + ac
        add("Corners over 9.5", f(r.get("o95")), lr.get("o95"), tot > 9.5)
        add("Corners over 10.5", f(r.get("o105")), lr.get("o105"), tot > 10.5)
        add("Both 4+ corners", f(r.get("both4")), lr.get("both4"), hc >= 4 and ac >= 4)
        if hc != ac:
            add("Who wins more corners", f(r.get("home_more")), lr.get("home_more"), hc > ac)
        add("BTTS", f(r.get("btts")), lr.get("btts"), hg > 0 and ag > 0)
        add("Over 2.5 goals", f(r.get("o25")), lr.get("o25"), hg + ag > 2)
        add("Home clean sheet", f(r.get("cs_home")), lr.get("cs_home"), ag == 0)
        add("Away clean sheet", f(r.get("cs_away")), lr.get("cs_away"), hg == 0)
        kr = cards_result(r)
        if kr:
            add("Cards over 3.5", f(r.get("k35")), lr.get("k35"), kr[0] + kr[1] > 3.5)
            add("Cards over 4.5", f(r.get("k45")), lr.get("k45"), kr[0] + kr[1] > 4.5)
    return [{"market": k, "n": v[0], "brier": v[1] / v[0], "base": v[2] / v[0],
             "skill": 1 - v[1] / v[2] if v[2] else None} for k, v in acc.items()]


def league_rates(results, before, fallback=None, min_matches=30):
    """
    Plain league frequencies from matches before a date - the baseline any
    model has to beat. Until a league has min_matches this season, last
    season's rates (fallback) stand in.
    """
    out = {}
    for lg in {m["league"] for m in list(results) + list(fallback or [])}:
        ms = [m for m in results if m["league"] == lg and m["date"] < before
              and m.get("hc") is not None and m.get("hg") is not None]
        if len(ms) < min_matches and fallback:
            ms = [m for m in fallback if m["league"] == lg and m.get("hc") is not None
                  and m.get("hg") is not None] or ms
        if not ms:
            continue
        n = len(ms)
        dec = [m for m in ms if m["hc"] != m["ac"]]
        out[lg] = {
            "o95": sum(m["hc"] + m["ac"] > 9.5 for m in ms) / n,
            "o105": sum(m["hc"] + m["ac"] > 10.5 for m in ms) / n,
            "both4": sum(m["hc"] >= 4 and m["ac"] >= 4 for m in ms) / n,
            "home_more": sum(m["hc"] > m["ac"] for m in dec) / max(len(dec), 1),
            "btts": sum(m["hg"] > 0 and m["ag"] > 0 for m in ms) / n,
            "o25": sum(m["hg"] + m["ag"] > 2 for m in ms) / n,
            "cs_home": sum(m["ag"] == 0 for m in ms) / n,
            "cs_away": sum(m["hg"] == 0 for m in ms) / n,
            "cs": sum((m["ag"] == 0) + (m["hg"] == 0) for m in ms) / (2 * n),
            "u3": sum((m["hc"] < 3) + (m["ac"] < 3) for m in ms) / (2 * n),
            "avg_corners": sum(m["hc"] + m["ac"] for m in ms) / n,
        }
        ks = [m for m in ms if m.get("hy") is not None and m.get("ay") is not None]
        if ks:
            tk = [m["hy"] + m["ay"] + (m.get("hr") or 0) + (m.get("ar") or 0) for m in ks]
            out[lg].update(avg_cards=sum(tk) / len(ks), k35=sum(t > 3.5 for t in tk) / len(ks),
                           k45=sum(t > 4.5 for t in tk) / len(ks),
                           red=sum(((m.get("hr") or 0) + (m.get("ar") or 0)) > 0 for m in ks) / len(ks))
    return out
