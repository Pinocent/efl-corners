"""
Downloads. Everything comes back as plain dicts with canonical team names.

  results   football-data.co.uk  - goals, corners, xG, shots, cards, fouls, referee
  referees  football-data fixtures.csv - referee appointments for the next few
                                   days (published about a day before), plus
                                   referees.csv written by the Cowork task
  fixtures  fixturedownload.com  - the full season schedule, all three divisions
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
FIXTURE_URLS = {
    "Championship": ["https://fixturedownload.com/download/championship-{yr}-GMTStandardTime.csv",
                     "https://fixturedownload.com/download/efl-championship-{yr}-GMTStandardTime.csv"],
    "League 1": ["https://fixturedownload.com/download/efl-league-one-{yr}-GMTStandardTime.csv"],
    "League 2": ["https://fixturedownload.com/download/efl-league-two-{yr}-GMTStandardTime.csv"],
}

NUM_FIELDS = ["hg", "ag", "hc", "ac", "hxg", "axg", "hs", "as_", "hst", "ast",
              "hy", "ay", "hr", "ar", "hf", "af",        # yellow cards, red cards, fouls
              "hp", "ap", "hcr", "acr"]                  # hp/ap possession %, hcr/acr crosses
FIELDS = ["date", "time", "league", "home", "away", "ref"] + NUM_FIELDS + ["source"]
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
REFEREE_FILE = "referees.csv"

# football-data column -> our field
_FD_MAP = {"FTHG": "hg", "FTAG": "ag", "HC": "hc", "AC": "ac", "HxG": "hxg",
           "AxG": "axg", "HS": "hs", "AS": "as_", "HST": "hst", "AST": "ast",
           "HR": "hr", "AR": "ar", "HY": "hy", "AY": "ay", "HF": "hf", "AF": "af"}


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


def ref_key(name):
    """
    One spelling per referee: 'B Toner'. football-data writes initial and
    surname (with the odd typo - 'Mm Coy', 'O  Yates'); Flashscore writes the
    full name ('Ben Toner'). Returns '' when there's no name.
    """
    words = str(name or "").replace(".", " ").split()
    if not words:
        return ""
    last = words[-1]
    if len(last) > 1 and last[0].islower() and last[1].isupper():   # 'jBrooks'
        last = last[1:]
    last = last[0].upper() + last[1:]
    return f"{words[0][0].upper()} {last}" if len(words) > 1 else last


def get_upcoming_referees(log=print):
    """{(home, away): referee} for the next few days' matches (football-data)."""
    try:
        text = fetch(FIXTURES_URL)
    except Exception as e:
        log(f"  ! referee appointments unavailable ({e})")
        return {}
    out = {}
    for r in csv.DictReader(io.StringIO(text)):
        if (r.get("Div") or "").strip() in LEAGUES and (r.get("Referee") or "").strip():
            out[(canon(r.get("HomeTeam")), canon(r.get("AwayTeam")))] = ref_key(r["Referee"])
    return out


def get_manual_referees(path):
    """referees.csv (Date,League,Home,Away,Referee), written by the Cowork task."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            g = {k.lower().strip(): v for k, v in r.items() if k}
            ref = ref_key(g.get("referee"))
            if ref and g.get("home") and g.get("away"):
                out[(canon(g["home"]), canon(g["away"]))] = ref
    return out


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
             "home": h, "away": a, "source": source, "ref": ref_key(r.get("Referee"))}
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


SCHEDULE_FIELDS = ["date", "time", "league", "home", "away"]


def schedule_path(folder, season):
    """The saved copy of a season's fixture list (repository root)."""
    return os.path.join(folder, f"schedule_{season}.csv")


def get_fixtures(season, cache=None, log=print, status=None):
    """
    Whole-season schedule for all three divisions. fixturedownload.com
    sometimes refuses automated requests (it blocked the cloud's on 29 Sep), so
    every good download is saved to `cache`, and a division that can't be
    downloaded comes from the saved copy instead. `status` (a dict) is filled
    with what happened, for the page.
    """
    fresh = _download_fixtures(season, log)
    got = {f["league"] for f in fresh}
    if status is not None:
        status.update(source="download", missing=[])
    if not cache:
        return fresh
    saved = []
    if os.path.exists(cache):
        for r in read_csv(cache):
            d = parse_date(r.get("date"))
            if d:
                saved.append({"date": d, "time": r.get("time", ""), "league": r["league"],
                              "home": r["home"], "away": r["away"]})
    if len(got) == len(FIXTURE_URLS):
        new = sorted(fresh, key=lambda f: (f["date"], f["league"], f["home"]))
        old = sorted(saved, key=lambda f: (f["date"], f["league"], f["home"]))
        if new != old:
            write_csv(cache, new, SCHEDULE_FIELDS)
        return fresh
    missing = [lg for lg in FIXTURE_URLS if lg not in got]
    use = [f for f in saved if f["league"] in missing]
    if use:
        log(f"  ! fixture list download failed for {', '.join(missing)}: using the saved copy")
        if status is not None:
            status.update(source="saved copy", missing=missing)
    elif status is not None:
        status.update(source="unavailable", missing=missing)
    return fresh + use


def _download_fixtures(season, log=print):
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
    Date,League,Home,Away,HomeGoals,AwayGoals,HomeCorners,AwayCorners
    optional: HomePossession,AwayPossession,HomeCrosses,AwayCrosses,
              HomeYellow,AwayYellow,HomeRed,AwayRed,HomeFouls,AwayFouls,Referee
    (crosses as attempted crosses; "7/22" or "22" both read as 22)
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
                  "hc": hc, "ac": ac, "source": "manual",
                  "hp": _stat(g, "homepossession", "home possession", "hp"),
                  "ap": _stat(g, "awaypossession", "away possession", "ap"),
                  "hcr": _stat(g, "homecrosses", "home crosses", "hcr"),
                  "acr": _stat(g, "awaycrosses", "away crosses", "acr"),
                  "hy": _stat(g, "homeyellow", "homeyellows", "hy"),
                  "ay": _stat(g, "awayyellow", "awayyellows", "ay"),
                  "hr": _stat(g, "homered", "homereds", "hr"),
                  "ar": _stat(g, "awayred", "awayreds", "ar"),
                  "hf": _stat(g, "homefouls", "hf"), "af": _stat(g, "awayfouls", "af"),
                  "ref": ref_key(g.get("referee"))})
        out.append(m)
    if out:
        log(f"  Manual results: {len(out)} rows")
    return out


def _stat(g, *names):
    """A number from a manual column: '64', '64%', '7/22' (-> 22 attempted)."""
    for n in names:
        v = (g.get(n) or "").strip().rstrip("%")
        if not v:
            continue
        if "/" in v:
            v = v.split("/")[-1]
        x = fnum(v.split("(")[0].strip())
        if x is not None:
            return x
    return None


def merge_results(official, manual, known):
    """
    Official feed wins. A manual row is kept only when the feed has no match
    between the same two clubs within three days of it.

    known: {(league, club)} from the official feed and fixture list. A manual
    row naming a club that isn't there (a typo, or a spelling the name list
    doesn't cover yet) is set aside rather than creating a phantom club -
    that's how the old spreadsheet ended up double-counting matches.
    Returns (results, manual rows used, [skipped descriptions]).
    """
    have = {}
    for m in official:
        have.setdefault((m["home"], m["away"]), []).append(m["date"])
    by_key = {}
    for m in official:
        by_key.setdefault((m["home"], m["away"]), []).append(m)
    extra, skipped = [], []
    for m in manual:
        bad = [t for t in (m["home"], m["away"]) if (m["league"], t) not in known]
        if bad:
            skipped.append(f'{m["date"]:%-d %b} {m["home"]} v {m["away"]}: '
                           f'{" and ".join(bad)} not recognised in {m["league"]}')
            continue
        same = [o for o in by_key.get((m["home"], m["away"]), []) if abs((o["date"] - m["date"]).days) <= 3]
        if same:
            # the feed's numbers win, but it has no possession or crosses:
            # keep Flashscore's
            for k in ("hp", "ap", "hcr", "acr", "ref"):
                if m.get(k) and not same[0].get(k):
                    same[0][k] = m[k]
        else:
            extra.append(m)
    return official + extra, len(extra), skipped


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
