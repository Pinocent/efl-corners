#!/usr/bin/env python3
"""
What the scheduled collection task should fetch right now. Prints JSON:

  results_needed   finished EFL matches (last 4 days) that neither the official
                   feed nor manual_results.csv has yet
  referees_needed  EFL matches in the next 6 days with no referee known yet
                   (not in the EFL's appointments, football-data's fixtures
                   file or referees.csv)
  called_off       matches in that window the EFL's referee appointments leave
                   out: not going ahead that day, so nothing to collect

Team names are the board's own spellings - write them into the CSVs exactly
as given here so every row is recognised.

  python3 collect_todo.py
"""

import json
import os
import sys
from datetime import date, timedelta

from tracker import efl, sources

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    today = date.today()
    season = sources.season_code(today)
    quiet = lambda *_: None
    official = sources.get_results(season, None, log=quiet)
    have = {(m["home"], m["away"]) for m in official}
    manual = sources.get_manual(os.path.join(HERE, "manual_results.csv"), log=quiet)
    have |= {(m["home"], m["away"]) for m in manual}
    # also refreshes the saved fixture list, which the cloud falls back on
    schedule = sources.get_fixtures(season, sources.schedule_path(HERE, season), log=quiet)
    # also refreshes the saved EFL appointments, which the cloud falls back on
    apps = efl.get_appointments(os.path.join(HERE, efl.FILE), log=quiet, today=today)
    refs = sources.get_upcoming_referees(log=quiet)
    refs.update(sources.get_manual_referees(os.path.join(HERE, sources.REFEREE_FILE)))
    refs.update(efl.referees(apps, today))
    window = [f for f in schedule if today - timedelta(days=4) <= f["date"] <= today + timedelta(days=6)]
    off = efl.postponed(apps, window, have)

    def row(f):
        return {"date": f["date"].isoformat(), "kickoff": f.get("time", ""), "league": f["league"],
                "home": f["home"], "away": f["away"]}

    is_off = lambda f: (f["home"], f["away"], f["date"]) in off
    results_needed = [row(f) for f in window
                      if f["date"] <= today and (f["home"], f["away"]) not in have and not is_off(f)]
    referees_needed = [row(f) for f in window
                       if f["date"] >= today and (f["home"], f["away"]) not in have | set(refs) and not is_off(f)]
    called_off = [row(f) for f in window if is_off(f)]
    json.dump({"today": today.isoformat(), "results_needed": results_needed,
               "referees_needed": referees_needed, "called_off": called_off}, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
