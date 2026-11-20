"""
espn_check.py: Flashscore rows only reach manual_results.csv when they're
sane and ESPN agrees (or a second reading confirmed them).

  python3 -m unittest discover tests
"""

import csv
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import espn_check as ec  # noqa: E402


def row(**kw):
    r = {"date": "2026-11-14", "league": "League 2", "home": "Fleetwood", "away": "Tranmere",
         "status": "finished", "HomeGoals": 1, "AwayGoals": 0, "HomeCorners": 8, "AwayCorners": 6,
         "HomePossession": 51, "AwayPossession": 49, "HomeCrosses": 30, "AwayCrosses": 25,
         "HomeYellow": 1, "AwayYellow": 1, "HomeRed": 0, "AwayRed": 0, "HomeFouls": 9, "AwayFouls": 8,
         "Referee": "P Howard", "fs_home": "Fleetwood", "fs_away": "Tranmere"}
    r.update(kw)
    return r


def fake_espn(corners=(8, 6), goals=(1, 0), fouls=(9, 8), listed=True):
    def get(url):
        if "scoreboard" in url:
            if not listed:
                return {"events": []}
            return {"events": [{"id": "1", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"displayName": "Fleetwood Town"}},
                {"homeAway": "away", "team": {"displayName": "Tranmere Rovers"}}]}]}]}
        teams = [("h", "home", 0), ("a", "away", 1)]
        return {"header": {"competitions": [{"competitors": [
                    {"team": {"id": t}, "homeAway": s, "score": str(goals[i])} for t, s, i in teams]}]},
                "boxscore": {"teams": [{"team": {"id": t}, "statistics": [
                    {"name": "wonCorners", "displayValue": str(corners[i])},
                    {"name": "foulsCommitted", "displayValue": str(fouls[i])}]} for t, s, i in teams]}}
    return get


class Check(unittest.TestCase):
    def setUp(self):
        ec._boards.clear()

    def test_agreement_is_written(self):
        out = ec.check({"rows": [row()]}, get=fake_espn())
        self.assertEqual(len(out["agreed"]), 1)

    def test_corner_disagreement_is_disputed(self):
        out = ec.check({"rows": [row()]}, get=fake_espn(corners=(8, 7)))
        self.assertEqual(out["disputed"][0]["why"], ["AwayCorners 6 v ESPN 7"])

    def test_match_espn_lacks_is_disputed(self):
        out = ec.check({"rows": [row()]}, get=fake_espn(listed=False))
        self.assertEqual(len(out["disputed"]), 1)

    def test_confirmed_row_skips_espn(self):
        out = ec.check({"rows": [row(confirmed=True)]}, get=fake_espn(corners=(1, 1)))
        self.assertEqual(len(out["confirmed"]), 1)

    def test_missing_cards_rejected_even_if_confirmed(self):
        out = ec.check({"rows": [row(HomeYellow=None, confirmed=True)]}, get=fake_espn())
        self.assertIn("HomeYellow missing", out["rejected"][0]["why"])

    def test_silly_numbers_rejected(self):
        for bad in ({"HomeCorners": 31}, {"HomePossession": 70}, {"AwayRed": -1}, {"HomeGoals": "2"}):
            out = ec.check({"rows": [row(**bad)]}, get=fake_espn())
            self.assertEqual(len(out["rejected"]), 1, bad)

    def test_wrong_match_rejected(self):
        out = ec.check({"rows": [row(fs_away="Barrow")]}, get=fake_espn())
        self.assertEqual(len(out["rejected"]), 1)

    def test_unfinished_skipped(self):
        out = ec.check({"rows": [row(status="postponed")]}, get=fake_espn())
        self.assertEqual(out["skipped"][0]["status"], "postponed")


class ExactCopy(unittest.TestCase):
    # what flashscore_stats.js returned for one row in the browser on 20 Nov 2026
    BROWSER = ('{"check":"8b51c5b","rows":[{"date":"2026-11-14","home":"Fleetwood","away":"Tranmere",'
               '"HomeCorners":8,"Referee":null,"notes":["not on pitch: Bruce A."]}]}')

    def test_browser_checksum_matches(self):
        import json
        p = json.loads(self.BROWSER)
        p["check"] = ec.checksum(p["rows"])          # computed the same way as the browser does
        self.assertTrue(ec.exact_copy(p))

    def test_one_changed_digit_is_caught(self):
        import json
        p = json.loads(self.BROWSER)
        p["check"] = ec.checksum(p["rows"])
        p["rows"][0]["HomeCorners"] = 9
        self.assertFalse(ec.exact_copy(p))

    def test_confirmed_flag_doesnt_break_it(self):
        import json
        p = json.loads(self.BROWSER)
        p["check"] = ec.checksum(p["rows"])
        p["rows"][0]["confirmed"] = True
        self.assertTrue(ec.exact_copy(p))

    def test_real_browser_output(self):
        # saved exactly as flashscore_stats.js returned it in the browser (20 Nov 2026):
        # the check the browser computed must match Python's
        import json
        with open(os.path.join(os.path.dirname(__file__), "flashscore_sample.json")) as fh:
            p = json.load(fh)
        self.assertEqual(p["check"], "96758380")
        self.assertTrue(ec.exact_copy(p))


class Append(unittest.TestCase):
    def test_appends_once_and_keeps_existing_rows(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "manual_results.csv")
            with open(path, "w", newline="") as fh:
                fh.write(",".join(ec.FIELDS) + "\n")
                fh.write("2026-10-24,League 2,York,Port Vale,2,0,2,8,48,52,13,18,0,1,0,0,13,7,David Webb")  # no newline
            self.assertEqual(len(ec.append([row(HomeCrosses=None)], path)), 1)
            self.assertEqual(ec.append([row(home="Fleetwood Town")], path), [])    # same match, other spelling
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual([r["Home"] for r in rows], ["York", "Fleetwood"])
            self.assertEqual(rows[0]["Referee"], "David Webb")
            self.assertEqual((rows[1]["HomeCrosses"], rows[1]["Referee"]), ("", "P Howard"))


if __name__ == "__main__":
    unittest.main()
