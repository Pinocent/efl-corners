# EFL Corners Board

Predicts corners, corner handicaps and goals for the Championship, League 1
and League 2, logs every prediction, and marks it against the result.

## Run it

- **On the Mac:** double-click `Update Dashboard.command`. It opens `docs/index.html`.
- **In the cloud:** push this folder to a GitHub repo and turn on Pages
  (Settings → Pages → Deploy from branch → `main` / `docs`). The workflow in
  `.github/workflows/update.yml` then runs every morning at 07:00 UTC and
  republishes the dashboard, with no terminal involved.

No packages to install. Python 3.9+ standard library only.

## Files

| File | What it is |
|---|---|
| `data/matches.csv` | Every result this season, with gameweek, corners, goals, xG, shots and odds |
| `data/predictions.csv` | Every prediction made, frozen on match day (`v3` = this model, `v2` = the old spreadsheet's) |
| `manual_results.csv` | Flashscore numbers for matches the feed hasn't published yet. The official feed replaces them once it has them |
| `docs/index.html` | The dashboard |
| `backtest.py` | Replays past seasons to test the model: `python3 backtest.py 2526` |
| `tracker/` | The code: `rounds.py` gameweeks, `model.py` ratings, `markets.py` probabilities, `evaluate.py` marking |

`update_corners.py` and `corners_tracker.xlsx` are the old version, left untouched.

## Tuning

Model weights are in `PARAMS` at the top of `tracker/model.py`, and call
thresholds are in `CALL` in `tracker/evaluate.py`. After changing a weight,
run `python3 backtest.py 2526` and keep the change only if the Brier and
LL columns go down.
