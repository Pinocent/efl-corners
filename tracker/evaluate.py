"""
The prediction log, and marking it against what happened.

Predictions are stored in data/predictions.csv. A fixture's prediction is
refreshed on every run until its match day, then frozen - it is never
rewritten once the match could have started, so the record is honest.

A "call" is a prediction confident enough to act on. Only calls are counted
in hit rates; every probability is also scored with a Brier score against
the league-average baseline, which says whether the model knows anything
at all beyond "corners in League 2 average about 10".
"""

import math
from datetime import date

TOTAL_LINES = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5)

# how sure the model must be before a prediction becomes a call
CALL = {"total": 0.58, "both4": 0.60, "btts": 0.60, "o25": 0.60}

# Prediction tags - not calls. Each marks where the model says something
# clearly different from the norm; the rates in brackets are from replaying
# the last two seasons.
TAG = {
    "big_gap": 2.0,   # expected corners differ by this much (fav won more ~80%)
    "low_u3": 0.30,   # side's chance of 0-2 corners (happened ~35%, usual 19%)
    "cs": 0.35,       # clean sheet chance (kept ~37%, usual 27%)
}

FIELDS = (["model", "made", "round", "date", "time", "league", "home", "away",
           "eh", "ea", "et", "main_line"] +
          [f"o{str(l).replace('.', '')}" for l in TOTAL_LINES] +
          ["home4", "away4", "both4", "home_u3", "away_u3", "home_more",
           "lh", "la", "p_home", "p_draw", "p_away", "btts", "o25", "cs_home",
           "cs_away", "sample"])


def _k(line):
    return f"o{str(line).replace('.', '')}"


def to_row(fx, pred, made, model="v3"):
    c, g = pred["corners"], pred["goals"]
    row = {"model": model, "made": made.isoformat(), "round": fx.get("round", ""),
           "date": fx["date"].isoformat(), "time": fx.get("time", ""),
           "league": fx["league"], "home": fx["home"], "away": fx["away"],
           "eh": c["eh"], "ea": c["ea"], "et": c["et"], "main_line": c["main_line"],
           "home4": c["home4"], "away4": c["away4"], "both4": c["both4"],
           "home_u3": c["home_u3"], "away_u3": c["away_u3"],
           "home_more": c["home_more"],
           "lh": g["lh"], "la": g["la"], "p_home": g["home"], "p_draw": g["draw"],
           "p_away": g["away"], "btts": g["btts"], "o25": g["o25"],
           "cs_home": g["cs_home"], "cs_away": g["cs_away"],
           "sample": pred.get("sample", "")}
    for l in TOTAL_LINES:
        row[_k(l)] = c["totals"][l]
    return row


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def upsert(store, rows, today):
    """
    store: {(model, home, away, date): row}. A stored row is replaced only
    while its match is still to come.
    """
    n = 0
    for r in rows:
        key = (r["model"], r["home"], r["away"], r["date"])
        old = store.get(key)
        if old and date.fromisoformat(old["date"]) < today:
            continue                      # frozen: match day has passed
        store[key] = r
        n += 1
    return n


# Bookmakers set a corners main line near the league's typical total, so a
# call is only interesting against that line - "over 7.5" lands most weeks
# and pays next to nothing. run.py replaces these with this season's figures.
BOOK_LINE = {"Championship": 10.5, "League 1": 10.5, "League 2": 9.5}


def book_lines(rates):
    for lg, r in rates.items():
        BOOK_LINE[lg] = math.floor(r["avg_corners"]) + 0.5


def calls_for(p):
    """The confident calls in one stored prediction: [(market, label, prob, check)]."""
    out = []
    lg = p["league"]

    # totals, against the league's usual main line; unders only where offered
    l = BOOK_LINE.get(lg, 10.5)
    q = f(p.get(_k(l)))
    if q is not None and q >= CALL["total"]:
        out.append(("Corners total", f"Over {l}", q, lambda hc, ac, hg, ag, l=l: hc + ac > l))
    elif q is not None and lg != "League 2" and 1 - q >= CALL["total"]:
        out.append(("Corners total", f"Under {l}", 1 - q, lambda hc, ac, hg, ag, l=l: hc + ac < l))

    b4 = f(p.get("both4"))
    if b4 is not None and b4 >= CALL["both4"]:
        out.append(("Both 4+ corners", "Yes", b4, lambda hc, ac, hg, ag: hc >= 4 and ac >= 4))

    bt = f(p.get("btts"))
    if bt is not None:
        if bt >= CALL["btts"]:
            out.append(("BTTS", "Yes", bt, lambda hc, ac, hg, ag: hg > 0 and ag > 0))
        elif 1 - bt >= CALL["btts"]:
            out.append(("BTTS", "No", 1 - bt, lambda hc, ac, hg, ag: not (hg > 0 and ag > 0)))
    o = f(p.get("o25"))
    if o is not None:
        if o >= CALL["o25"]:
            out.append(("Goals 2.5", "Over", o, lambda hc, ac, hg, ag: hg + ag > 2))
        elif 1 - o >= CALL["o25"]:
            out.append(("Goals 2.5", "Under", 1 - o, lambda hc, ac, hg, ag: hg + ag < 3))
    return out


def settle(store, results):
    """Attach actual results (same fixture within a week) to each prediction."""
    idx = {}
    for m in results:
        idx.setdefault((m["home"], m["away"]), []).append(m)
    out = []
    for p in store.values():
        d = date.fromisoformat(p["date"])
        m = next((m for m in idx.get((p["home"], p["away"]), [])
                  if abs((m["date"] - d).days) <= 7), None)
        r = dict(p)
        if m and m.get("hc") is not None and m.get("hg") is not None:
            r.update(hc=int(m["hc"]), ac=int(m["ac"]), hg=int(m["hg"]), ag=int(m["ag"]))
        out.append(r)
    return out


def mark(r):
    """Calls for a settled prediction, with hit/miss."""
    if r.get("hc") is None:
        return []
    return [{"market": mk, "pick": lab, "p": round(q, 3),
             "hit": bool(chk(r["hc"], r["ac"], r["hg"], r["ag"]))}
            for mk, lab, q, chk in calls_for(r)]


def brier_block(settled, league_rates):
    """
    Brier score per market for the model vs a league-average baseline.
    Skill > 0 means the model beats just quoting the league average.
    """
    acc = {}

    def add(name, p, base, outcome):
        if p is None or base is None:
            return
        a = acc.setdefault(name, [0, 0.0, 0.0])
        a[0] += 1
        a[1] += (p - outcome) ** 2
        a[2] += (base - outcome) ** 2

    for r in settled:
        if r.get("hc") is None:
            continue
        lr = league_rates.get(r["league"], {})
        tot = r["hc"] + r["ac"]
        add("Corners over 9.5", f(r.get("o95")), lr.get("o95"), tot > 9.5)
        add("Corners over 10.5", f(r.get("o105")), lr.get("o105"), tot > 10.5)
        add("Both 4+ corners", f(r.get("both4")), lr.get("both4"), r["hc"] >= 4 and r["ac"] >= 4)
        if r["hc"] != r["ac"]:
            add("Who wins more corners", f(r.get("home_more")), lr.get("home_more"), r["hc"] > r["ac"])
        add("BTTS", f(r.get("btts")), lr.get("btts"), r["hg"] > 0 and r["ag"] > 0)
        add("Over 2.5 goals", f(r.get("o25")), lr.get("o25"), r["hg"] + r["ag"] > 2)
        add("Home clean sheet", f(r.get("cs_home")), lr.get("cs_home"), r["ag"] == 0)
        add("Away clean sheet", f(r.get("cs_away")), lr.get("cs_away"), r["hg"] == 0)
    return [{"market": k, "n": v[0], "brier": v[1] / v[0], "base": v[2] / v[0],
             "skill": 1 - v[1] / v[2] if v[2] else None} for k, v in acc.items()]


def league_rates(results, before):
    """Plain league frequencies from matches before a date - the baseline."""
    out = {}
    for lg in {m["league"] for m in results}:
        ms = [m for m in results if m["league"] == lg and m["date"] < before
              and m.get("hc") is not None and m.get("hg") is not None]
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
    return out
