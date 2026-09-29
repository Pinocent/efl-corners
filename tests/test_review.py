"""
Checks that the self-review behaves: corrects a real, lasting bias, ignores
noise, and rolls back a correction that makes things worse.

  python3 -m unittest discover tests
"""

import json
import random
import sys
import os
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tracker import review  # noqa: E402


def rows_for(n, start, league="League 2", bias=1.0, seed=1, used_factor=1.0):
    """n matches with cards expected ~3.8; actual drawn around expected x bias."""
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        eh, ea = rnd.uniform(1.4, 2.4), rnd.uniform(1.4, 2.4)
        lam = (eh + ea) * bias
        tot = sum(1 for _ in range(60) if rnd.random() < lam / 60)     # ~Poisson(lam)
        hk = sum(1 for _ in range(tot) if rnd.random() < eh / (eh + ea))
        d = start + timedelta(days=i // 12)
        out.append({"date": d.isoformat(), "league": league, "round": d.isoformat(),
                    "kh": eh * used_factor, "ka": ea * used_factor, "hk": hk, "ak": tot - hk,
                    "raw": json.dumps({"k": [eh, ea]})})
    return out


class SelfReview(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 12, 1)
        self.state = review.load("/nonexistent", self.today)

    def test_ignores_noise(self):
        rows = rows_for(300, date(2026, 8, 1), bias=1.0, seed=2)
        changes = review.decide(self.state, rows, self.today)
        self.assertEqual(changes, [])
        self.assertFalse(review.Adjuster(self.state).any())

    def test_corrects_a_lasting_bias(self):
        rows = rows_for(400, date(2026, 8, 1), bias=1.12, seed=3)
        changes = review.decide(self.state, rows, self.today)
        level = self.state["active"]["cards"]["level"]["League 2"]
        self.assertTrue(any("League 2" in c for c in changes))
        self.assertGreater(level, 1.03)
        self.assertLessEqual(level, 1.10)              # capped, and cautious
        a, b = review.Adjuster(self.state)("cards", "League 2", 2.0, 2.0)
        self.assertAlmostEqual(a + b, 4.0 * level, places=6)
        a, b = review.Adjuster(self.state)("cards", "League 1", 2.0, 2.0)
        self.assertAlmostEqual(a + b, 4.0, places=6)   # other divisions untouched

    def test_rolls_back_when_it_hurts(self):
        rows = rows_for(400, date(2026, 8, 1), bias=1.12, seed=4)
        review.decide(self.state, rows, self.today)
        level = self.state["active"]["cards"]["level"]["League 2"]
        self.assertGreater(level, 1.0)
        # the bias disappears: the next 120 matches were predicted with the
        # correction (shown = raw x level) but came in at the raw expectation
        later = date.fromisoformat(self.state["active"]["cards"]["since"]) + timedelta(days=1)
        after = rows_for(120, later, bias=1.0, seed=5, used_factor=level)
        changes = review.decide(self.state, rows + after, self.today + timedelta(days=21))
        self.assertTrue(any("Rolled back" in c for c in changes))
        self.assertFalse(review.Adjuster(self.state).any())
        # and it isn't tried again straight away
        review.decide(self.state, rows + after, self.today + timedelta(days=28))
        self.assertFalse(review.Adjuster(self.state).any())

    def test_calibration_slope(self):
        rnd = random.Random(7)
        ps = [rnd.uniform(0.2, 0.8) for _ in range(3000)]
        # outcomes follow a flatter curve than the model says: overconfident
        os_ = [rnd.random() < 0.5 + (p - 0.5) * 0.5 for p in ps]
        slope, se = review.calibration_slope(ps, os_)
        self.assertLess(slope, 0.8)
        honest = [rnd.random() < p for p in ps]
        slope, se = review.calibration_slope(ps, honest)
        self.assertLess(abs(slope - 1), 3 * se)


if __name__ == "__main__":
    unittest.main()
