"""
Downloads. Everything comes back as plain dicts with canonical team names.

  results   football-data.co.uk  - goals, corners, xG, shots, pre-match odds
  fixtures  fixturedownload.com  - the full season schedule, all three divisions
  odds      football-data fixtures.csv - odds for the next few days' fixtures
  manual    manual_results.csv   - Flashscore numbers typed in (or written by
                                   the Cowork skill) before the feed catches up
"""

import csv
import io
import os
import time
import urllib.request
from datetime import date, datetime

from .teams import LEAGUES, canon

RESULTS_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
ODDS_URL = "https://www.football-data.co.uk/fixtures.csv"
FIXTURE_URLS = {
    "Championship": ["https://fixturedownload.com/download/championship-{yr}-GMTStandardTime.csv",
                     "https://fixturedownload.com/download/efl-championship-{yr}-GMTStandardTime.csv"],
    "League 1": ["https://fixturedownload.com/download/efl-league-one-{yr}-GMTStandardTime.csv"],
    "League 2": ["https://fixturedownload.com/download/efl-league-two-{yr}-GMTStandardTime.csv"],
}

NUM_FIELDS = ["hg", "ag", "hc", "ac", "hxg", "axg", "hs", "as_", "hst", "ast",
              "oh", "od", "oa", "oo25", "ou25", "hr", "ar"]
FIELDS = ["date", "time", "league", "home", "away"] + NUM_FIELDS + ["source"]

# football-data column -> our field
_FD_MAP = {"FTHG": "hg", "FTAG": "ag", "HC": "hc", "AC": "ac", "HxG": "hxg",
           "AxG": "axg", "HS": "hs", "AS": "as_", "HST": "hst", "AST": "ast",
           "AvgH": "oh", "AvgD": "od", "AvgA": "oa",
           "Avg>2.5": "oo25", "Avg<2.5": "ou25", "HR": "hr", "AR": "ar"}


def season_code(today=None):
    t = today or date.today()
    start = t.year if t.month >= 7 else t.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def prev_season(code):
    a = int(code[:2]) - 1
    return f"{a:02d}{a + 1:02d}"


def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8-sig", errors="replace")
        except Exception as e:          # network blips are common; retry
            last = e
            time.sleep(1 + 2 * i)
    raise last


def parse_date(raw):
    raw = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            pass
    return None


def fnum(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _fd_rows(text, league, source):
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        d = parse_date(r.get("Date"))
        h, a = canon(r.get("HomeTeam")), canon(r.get("AwayTeam"))
        if not (d and h and a):
            continue
        m = {"date": d, "time": (r.get("Time") or "").strip(), "league": league,
             "home": h, "away": a, "source": source}
        for col, key in _FD_MAP.items():
            m[key] = fnum(r.get(col))
        out.append(m)
    return out


def get_results(season, cache_dir=None, log=print):
    """
    One season of results. Finished seasons are cached on disk - they never
    change, and it saves hammering the site on every run.
    """
    out = []
    for div, league in LEAGUES.items():
        cached = cache_dir and os.path.join(cache_dir, f"{season}_{div}.csv")
        text = None
        if cached and os.path.exists(cached) and season != season_code():
            with open(cached, encoding="utf-8") as fh:
                text = fh.read()
        if text is None:
            try:
                text = fetch(RESULTS_URL.format(season=season, div=div))
            except Exception as e:
                log(f"  ! {league} {season}: download failed ({e})")
                continue
            if cached and season != season_code():
                os.makedirs(cache_dir, exist_ok=True)
                with open(cached, "w", encoding="utf-8") as fh:
                    fh.write(text)
        rows = [m for m in _fd_rows(text, league, "football-data")
                if m["hg"] is not None]
        out.extend(rows)
        if season == season_code():
            log(f"  {league}: {len(rows)} results")
    return out


def get_upcoming_odds(log=print):
    """Pre-match odds for fixtures in the next few days (football-data)."""
    try:
        text = fetch(ODDS_URL)
    except Exception as e:
        log(f"  ! odds download failed ({e})")
        return []
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        div = (r.get("Div") or "").strip()
        if div in LEAGUES:
            out.extend(_fd_rows(_one_row_csv(r), LEAGUES[div], "odds"))
    return out


def _one_row_csv(r):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(r.keys()))
    w.writeheader()
    w.writerow(r)
    return buf.getvalue()


def get_fixtures(season, log=print):
    """Whole-season schedule for all three divisions."""
    yr = 2000 + int(season[:2])
    out = []
    for league, patterns in FIXTURE_URLS.items():
        got = []
        for pat in patterns:
            try:
                text = fetch(pat.format(yr=yr), tries=1)
            except Exception:
                continue
            for r in csv.DictReader(io.StringIO(text)):
                raw = (r.get("Date") or "").strip()
                try:
                    dt = datetime.strptime(raw, "%d/%m/%Y %H:%M")
                except ValueError:
                    continue
                h, a = canon(r.get("Home Team")), canon(r.get("Away Team"))
                if h and a:
                    got.append({"date": dt.date(), "time": dt.strftime("%H:%M"),
                                "league": league, "home": h, "away": a})
            if got:
                break
        if not got:
            log(f"  ! {league}: fixture list unavailable")
        out.extend(got)
    return out


def get_manual(path, log=print):
    """
    manual_results.csv - header row required:
    Date,League,Home,Away,HomeGoals,AwayGoals,HomeCorners,AwayCorners[,HomePossession,AwayPossession]
    """
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        g = {k.lower().strip(): v for k, v in r.items() if k}
        d = parse_date(g.get("date"))
        league = (g.get("league") or "").strip()
        h, a = canon(g.get("home")), canon(g.get("away"))
        hc, ac = fnum(g.get("homecorners")), fnum(g.get("awaycorners"))
        if not (d and h and a) or hc is None or ac is None:
            continue
        if league not in LEAGUES.values():
            continue
        m = {k: None for k in NUM_FIELDS}
        m.update({"date": d, "time": "", "league": league, "home": h, "away": a,
                  "hg": fnum(g.get("homegoals")), "ag": fnum(g.get("awaygoals")),
                  "hc": hc, "ac": ac, "source": "manual"})
        out.append(m)
    if out:
        log(f"  Manual results: {len(out)} rows")
    return out


def merge_results(official, manual):
    """
    Official feed wins. A manual row is kept only when the feed has no match
    between the same two clubs within three days of it.
    """
    have = {}
    for m in official:
        have.setdefault((m["home"], m["away"]), []).append(m["date"])
    extra = [m for m in manual
             if not any(abs((d - m["date"]).days) <= 3
                        for d in have.get((m["home"], m["away"]), []))]
    return official + extra, len(extra)


def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (v.isoformat() if isinstance(v, date) else
                            ("" if v is None else v)) for k, v in r.items()})


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
