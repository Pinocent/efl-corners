#!/usr/bin/env python3
"""
Second opinion on the nightly Flashscore figures, from ESPN's public match
feed, before anything reaches manual_results.csv.

flashscore_stats.js (run in the browser by the nightly task) reads each match
from Flashscore and the task saves its output as flashscore_pending.json. This
script checks every finished row:

  sane      goals, corners and cards present; figures in a believable range
  espn      ESPN agrees on goals, corners and fouls (ESPN has no crosses or
            referee, and counts cards differently, so those aren't compared)

and sorts the rows into:

  agreed     passed both: written to manual_results.csv with --write
  disputed   ESPN differs, or ESPN doesn't have the match: a second reading
             of the Flashscore page is needed (the task asks Sonnet)
  rejected   failed the sanity checks: never written
  skipped    not finished on Flashscore (postponed, not found, page error)

A disputed row the task has re-read and confirmed carries "confirmed": true
in flashscore_pending.json; --write then writes it too, if it's sane.
Nothing is checked or written unless the file is an exact copy of what the
browser returned: its "check" field must match the rows (a hand-copied digit
that changed would otherwise slip through).
Rows already in manual_results.csv are never added again, and existing rows
are never changed.

  python3 espn_check.py           report only (JSON)
  python3 espn_check.py --write   report, and append agreed/confirmed rows

Flashscore matched football-data.co.uk's corners on 150 of 150 team figures
in November 2026's check; ESPN on 97%. So a disagreement is usually ESPN's,
which is why a dispute means "read Flashscore again", not "drop the match".
"""

import csv
import json
import os
import sys
import urllib.request

from tracker.teams import canon

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, "flashscore_pending.json")
MANUAL = os.path.join(HERE, "manual_results.csv")
FIELDS = ["Date", "League", "Home", "Away", "HomeGoals", "AwayGoals", "HomeCorners", "AwayCorners",
          "HomePossession", "AwayPossession", "HomeCrosses", "AwayCrosses", "HomeYellow", "AwayYellow",
          "HomeRed", "AwayRed", "HomeFouls", "AwayFouls", "Referee"]
ESPN_LEAGUE = {"Championship": "eng.2", "League 1": "eng.3", "League 2": "eng.4"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/{what}"
REQUIRED = ["HomeGoals", "AwayGoals", "HomeCorners", "AwayCorners",
            "HomeYellow", "AwayYellow", "HomeRed", "AwayRed"]
LIMITS = {"Goals": 12, "Corners": 25, "Yellow": 10, "Red": 4, "Fouls": 35, "Crosses": 80}
COMPARED = [("Goals", None), ("Corners", "wonCorners"), ("Fouls", "foulsCommitted")]


def sanity(row):
    """Reasons a finished row can't be trusted ([] if none)."""
    bad = [f"{k} missing" for k in REQUIRED if row.get(k) is None]
    for stat, top in LIMITS.items():
        for side in ("Home", "Away"):
            v = row.get(side + stat)
            if v is not None and not (isinstance(v, int) and 0 <= v <= top):
                bad.append(f"{side}{stat} {v!r} out of range")
    hp, ap = row.get("HomePossession"), row.get("AwayPossession")
    if hp is not None and ap is not None and not 98 <= hp + ap <= 102:
        bad.append(f"possession {hp}+{ap} doesn't add up to 100")
    for side in ("Home", "Away"):
        if (row.get(side + "Red") or 0) > 0 and row.get(side + "Yellow") is None:
            bad.append(f"{side} cards incomplete")
    fh, fa = row.get("fs_home"), row.get("fs_away")
    if fh and canon(fh) != canon(row["home"]) or fa and canon(fa) != canon(row["away"]):
        bad.append(f"Flashscore match is {fh} v {fa}, not {row['home']} v {row['away']}")
    return bad


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "efl-corners-board"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


_boards = {}


def espn_stats(row, get=_get):
    """{'home': {...}, 'away': {...}} from ESPN for this match, or None."""
    lg = ESPN_LEAGUE.get(row["league"])
    if not lg:
        return None
    key = (lg, row["date"])
    if key not in _boards:
        try:
            sb = get(ESPN.format(lg=lg, what="scoreboard") + "?dates=" + row["date"].replace("-", ""))
        except Exception:
            sb = {}
        _boards[key] = {}
        for e in sb.get("events", []):
            cs = {c["homeAway"]: c for c in e["competitions"][0]["competitors"]}
            if "home" in cs and "away" in cs:
                _boards[key][(canon(cs["home"]["team"]["displayName"]),
                              canon(cs["away"]["team"]["displayName"]))] = e["id"]
    eid = _boards[key].get((canon(row["home"]), canon(row["away"])))
    if not eid:
        return None
    try:
        s = get(ESPN.format(lg=lg, what="summary") + f"?event={eid}")
        side = {c["team"]["id"]: c["homeAway"] for c in s["header"]["competitions"][0]["competitors"]}
        score = {c["homeAway"]: c.get("score") for c in s["header"]["competitions"][0]["competitors"]}
        out = {side[t["team"]["id"]]: {x["name"]: x.get("displayValue") for x in t["statistics"]}
               for t in s["boxscore"]["teams"]}
        for k in ("home", "away"):
            out[k]["goals"] = score.get(k)
        return out
    except Exception:
        return None


def compare(row, espn):
    """Figures where ESPN and Flashscore differ: ['HomeCorners 5 v ESPN 6', ...]."""
    diffs = []
    for stat, key in COMPARED:
        for side, s in (("Home", "home"), ("Away", "away")):
            mine = row.get(side + stat)
            theirs = espn[s].get(key or "goals")
            if mine is None or theirs in (None, ""):
                continue
            if round(float(theirs)) != mine:
                diffs.append(f"{side}{stat} {mine} v ESPN {round(float(theirs))}")
    return diffs


def existing(path=MANUAL):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {(r["Date"], canon(r["Home"]), canon(r["Away"])) for r in csv.DictReader(fh)}


def append(rows, path=MANUAL):
    """Add rows to manual_results.csv (header kept, nothing existing touched)."""
    have = existing(path)
    new = [r for r in rows if (r["date"], canon(r["home"]), canon(r["away"])) not in have]
    if not new:
        return []
    with open(path, "rb") as fh:
        data = fh.read()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        if data and not data.endswith(b"\n"):
            fh.write("\n")
        w = csv.writer(fh, lineterminator="\n")
        for r in new:
            w.writerow([r["date"], r["league"], r["home"], r["away"]] +
                       ["" if r.get(f) is None else r[f] for f in FIELDS[4:]])
    return new


def checksum(rows):
    """The checksum flashscore_stats.js puts in "check" (FNV-1a over JSON.stringify(rows))."""
    body = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    h = 0x811c9dc5
    units = body.encode("utf-16-le")
    for i in range(0, len(units), 2):
        h ^= units[i] | units[i + 1] << 8
        h = (h * 0x01000193) & 0xFFFFFFFF
    return format(h, "x")


def exact_copy(pending):
    """True if the rows are exactly what the browser returned (rows marked
    confirmed afterwards are compared as they were before)."""
    rows = [{k: v for k, v in r.items() if k != "confirmed"} for r in pending.get("rows", [])]
    return pending.get("check") == checksum(rows)


def check(pending, get=_get):
    out = {"agreed": [], "disputed": [], "rejected": [], "skipped": [], "confirmed": []}
    for row in pending.get("rows", []):
        label = f"{row['date']} {row['home']} v {row['away']}"
        if row.get("status") != "finished":
            out["skipped"].append({"match": label, "status": row.get("status")})
            continue
        bad = sanity(row)
        if bad:
            out["rejected"].append({"match": label, "why": bad})
            continue
        if row.get("confirmed"):
            out["confirmed"].append(row)
            continue
        espn = espn_stats(row, get)
        if espn is None:
            out["disputed"].append({"match": label, "why": ["ESPN doesn't have this match"], "row": row})
            continue
        diffs = compare(row, espn)
        if diffs:
            out["disputed"].append({"match": label, "why": diffs, "row": row})
        else:
            out["agreed"].append(row)
    return out


def main():
    write = "--write" in sys.argv[1:]
    try:
        with open(PENDING, encoding="utf-8") as fh:
            pending = json.load(fh)
    except (OSError, ValueError) as e:
        print(json.dumps({"error": f"can't read flashscore_pending.json: {e}"}))
        sys.exit(1)
    if not exact_copy(pending):
        print(json.dumps({"error": "flashscore_pending.json isn't an exact copy of what the browser "
                                   "returned (its check doesn't match): nothing written. Save the "
                                   "string again, exactly as returned."}))
        sys.exit(1)
    out = check(pending)
    written = append(out["agreed"] + out["confirmed"]) if write else []
    label = lambda r: f"{r['date']} {r['home']} v {r['away']}"
    report = {
        "written": [label(r) for r in written] if write else "not written (run with --write)",
        "agreed": [label(r) for r in out["agreed"]],
        "confirmed": [label(r) for r in out["confirmed"]],
        "disputed": [{k: d[k] for k in ("match", "why")} for d in out["disputed"]],
        "rejected": out["rejected"],
        "skipped": out["skipped"],
        "with_possession": sum(r.get("HomePossession") is not None for r in out["agreed"] + out["confirmed"]),
        "with_crosses": sum(r.get("HomeCrosses") is not None for r in out["agreed"] + out["confirmed"]),
        "with_fouls": sum(r.get("HomeFouls") is not None for r in out["agreed"] + out["confirmed"]),
    }
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
