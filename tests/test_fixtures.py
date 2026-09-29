"""
The fixture list must survive fixturedownload.com refusing a download (it
blocked the cloud on 29 Sep and the future gameweeks vanished from the page).

  python3 -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracker import sources  # noqa: E402

GOOD = [{"date": date(2026, 10, 3), "time": "15:00", "league": lg, "home": f"H{i}{lg}", "away": f"A{i}{lg}"}
        for lg in ("Championship", "League 1", "League 2") for i in range(3)]


class FixtureFallback(unittest.TestCase):
    def setUp(self):
        self.real = sources._download_fixtures
        self.cache = os.path.join(tempfile.mkdtemp(), "schedule_2627.csv")

    def tearDown(self):
        sources._download_fixtures = self.real

    def test_good_download_is_saved_and_used_when_blocked(self):
        sources._download_fixtures = lambda season, log=print: list(GOOD)
        self.assertEqual(len(sources.get_fixtures("2627", self.cache, log=lambda *_: None)), 9)
        self.assertTrue(os.path.exists(self.cache))
        sources._download_fixtures = lambda season, log=print: []          # blocked
        st = {}
        got = sources.get_fixtures("2627", self.cache, log=lambda *_: None, status=st)
        self.assertEqual(len(got), 9)
        self.assertEqual(st["source"], "saved copy")

    def test_one_division_blocked(self):
        sources._download_fixtures = lambda season, log=print: list(GOOD)
        sources.get_fixtures("2627", self.cache, log=lambda *_: None)
        sources._download_fixtures = lambda season, log=print: [f for f in GOOD if f["league"] != "League 1"]
        st = {}
        got = sources.get_fixtures("2627", self.cache, log=lambda *_: None, status=st)
        self.assertEqual(sorted({f["league"] for f in got}), ["Championship", "League 1", "League 2"])
        self.assertEqual(st["missing"], ["League 1"])


if __name__ == "__main__":
    unittest.main()
