"""
Calls are only made in markets Sky Bet offers for the division (oddschecker,
17 Oct 2026): cards in the Championship only; corner unders and both 4+ not in
League 2.

  python3 -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracker import evaluate as ev  # noqa: E402

# a prediction confident in every market
P = {"line": "10.5", "o105": "0.3", "kline": "3.5", "k35": "0.8", "dom_home": "0.7",
     "both4": "0.7", "btts": "0.7", "o25": "0.3"}


def markets(league, **kw):
    return {c["market"]: c["pick"] for c in ev.calls_for(dict(P, league=league, **kw))}


class CallsFollowSkyBet(unittest.TestCase):
    def test_championship_gets_every_market(self):
        self.assertEqual(markets("Championship"), {"Corners total": "Under 10.5", "Cards total": "Over 3.5",
                         "Corner dominance": "Home", "Both 4+ corners": "Yes", "BTTS": "Yes", "Goals 2.5": "Under"})

    def test_no_card_calls_below_the_championship(self):
        self.assertNotIn("Cards total", markets("League 1"))
        self.assertNotIn("Cards total", markets("League 2"))

    def test_league_2_corners_overs_only(self):
        self.assertNotIn("Corners total", markets("League 2"))                       # an under: not offered
        self.assertEqual(markets("League 2", o105="0.7")["Corners total"], "Over 10.5")
        self.assertNotIn("Both 4+ corners", markets("League 2"))
        self.assertIn("Both 4+ corners", markets("League 1"))


if __name__ == "__main__":
    unittest.main()
