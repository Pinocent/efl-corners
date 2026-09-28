# EFL Corners Board

Predicts corners, corner handicaps and goals for the Championship, League 1
and League 2, logs every prediction, and marks it against the result.

## Run it

- **On the Mac:** double-click `Update Dashboard.command`. It opens `docs/index.html`.
- **In the cloud:** push this folder to a GitHub repo and turn on Pages
  (Settings → Pages → Deploy from branch → `main` / `docs`). The workflow in
  `.github/workflows/update.yml` then runs at 07:00 and 19:00 UTC every day and
  republishes the dashboard, with no terminal involved.

No packages to install. Python 3.9+ standard library only.

## Adding Flashscore results early (and possession and crosses)

1. In Claude's Cowork tab, with this folder selected, ask: "Run the Midweek
   Corners Check for Saturday 3 October, and include each side's crosses as
   HomeCrosses and AwayCrosses" (any date). It adds rows to
   `manual_results.csv`. Possession and crosses are optional columns
   (`HomePossession, AwayPossession, HomeCrosses, AwayCrosses`); "64%" and
   Flashscore's "7/22" crosses format are both understood.
2. Double-click `Send Flashscore Results.command`. It uploads the file to
   GitHub, and the online dashboard refreshes about 2 minutes later.

The official feed replaces the corners and goals when it publishes them,
but possession and crosses (which the feed doesn't have) are kept. They show
on the team pages and match details; they don't feed the predictions yet,
because there isn't enough of them to test whether they help.

## Files

| File | What it is |
|---|---|
| `data/matches.csv` | Every result this season, with gameweek, corners, goals, xG, shots and odds |
| `data/predictions.csv` | Every prediction and its calls, frozen at kick-off, with the result once known (`v3` = this model, `v2` = the old spreadsheet's) |
| `manual_results.csv` | Flashscore numbers for matches the feed hasn't published yet. The official feed replaces them once it has them |
| `docs/index.html` | The dashboard |
| `backtest.py` | Replays every season since 2018-19 to test the model: `python3 backtest.py`, or `tune` to re-tune the settings |
| `tracker/` | The code: `rounds.py` gameweeks, `model.py` ratings, `markets.py` probabilities, `evaluate.py` calls and marking, `flags.py` warnings |

The old spreadsheet version is in `old version/`, untouched.

## Tuning

Model weights are in `PARAMS` at the top of `tracker/model.py`, and call
thresholds are in `CALL` in `tracker/evaluate.py`. After changing a weight,
run `python3 backtest.py 2526` and keep the change only if the Brier and
LL columns go down.
