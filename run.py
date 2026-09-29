#!/usr/bin/env python3
"""
The update. No arguments, no third-party packages. Runs the same on a Mac
or in GitHub Actions (every three hours there, and whenever the Mac syncs).

  1. downloads results (goals, corners, xG, shots, red cards) + the fixture list
  2. merges manual_results.csv for matches the feed hasn't published yet
  3. works out the gameweeks from the dates
  4. fits the model and predicts the gameweeks starting soon
  5. logs predictions and their calls (frozen at kick-off), marks finished ones
  6. writes data/*.csv and the dashboard at docs/index.html
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

from tracker import evaluate as ev
from tracker import markets
from tracker import sources
from tracker.cards import CardModel, cards_of
from tracker.flags import fixture_flags
from tracker.legacy import independent_markets
from tracker.model import PARAMS, Model, learn_movers
from tracker import review
from tracker.rounds import detect_rounds

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CACHE = os.path.join(DATA, "cache")
DOCS = os.path.join(HERE, "docs")
PRED_FILE = os.path.join(DATA, "predictions.csv")
MATCH_FILE = os.path.join(DATA, "matches.csv")
MANUAL_FILE = os.path.join(HERE, "manual_results.csv")
REFEREE_FILE = os.path.join(HERE, sources.REFEREE_FILE)
STRICT, LENIENT = 1.10, 0.90   # referee factor at which the page calls a referee strict / lenient
LEGACY_XLSX = os.path.join(HERE, "old version", "corners_tracker.xlsx")
REVIEW_FILE = os.path.join(DATA, "self_review.json")
LAST_SYNC_FILE = os.path.join(HERE, "last_sync.txt")   # written by sync_to_github.sh on the Mac
TEMPLATE = os.path.join(HERE, "tracker", "dashboard.html")
LOOKAHEAD_DAYS = 10     # predict every gameweek starting within this many days
FIRST_SEASON = "1718"   # history goes back to here (cached after the first download)


def last_sync():
    """When the Mac's nightly task last reached GitHub (UTC, ISO), or "" if never."""
    try:
        with open(LAST_SYNC_FILE) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def load_store():
    return {(r["model"], r["home"], r["away"], r["date"]): r
            for r in sources.read_csv(PRED_FILE)}


def import_legacy(store):
    """One-off: bring the old spreadsheet's predictions across as model 'v2'."""
    if any(k[0] == "v2" for k in store) or not os.path.exists(LEGACY_XLSX):
        return 0
    try:
        import openpyxl
    except ImportError:
        return 0
    from tracker.markets import goal_markets
    from tracker.teams import canon
    ws = openpyxl.load_workbook(LEGACY_XLSX, read_only=True)["Predictions"]
    n = 0
    for row in ws.iter_rows(min_row=5, values_only=True):
        if not row or not row[2] or row[4] is None:
            continue
        d = sources.parse_date(row[0])
        fx = {"date": d, "league": row[1], "home": canon(row[2]), "away": canon(row[3])}
        c = independent_markets(float(row[4]), float(row[5]))
        g = goal_markets(float(row[8] or .2), float(row[9] or .2), rho=0.0)
        r = ev.to_row(fx, {"corners": c, "goals": g}, d, model="v2")
        store[("v2", r["home"], r["away"], r["date"])] = r
        n += 1
    return n


def upgrade_rows(store):
    """
    Rows written before calls were stored in the log get their calls worked
    out once, now, and frozen from then on. Old-spreadsheet (v2) rows also
    get their per-side chances filled in the way v2 made them.
    """
    n = dom = 0
    for r in store.values():
        if r["model"] == "v2" and not r.get("ph"):
            c = independent_markets(float(r["eh"]), float(r["ea"]))
            r.update(home_u3=c["home_u3"], away_u3=c["away_u3"],
                     ph=";".join(f"{x:.4f}" for x in c["ph"]),
                     pa=";".join(f"{x:.4f}" for x in c["pa"]))
        if not r.get("calls"):
            ev.freeze_calls(r)
            n += 1
        # corner dominance was added later: work it out once for older rows, from
        # the expected corners frozen at kick-off, and add the call if it qualifies
        if r["model"] == "v3" and not r.get("dom_home") and r.get("eh") and r.get("ea"):
            j = markets.corner_joint(float(r["eh"]), float(r["ea"]), PARAMS["total_size"], PARAMS["split_kappa"])
            r["dom_home"] = round(sum(p for (h, a), p in j.items() if h > a), 6)
            r["dom_away"] = round(sum(p for (h, a), p in j.items() if a > h), 6)
            ev.add_call(r, ev.dominance_call(r))
            dom += 1
    return n, dom


def history(season):
    """Every season from FIRST_SEASON up to (not including) `season`, oldest first."""
    out, s = [], FIRST_SEASON
    while s != season:
        out.append((s, sources.get_results(s, CACHE, log=lambda *_: None)))
        a = int(s[:2]) + 1
        s = f"{a:02d}{a + 1:02d}"
    return out


def match_rows(results):
    """This season's results, compact, for the team pages."""
    keys = (("d", "date"), ("lg", "league"), ("h", "home"), ("a", "away"), ("hc", "hc"),
            ("ac", "ac"), ("hg", "hg"), ("ag", "ag"), ("hxg", "hxg"), ("axg", "axg"),
            ("hs", "hs"), ("as", "as_"), ("hst", "hst"), ("ast", "ast"), ("hr", "hr"),
            ("ar", "ar"), ("hp", "hp"), ("ap", "ap"), ("hcr", "hcr"), ("acr", "acr"),
            ("hy", "hy"), ("ay", "ay"), ("hf", "hf"), ("af", "af"), ("ref", "ref"))
    out = []
    for m in sorted(results, key=lambda m: (m["date"], m["home"])):
        r = {}
        for k, src in keys:
            v = m.get(src)
            if v is None:
                continue
            if v == "":
                continue
            if isinstance(v, date):
                v = v.isoformat()
            elif isinstance(v, float):
                v = int(v) if v.is_integer() else round(v, 2)
            r[k] = v
        out.append(r)
    return out


def season_summary(matches):
    """Per club: division and corners/goals per match over a season."""
    t = {}
    for m in matches:
        if m.get("hc") is None:
            continue
        for team, cf, ca, gf, ga in ((m["home"], m["hc"], m["ac"], m["hg"], m["ag"]),
                                     (m["away"], m["ac"], m["hc"], m["ag"], m["hg"])):
            v = t.setdefault(team, {"league": m["league"], "p": 0, "cf": 0, "ca": 0, "gf": 0, "ga": 0})
            v["p"] += 1; v["cf"] += cf; v["ca"] += ca; v["gf"] += gf or 0; v["ga"] += ga or 0
    return {k: {"league": v["league"], "p": v["p"], "cf": round(v["cf"] / v["p"], 2),
                "ca": round(v["ca"] / v["p"], 2), "gf": round(v["gf"] / v["p"], 2),
                "ga": round(v["ga"] / v["p"], 2)} for k, v in t.items()}


def backfill(store, results, rounds, prior, last, today, movers=None, hist=(), card_prior=None):
    """
    First run of a season only: replay each finished gameweek as if the model
    had been running, fitting only on matches played before it, and against
    the corners line as it stood then. Gives the track record a history
    straight away without peeking at results.
    """
    if any(k[0] == "v3" and k[3] >= min(r["id"] for r in rounds.values()) for k in store):
        return 0
    saved, saved_k = dict(ev.BOOK_LINE), dict(ev.CARD_LINE)
    n = 0
    for r in rounds.values():
        if r["end"] >= today or r["n"] < 3:
            continue
        ev.book_lines(ev.league_rates(results, r["start"], fallback=last))
        mdl = Model().fit(results, r["start"], prior, movers)
        cmdl = CardModel().fit(results, r["start"], hist, card_prior)
        for m in results:
            if m.get("round") != r["id"]:
                continue
            p = mdl.predict(m)
            if p:
                # the referee was known before kick-off (published the day before)
                row = ev.to_row(m, p, r["start"], cards=cmdl.predict(m, m.get("ref")))
                store[("v3", row["home"], row["away"], row["date"])] = row
                n += 1
    ev.BOOK_LINE.clear()
    ev.BOOK_LINE.update(saved)
    ev.CARD_LINE.clear()
    ev.CARD_LINE.update(saved_k)
    return n


def team_table(results, model):
    rows = {}
    for m in sorted(results, key=lambda x: x["date"]):
        if m.get("hc") is None:
            continue
        for side, t, cf, ca, xf, xa in (
                ("home", m["home"], m["hc"], m["ac"], m.get("hxg"), m.get("axg")),
                ("away", m["away"], m["ac"], m["hc"], m.get("axg"), m.get("hxg"))):
            r = rows.setdefault(t, {"team": t, "league": m["league"], "hist": [],
                                    "home": [0, 0, 0], "away": [0, 0, 0], "xg": [0, 0, 0],
                                    "cards": [0, 0, 0, 0]})
            kf, ka_ = cards_of(m, "h" if side == "home" else "a"), cards_of(m, "a" if side == "home" else "h")
            if kf is not None:
                r["cards"][0] += 1; r["cards"][1] += kf; r["cards"][2] += ka_
                r["cards"][3] += (m.get("hf") if side == "home" else m.get("af")) or 0
            v = r[side]
            v[0] += 1; v[1] += cf; v[2] += ca
            if xf is not None:
                r["xg"][0] += 1; r["xg"][1] += xf; r["xg"][2] += xa
            r["hist"].append((cf, ca))
    out = []
    for t, r in rows.items():
        h, a, x = r["home"], r["away"], r["xg"]
        n = h[0] + a[0]
        last = r["hist"][-6:]
        # what the model expects against an average opponent, same method as
        # the match predictions (an unknown club rates as exactly average)
        eh, _ = model.expect_corners({"league": r["league"], "home": t, "away": "\0avg"})
        _, ea = model.expect_corners({"league": r["league"], "home": "\0avg", "away": t})
        out.append({
            "team": t, "league": r["league"], "p": n,
            "cf": (h[1] + a[1]) / n, "ca": (h[2] + a[2]) / n,
            "h_cf": h[1] / h[0] if h[0] else None, "h_ca": h[2] / h[0] if h[0] else None,
            "a_cf": a[1] / a[0] if a[0] else None, "a_ca": a[2] / a[0] if a[0] else None,
            "f_cf": sum(c for c, _ in last) / len(last), "f_ca": sum(c for _, c in last) / len(last),
            "xgf": x[1] / x[0] if x[0] else None, "xga": x[2] / x[0] if x[0] else None,
            "m_h_cf": eh, "m_a_cf": ea,
            "kf": r["cards"][1] / r["cards"][0] if r["cards"][0] else None,
            "ka": r["cards"][2] / r["cards"][0] if r["cards"][0] else None,
            "ff": r["cards"][3] / r["cards"][0] if r["cards"][0] else None,
        })
    return sorted(out, key=lambda r: (r["league"], -r["cf"]))


def review_round(rows, rates):
    """One gameweek's marking: calls, and the prediction measures."""
    calls = [c for r in rows for c in r["calls"]]
    by = {}
    for c in calls:
        v = by.setdefault(c["market"], [0, 0])
        v[0] += 1; v[1] += c["hit"]
    res = [ev.result(r) for r in rows]
    eh = [float(r["eh"]) for r in rows]
    ea = [float(r["ea"]) for r in rows]
    dec = [i for i, x in enumerate(res) if x[0] != x[1]]
    T = ev.TAG
    gap = [i for i in range(len(rows)) if abs(eh[i] - ea[i]) >= T["big_gap"]]
    cs, u3 = [], []
    for r, (hc, ac, hg, ag) in zip(rows, res):
        cs += [ag == 0] if float(r["cs_home"]) >= T["cs"] else []
        cs += [hg == 0] if float(r["cs_away"]) >= T["cs"] else []
        if r["league"] != "League 2":
            for p, n in ((ev.f(r.get("home_u3")), hc), (ev.f(r.get("away_u3")), ac)):
                if p is not None and p >= T["low_u3"]:
                    u3.append(n < 3)
    return {
        "matches": len(rows), "calls": len(calls), "hits": sum(c["hit"] for c in calls),
        "by_market": by,
        "side_n": len(dec), "side_hit": sum((eh[i] > ea[i]) == (res[i][0] > res[i][1]) for i in dec),
        "corner_mae": sum(abs(eh[i] + ea[i] - x[0] - x[1]) for i, x in enumerate(res)) / len(rows),
        "base_mae": sum(abs(rates.get(r["league"], {}).get("avg_corners", 10) - x[0] - x[1])
                        for r, x in zip(rows, res)) / len(rows),
        "gap_n": len(gap), "gap_hit": sum((res[i][0] > res[i][1]) if eh[i] >= ea[i] else (res[i][1] > res[i][0])
                                          for i in gap),
        "cs_n": len(cs), "cs_hit": sum(cs), "u3_n": len(u3), "u3_hit": sum(u3),
    }


def main():
    now = datetime.now(ev.UK) if ev.UK else datetime.now()
    today = now.date()
    season = sources.season_code(today)
    season_start = date(2000 + int(season[:2]), 7, 1)
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(DOCS, exist_ok=True)
    print(f"\n  EFL corners & goals - season {season[:2]}/{season[2:]}\n")

    official = sources.get_results(season, CACHE)
    fixture_status = {}
    schedule = sources.get_fixtures(season, sources.schedule_path(HERE, season), status=fixture_status)
    if not official and not schedule:
        sys.exit("  Nothing downloaded - check the connection. Nothing was changed.")
    known = {(m["league"], t) for m in official + schedule for t in (m["home"], m["away"])}
    results, n_manual, skipped = sources.merge_results(
        official, sources.get_manual(MANUAL_FILE), known)
    if n_manual:
        print(f"  {n_manual} Flashscore result(s) used until the feed catches up")
    for s in skipped:
        print(f"  ! Flashscore row not used - {s}")

    past = history(season)
    last = past[-1][1] if past else []
    prior = None
    if last:
        prior = Model().fit(last, max(m["date"] for m in last) + timedelta(days=1)).priors()
    # how clubs arriving by promotion or relegation have done, from the history
    movers = learn_movers(past)
    # cards: every earlier season for the referees, last season for team priors
    hist = [m for _, ms in past for m in ms]
    card_prior = None
    if last:
        card_prior = CardModel().fit(last, max(m["date"] for m in last) + timedelta(days=1),
                                     [m for _, ms in past[:-1] for m in ms]).priors()
    # referee appointments: the Cowork file first, football-data's (official, later) wins
    refs_up = sources.get_manual_referees(REFEREE_FILE)
    refs_up.update(sources.get_upcoming_referees())

    # the usual corners line, as things stand today (last season's until this
    # season has enough matches)
    rates_now = ev.league_rates(results, today + timedelta(days=1), fallback=last)
    ev.book_lines(rates_now)

    played = {(m["home"], m["away"]) for m in results}
    todo = [f for f in schedule if (f["home"], f["away"]) not in played
            and f["date"] >= today - timedelta(days=3)]
    rounds = detect_rounds(results + todo)
    model = Model().fit(results, today + timedelta(days=1), prior, movers)
    card_model = CardModel().fit(results, today + timedelta(days=1), hist, card_prior)

    # ---- which gameweeks to predict: every one starting soon, and any in progress
    upcoming = [r for r in rounds.values()
                if r["end"] >= today and r["start"] <= today + timedelta(days=LOOKAHEAD_DAYS)]
    if not upcoming:
        upcoming = [r for r in rounds.values() if r["end"] >= today][:1]
    up_ids = {r["id"] for r in upcoming}

    store = load_store()
    n = import_legacy(store)
    if n:
        print(f"  Imported {n} predictions from the old spreadsheet")
    n, dom = upgrade_rows(store)
    if n:
        print(f"  Stored calls for {n} older predictions")
    if dom:
        print(f"  Worked out corner-dominance chances for {dom} earlier predictions")
    if rounds:
        n = backfill(store, results, rounds, prior, last, today, movers, hist, card_prior)
        if n:
            print(f"  Replayed {n} predictions for this season's earlier gameweeks")
    by_start = sorted(rounds.values(), key=lambda r: r["start"])
    for r in store.values():
        d = date.fromisoformat(r["date"])
        if d >= season_start:
            r["round"] = next((x["id"] for x in by_start if x["start"] <= d <= x["end"]), r.get("round", ""))
    ev.settle(store, results)

    # ---- the weekly self-review: once a gameweek is marked, check every market
    # and decide whether any cautious correction is warranted (tracker/review.py)
    state = review.load(REVIEW_FILE, today)
    checkin = os.path.join(HERE, "checkin.txt")     # written by self_review.py --checked-in
    if os.path.exists(checkin):
        with open(checkin) as fh:
            recorded = fh.read().strip()
        if recorded and recorded > state.get("last_checkin", ""):
            state["last_checkin"] = recorded
    season_v3 = [r for r in store.values() if r["model"] == "v3" and date.fromisoformat(r["date"]) >= season_start]
    marked_v3 = [dict(r, calls=ev.mark(r)) for r in season_v3]
    snap = review.weekly(state, season_v3, marked_v3, rounds, today)
    if snap:
        print(f"  Self-review after gameweek {snap['gameweek']}: "
              + (f"{len(snap['flags'])} thing(s) flagged" if snap["flags"] else "nothing beyond normal variation"))
        for line in snap["changes"]:
            print(f"    {line}")
    adjust = review.Adjuster(state)

    # ---- predict every gameweek starting soon (and any in progress)
    new_rows = []
    for f in todo:
        if f.get("round") in up_ids:
            row_date = {"date": f["date"].isoformat(), "time": f.get("time", "")}
            if ev.started(row_date, now):
                continue                  # kicked off: its prediction is frozen
            p = model.predict(f, adjust=adjust if adjust.any() else None)
            if p:
                ref = refs_up.get((f["home"], f["away"]))
                cards = card_model.predict(f, ref, adjust=adjust if adjust.any() else None)
                new_rows.append(ev.to_row(f, p, today, cards=cards, cal=adjust.version if adjust.any() else ""))
    ev.upsert(store, new_rows, now)
    review.save(REVIEW_FILE, state)
    sources.write_csv(PRED_FILE, sorted(store.values(), key=lambda r: (r["date"], r["model"], r["home"])),
                      ev.FIELDS)
    sources.write_csv(MATCH_FILE, sorted(results, key=lambda m: (m["date"], m["league"], m["home"])),
                      ["round"] + sources.FIELDS)

    # ---- mark this season's predictions
    rows = [dict(r) for r in store.values() if date.fromisoformat(r["date"]) >= season_start]
    for r in rows:
        r["calls"] = ev.mark(r)
    rates_at = {rid: ev.league_rates(results, rounds[rid]["start"], fallback=last) for rid in rounds}
    reviews = []
    for model_name in ("v3", "v2"):
        for rid in sorted({r["round"] for r in rows if r["model"] == model_name}):
            done = [r for r in rows if r["model"] == model_name and r["round"] == rid and ev.result(r)]
            if done:
                reviews.append({"round": rid, "model": model_name,
                                **review_round(done, rates_at.get(rid, rates_now))})
    skill = {mn: ev.brier_block([r for r in rows if r["model"] == mn],
                                lambda r: rates_at.get(r["round"], rates_now))
             for mn in ("v3", "v2")}

    shown = sorted({r["round"] for r in rows} | up_ids)
    rinfo = []
    for rid in shown:
        r = rounds.get(rid)
        if not r:
            # a gameweek with predictions but missing from today's fixture list
            # (a download failed): rebuild it from its own matches rather than hide it
            ds = sorted(date.fromisoformat(x["date"]) for x in rows if x.get("round") == rid)
            if not ds:
                continue
            kind = "Midweek" if sum(d.weekday() in (1, 2, 3) for d in ds) * 2 >= len(ds) else "Weekend"
            prior_n = [x["n"] for x in rinfo]
            r = {"start": ds[0], "end": ds[-1], "kind": kind, "n": (max(prior_n) + 1) if prior_n else 0,
                 "label": f"{kind} · {ds[0]:%a %-d %b}" + (f" – {ds[-1]:%a %-d %b}" if ds[-1] != ds[0] else "")}
        status = "upcoming" if r["start"] > today else ("completed" if r["end"] < today else "in play")
        rinfo.append({"id": rid, "label": r["label"], "kind": r["kind"], "n": r["n"],
                      "start": r["start"].isoformat(), "end": r["end"].isoformat(),
                      "status": status})

    text_keys = {"model", "made", "round", "date", "time", "league", "home", "away", "calls", "ref"}

    def clean(r):
        out = {}
        for k, v in r.items():
            if k in ("ph", "pa", "kph", "kpa"):
                out[k] = [float(x) for x in v.split(";")] if v else None
            elif k in text_keys:
                out[k] = v
            else:
                fv = ev.f(v)
                out[k] = (int(fv) if k in ("hc", "ac", "hg", "ag", "hk", "ak", "hred") else round(fv, 4)) if fv is not None else None
        return out

    fixtures = [clean(r) for r in rows if r["round"] in shown]
    # warnings about each side's recent matches, as they stood before kick-off
    cache = {}
    for x in fixtures:
        if x["model"] == "v3":
            fx = {"home": x["home"], "away": x["away"], "league": x["league"],
                  "date": date.fromisoformat(x["date"])}
            x["flags"] = fixture_flags(fx, results, model, PARAMS["nb_size"], cache)
    # referees on the page: this season's and anyone appointed to an upcoming match
    shown_refs = {x["ref"] for x in fixtures if x.get("ref")} | {m.get("ref") for m in results if m.get("ref")}
    ref_rows = []
    everything = hist + results
    for r in sorted(r for r in shown_refs if r):
        ms = [m for m in everything if m.get("ref") == r and cards_of(m, "h") is not None]
        if not ms:
            continue
        tot = [cards_of(m, "h") + cards_of(m, "a") for m in ms]
        ref_rows.append({"ref": r, "factor": round(card_model.refs.get(r), 3), "n": len(ms),
                         "cpm": round(sum(tot) / len(ms), 2),
                         "o35": round(sum(t > 3.5 for t in tot) / len(ms), 3),
                         "red": round(sum(((m.get("hr") or 0) + (m.get("ar") or 0)) > 0 for m in ms) / len(ms), 3),
                         "fpm": round(sum((m.get("hf") or 0) + (m.get("af") or 0) for m in ms) / len(ms), 1),
                         "season": sum(1 for m in results if m.get("ref") == r)})
    latest = {}
    for m in official:
        latest[m["league"]] = max(latest.get(m["league"], date.min), m["date"])

    payload = {
        "meta": {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                 "today": today.isoformat(), "season": f"20{season[:2]}-{season[2:]}",
                 "latest": {k: v.isoformat() for k, v in latest.items()},
                 "manual": n_manual, "skipped": skipped, "matches": len(results),
                 "call": ev.CALL, "tag": ev.TAG, "book_line": ev.BOOK_LINE,
                 "card_line": ev.CARD_LINE, "strict": STRICT, "lenient": LENIENT,
                 "fixtures_source": fixture_status,
                 "last_sync": last_sync()},
        "rounds": rinfo, "fixtures": fixtures, "reviews": reviews, "skill": skill,
        "rates": rates_now, "teams": team_table(results, model),
        "matches": match_rows(results), "last_season": season_summary(last),
        "referees": ref_rows,
        "selfreview": {**review.summary(state, today),
                       "health": review.health([r for r in rows if r["model"] == "v3"],
                                               [r for r in rows if r["model"] == "v3"])},
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
        calls = sum(len(x["calls"]) for x in fx)
        print(f"\n  {r['label']}: {len(fx)} matches predicted, {calls} call(s)")
        for x in sorted(fx, key=lambda x: -abs(x["eh"] - x["ea"]))[:5]:
            print(f"    {x['home']:>16} {x['eh']:4.1f} - {x['ea']:<4.1f} {x['away']:<16} gap {abs(x['eh'] - x['ea']):.1f}")
    done = [v for v in reviews if v["model"] == "v3"]
    if done:
        v = done[-1]
        print(f"\n  Last marked gameweek {v['round']}: {v['hits']}/{v['calls']} calls landed")
    print(f"\n  Dashboard: {os.path.join(DOCS, 'index.html')}\n")


if __name__ == "__main__":
    main()
