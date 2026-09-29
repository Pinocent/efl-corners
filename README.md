# EFL Corners Board

Predicts corners, corner handicaps and goals for the Championship, League 1
and League 2, logs every prediction, and marks it against the result.

## Run it

- **On the Mac:** double-click `Update Dashboard.command`. It opens `docs/index.html`.
- **In the cloud:** push this folder to a GitHub repo and turn on Pages
  (Settings → Pages → Deploy from branch → `main` / `docs`). The workflow in
  `.github/workflows/update.yml` then runs at 09:17 and 21:17 UTC every day (about
  10am and 10pm UK summer time) and republishes the dashboard, with no terminal
  involved. GitHub starts scheduled runs when it has capacity, and one has
  started hours late, so the "Updated" time on the page is when the build
  actually ran. Pushing `manual_results.csv`, `referees.csv`, `run.py` or
  anything in `tracker/` starts a run straight away.

No packages to install. Python 3.9+ standard library only.

## Nightly collection (Claude scheduled task)

A scheduled task in the Claude desktop app, **EFL board: collect results,
cards and referees**, runs every evening at 22:30 while the app is open (if
the app is closed then, it runs the next time the app opens):

1. `python3 collect_todo.py` lists EFL matches from the last 4 days that the
   board has no result for, and matches in the next 6 days with no referee.
   If both lists are empty, the task stops without opening a website.
2. It reads each finished match on Flashscore (goals, corners, yellow and red
   cards, fouls, possession, crosses, referee) into `manual_results.csv`, and
   announced referee appointments into `referees.csv`.
3. `./sync_to_github.sh` uploads both files. The push starts the cloud
   update, and the online board refreshes about two minutes later.

The official results feed replaces the Flashscore corners, goals and cards
when it publishes them, usually a day or two later. Possession, crosses and
referees are kept.

To run it by hand: "Run now" on the task in the Claude app's Scheduled list,
or double-click `Send Flashscore Results.command` after editing the CSVs
yourself.

## Files

| File | What it is |
|---|---|
| `data/matches.csv` | Every result this season: gameweek, corners, goals, xG, shots, cards, fouls, referee |
| `data/predictions.csv` | Every prediction and its calls, frozen at kick-off, with the result once known (`v3` = this model, `v2` = the old spreadsheet's) |
| `manual_results.csv` | Flashscore numbers for matches the feed hasn't published yet (filled nightly by the scheduled task) |
| `referees.csv` | Referee appointments for upcoming matches (filled nightly by the scheduled task) |
| `docs/index.html` | The dashboard: Gameweek, Track record, Teams and Referees views, every section a dropdown |
| `backtest.py` | Replays every season since 2018-19: `python3 backtest.py` (corners and goals), `cards`, or `tune` / `cards-tune` to re-tune |
| `tracker/` | The code: `rounds.py` gameweeks, `model.py` corners and goals, `cards.py` cards and referees, `markets.py` probabilities, `evaluate.py` calls and marking, `flags.py` warnings |

The old spreadsheet version is in `old version/`, untouched.

## Tuning

Model weights are in `PARAMS` at the top of `tracker/model.py`, and call
thresholds are in `CALL` in `tracker/evaluate.py`. After changing a weight,
run `python3 backtest.py 2526` and keep the change only if the Brier and
LL columns go down.
