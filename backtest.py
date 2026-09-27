#!/usr/bin/env python3
"""
Walk-forward backtest: replay a season round by round, predicting each round
using only matches played before it, then score against what happened.

  python3 backtest.py              # last season, old (v2) vs new (v3)
  python3 backtest.py 2526 tune    # also search the model's blend weights

Lower is better for every column except 'hit%'.
  MAE tot    average miss on total corners
  LL team    log-loss of each side's corner count, 0-15+ (the sharpest test)
  Brier ...  squared error of the probability for that market (0.25 = coin flip)
"""

import math
import os
import sys
from datetime import timedelta

from tracker import markets
from tracker.legacy import predict_v2
from tracker.model import PARAMS, Model
from tracker.rounds import detect_rounds
from tracker.sources import get_results, prev_season

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "cache")


def load(season):
    return get_results(season, CACHE, log=lambda *_: None)


def evaluate(matches, prior_model, predictor, skip_rounds=3):
    rounds = detect_rounds(matches)
    ids = list(rounds)[skip_rounds:]
    s = {k: 0.0 for k in ("n", "mae", "ll", "b95", "b105", "bboth", "bbtts",
                          "bo25", "bcs", "gmae", "ng", "o95_n", "o95_hit")}
    for rid in ids:
        r = rounds[rid]
        batch = [m for m in matches if m["round"] == rid and m.get("hc") is not None]
        if not batch:
            continue
        pred = predictor(matches, r["start"], prior_model)
        for m in batch:
            p = pred(m)
            if not p:
                continue
            c, g = p["corners"], p["goals"]
            hc, ac = int(m["hc"]), int(m["ac"])
            tot = hc + ac
            s["n"] += 1
            s["mae"] += abs(c["et"] - tot)
            ph, pa = c["ph"], c["pa"]          # each side's chances, 0..15
            s["ll"] += -math.log(max(ph[min(hc, 15)], 1e-9)) - math.log(max(pa[min(ac, 15)], 1e-9))
            s["b95"] += (c["totals"][9.5] - (tot > 9.5)) ** 2
            s["b105"] += (c["totals"][10.5] - (tot > 10.5)) ** 2
            s["bboth"] += (c["both4"] - (hc >= 4 and ac >= 4)) ** 2
            if c["totals"][9.5] >= 0.65 or c["totals"][9.5] <= 0.35:
                s["o95_n"] += 1
                s["o95_hit"] += (c["totals"][9.5] >= 0.5) == (tot > 9.5)
            if m.get("hg") is not None:
                hg, ag = m["hg"], m["ag"]
                s["ng"] += 1
                s["gmae"] += abs(g["lt"] - hg - ag)
                s["bbtts"] += (g["btts"] - (hg > 0 and ag > 0)) ** 2
                s["bo25"] += (g["o25"] - (hg + ag > 2)) ** 2
                s["bcs"] += ((g["cs_home"] - (ag == 0)) ** 2 + (g["cs_away"] - (hg == 0)) ** 2) / 2
    n, ng = s["n"] or 1, s["ng"] or 1
    return {"n": int(s["n"]), "MAE tot": s["mae"] / n, "LL team": s["ll"] / n / 2,
            "Brier O9.5": s["b95"] / n, "Brier O10.5": s["b105"] / n,
            "Brier both4": s["bboth"] / n, "strong O9.5 hit%": 100 * s["o95_hit"] / (s["o95_n"] or 1),
            "strong n": int(s["o95_n"]),
            "Goals MAE": s["gmae"] / ng, "Brier BTTS": s["bbtts"] / ng,
            "Brier O2.5": s["bo25"] / ng, "Brier CS": s["bcs"] / ng}


def v2(matches, asof, _prior):
    return lambda m: predict_v2(matches, asof, m)


def v3(params):
    def run(matches, asof, prior):
        mdl = Model(params).fit(matches, asof, prior)
        return mdl.predict
    return run


def baseline(matches, asof, _prior):
    """League averages only - the bar any model has to clear."""
    past = [m for m in matches if m["date"] < asof and m.get("hc") is not None]
    mu = {}
    for m in past:
        v = mu.setdefault(m["league"], [0, 0, 0, 0, 0])
        v[0] += m["hc"]; v[1] += m["ac"]; v[2] += m["hg"]; v[3] += m["ag"]; v[4] += 1

    def pred(m):
        v = mu.get(m["league"])
        if not v or v[4] < 10:
            return None
        return {"corners": markets.corner_markets(v[0] / v[4], v[1] / v[4], m["league"]),
                "goals": markets.goal_markets(v[2] / v[4], v[3] / v[4])}
    return pred


def prior_for(season):
    old = load(prev_season(season))
    if not old:
        return None
    end = max(m["date"] for m in old) + timedelta(days=1)
    return Model(PARAMS).fit(old, end).priors()


def show(name, res):
    keys = ["n", "MAE tot", "LL team", "Brier O9.5", "Brier O10.5", "Brier both4",
            "strong O9.5 hit%", "strong n", "Goals MAE", "Brier BTTS", "Brier O2.5", "Brier CS"]
    if name == "header":
        print(f'{"":24}' + "".join(f"{k:>12}" for k in keys))
        return
    print(f"{name:24}" + "".join(
        f"{res[k]:>12.0f}" if k in ("n", "strong n") else f"{res[k]:>12.3f}" for k in keys))


def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2526"
    matches = load(season)
    print(f"Season {season}: {len(matches)} matches\n")
    prior = prior_for(season)
    show("header", None)
    show("league average only", evaluate(matches, prior, baseline))
    show("v2 (old)", evaluate(matches, prior, v2))
    show("v3 (new)", evaluate(matches, prior, v3(PARAMS)))

    if "tune" in sys.argv:
        print("\nOne-at-a-time sensitivity (v3):")
        grid = {"half_life": [60, 120, 365], "k_team": [3, 5, 8],
                "k_venue": [5, 10, 20], "shot_blend": [0, 0.4, 0.7],
                "goal_k_team": [12, 25, 40], "goal_half_life": [60, 120, 240],
                "prior_regress": [0, 0.5, 0.8]}
        for k, vals in grid.items():
            for v in vals:
                p = dict(PARAMS, **{k: v})
                show(f"  {k}={v:g}", evaluate(matches, prior if p["prior_regress"] else None, v3(p)))


if __name__ == "__main__":
    main()
