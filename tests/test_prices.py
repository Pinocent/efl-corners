"""
Sky Bet prices go onto the calls (latest before kick-off, then frozen), and a
call's edge is the model's chance times the price, less the stake.

  python3 -m unittest discover tests
"""

import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracker import evaluate as ev  # noqa: E402

FIELDS = ["date", "league", "home", "away", "market", "pick", "match", "odds", "decimal"]


def prices(odds, dec, fetched="2026-10-16T22:00:00Z"):
    return {"fetched": fetched, "fields": FIELDS,
            "rows": [["2026-10-17", "Championship", "Millwall", "Lincoln", "Corner dominance", "Home",
                      "Millwall v Lincoln", odds, dec]]}


class Prices(unittest.TestCase):
    def setUp(self):
        self.store = {"k": {"model": "v3", "date": "2026-10-17", "time": "15:00", "home": "Millwall",
                            "away": "Lincoln", "calls": json.dumps([{"market": "Corner dominance",
                                                                       "pick": "Home", "p": 0.74}])}}

    def test_price_and_edge_on_the_call(self):
        ev.attach_prices(self.store, prices("8/13", 1.615), datetime(2026, 10, 16, 23, tzinfo=timezone.utc))
        c = ev.mark(self.store["k"])[0]
        self.assertEqual(c["sb"], "8/13")
        self.assertAlmostEqual(c["edge"], round(0.74 * 1.615 - 1, 3))

    def test_frozen_at_kick_off(self):
        ev.attach_prices(self.store, prices("8/13", 1.615), datetime(2026, 10, 16, 23, tzinfo=timezone.utc))
        ev.attach_prices(self.store, prices("1/2", 1.5), datetime(2026, 10, 17, 16, tzinfo=timezone.utc))
        self.assertEqual(ev.mark(self.store["k"])[0]["sb"], "8/13")


if __name__ == "__main__":
    unittest.main()
