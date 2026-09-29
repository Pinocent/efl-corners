#!/usr/bin/env python3
"""
The model's weekly self-review, from the command line.

  python3 self_review.py               # the latest review, corrections in force, next check-in
  python3 self_review.py --checked-in  # record that we've done our check-in today, and upload it

The review itself runs in the cloud after each gameweek is marked (run.py,
tracker/review.py); this reads the result from data/self_review.json, which
./sync_to_github.sh keeps up to date with GitHub.
"""

import json
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "data", "self_review.json")
CHECKIN = os.path.join(HERE, "checkin.txt")


def show():
    if not os.path.exists(STATE):
        print("The self-review hasn't run yet: it runs once the next gameweek is marked.")
        return
    with open(STATE) as fh:
        s = json.load(fh)
    reviews = s.get("reviews") or []
    if reviews:
        r = reviews[-1]
        print(f"Latest self-review: after gameweek {r.get('gameweek')} ({r.get('label')}), run {r.get('date')}")
        if r.get("flags"):
            print("Flagged:")
            for t in r["flags"]:
                print(f"  - {t}")
        else:
            print("Nothing beyond normal variation.")
        if r.get("changes"):
            print("Changed:")
            for t in r["changes"]:
                print(f"  - {t}")
    active = []
    for comp, act in (s.get("active") or {}).items():
        for lg, v in (act.get("level") or {}).items():
            if abs(v - 1) > 1e-9:
                active.append(f"{lg}: expected {comp} x{v:.2f}")
        if abs(act.get("spread", 1.0) - 1) > 1e-9:
            active.append(f"{comp}: spread x{act['spread']:.2f}")
    print("Corrections in force: " + ("; ".join(active) if active else "none"))
    last = s.get("last_checkin")
    if os.path.exists(CHECKIN):
        with open(CHECKIN) as fh:
            last = max(filter(None, [last, fh.read().strip()]))
    if last:
        due = date.fromisoformat(last).toordinal() + 7 * 7
        due = date.fromordinal(due)
        print(f"Last check-in: {last}. Next due: {due.isoformat()}" + (" (due now)" if date.today() >= due else ""))


def checked_in():
    with open(CHECKIN, "w") as fh:
        fh.write(date.today().isoformat() + "\n")
    print(f"Check-in recorded for {date.today().isoformat()}.")
    subprocess.call([os.path.join(HERE, "sync_to_github.sh")])


if __name__ == "__main__":
    checked_in() if "--checked-in" in sys.argv else show()
