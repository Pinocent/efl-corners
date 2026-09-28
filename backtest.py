#!/usr/bin/env python3
"""
Walk-forward backtest: replay seasons gameweek by gameweek, predicting each
gameweek using only matches played before it, then score against what
happened. Every season since 2018-19 is available.

  python3 backtest.py                 # every season: league average vs old (v2) vs new
  python3 backtest.py 2526            # one season
  python3 backtest.py tune            # search the settings on TRAIN, check on TEST
  python3 backtest.py early           # the first 8 gameweeks only (tests starting ratings)

Settings are chosen on TRAIN seasons and only then checked on TEST seasons,
so the test numbers are honest. 2019-20 (cut short by Covid) and 2020-21
(played without crowds, so almost no home advantage) are left out of both.

Lower is better for every column except the hit rate.
  MAE tot    average miss on total corners
  LL team    log-loss of each side's corner count, 0-15+ (the sharpest test)
  Brier ...  squared error of the probability for that market (0.25 = coin flip)
"""

import math
import os
import sys
from datetime import timedelta
from multiprocessing import Pool

from tracker import markets
from tracker.legacy import predict_v2
from tracker.model import PARAMS, Model, learn_movers
from tracker.rounds import detect_rounds
from tracker.sources import get_results

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "cache")
ALL = ["1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526"]
TRAIN = ["1819", "2122", "2223", "2324"]
TEST = ["2425", "2526"]

_loaded = {}


def load(season):
    if season not in _loaded:
        _loaded[season] = get_results(season, CACHE, log=lambda *_: None)
    return _loaded[season]


def context(season, params=PARAMS):
    """Last season's ratings and the promoted/relegated table, from before `season`."""
    earlier = ALL[:ALL.index(season)] if season in ALL else ALL
    prev = load(earlier[-1]) if earlier else []
    prior = None
    if prev:
        prior = Model(params).fit(prev, max(m["date"] for m in prev) + timedelta(days=1)).priors()
    movers = learn_movers([(s, load(s)) for s in earlier], params) if len(earlier) >= 2 else {}
    return prior, movers


def evaluate(season, predictor, params=PARAMS, first=3, last=999):
    """Score one season, gameweeks first..last (0-based). Returns raw sums."""
    matches = load(season)
    prior, movers = context(season, params)
    rounds = detect_rounds(matches)
    s = {k: 0.0 for k in ("n", "mae", "ll", "b95", "b105", "bboth", "bbtts", "bo25",
                          "bcs", "gmae", "ng", "o95_n", "o95_hit")}
    for rid in list(rounds)[first:last]:
        batch = [m for m in matches if m["round"] == rid and m.get("hc") is not None]
        if not batch:
            continue
        pred = predictor(matches, rounds[rid]["start"], prior, movers, params)
        for m in batch:
            p = pred(m)
            if not p:
                continue
            c, g = p["corners"], p["goals"]
            hc, ac = int(m["hc"]), int(m["ac"])
            tot = hc + ac
            s["n"] += 1
            s["mae"] += abs(c["et"] - tot)
            s["ll"] += -math.log(max(c["ph"][min(hc, 15)], 1e-9)) - math.log(max(c["pa"][min(ac, 15)], 1e-9))
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
    return s


def summarise(parts):
    """Pool raw sums from several seasons into the reported measures."""
    t = {}
    for s in parts:
        for k, v in s.items():
            t[k] = t.get(k, 0) + v
    n, ng = t["n"] or 1, t["ng"] or 1
    return {"n": int(t["n"]), "MAE tot": t["mae"] / n, "LL team": t["ll"] / n / 2,
            "Brier O9.5": t["b95"] / n, "Brier O10.5": t["b105"] / n,
            "Brier both4": t["bboth"] / n, "strong O9.5 hit%": 100 * t["o95_hit"] / (t["o95_n"] or 1),
            "strong n": int(t["o95_n"]),
            "Goals MAE": t["gmae"] / ng, "Brier BTTS": t["bbtts"] / ng,
            "Brier O2.5": t["bo25"] / ng, "Brier CS": t["bcs"] / ng}


# ---- predictors: (matches, asof, prior, movers, params) -> fixture -> prediction

def v3(matches, asof, prior, movers, params):
    return Model(params).fit(matches, asof, prior, movers).predict


def v3_no_movers(matches, asof, prior, movers, params):
    return Model(params).fit(matches, asof, prior, None).predict


def v2(matches, asof, prior, movers, params):
    return lambda m: predict_v2(matches, asof, m)


def baseline(matches, asof, prior, movers, params):
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


KEYS = ["n", "MAE tot", "LL team", "Brier O9.5", "Brier O10.5", "Brier both4",
        "strong O9.5 hit%", "strong n", "Goals MAE", "Brier BTTS", "Brier O2.5", "Brier CS"]


def show(name, res=None):
    if res is None:
        print(f'{name:30}' + "".join(f"{k:>12}" for k in KEYS))
        return
    print(f"{name:30}" + "".join(
        f"{res[k]:>12.0f}" if k in ("n", "strong n") else f"{res[k]:>12.4f}" for k in KEYS))


# ---- tuning (parallel: one job per setting per season)

def _job(args):
    params, season, first, last = args
    return evaluate(season, v3, params, first, last)


def run_many(configs, seasons, first=3, last=999, procs=None):
    jobs = [(p, s, first, last) for p in configs for s in seasons]
    with Pool(procs or max(1, (os.cpu_count() or 2) - 1)) as pool:
        out = pool.map(_job, jobs)
    k = len(seasons)
    return [summarise(out[i * k:(i + 1) * k]) for i in range(len(configs))]


CORNER_GRID = {"half_life": [45, 60, 90, 120], "k_team": [8, 12, 16, 24],
               "k_venue": [20, 40, 80], "shot_blend": [0.2, 0.4, 0.6],
               "prior_regress": [0.3, 0.5, 0.7], "total_size": [40.0, 80.0, 160.0],
               "split_kappa": [12.0, 18.0, 25.0]}
GOAL_GRID = {"goal_k_team": [15.0, 25.0, 40.0], "goal_half_life": [60, 120, 240],
             "goal_mix": [(0.3, 0.35, 0.35), (0.2, 0.4, 0.4), (0.1, 0.45, 0.45)],
             "dc_rho": [-0.13, -0.08, -0.03]}


def corner_score(r):
    return r["LL team"] + r["Brier O9.5"] + r["Brier O10.5"] + r["Brier both4"]


def goal_score(r):
    return r["Brier BTTS"] + r["Brier O2.5"] + r["Brier CS"]


def tune():
    best = dict(PARAMS)
    for grid, score in ((CORNER_GRID, corner_score), (GOAL_GRID, goal_score)):
        for rnd in (1, 2):
            for key, values in grid.items():
                configs = [dict(best, **{key: v}) for v in values]
                res = run_many(configs, TRAIN)
                pick = min(range(len(values)), key=lambda i: score(res[i]))
                marks = "  ".join(f"{v}{'*' if i == pick else ''}: {score(res[i]):.4f}"
                                  for i, v in enumerate(values))
                print(f"  pass {rnd}  {key:15} {marks}", flush=True)
                best[key] = values[pick]
    print("\nChosen on", ", ".join(TRAIN), ":")
    for k in list(CORNER_GRID) + list(GOAL_GRID):
        if best[k] != PARAMS[k]:
            print(f"  {k}: {PARAMS[k]} -> {best[k]}")
    print("\nChecked on", ", ".join(TEST), "(not used for choosing):")
    show("")
    for name, p in (("current settings", PARAMS), ("tuned settings", best)):
        show(name, run_many([p], TEST)[0])
    return best


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg == "tune":
        tune()
        return
    if arg == "early":
        print("First 8 gameweeks of each season - tests where ratings start from\n")
        show("")
        for name, pred in (("league average only", baseline), ("new, all start at average", v3_no_movers),
                           ("new, arrivals from history", v3)):
            show(name, summarise([evaluate(s, pred, first=0, last=8) for s in TRAIN + TEST]))
        return
    seasons = [arg] if arg != "all" else TRAIN + TEST
    show("")
    for s in seasons:
        print(f"-- 20{s[:2]}-{s[2:]}")
        for name, pred in (("league average only", baseline), ("v2 (old spreadsheet)", v2), ("v3 (new)", v3)):
            show(f"  {name}", summarise([evaluate(s, pred)]))


if __name__ == "__main__":
    main()
