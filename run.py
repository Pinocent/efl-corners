#!/usr/bin/env python3
"""
Weekly (or daily) update. No arguments, no third-party packages.

  1. downloads results (goals, corners, xG, shots, odds) + the fixture list
  2. merges manual_results.csv for matches the feed hasn't published yet
  3. works out the gameweeks from the dates
  4. fits the model and predicts the upcoming rounds
  5. logs predictions (frozen at kick-off) and marks past ones
  6. writes data/*.csv and the dashboard at docs/index.html

Runs the same on a Mac or in GitHub Actions.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

from tracker import evaluate as ev
from tracker import markets, sources
from tracker.model import PARAMS, Model
from tracker.rounds import detect_rounds
from tracker.teams import unknown_names

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "cache")
DOCS = os.path.join(HERE, "docs")
PRED_FILE = os.path.join(DATA, "predictions.csv")
MATCH_FILE = os.path.join(DATA, "matches.csv")
MANUAL_FILE = os.path.join(HERE, "manual_results.csv")
TEMPLATE = os.path.join(HERE, "tracker", "dashboard.html")
LOOKAHEAD_DAYS = 10     # show every round starting within this many days


def load_store():
    return {(r["model"], r["home"], r["away"], r["date"]): r
            for r in sources.read_csv(PRED_FILE)}


def import_legacy(store):
    """One-off: bring the old spreadsheet's predictions across as model 'v2'."""
    path = os.path.join(HERE, "corners_tracker.xlsx")
    if any(k[0] == "v2" for k in store) or not os.path.exists(path):
        return 0
    try:
        import openpyxl
    except ImportError:
        return 0
    from tracker.teams import canon
    ws = openpyxl.load_workbook(path, read_only=True)["Predictions"]
    n = 0
    for row in ws.iter_rows(min_row=5, values_only=True):
        if not row or not row[2] or row[4] is None:
            continue
        d = sources.parse_date(row[0])
        fx = {"date": d, "league": row[1], "home": canon(row[2]), "away": canon(row[3])}
        c = markets.corner_markets(float(row[4]), float(row[5]), None, fx["league"])
        g = markets.goal_markets(float(row[8] or .2), float(row[9] or .2), rho=0.0)
        r = ev.to_row(fx, {"corners": c, "goals": g}, d, model="v2")
        store[("v2", r["home"], r["away"], r["date"])] = r
        n += 1
    return n


def backfill(store, results, rounds, prior, today):
    """
    First run only: replay each finished round this season as if the model had
    been running, fitting only on matches before that round started. Gives the
    scorecard a history straight away without peeking at results.
    """
    if any(k[0] == "v3" for k in store):
        return 0
    n = 0
    for r in rounds.values():
        if r["end"] >= today or r["n"] < 3:
            continue
        mdl = Model().fit(results, r["start"], prior)
        for m in results:
            if m.get("round") != r["id"]:
                continue
            p = mdl.predict(m)
            if p:
                row = ev.to_row(m, p, r["start"])
                store[("v3", row["home"], row["away"], row["date"])] = row
                n += 1
    return n


def team_table(results, model):
    rows = {}
    for m in sorted(results, key=lambda x: x["date"]):
        if m.get("hc") is None:
            continue
        for side, t, cf, ca, gf, ga, xf, xa in (
                ("home", m["home"], m["hc"], m["ac"], m["hg"], m["ag"], m.get("hxg"), m.get("axg")),
                ("away", m["away"], m["ac"], m["hc"], m["ag"], m["hg"], m.get("axg"), m.get("hxg"))):
            r = rows.setdefault(t, {"team": t, "league": m["league"], "hist": [],
                                    "home": [0, 0, 0, 0, 0], "away": [0, 0, 0, 0, 0],
                                    "xg": [0, 0, 0]})
            v = r[side]
            v[0] += 1; v[1] += cf; v[2] += ca; v[3] += gf or 0; v[4] += ga or 0
            if xf is not None:
                r["xg"][0] += 1; r["xg"][1] += xf; r["xg"][2] += xa
            r["hist"].append((cf, ca))
    out = []
    rat = model.corners.table()
    for t, r in rows.items():
        h, a, x = r["home"], r["away"], r["xg"]
        n = h[0] + a[0]
        last = r["hist"][-6:]
        mu = model.corners.mu.get(r["league"], (5, 4.5))
        rt = rat.get(t, {})
        out.append({
            "team": t, "league": r["league"], "p": n,
            "cf": (h[1] + a[1]) / n, "ca": (h[2] + a[2]) / n,
            "h_p": h[0], "h_cf": h[1] / h[0] if h[0] else None, "h_ca": h[2] / h[0] if h[0] else None,
            "a_p": a[0], "a_cf": a[1] / a[0] if a[0] else None, "a_ca": a[2] / a[0] if a[0] else None,
            "f_cf": sum(c for c, _ in last) / len(last), "f_ca": sum(c for _, c in last) / len(last),
            "gf": (h[3] + a[3]) / n, "ga": (h[4] + a[4]) / n,
            "xgf": x[1] / x[0] if x[0] else None, "xga": x[2] / x[0] if x[0] else None,
            # model's corners for/against against an average side, at home
            "m_h_cf": mu[0] * rt.get("att", 1) * rt.get("home_att", 1),
            "m_a_cf": mu[1] * rt.get("att", 1) * rt.get("away_att", 1),
            "m_h_ca": mu[1] * rt.get("dfn", 1) * rt.get("home_dfn", 1),
            "m_a_ca": mu[0] * rt.get("dfn", 1) * rt.get("away_dfn", 1),
        })
    return sorted(out, key=lambda r: (r["league"], -r["cf"]))


def main():
    today = date.today()
    now = datetime.now(timezone.utc)
    season = sources.season_code(today)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)
    print(f"\n  EFL corners & goals - season {season[:2]}/{season[2:]}\n")

    official = sources.get_results(season, CACHE)
    manual = sources.get_manual(MANUAL_FILE)
    results, n_manual = sources.merge_results(official, manual)
    if not results:
        sys.exit("  Nothing downloaded - check the connection. Nothing was changed.")
    if n_manual:
        print(f"  {n_manual} manual result(s) used until the feed catches up")

    last = sources.get_results(sources.prev_season(season), CACHE, log=lambda *_: None)
    prior = None
    if last:
        end = max(m["date"] for m in last) + timedelta(days=1)
        prior = Model().fit(last, end).priors()

    schedule = sources.get_fixtures(season)
    played = {(m["home"], m["away"]) for m in results}
    todo = [f for f in schedule if (f["home"], f["away"]) not in played]
    odds = {(o["home"], o["away"]): o for o in sources.get_upcoming_odds()}
    for f in todo:
        o = odds.get((f["home"], f["away"]))
        if o:
            f.update({k: o[k] for k in ("oh", "od", "oa", "oo25", "ou25")})
    # a result dated today or later can only be a manual entry; still counts
    todo = [f for f in todo if f["date"] >= today - timedelta(days=3)]

    rounds = detect_rounds(results + todo)
    model = Model().fit(results, today + timedelta(days=1), prior)

    # ---- predict every round starting soon (and the current one)
    upcoming = [r for r in rounds.values()
                if r["end"] >= today and r["start"] <= today + timedelta(days=LOOKAHEAD_DAYS)]
    if not upcoming:
        upcoming = [r for r in rounds.values() if r["end"] >= today][:1]
    up_ids = {r["id"] for r in upcoming}
    new_rows = []
    for f in todo:
        if f.get("round") in up_ids and f["date"] >= today:
            p = model.predict(f)
            if p:
                new_rows.append(ev.to_row(f, p, today))

    store = load_store()
    n_legacy = import_legacy(store)
    if n_legacy:
        print(f"  Imported {n_legacy} predictions from the old spreadsheet")
    n_back = backfill(store, results, rounds, prior, today)
    if n_back:
        print(f"  Back-filled {n_back} predictions for this season's earlier rounds")
    ev.upsert(store, new_rows, today)
    by_date = sorted(rounds.values(), key=lambda r: r["start"])
    for r in store.values():
        d = date.fromisoformat(r["date"])
        r["round"] = next((x["id"] for x in by_date if x["start"] <= d <= x["end"]), r.get("round", ""))
    sources.write_csv(PRED_FILE, sorted(store.values(), key=lambda r: (r["date"], r["model"], r["home"])),
                      ev.FIELDS)
    sources.write_csv(MATCH_FILE, sorted(results, key=lambda m: (m["date"], m["league"], m["home"])),
                      ["round"] + sources.FIELDS)

    # ---- mark
    rates = ev.league_rates(results, today)
    ev.book_lines(rates)
    settled = ev.settle(store, results)
    for r in settled:
        r["calls"] = ev.mark(r) if r.get("hc") is not None else [
            {"market": mk, "pick": lab, "p": round(q, 3)} for mk, lab, q, _ in ev.calls_for(r)]

    reviews = []
    for model_name in ("v3", "v2"):
        for rid in sorted({r["round"] for r in settled if r["model"] == model_name}):
            rs = [r for r in settled if r["model"] == model_name and r["round"] == rid
                  and r.get("hc") is not None]
            calls = [c for r in rs for c in r["calls"]]
            if not rs:
                continue
            by = {}
            for c in calls:
                v = by.setdefault(c["market"], [0, 0])
                v[0] += 1; v[1] += c["hit"]
            reviews.append({"round": rid, "model": model_name, "matches": len(rs),
                            "calls": len(calls), "hits": sum(c["hit"] for c in calls),
                            "by_market": by,
                            "corner_mae": sum(abs(float(r["et"]) - r["hc"] - r["ac"]) for r in rs) / len(rs)})

    skill = {mn: ev.brier_block([r for r in settled if r["model"] == mn], rates)
             for mn in ("v3", "v2")}

    shown_rounds = sorted({r["round"] for r in settled} | up_ids)
    rinfo = []
    for rid in shown_rounds:
        r = rounds.get(rid)
        if not r:
            continue
        status = "upcoming" if r["start"] > today else ("completed" if r["end"] < today else "in play")
        rinfo.append({"id": rid, "label": r["label"], "kind": r["kind"], "n": r["n"],
                      "start": r["start"].isoformat(), "end": r["end"].isoformat(),
                      "status": status})

    def clean(r):
        out = {}
        for k, v in r.items():
            if k in ("made",):
                continue
            fv = ev.f(v) if k not in ("home", "away", "league", "round", "date", "time",
                                     "model", "calls") else None
            out[k] = round(fv, 4) if fv is not None else v
        return out

    fixtures = [clean(r) for r in settled if r["round"] in shown_rounds]
    latest = {}
    for m in official:
        latest[m["league"]] = max(latest.get(m["league"], date.min), m["date"])

    payload = {
        "meta": {"updated": now.strftime("%Y-%m-%d %H:%M UTC"), "today": today.isoformat(),
                 "season": f"20{season[:2]}-{season[2:]}",
                 "latest": {k: v.isoformat() for k, v in latest.items()},
                 "manual": n_manual, "unknown": unknown_names(),
                 "matches": len(results), "call": ev.CALL, "book_line": ev.BOOK_LINE, "params": PARAMS},
        "rounds": rinfo, "fixtures": fixtures, "reviews": reviews, "skill": skill,
        "rates": rates, "teams": team_table(results, model),
    }
    with open(os.path.join(DOCS, "data.json"), "w") as fh:
        json.dump(payload, fh, default=str)
    with open(TEMPLATE, encoding="utf-8") as fh:
        html = fh.read()
    blob = json.dumps(payload, default=str).replace("</", "<\\/")
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as fh:
        fh.write('<!doctype html>\n<html lang="en">\n'
                 + html.replace("/*__DATA__*/null", blob) + "\n</html>\n")

    # ---- terminal summary
    for r in upcoming:
        fx = [x for x in fixtures if x["round"] == r["id"] and x["model"] == "v3"]
        print(f"\n  {r['label']}  ({len(fx)} fixtures predicted)")
        gaps = sorted(fx, key=lambda x: -abs(x["eh"] - x["ea"]))[:5]
        for x in gaps:
            print(f"    {x['home']:>16} {x['eh']:4.1f} - {x['ea']:<4.1f} {x['away']:<16} gap {abs(x['eh'] - x['ea']):.1f}")
    done = [v for v in reviews if v["model"] == "v3"]
    if done:
        v = done[-1]
        print(f"\n  Last marked round {v['round']}: {v['hits']}/{v['calls']} calls landed")
    if unknown_names():
        print("\n  Unrecognised club names (add to tracker/teams.py):", ", ".join(unknown_names()))
    print(f"\n  Dashboard: {os.path.join(DOCS, 'index.html')}\n")


if __name__ == "__main__":
    main()
