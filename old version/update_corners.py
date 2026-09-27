#!/usr/bin/env python3
"""
update_corners.py  -  v2

Run it and everything happens:
  1. downloads full-time results + corners for Championship, League 1, League 2
  2. adds new matches to Match Log (and corrects any provisional figures)
  3. downloads this week's upcoming fixtures
  4. rebuilds Team Summary, Fixtures and Findings from scratch

Only Match Log accumulates. The other tabs are regenerated every run, so
nothing can go stale or point at the wrong row.

Put this next to corners_tracker.xlsx and run it. No arguments.
"""

import csv
import io
import math
import re
import os
import shutil
import sys
import urllib.request
from datetime import datetime, date, timedelta

WORKBOOK = "corners_tracker.xlsx"

DIVISIONS = {"E1": "Championship", "E2": "League 1", "E3": "League 2"}
RESULTS_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"

# Manually-verified results (e.g. a Claude Cowork task reading Flashscore).
# Same folder as the workbook. Merged before the official download, so the
# official feed corrects any manual entry it later disagrees with.
MANUAL_FILE = "manual_results.csv"

# Second fixture source - full-season schedules, whichever divisions exist.
OF_BASE = "https://raw.githubusercontent.com/openfootball/england/master/{season}/"
OF_FILES = {"2-championship.txt": "Championship",
            "3-league1.txt": "League 1",
            "4-league2.txt": "League 2"}

# Third source - full-season EFL fixture lists. Several URL shapes are tried
# because the naming has changed over the years; the first that parses wins.
FD_LEAGUES = {
    "Championship": ["efl-championship"],
    "League 1": ["efl-league-one", "efl-league-1"],
    "League 2": ["efl-league-two", "efl-league-2"],
}
FD_PATTERNS = [
    "https://fixturedownload.com/download/{slug}-{yr}-GMTStandardTime.csv",
    "https://fixturedownload.com/download/{slug}-{yr}-UTC.csv",
    "https://fixturedownload.com/download/{slug}-{yr}.csv",
]

# Show a projection once a team has this many matches logged.
# Set to 1 = project from the very first match. The Sample column tells you
# how much is actually behind each number.
MIN_MATCHES = 1
# "Recent form" window, in matches.
FORM_WINDOW = 6
# How far the projection must sit from the bookmaker's line before it counts
# as a call worth recording. Below this, the two effectively agree.
EDGE_THRESHOLD = 1.5

# --- What counts as worth showing on This Week -------------------------
# Only fixtures meeting one of these two tests are listed.
# --- Per-team thresholds ----------------------------------------------
# These are about ONE team's own corner count in a match, not the match total.
TEAM_HIGH = 7.0          # flag a team projected to take this many or more
TEAM_LOW = 4.0           # flag a team projected to take fewer than this
BOTH_MIN = 4.0           # flag matches where BOTH sides clear this

HIGH_TOTAL = 13.0        # projected total corners at or above this
DOMINATION_GAP = 5.0     # one side projected to out-corner the other by this

ALIASES = {
    "Cambridge": "Cambridge Utd", "Cambridge United": "Cambridge Utd",
    "Sheffield Weds": "Sheff Wed", "Sheffield United": "Sheff Utd",
    "Peterboro": "Peterborough", "Bristol Rvs": "Bristol Rovers",
    "Nott'm Forest": "Nottm Forest", "Burton": "Burton Albion",
    "Wimbledon": "AFC Wimbledon", "Crawley": "Crawley Town",
    "Fleetwood": "Fleetwood Town", "Salford": "Salford City",
    "York": "York City", "Leyton": "Leyton Orient",
    "Newport": "Newport County", "Oldham": "Oldham Athletic",
    "Luton": "Luton Town", "Leicester": "Leicester City",
    "Oxford": "Oxford United", "Stockport County": "Stockport",
    "Wigan Athletic": "Wigan", "Wycombe Wanderers": "Wycombe",
    "Plymouth Argyle": "Plymouth", "Huddersfield Town": "Huddersfield",
    "Mansfield Town": "Mansfield", "Grimsby Town": "Grimsby",
    "Crewe Alexandra": "Crewe", "Northampton Town": "Northampton",
    "Exeter City": "Exeter", "Accrington Stanley": "Accrington",
    "Tranmere Rovers": "Tranmere", "Rotherham United": "Rotherham",
    "Shrewsbury Town": "Shrewsbury", "Swindon Town": "Swindon",
    "Cheltenham Town": "Cheltenham", "Colchester United": "Colchester",
    "Doncaster Rovers": "Doncaster",
    "MK Dons": "Milton Keynes Dons", "Milton Keynes": "Milton Keynes Dons",
    "Sheffield Wed": "Sheff Wed", "Sheffield Weds": "Sheff Wed",
    "Sheff Weds": "Sheff Wed", "Nottingham Forest": "Nottm Forest",
    "West Bromwich Albion": "West Brom", "Queens Park Rangers": "QPR",
    "Blackburn Rovers": "Blackburn", "Bolton Wanderers": "Bolton",
    "Forest Green": "Forest Green Rovers", "Wycombe Wands": "Wycombe",
    # long-form names as written by the openfootball fixture files
    "Wolverhampton Wanderers": "Wolves", "Preston North End": "Preston",
    "Sheffield United": "Sheff Utd", "Sheffield Wednesday": "Sheff Wed",
    "West Bromwich Albion": "West Brom", "Queens Park Rangers": "QPR",
    "Brighton and Hove Albion": "Brighton", "Leeds United": "Leeds",
    "Newcastle United": "Newcastle", "Manchester United": "Man United",
    "Manchester City": "Man City", "Tottenham Hotspur": "Tottenham",
    "Nottingham Forest": "Nottm Forest", "Leicester City": "Leicester City",
    "Huddersfield Town": "Huddersfield", "Ipswich Town": "Ipswich",
    "Hull City": "Hull", "Stoke City": "Stoke", "Swansea City": "Swansea",
    "Cardiff City": "Cardiff", "Norwich City": "Norwich",
    "Coventry City": "Coventry", "Derby County": "Derby",
    "Charlton Athletic": "Charlton", "Birmingham City": "Birmingham",
    "Lincoln City": "Lincoln", "West Ham United": "West Ham",
    "Blackburn Rovers": "Blackburn", "Bolton Wanderers": "Bolton",
    "Oxford United": "Oxford United", "Luton Town": "Luton Town",
}

FIRST = 7  # first data row on Match Log


# ------------------------------------------------------------------ util

def _dt_now():
    return datetime.now().strftime("%a %d %b, %H:%M")


def die(msg):
    print("\n  " + msg + "\n")
    try:
        input("Press Enter to close...")
    except EOFError:
        pass
    sys.exit(1)


def season_code(today=None):
    t = today or date.today()
    start = t.year if t.month >= 7 else t.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def canon(n):
    return ALIASES.get((n or "").strip(), (n or "").strip())


_SUFFIX = {"fc", "afc"}
_FILLER = {"fc", "afc", "utd", "united", "town", "city", "rovers", "athletic",
           "albion", "county", "wanderers", "the", "and", "association"}


def _words(name):
    return "".join(ch.lower() if ch.isalnum() else " " for ch in str(name or "")).split()


def norm_strict(name):
    """Lowercase alphanumerics, minus a trailing FC/AFC only."""
    return "".join(w for w in _words(name) if w not in _SUFFIX)


def norm_loose(name):
    """Also drops Town/City/United/Rovers-style padding. Can be ambiguous."""
    w = _words(name)
    core = [x for x in w if x not in _FILLER]
    return "".join(core or w)


def resolve_team(name, known):
    """
    Match a fixture's team name to a team already in the data:
    exact -> alias -> strict normalised -> loose normalised.
    Loose is only accepted when it maps to exactly one team, so
    Bristol City can never be mistaken for Bristol Rovers.
    """
    name = (name or "").strip()
    if name in known:
        return name
    if ALIASES.get(name) in known:
        return ALIASES[name]

    # alias table, matched loosely on its own keys (handles "... FC" suffixes)
    for src, dst in ALIASES.items():
        if dst in known and norm_strict(src) == norm_strict(name):
            return dst

    target = norm_strict(name)
    hits = [k for k in known if norm_strict(k) == target]
    if len(hits) == 1:
        return hits[0]

    target = norm_loose(name)
    hits = [k for k in known if norm_loose(k) == target]
    return hits[0] if len(hits) == 1 else None


def parse_date(raw):
    raw = str(raw or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            pass
    return None


def football_week(d):
    """Bucket runs Thursday to Wednesday, so a Thu-Mon round stays together."""
    return (d - timedelta(days=3)).isocalendar()[:2]


def is_weekend_round(d):
    """Thursday to Monday counts as the gameweek. Tuesday/Wednesday is midweek."""
    return d.weekday() in (3, 4, 5, 6, 0)


def label_gameweeks(matches):
    """
    Number the Thu-Mon rounds 1, 2, 3...  Midweek (Tue/Wed) matches are
    labelled MW1, MW2... against the round they follow, so cup replays and
    rearranged fixtures never inflate the gameweek count.
    Returns {bucket: number} for the weekend rounds.
    """
    weekend_buckets = sorted({football_week(m["date"]) for m in matches
                              if m["date"] and is_weekend_round(m["date"])})
    number = {b: i + 1 for i, b in enumerate(weekend_buckets)}

    labels = {}
    for m in matches:
        d = m["date"]
        if not d:
            continue
        b = football_week(d)
        if is_weekend_round(d):
            labels[id(m)] = number.get(b)
        else:
            prior = [n for bb, n in number.items() if bb <= b]
            labels[id(m)] = f"MW{max(prior)}" if prior else "MW0"
    return labels, number


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig", errors="replace")


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ download

def get_results(season):
    out, failed = [], []
    for div, league in DIVISIONS.items():
        try:
            rows = list(csv.DictReader(io.StringIO(
                fetch(RESULTS_URL.format(season=season, div=div)))))
        except Exception as e:
            print(f"  ! {league}: download failed ({e})")
            failed.append(league)
            continue
        if not rows or "HC" not in rows[0]:
            print(f"  ! {league}: no corner data in file yet")
            failed.append(league)
            continue
        kept = 0
        for r in rows:
            d = parse_date(r.get("Date"))
            hc, ac = num(r.get("HC")), num(r.get("AC"))
            h, a = canon(r.get("HomeTeam")), canon(r.get("AwayTeam"))
            if not (d and h and a) or hc is None or ac is None:
                continue
            out.append({"date": d, "league": league, "home": h, "away": a,
                        "hg": num(r.get("FTHG")), "ag": num(r.get("FTAG")),
                        "hc": hc, "ac": ac})
            kept += 1
        print(f"  {league}: {kept} matches with corner data")
    return out, failed


MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
     "Nov", "Dec"], 1)}
_OF_DATE = re.compile(r"^[A-Z][a-z]{2}\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$")
_OF_GAME = re.compile(r"^(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s*$")


def parse_openfootball(text, league, start_year):
    """Schedules are written as date headers followed by indented fixtures."""
    out, cur, cur_time, year = [], None, None, start_year
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.startswith(("=", "#", "\u25aa")):
            continue
        m = _OF_DATE.match(line)
        if m:
            mon, day, yr = m.group(1), int(m.group(2)), m.group(3)
            if yr:
                year = int(yr)
            elif cur and MONTHS.get(mon, 0) < cur.month:
                year += 1                      # season rolled into January
            try:
                cur = date(year, MONTHS[mon], day)
            except (KeyError, ValueError):
                cur = None
            cur_time = None
            continue
        if cur is None:
            continue
        g = _OF_GAME.match(line)
        if not g:
            continue
        t, home, away = g.group(1), g.group(2).strip(), g.group(3).strip()
        if t:
            cur_time = t
        if "  " in home:                       # column padding
            home = home.split("  ")[0].strip()
        out.append({"date": cur, "time": cur_time or "", "league": league,
                    "home": home, "away": away})
    return out


def get_openfootball(season):
    """season '2627' -> folder '2026-27'."""
    folder = f"20{season[:2]}-{season[2:]}"
    found = []
    for fname, league in OF_FILES.items():
        try:
            text = fetch(OF_BASE.format(season=folder) + fname)
        except Exception:
            continue                            # division not published yet
        got = parse_openfootball(text, league, 2000 + int(season[:2]))
        if got:
            found.extend(got)
            print(f"  Schedule: {league} - {len(got)} fixtures")
    return found


def parse_fixturedownload(text, league):
    """CSV with Date like '22/08/2026 15:00' plus Home Team / Away Team."""
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    cols = {c.lower().strip(): c for c in rows[0]}
    hcol = cols.get("home team") or cols.get("hometeam")
    acol = cols.get("away team") or cols.get("awayteam")
    dcol = cols.get("date") or cols.get("date utc")
    if not (hcol and acol and dcol):
        return []
    out = []
    for r in rows:
        raw = str(r.get(dcol) or "").strip()
        d, t = None, ""
        for fmt in ("%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                d, t = dt.date(), (dt.strftime("%H:%M") if "%H" in fmt else "")
                break
            except ValueError:
                continue
        home, away = (r.get(hcol) or "").strip(), (r.get(acol) or "").strip()
        if d and home and away:
            out.append({"date": d, "time": t, "league": league,
                        "home": home, "away": away})
    return out


def get_fixturedownload(season, wanted):
    """season '2627' -> the 2026 season lists. Only fetches leagues we lack."""
    yr = 2000 + int(season[:2])
    found = []
    for league in wanted:
        got = []
        for slug in FD_LEAGUES.get(league, []):
            for pat in FD_PATTERNS:
                url = pat.format(slug=slug, yr=yr)
                try:
                    got = parse_fixturedownload(fetch(url), league)
                except Exception:
                    continue
                if got:
                    print(f"  Schedule: {league} - {len(got)} fixtures "
                          f"(fixturedownload)")
                    break
            if got:
                break
        found.extend(got)
    return found


def get_manual(here):
    """
    Read manual_results.csv if present. Expected columns (header row required):
    Date,League,Home,Away,HomeGoals,AwayGoals,HomeCorners,AwayCorners
    Date as YYYY-MM-DD or DD/MM/YYYY. League: Championship / League 1 / League 2.
    Rows without corner counts are skipped.
    """
    path = os.path.join(here, MANUAL_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except Exception as e:
        print(f"  ! {MANUAL_FILE}: could not read ({e})")
        return []
    cols = {c.lower().strip(): c for c in (rows[0] if rows else {})}
    def g(r, *names):
        for n in names:
            if n in cols:
                return r.get(cols[n])
        return None
    out = []
    for r in rows:
        d = parse_date(g(r, "date"))
        league = str(g(r, "league") or "").strip()
        home = canon(g(r, "home", "hometeam", "home team"))
        away = canon(g(r, "away", "awayteam", "away team"))
        hc = num(g(r, "homecorners", "home corners", "hc"))
        ac = num(g(r, "awaycorners", "away corners", "ac"))
        if not (d and home and away) or hc is None or ac is None:
            continue
        if league not in ("Championship", "League 1", "League 2"):
            continue
        out.append({"date": d, "league": league, "home": home, "away": away,
                    "hg": num(g(r, "homegoals", "home goals", "fthg")),
                    "ag": num(g(r, "awaygoals", "away goals", "ftag")),
                    "hc": hc, "ac": ac})
    if out:
        print(f"  Manual results: {len(out)} from {MANUAL_FILE}")
    return out


def get_fixtures():
    try:
        rows = list(csv.DictReader(io.StringIO(fetch(FIXTURES_URL))))
    except Exception as e:
        print(f"  ! upcoming fixtures: download failed ({e})")
        return None
    out = []
    for r in rows:
        div = (r.get("Div") or "").strip()
        if div not in DIVISIONS:
            continue
        d = parse_date(r.get("Date"))
        h, a = canon(r.get("HomeTeam")), canon(r.get("AwayTeam"))
        if not (d and h and a):
            continue
        out.append({"date": d, "time": (r.get("Time") or "").strip(),
                    "league": DIVISIONS[div], "home": h, "away": a})
    print(f"  Upcoming fixtures: {len(out)} found")
    return out


# ------------------------------------------------------------------ sheets

def harvest_lines(wb):
    """
    Collect bookmaker lines the user has typed. Two places to look:
      - the Lines sheet, which holds everything from past weeks
      - the This Week sheet, where this week's typing lands
    Columns are located by their headers, not fixed positions, so the layout
    can move again without breaking this.
    """
    lines = {}

    def col_of(ws, header_row, *names):
        wanted = {n.lower() for n in names}
        for c in range(1, ws.max_column + 1):
            if str(ws.cell(header_row, c).value or "").strip().lower() in wanted:
                return c
        return None

    # ---- past weeks -------------------------------------------------
    if "Lines" in wb.sheetnames:
        ws = wb["Lines"]
        hdr = 4
        c_date = col_of(ws, hdr, "date")
        c_fix = col_of(ws, hdr, "fixture")
        c_line = col_of(ws, hdr, "line")
        c_proj = col_of(ws, hdr, "projected", "projection")
        c_call = col_of(ws, hdr, "call")
        if c_fix and c_line:
            for r in range(hdr + 1, ws.max_row + 1):
                fixture = ws.cell(r, c_fix).value
                line = num_f(ws.cell(r, c_line).value)
                if not fixture or line is None or " v " not in str(fixture):
                    continue
                home, away = [p.strip() for p in str(fixture).split(" v ", 1)]
                lines[(home, away)] = {
                    "date": parse_date(ws.cell(r, c_date).value) if c_date else None,
                    "line": line,
                    "proj": num_f(ws.cell(r, c_proj).value) if c_proj else None,
                    "call": ws.cell(r, c_call).value if c_call else None,
                }

    # ---- this week's typing -----------------------------------------
    fixture_sheets = [n for n in wb.sheetnames
                      if n == "This Week" or n.startswith(("Weekend ", "Midweek "))]
    for sheet in fixture_sheets:
        ws = wb[sheet]
        hdr = next((r for r in (4, 5, 6) if any(
            str(ws.cell(r, c).value or "").strip().lower() == "fixture"
            for c in range(1, ws.max_column + 1))), 5)
        c_fix = col_of(ws, hdr, "fixture")
        c_line = col_of(ws, hdr, "line")
        c_proj = col_of(ws, hdr, "expected corners")
        c_call = col_of(ws, hdr, "call")
        if c_fix and c_line:
            for r in range(hdr + 1, ws.max_row + 1):
                fixture = ws.cell(r, c_fix).value
                line = num_f(ws.cell(r, c_line).value)
                if not fixture or line is None or " v " not in str(fixture):
                    continue
                home, away = [p.strip() for p in str(fixture).split(" v ", 1)]
                prev = lines.get((home, away), {})
                lines[(home, away)] = {
                    "date": prev.get("date"),
                    "line": line,
                    # freeze the projection from when the call was first made
                    "proj": prev.get("proj") or (num_f(ws.cell(r, c_proj).value)
                                                 if c_proj else None),
                    "call": prev.get("call") or (ws.cell(r, c_call).value
                                                 if c_call else None),
                }
    return lines


def settle_lines(lines, played):
    """
    Match each recorded line against the finished match, and score whether
    the projection's call (over or under the line) was right.
    """
    index = {(m["home"], m["away"]): m for m in played}
    out = []
    for key, rec in lines.items():
        home, away = key
        m = index.get(key)
        league = m["league"] if m else ""
        actual = None
        if m and m["hc"] is not None and m["ac"] is not None:
            actual = m["hc"] + m["ac"]
        out.append({
            "date": rec.get("date") or (m["date"] if m else None),
            "league": league, "home": home, "away": away,
            "line": rec["line"], "actual": actual,
            "proj": rec.get("proj"), "call": rec.get("call"),
        })
    return out


def score_call(proj_total, line, actual):
    """Returns (call, diff, result, correct) - any may be None."""
    if proj_total is None or line is None:
        return None, None, None, None
    diff = proj_total - line
    call = None
    if abs(diff) >= EDGE_THRESHOLD:
        call = "Over" if diff > 0 else "Under"
    if actual is None:
        return call, diff, None, None
    if actual == line:
        return call, diff, "Push", None
    result = "Over" if actual > line else "Under"
    correct = None if call is None else (call == result)
    return call, diff, result, correct


def pois_p_at_least(mu, k):
    """P(count >= k) for a Poisson with mean mu. Corner counts are roughly
    Poisson - a workable approximation, not gospel."""
    if mu is None or mu <= 0:
        return None
    p, term = 0.0, math.exp(-mu)
    for i in range(0, k):
        p += term
        term *= mu / (i + 1)
    return max(0.0, min(1.0, 1.0 - p))


def fair(p):
    """Fair decimal odds for a probability."""
    return (1.0 / p) if p and p > 0.01 else None


def num_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


FIXTURE_WINDOW_DAYS = 12


def upcoming(fixtures, week_numbers, today=None):
    """
    Everything playable in the next week, midweek included, across all
    three divisions.
    """
    today = today or date.today()
    horizon = today + timedelta(days=FIXTURE_WINDOW_DAYS)
    chosen = [f for f in fixtures if today <= f["date"] <= horizon]

    for f in chosen:
        numbers = week_numbers.get(f["league"], {})
        f["gw"] = numbers.get(football_week(f["date"]), len(numbers) + 1)

    if not chosen:
        return [], "no fixtures in the next week", len(fixtures)

    span = sorted({f["date"] for f in chosen})
    when = (span[0].strftime("%a %d %b") if len(span) == 1
            else f'{span[0].strftime("%a %d %b")} - {span[-1].strftime("%a %d %b")}')
    return chosen, when, len(fixtures) - len(chosen)


def segment_fixtures(fixtures):
    """
    Split upcoming fixtures into consecutive weekend (Thu-Mon) and midweek
    (Tue-Wed) blocks, oldest first. Returns [(label, fixtures), ...].
    """
    groups = {}
    for f in fixtures:
        key = (football_week(f["date"]), is_weekend_round(f["date"]))
        groups.setdefault(key, []).append(f)
    segs = sorted(groups.values(), key=lambda g: min(f["date"] for f in g))
    out = []
    for g in segs:
        d0 = min(f["date"] for f in g)
        d1 = max(f["date"] for f in g)
        kind = "Weekend" if is_weekend_round(d0) else "Midweek"
        if d0 == d1:
            span = d0.strftime("%d %b")
        elif d0.month == d1.month:
            span = f'{d0.strftime("%d")}-{d1.strftime("%d %b")}'
        else:
            span = f'{d0.strftime("%d %b")}-{d1.strftime("%d %b")}'
        out.append((f"{kind} {span}", g))
    return out


def read_log(ml):
    rows = []
    for r in range(FIRST, ml.max_row + 1):
        h, a = ml.cell(r, 4).value, ml.cell(r, 5).value
        if not h or not a:
            continue
        rows.append({
            "row": r, "date": parse_date(ml.cell(r, 1).value),
            "league": ml.cell(r, 3).value, "home": str(h).strip(),
            "away": str(a).strip(), "hc": num(ml.cell(r, 8).value),
            "ac": num(ml.cell(r, 9).value),
            "hg": num(ml.cell(r, 6).value), "ag": num(ml.cell(r, 7).value),
        })
    return rows


def build_stats(rows):
    """Per-team totals, split by venue, plus a recent-form window."""
    stats = {}

    def slot(team, league):
        return stats.setdefault(team, {
            "league": league, "hp": 0, "ap": 0,
            "hcf": 0, "hca": 0, "acf": 0, "aca": 0, "hist": [],
            # goals: home scored/conceded, away scored/conceded, matches with goals
            "hgp": 0, "agp": 0, "hgf": 0, "hga": 0, "agf": 0, "aga": 0,
        })

    for m in sorted(rows, key=lambda x: (x["date"] or date.min)):
        if m.get("hg") is not None and m.get("ag") is not None:
            hs = slot(m["home"], m["league"])
            as_ = slot(m["away"], m["league"])
            hs["hgp"] += 1; hs["hgf"] += m["hg"]; hs["hga"] += m["ag"]
            as_["agp"] += 1; as_["agf"] += m["ag"]; as_["aga"] += m["hg"]
        if m["hc"] is None or m["ac"] is None:
            continue
        h = slot(m["home"], m["league"])
        a = slot(m["away"], m["league"])
        h["hp"] += 1; h["hcf"] += m["hc"]; h["hca"] += m["ac"]
        a["ap"] += 1; a["acf"] += m["ac"]; a["aca"] += m["hc"]
        h["hist"].append((m["hc"], m["ac"]))
        a["hist"].append((m["ac"], m["hc"]))

    for t, s in stats.items():
        s["p"] = s["hp"] + s["ap"]
        s["cf"] = s["hcf"] + s["acf"]
        s["ca"] = s["hca"] + s["aca"]
        recent = s["hist"][-FORM_WINDOW:]
        s["form_f"] = sum(x[0] for x in recent) / len(recent) if recent else None
        s["form_a"] = sum(x[1] for x in recent) / len(recent) if recent else None
        s["home_cf"] = s["hcf"] / s["hp"] if s["hp"] else None
        s["home_ca"] = s["hca"] / s["hp"] if s["hp"] else None
        s["away_cf"] = s["acf"] / s["ap"] if s["ap"] else None
        s["away_ca"] = s["aca"] / s["ap"] if s["ap"] else None
        s["avg_f"] = s["cf"] / s["p"] if s["p"] else None
        s["avg_a"] = s["ca"] / s["p"] if s["p"] else None
        # goal rates
        s["gp"] = s["hgp"] + s["agp"]
        s["home_gf"] = s["hgf"] / s["hgp"] if s["hgp"] else None
        s["home_ga"] = s["hga"] / s["hgp"] if s["hgp"] else None
        s["away_gf"] = s["agf"] / s["agp"] if s["agp"] else None
        s["away_ga"] = s["aga"] / s["agp"] if s["agp"] else None
        s["gf"] = (s["hgf"] + s["agf"]) / s["gp"] if s["gp"] else None
        s["ga"] = (s["hga"] + s["aga"]) / s["gp"] if s["gp"] else None
    return stats


def project(stats, home, away):
    """Venue-aware projection. Returns (home, away, total, note) or Nones."""
    h, a = stats.get(home), stats.get(away)
    if not h or not a:
        return None, None, None, "team not yet in data"
    if h["p"] < MIN_MATCHES or a["p"] < MIN_MATCHES:
        return None, None, None, f"under {MIN_MATCHES} matches"

    # Prefer venue-specific rates; fall back to overall if a side hasn't
    # played enough at that venue yet.
    h_for = h["home_cf"] if h["hp"] >= 1 else h["avg_f"]
    a_against = a["away_ca"] if a["ap"] >= 1 else a["avg_a"]
    a_for = a["away_cf"] if a["ap"] >= 1 else a["avg_f"]
    h_against = h["home_ca"] if h["hp"] >= 1 else h["avg_a"]
    if None in (h_for, a_against, a_for, h_against):
        return None, None, None, "insufficient venue data"

    # Floor the estimates: no side is truly incapable of winning a corner,
    # and a zero mean would produce impossible-looking certainties.
    ph = max((h_for + a_against) / 2, 0.5)
    pa = max((a_for + h_against) / 2, 0.5)
    return ph, pa, ph + pa, ""


# ------------------------------------------------------------------ writing

def style(wb):
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    thin = Side(style="thin", color="BFBFBF")
    return {
        "hdr_fill": PatternFill("solid", fgColor="1F3864"),
        "hdr_font": Font(name="Arial", bold=True, color="FFFFFF", size=10),
        "title": Font(name="Arial", bold=True, size=14, color="1F3864"),
        "note": Font(name="Arial", italic=True, size=9, color="595959"),
        "blue": Font(name="Arial", size=10, color="0000FF"),
        "plain": Font(name="Arial", size=10),
        "bold": Font(name="Arial", bold=True, size=10),
        "hi": PatternFill("solid", fgColor="FFF2CC"),
        "miss": PatternFill("solid", fgColor="F6DEDE"),
        "warn": Font(name="Arial", size=10, bold=True, color="B26B00"),
        "centre": Alignment(horizontal="center"),
        "left": Alignment(horizontal="left"),
        "wrap": Alignment(horizontal="center", wrap_text=True),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
    }


def header(ws, row, labels, S):
    for i, h in enumerate(labels, start=1):
        c = ws.cell(row, i, h)
        c.fill, c.font, c.alignment, c.border = (
            S["hdr_fill"], S["hdr_font"], S["wrap"], S["border"])


def widths(ws, ws_widths):
    from openpyxl.utils import get_column_letter
    for i, w in enumerate(ws_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def fresh(wb, name, index):
    if name in wb.sheetnames:
        del wb[name]
    return wb.create_sheet(name, index)


def verdict(ph, pa, tot, hs, as_, home, away):
    """One plain-English line about the fixture."""
    gap = abs(ph - pa)
    bits = []
    if tot >= 12:
        bits.append("high-corner match")
    elif tot <= 8:
        bits.append("low-corner match")
    else:
        bits.append("middling")
    if gap >= 3:
        bits.append(f"{home if ph > pa else away} should dominate")
    elif gap < 1:
        bits.append("evenly matched")
    return "; ".join(bits)


def team_flags(ph, pa, home, away):
    """Per-team observations about a fixture."""
    if ph is None:
        return [], []
    tags, notes = [], []
    if ph >= TEAM_HIGH:
        tags.append("HIGH")
        notes.append(f"{home} {ph:.1f}")
    if pa >= TEAM_HIGH:
        tags.append("HIGH")
        notes.append(f"{away} {pa:.1f}")
    if ph < TEAM_LOW:
        tags.append("LOW")
        notes.append(f"{home} {ph:.1f}")
    if pa < TEAM_LOW:
        tags.append("LOW")
        notes.append(f"{away} {pa:.1f}")
    if ph >= BOTH_MIN and pa >= BOTH_MIN:
        tags.append("BOTH 4+")
    return sorted(set(tags)), notes


def write_this_week(wb, fixtures, stats, S, round_desc, lines,
                    sheet_name="This Week", position=0):
    """Per-team corner projections, with the notable ones called out."""
    ws = fresh(wb, sheet_name, position)
    ws["A1"] = f"{sheet_name}" if sheet_name != "This Week" else (
        f"This Week - {round_desc}" if round_desc else "This Week")
    ws["A1"].font = S["title"]
    ws["A2"] = ("Each side's own projected corner count. 'Home' is how many the home "
                "team is expected to take; 'Away' likewise. Total is just the two added.")
    ws["A2"].font = S["note"]
    ws["A3"] = (f"HIGH = that side projected at {TEAM_HIGH:.0f}+ corners.  "
                f"LOW = under {TEAM_LOW:.0f}.  "
                f"BOTH 4+ = both sides above {BOTH_MIN:.0f}.")
    ws["A3"].font = S["note"]

    known = set(stats)
    rows, unmatched = [], []
    for f in fixtures:
        home = resolve_team(f["home"], known)
        away = resolve_team(f["away"], known)
        for original, found in ((f["home"], home), (f["away"], away)):
            if not found:
                unmatched.append(original)
        if home and away:
            ph, pa, tot, why = project(stats, home, away)
        else:
            ph = pa = tot = None
            missing = [o for o, r_ in ((f["home"], home), (f["away"], away)) if not r_]
            why = f"no data yet for {' and '.join(missing)}"
        f["home"] = home or f["home"]
        f["away"] = away or f["away"]
        prior = lines.get((f["home"], f["away"]))
        line = prior["line"] if prior else None
        call, diff, _, _ = score_call(tot, line, None)
        tags, notes = team_flags(ph, pa, f["home"], f["away"])
        # market alignment: P(both sides reach 4+), assuming independence
        p_both = None
        if ph is not None:
            p_h, p_a = pois_p_at_least(ph, 4), pois_p_at_least(pa, 4)
            p_both = p_h * p_a if (p_h and p_a) else None
        # if a line is typed, P(total goes over it)
        p_over = None
        if tot is not None and line is not None:
            p_over = pois_p_at_least(tot, int(math.floor(line)) + 1)
        rows.append((f, ph, pa, tot, line, call, tags, notes, why, p_both, p_over))

    rows.sort(key=lambda x: (x[3] is None, -(x[3] or 0)))

    header(ws, 5, ["Fixture", "KO", "League", "Home Corners", "Away Corners",
                   "Total", "Flags", "Detail", "Line", "Call"], S)
    r = 6
    for f, ph, pa, tot, line, call, tags, notes, why, p_both, p_over in rows:
        if p_over is not None and call:
            notes = notes + [f"P({call} {line:g}) ~ {p_over*100 if call=='Over' else (1-p_over)*100:.0f}%"]
        vals = [f'{f["home"]} v {f["away"]}',
                f'{f["date"].strftime("%a")} {f["time"]}'.strip(),
                f["league"], ph, pa, tot, ", ".join(tags),
                "; ".join(notes) if notes else (why if tot is None else ""),
                line, call]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["plain"]
            c.border = S["border"]
            if i in (2, 3, 4, 5, 6, 7, 9, 10):
                c.alignment = S["centre"]
            if i in (4, 5, 6):
                c.number_format = "0.0"
            if i == 4 and ph is not None and ph >= TEAM_HIGH:
                c.font = S["bold"]; c.fill = S["hi"]
            if i == 4 and ph is not None and ph < TEAM_LOW:
                c.font = S["warn"]
            if i == 5 and pa is not None and pa >= TEAM_HIGH:
                c.font = S["bold"]; c.fill = S["hi"]
            if i == 5 and pa is not None and pa < TEAM_LOW:
                c.font = S["warn"]
            if i == 7 and v:
                c.font = S["bold"]
            if i == 8:
                c.font = S["note"]
            if i == 9:
                c.font = S["blue"]; c.number_format = "0.0"
            if i == 10 and v:
                c.font = S["bold"]
        r += 1

    done = [x for x in rows if x[1] is not None]

    def section(title, entries, r):
        ws.cell(r, 1, title).font = S["bold"]
        r += 1
        if not entries:
            c = ws.cell(r, 1, "none this week")
            c.font = S["note"]
            return r + 2
        for text in entries:
            c = ws.cell(r, 1, text)
            c.font = S["plain"]
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
            r += 1
        return r + 1

    r += 1
    high = []
    for f, ph, pa, tot, *_ in done:
        if ph >= TEAM_HIGH:
            high.append(f'{f["home"]} {ph:.1f}  (home v {f["away"]})')
        if pa >= TEAM_HIGH:
            high.append(f'{f["away"]} {pa:.1f}  (away at {f["home"]})')
    high.sort(key=lambda t: -float(t.split("  ")[0].split()[-1]))
    r = section(f"Teams projected {TEAM_HIGH:.0f}+ corners", high, r)

    low = []
    for f, ph, pa, tot, *_ in done:
        if ph < TEAM_LOW:
            low.append(f'{f["home"]} {ph:.1f}  (home v {f["away"]})')
        if pa < TEAM_LOW:
            low.append(f'{f["away"]} {pa:.1f}  (away at {f["home"]})')
    low.sort(key=lambda t: float(t.split("  ")[0].split()[-1]))
    r = section(f"Teams projected under {TEAM_LOW:.0f} corners", low, r)

    both_rows = []
    for row in done:
        f, ph, pa = row[0], row[1], row[2]
        p_both = row[9]
        if ph >= BOTH_MIN and pa >= BOTH_MIN and p_both:
            weak = min(ph, pa)
            both_rows.append((p_both, weak,
                f'{f["home"]} {ph:.1f} v {f["away"]} {pa:.1f}  -  '
                f'P(both 4+) ~ {p_both*100:.0f}%, fair {fair(p_both):.2f}'))
    both_rows.sort(key=lambda x: -x[0])
    r = section(f"Both teams {BOTH_MIN:.0f}+ corners (ranked by likelihood)",
                [t for _, _, t in both_rows], r)

    ws.cell(r, 1, "Worth remembering").font = S["bold"]
    r += 1
    for line_txt in [
        "A team's projected count is its own corner rate at this venue averaged with what "
        "the opponent tends to concede. It is an average, not a forecast of one match.",
        "Probabilities use a Poisson approximation and assume the two sides are independent "
        "- they aren't quite, so treat fair odds as rough, especially on thin data.",
        "Corners follow the scoreline. A side chasing a goal wins corners whatever its style.",
    ]:
        c = ws.cell(r, 1, line_txt)
        c.font = S["note"]
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=10)
        r += 1

    widths(ws, [34, 10, 13, 14, 14, 8, 16, 34, 8, 8])
    ws.freeze_panes = "A6"
    return rows, sorted(set(unmatched)), rows


def pois_p_exact(mu, k):
    """P(count == k) for a Poisson with mean mu."""
    if mu is None or mu < 0:
        return None
    return math.exp(-mu) * (mu ** k) / math.factorial(k)


def project_goals(stats, home, away):
    """
    Expected goals for each side: its scoring rate at this venue averaged
    with what the opponent concedes at theirs. Same shape as the corner model.
    """
    h, a = stats.get(home), stats.get(away)
    if not h or not a or not h.get("gp") or not a.get("gp"):
        return None, None
    h_for = h["home_gf"] if h["hgp"] else h["gf"]
    a_against = a["away_ga"] if a["agp"] else a["ga"]
    a_for = a["away_gf"] if a["agp"] else a["gf"]
    h_against = h["home_ga"] if h["hgp"] else h["ga"]
    if None in (h_for, a_against, a_for, h_against):
        return None, None
    # Same floor as corners - a team with no away goals yet is not a team
    # that cannot score, and a zero mean gives a 100% clean sheet claim.
    return max((h_for + a_against) / 2, 0.20), max((a_for + h_against) / 2, 0.20)


def goal_markets(lh, la):
    """
    Turn two expected-goal figures into the markets that get bet on.
    Assumes each side's goals are Poisson and independent - the standard
    simplification, and slightly optimistic about draws.
    """
    if lh is None or la is None:
        return {}
    p_home_blank = pois_p_exact(la, 0)          # away fail to score
    p_away_blank = pois_p_exact(lh, 0)          # home fail to score
    btts = (1 - p_away_blank) * (1 - p_home_blank)
    tot = lh + la
    p0, p1, p2, p3 = (pois_p_exact(tot, k) for k in range(4))
    return {
        "lh": lh, "la": la, "total": tot,
        "cs_home": p_home_blank,                 # home keeps a clean sheet
        "cs_away": p_away_blank,                 # away keeps a clean sheet
        "cs_any": p_home_blank + p_away_blank - p_home_blank * p_away_blank,
        "btts": btts,
        "over25": 1 - (p0 + p1 + p2),
        "over35": 1 - (p0 + p1 + p2 + p3),
    }


def goals_sheet_name(label):
    """Excel caps sheet names at 31 characters."""
    return f"Goals {label}"[:31]


def write_goals(wb, all_fixtures, stats, S, position=3, label=""):
    """Goal-market view for one block of fixtures."""
    name = goals_sheet_name(label) if label else "Goals"
    ws = fresh(wb, name, position)
    ws["A1"] = f"Goals - {label}" if label else "Goals"
    ws["A1"].font = S["title"]
    ws["A2"] = ("Clean sheets, over/under and both teams to score, for the fixtures "
                "on the matching corners tab.")
    ws["A2"].font = S["note"]
    ws["A3"] = ("Probabilities assume each side's goals follow a Poisson distribution and "
                "are independent. Standard practice, but it slightly understates draws.")
    ws["A3"].font = S["note"]

    header(ws, 5, ["Fixture", "When", "League", "Exp Home", "Exp Away",
                   "Exp Total", "BTTS", "Over 2.5", "Over 3.5", "Home CS",
                   "Away CS", "Fair BTTS", "Fair O2.5"], S)
    known = set(stats)
    rows = []
    for f in all_fixtures:
        home = resolve_team(f["home"], known)
        away = resolve_team(f["away"], known)
        if not (home and away):
            continue
        lh, la = project_goals(stats, home, away)
        m = goal_markets(lh, la)
        if not m:
            continue
        rows.append((f, home, away, m))
    rows.sort(key=lambda x: -x[3]["total"])

    r = 6
    for f, home, away, m in rows:
        vals = [f'{home} v {away}',
                f'{f["date"].strftime("%a %d %b")} {f["time"]}'.strip(),
                f["league"],
                m["lh"], m["la"], m["total"], m["btts"], m["over25"], m["over35"],
                m["cs_home"], m["cs_away"],
                fair(m["btts"]), fair(m["over25"])]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["plain"]; c.border = S["border"]
            if i >= 2:
                c.alignment = S["centre"]
            if i in (4, 5, 6):
                c.number_format = "0.00"
            if i in (7, 8, 9, 10, 11):
                c.number_format = "0%"
            if i in (12, 13):
                c.number_format = "0.00"
            if i == 6:
                c.font = S["bold"]; c.fill = S["hi"]
        r += 1

    def section(title, entries, r):
        ws.cell(r, 1, title).font = S["bold"]
        r += 1
        if not entries:
            ws.cell(r, 1, "none").font = S["note"]
            return r + 2
        for t in entries:
            c = ws.cell(r, 1, t)
            c.font = S["plain"]
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
            r += 1
        return r + 1

    r += 1
    cs = []
    for f, home, away, m in rows:
        if m["cs_home"] >= 0.40:
            cs.append((m["cs_home"], f'{home} (home v {away}) - {m["cs_home"]*100:.0f}%, '
                                     f'fair {fair(m["cs_home"]):.2f}'))
        if m["cs_away"] >= 0.40:
            cs.append((m["cs_away"], f'{away} (away at {home}) - {m["cs_away"]*100:.0f}%, '
                                     f'fair {fair(m["cs_away"]):.2f}'))
    cs.sort(reverse=True)
    r = section("Most likely clean sheets (40%+)", [t for _, t in cs], r)

    big = [(m["over25"], f'{home} v {away} - {m["total"]:.2f} expected, '
                         f'{m["over25"]*100:.0f}% for 3+, fair {fair(m["over25"]):.2f}')
           for f, home, away, m in rows if m["over25"] >= 0.55]
    big.sort(reverse=True)
    r = section("Most likely 3+ goals", [t for _, t in big], r)

    bt = [(m["btts"], f'{home} v {away} - {m["btts"]*100:.0f}%, fair {fair(m["btts"]):.2f}')
          for f, home, away, m in rows if m["btts"] >= 0.55]
    bt.sort(reverse=True)
    r = section("Most likely both teams to score (55%+)", [t for _, t in bt], r)

    for txt in [
        "A clean sheet and 'both teams to score' are opposites - if one looks likely, "
        "check you are not backing both.",
        "Goal models are better behaved than corner models, but they still know nothing "
        "about injuries, red cards, or a side with nothing to play for.",
    ]:
        c = ws.cell(r, 1, txt)
        c.font = S["note"]
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=13)
        r += 1

    widths(ws, [34, 15, 13, 10, 10, 10, 8, 9, 9, 9, 9, 10, 10])
    ws.freeze_panes = "A6"
    return rows


def write_teams(wb, stats, S):
    ws = fresh(wb, "Teams", 1)
    ws["A1"] = "Team Averages"
    ws["A1"].font = S["title"]
    ws["A2"] = "Rebuilt every run. Sort any column to rank."
    ws["A2"].font = S["note"]
    header(ws, 4, ["Team", "League", "Played", "Avg Won", "Avg Conceded",
                   "Avg Total", "At Home Won", "At Home Conc",
                   "Away Won", "Away Conc", f"Last {FORM_WINDOW} Won",
                   f"Last {FORM_WINDOW} Conc", "Goals For", "Goals Against",
                   "Home GF", "Home GA", "Away GF", "Away GA"], S)
    r = 5
    for team in sorted(stats, key=lambda t: -(stats[t]["avg_f"] or 0)):
        s_ = stats[team]
        vals = [team, s_["league"], s_["p"], s_["avg_f"], s_["avg_a"],
                (s_["cf"] + s_["ca"]) / s_["p"] if s_["p"] else None,
                s_["home_cf"], s_["home_ca"], s_["away_cf"], s_["away_ca"],
                s_["form_f"], s_["form_a"],
                s_.get("gf"), s_.get("ga"), s_.get("home_gf"), s_.get("home_ga"),
                s_.get("away_gf"), s_.get("away_ga")]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["plain"]; c.border = S["border"]
            if i >= 3:
                c.alignment = S["centre"]
            if i >= 4:
                c.number_format = "0.0"
        r += 1
    widths(ws, [20, 13, 8, 10, 13, 10, 12, 13, 11, 11, 12, 13,
                10, 13, 9, 9, 9, 9])
    ws.freeze_panes = "A5"


def write_lines(wb, settled, S):
    """Only appears if you've actually entered lines."""
    rows = [x for x in settled if x["line"] is not None]
    if not rows:
        # nothing entered yet - drop the tab rather than show an empty one
        if "Lines" in wb.sheetnames:
            del wb["Lines"]
        return
    ws = fresh(wb, "Lines", 2)
    ws["A1"] = "Your Calls"
    ws["A1"].font = S["title"]
    scored = [x for x in rows if x.get("correct") is not None]
    hits = sum(1 for x in scored if x["correct"])
    if scored:
        pct = hits / len(scored) * 100
        note = ("far too few to mean anything" if len(scored) < 20
                else "still thin" if len(scored) < 50 else "a usable sample")
        ws["A2"] = f"Settled: {len(scored)}   Correct: {hits}   Hit rate: {pct:.0f}%   ({note})"
    else:
        ws["A2"] = "Nothing settled yet."
    ws["A2"].font = S["bold"]
    header(ws, 4, ["Date", "Fixture", "Line", "Projected", "Call",
                   "Actual", "Result", "Right?"], S)  # headers are read back
                                                      # by harvest_lines()
    r = 5
    for x in sorted(rows, key=lambda y: (y["date"] or date.min), reverse=True):
        vals = [x["date"].strftime("%d %b") if x["date"] else "",
                f'{x["home"]} v {x["away"]}', x["line"], x.get("proj"),
                x.get("call"), x["actual"], x.get("result"),
                "" if x.get("correct") is None else ("YES" if x["correct"] else "no")]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["plain"]; c.border = S["border"]
            if i != 2:
                c.alignment = S["centre"]
            if i in (3, 4):
                c.number_format = "0.0"
            if i == 8 and v == "YES":
                c.font = S["bold"]; c.fill = S["hi"]
        r += 1
    widths(ws, [10, 34, 8, 11, 8, 8, 9, 9])
    ws.freeze_panes = "A5"


def write_readme(wb, S):
    ws = fresh(wb, "Read Me", 1)
    ws["A1"] = "How to use this spreadsheet"
    ws["A1"].font = S["title"]

    lines = [
        ("", ""),
        ("THE SHORT VERSION", ""),
        ("", "Double-click 'Update Corners' once a week (Tuesday evening is best)."),
        ("", "Read the answer in the black window that opens - or open the This Week tab."),
        ("", "That's it. Everything below is detail."),
        ("", ""),
        ("YOUR WEEKLY ROUTINE", ""),
        ("1.", "Tuesday evening: double-click 'Update Corners'. It downloads the weekend's"),
        ("", "results and next weekend's fixtures, then rebuilds every tab."),
        ("2.", "Read the three lists it prints: teams projected 7+, teams under 4,"),
        ("", "and matches where both sides should reach 4+ corners."),
        ("3.", "Optional: before you place a bet, type the bookmaker's total-corners line"),
        ("", "into the blue Line column on This Week. The sheet then records a call"),
        ("", "(Over or Under) and scores it automatically once the match is played."),
        ("", ""),
        ("THE TABS", ""),
        ("Weekend / Midweek", "One sheet per upcoming block of fixtures, oldest first:"),
        ("", "this weekend, then the midweek round, then the following weekend."),
        ("", "Each shows every side's own projected corner count."),
        ("", "Home Corners = what the home team is expected to take. Away likewise."),
        ("", "Yellow highlight = that side projected at 7 or more. Amber text = under 4."),
        ("Read Me", "This page."),
        ("Teams", "Every team's averages: overall, home, away, and last-6 form."),
        ("", "Sort any column to rank. Rebuilt from scratch on every run."),
        ("Goals ...", "One goals tab per fixture block, matching the corners tabs."),
        ("", "Clean sheets, over/under, both teams to score, with fair prices."),
        ("Predictions", "Every projection ever made, frozen at the time, with the actual"),
        ("", "result beside it once the match is played."),
        ("Weekly Results", "Week by week: every call made, whether it came off, and the"),
        ("", "percentage correct for that week. Plus an all-weeks total."),
        ("Lines", "Only appears once you've typed a line and the match has been played."),
        ("", "Shows every call, whether it landed, and your running hit rate."),
        ("Match Log", "The raw history - one row per match, full-time corner counts."),
        ("", "The only tab that accumulates. You can add cup ties by hand here."),
        ("", ""),
        ("THE COLUMNS ON THIS WEEK", ""),
        ("Home / Away Corners", "That side's own projected count: its corner rate at this venue,"),
        ("", "averaged with what the opponent tends to concede there."),
        ("Total", "The two added together."),
        ("Flags", "HIGH = a side at 7+.  LOW = a side under 4.  BOTH 4+ = both sides at 4 or more."),
        ("Detail", "Which team earned the flag, plus probability notes when a line is entered."),
        ("Line", "BLUE = yours to type. The bookmaker's total-corners line, e.g. 9.5."),
        ("Call", "Filled in automatically when projection and line disagree by 1.5+ corners."),
        ("", ""),
        ("READING THE PROBABILITIES", ""),
        ("", "The 'both teams 4+' list shows a percentage and a fair price, e.g."),
        ("", "    Sheff Wed 10.0 v Bromley 5.5   fair 1.27   79%"),
        ("", "79% is the estimated chance both sides reach 4 corners. Fair 1.27 is the"),
        ("", "price that matches that chance. The rule is simple:"),
        ("", "    bookmaker's price ABOVE fair  =  possibly worth something"),
        ("", "    bookmaker's price BELOW fair  =  you are paying the bookmaker"),
        ("", "A projection can look 'safe' and still be a bad bet at the wrong price."),
        ("", ""),
        ("CHANGING THE THRESHOLDS", ""),
        ("", "Open update_corners.py in any text editor. Near the top:"),
        ("", "    TEAM_HIGH = 7.0     the 7+ flag"),
        ("", "    TEAM_LOW = 4.0      the under-4 flag"),
        ("", "    BOTH_MIN = 4.0      the both-teams flag"),
        ("", "Change the number, save, run again."),
        ("", ""),
        ("IF SOMETHING LOOKS WRONG", ""),
        ("No new matches added", "Corner data lags results by a day or two. Run again tomorrow."),
        ("A team appears twice", "Two spellings of one club. Tell Claude the pair - one-line fix."),
        ("Blank projections", "That team has no matches logged yet. Fills in as they play."),
        ("Everything broke", "corners_tracker_backup.xlsx is last week's copy, made every run."),
        ("", ""),
        ("THE HONEST BIT", ""),
        ("", "These numbers are averages from a small number of matches. Corners follow"),
        ("", "the scoreline - a team chasing a goal wins corners whatever its style - and"),
        ("", "the bookmaker's line reflects more information than this sheet holds."),
        ("", "When the two disagree, the sheet is usually the one that's wrong."),
        ("", "The Lines tab exists to test that honestly. Trust your hit rate over any"),
        ("", "single number here, and give it 20+ settled calls before trusting that."),
    ]
    r = 3
    for label, text in lines:
        c = ws.cell(r, 1, label)
        c.font = S["bold"] if label and not label.endswith(".") else S["plain"]
        if label.isupper() and label:
            c.font = S["bold"]
        t = ws.cell(r, 2, text)
        t.font = S["plain"]
        r += 1
    widths(ws, [24, 95])




# ------------------------------------------------------- prediction tracking

def read_predictions(wb):
    """Everything predicted in past runs, keyed on fixture + date."""
    out = {}
    if "Predictions" not in wb.sheetnames:
        return out
    ws = wb["Predictions"]
    keys = ["ph", "pa", "ptot", "p_both", "lh", "la", "ptotg",
            "p_btts", "p_o25", "p_csh", "p_csa"]
    for r in range(5, ws.max_row + 1):
        home, away = ws.cell(r, 3).value, ws.cell(r, 4).value
        if not home or not away:
            continue
        d = parse_date(ws.cell(r, 1).value)
        rec = {"date": d, "league": ws.cell(r, 2).value,
               "home": str(home).strip(), "away": str(away).strip()}
        for i, key in enumerate(keys, start=5):
            rec[key] = num_f(ws.cell(r, i).value)
        out[(rec["home"], rec["away"], d)] = rec
    return out


def record_predictions(preds, blocks, stats):
    """
    Store any fixture we can project and haven't stored yet. Written ONCE,
    when first made, never revised - otherwise the scorecard would be marking
    its own homework after the result was known.
    """
    known = set(stats)
    added = 0
    for _, seg in blocks:
        for f in seg:
            home = resolve_team(f["home"], known)
            away = resolve_team(f["away"], known)
            if not (home and away):
                continue
            key = (home, away, f["date"])
            if key in preds:
                continue
            ph, pa, tot, _ = project(stats, home, away)
            lh, la = project_goals(stats, home, away)
            m = goal_markets(lh, la)
            if ph is None and not m:
                continue
            p_both = None
            if ph is not None:
                a4, b4 = pois_p_at_least(ph, 4), pois_p_at_least(pa, 4)
                p_both = a4 * b4 if (a4 and b4) else None
            preds[key] = {
                "date": f["date"], "league": f["league"], "home": home, "away": away,
                "ph": ph, "pa": pa, "ptot": tot, "p_both": p_both,
                "lh": lh, "la": la, "ptotg": m.get("total"),
                "p_btts": m.get("btts"), "p_o25": m.get("over25"),
                "p_csh": m.get("cs_home"), "p_csa": m.get("cs_away"),
            }
            added += 1
    return added


def week_label(d):
    """The Thursday-to-Wednesday round a date belongs to, as a label."""
    if not d:
        return "unknown"
    thurs = d - timedelta(days=(d.weekday() - 3) % 7)
    return thurs.strftime("w/c %d %b")


def week_sort_key(d):
    if not d:
        return date.min
    return d - timedelta(days=(d.weekday() - 3) % 7)


def score_one(r):
    """
    Every testable call in a single prediction, as (name, hit) pairs.
    Only calls actually made are included - no call, nothing to score.
    """
    out = []
    hc, ac = r.get("hc"), r.get("ac")
    hg, ag = r.get("hg"), r.get("ag")
    if hc is not None and ac is not None:
        for pred, actual, side in ((r.get("ph"), hc, "home"), (r.get("pa"), ac, "away")):
            if pred is None:
                continue
            if pred >= TEAM_HIGH:
                out.append((f"{side} {TEAM_HIGH:.0f}+ corners", actual >= TEAM_HIGH))
            if pred < TEAM_LOW:
                out.append((f"{side} under {TEAM_LOW:.0f}", actual < TEAM_LOW))
        if r.get("p_both") is not None and r["p_both"] >= 0.5:
            out.append(("both 4+ corners", hc >= BOTH_MIN and ac >= BOTH_MIN))
        if r.get("ptot") is not None:
            out.append(("corner total within 2", abs(r["ptot"] - (hc + ac)) <= 2))
    if hg is not None and ag is not None:
        if r.get("p_btts") is not None and r["p_btts"] >= 0.5:
            out.append(("both teams to score", hg > 0 and ag > 0))
        if r.get("p_o25") is not None and r["p_o25"] >= 0.5:
            out.append(("over 2.5 goals", (hg + ag) >= 3))
        if r.get("p_csh") is not None and r["p_csh"] >= 0.40:
            out.append(("home shutout", ag == 0))
        if r.get("p_csa") is not None and r["p_csa"] >= 0.40:
            out.append(("away shutout", hg == 0))
        if r.get("ptotg") is not None:
            out.append(("goal total within 1", abs(r["ptotg"] - (hg + ag)) <= 1))
    return out


def weekly_breakdown(rows):
    """Group settled predictions into weeks and score each week on its own."""
    weeks = {}
    for r in rows:
        if r.get("hc") is None and r.get("hg") is None:
            continue                      # not played yet
        calls = score_one(r)
        if not calls:
            continue
        key = week_sort_key(r["date"])
        w = weeks.setdefault(key, {"label": week_label(r["date"]), "matches": 0,
                                   "calls": 0, "hits": 0, "rows": []})
        w["matches"] += 1
        w["calls"] += len(calls)
        w["hits"] += sum(1 for _, ok in calls if ok)
        w["rows"].append((r, calls))
    return [weeks[k] for k in sorted(weeks, reverse=True)]


def settle_predictions(preds, played):
    """Attach actual results wherever the match has since been played."""
    index = {(m["home"], m["away"]): m for m in played}
    rows = []
    for rec in preds.values():
        m = index.get((rec["home"], rec["away"]))
        act = {}
        if m and rec["date"] and m["date"] and abs((m["date"] - rec["date"]).days) <= 3:
            act = {"hc": m.get("hc"), "ac": m.get("ac"),
                   "hg": m.get("hg"), "ag": m.get("ag")}
        rows.append({**rec, **act})
    rows.sort(key=lambda x: (x["date"] or date.min), reverse=True)
    return rows


def score_predictions(rows):
    """Turn settled predictions into accuracy measures."""
    s = dict(corner_n=0, corner_abs=0.0, corner_within2=0,
             goal_n=0, goal_abs=0.0, goal_within1=0,
             high_n=0, high_hit=0, low_n=0, low_hit=0,
             both_n=0, both_hit=0, both_called=0, both_brier=0.0,
             btts_n=0, btts_hit=0, btts_called=0, btts_brier=0.0,
             o25_n=0, o25_hit=0, o25_called=0, o25_brier=0.0,
             cs_n=0, cs_hit=0)
    for r in rows:
        hc, ac = r.get("hc"), r.get("ac")
        hg, ag = r.get("hg"), r.get("ag")
        if hc is not None and ac is not None and r.get("ptot") is not None:
            err = abs(r["ptot"] - (hc + ac))
            s["corner_n"] += 1
            s["corner_abs"] += err
            s["corner_within2"] += 1 if err <= 2 else 0
            for pred, actual in ((r.get("ph"), hc), (r.get("pa"), ac)):
                if pred is None:
                    continue
                if pred >= TEAM_HIGH:
                    s["high_n"] += 1
                    s["high_hit"] += 1 if actual >= TEAM_HIGH else 0
                if pred < TEAM_LOW:
                    s["low_n"] += 1
                    s["low_hit"] += 1 if actual < TEAM_LOW else 0
            if r.get("p_both") is not None:
                hit = 1 if (hc >= BOTH_MIN and ac >= BOTH_MIN) else 0
                s["both_n"] += 1
                s["both_brier"] += (r["p_both"] - hit) ** 2
                if r["p_both"] >= 0.5:
                    s["both_called"] += 1
                    s["both_hit"] += hit
        if hg is not None and ag is not None:
            if r.get("ptotg") is not None:
                err = abs(r["ptotg"] - (hg + ag))
                s["goal_n"] += 1
                s["goal_abs"] += err
                s["goal_within1"] += 1 if err <= 1 else 0
            if r.get("p_btts") is not None:
                hit = 1 if (hg > 0 and ag > 0) else 0
                s["btts_n"] += 1
                s["btts_brier"] += (r["p_btts"] - hit) ** 2
                if r["p_btts"] >= 0.5:
                    s["btts_called"] += 1
                    s["btts_hit"] += hit
            if r.get("p_o25") is not None:
                hit = 1 if (hg + ag) >= 3 else 0
                s["o25_n"] += 1
                s["o25_brier"] += (r["p_o25"] - hit) ** 2
                if r["p_o25"] >= 0.5:
                    s["o25_called"] += 1
                    s["o25_hit"] += hit
            for p, blanked in ((r.get("p_csh"), ag == 0), (r.get("p_csa"), hg == 0)):
                if p is not None and p >= 0.40:
                    s["cs_n"] += 1
                    s["cs_hit"] += 1 if blanked else 0
    return s



def write_predictions(wb, rows, S, position):
    ws = fresh(wb, "Predictions", position)
    ws["A1"] = "Predictions vs Results"
    ws["A1"].font = S["title"]
    ws["A2"] = ("Every projection this workbook has made, frozen when it was made, "
                "with the actual result once the match is played. Newest first.")
    ws["A2"].font = S["note"]
    header(ws, 4, ["Date", "League", "Home", "Away",
                   "Pred H Cnr", "Pred A Cnr", "Pred Tot Cnr", "P Both 4+",
                   "Pred H Gls", "Pred A Gls", "Pred Tot Gls",
                   "P BTTS", "P Ov2.5", "P H Shut", "P A Shut",
                   "Act H Cnr", "Act A Cnr", "Act H Gls", "Act A Gls",
                   "Cnr Error", "Gls Error", "Verdict"], S)
    r = 5
    for x in rows:
        hc, ac = x.get("hc"), x.get("ac")
        hg, ag = x.get("hg"), x.get("ag")
        cerr = (abs(x["ptot"] - (hc + ac))
                if (hc is not None and ac is not None and x.get("ptot")) else None)
        gerr = (abs(x["ptotg"] - (hg + ag))
                if (hg is not None and ag is not None and x.get("ptotg")) else None)
        if cerr is None and gerr is None:
            verdict = "awaiting result"
        else:
            bits = []
            if cerr is not None:
                bits.append("corners " + ("good" if cerr <= 2 else
                                          "fair" if cerr <= 4 else "poor"))
            if gerr is not None:
                bits.append("goals " + ("good" if gerr <= 1 else
                                        "fair" if gerr <= 2 else "poor"))
            verdict = ", ".join(bits)
        vals = [x["date"].strftime("%Y-%m-%d") if x["date"] else "", x["league"],
                x["home"], x["away"], x.get("ph"), x.get("pa"), x.get("ptot"),
                x.get("p_both"), x.get("lh"), x.get("la"), x.get("ptotg"),
                x.get("p_btts"), x.get("p_o25"), x.get("p_csh"), x.get("p_csa"),
                hc, ac, hg, ag, cerr, gerr, verdict]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["plain"]; c.border = S["border"]
            if i > 2:
                c.alignment = S["centre"]
            if i in (5, 6, 7, 9, 10, 11, 20, 21):
                c.number_format = "0.0"
            if i in (8, 12, 13, 14, 15):
                c.number_format = "0%"
            if i == 22:
                c.alignment = S["left"]
                c.font = (S["note"] if v == "awaiting result" else
                          S["warn"] if "poor" in v else S["plain"])
        r += 1
    widths(ws, [11, 12, 18, 18, 10, 10, 11, 9, 10, 10, 11, 8, 8, 9, 9,
                9, 9, 9, 9, 9, 9, 22])
    ws.freeze_panes = "E5"


def write_scorecard(wb, weeks, S, position):
    """One block per week: every call made, whether it came off, and a % for the week."""
    ws = fresh(wb, "Weekly Results", position)
    ws["A1"] = "Weekly Results - prediction vs outcome"
    ws["A1"].font = S["title"]
    if weeks:
        latest = weeks[0]
        pct = latest["hits"] / latest["calls"] * 100 if latest["calls"] else 0
        ws["A2"] = (f'Latest week ({latest["label"]}): {latest["hits"]} of '
                    f'{latest["calls"]} calls correct - {pct:.0f}%')
    else:
        ws["A2"] = "No settled predictions yet. Fills in once fixtures have been played."
    ws["A2"].font = S["bold"]
    ws["A3"] = ("Only calls the sheet actually made are scored. A fixture with nothing "
                "flagged contributes nothing either way.")
    ws["A3"].font = S["note"]

    r = 5
    # summary table first
    header(ws, r, ["Week", "Matches", "Calls made", "Correct", "% correct"], S)
    r += 1
    tot_c = tot_h = 0
    for w in weeks:
        tot_c += w["calls"]; tot_h += w["hits"]
        vals = [w["label"], w["matches"], w["calls"], w["hits"],
                w["hits"] / w["calls"] if w["calls"] else None]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["plain"]; c.border = S["border"]
            if i > 1:
                c.alignment = S["centre"]
            if i == 5:
                c.number_format = "0%"
                c.font = S["bold"]
                if v is not None:
                    c.fill = S["hi"] if v >= 0.5 else S["miss"]
        r += 1
    if weeks:
        vals = ["ALL WEEKS", sum(w["matches"] for w in weeks), tot_c, tot_h,
                tot_h / tot_c if tot_c else None]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(r, i, v)
            c.font = S["bold"]; c.border = S["border"]
            if i > 1:
                c.alignment = S["centre"]
            if i == 5:
                c.number_format = "0%"
        r += 2

    # then the detail, week by week
    for w in weeks:
        pct = w["hits"] / w["calls"] if w["calls"] else 0
        c = ws.cell(r, 1, f'{w["label"]}  -  {w["hits"]}/{w["calls"]} correct '
                          f'({pct*100:.0f}%)')
        c.font = S["title"]
        r += 1
        header(ws, r, ["Fixture", "Call made", "Outcome", "Right?", "Actual"], S)
        r += 1
        for rec, calls in sorted(w["rows"], key=lambda x: x[0]["home"]):
            actual = []
            if rec.get("hc") is not None:
                actual.append(f'corners {rec["hc"]}-{rec["ac"]}')
            if rec.get("hg") is not None:
                actual.append(f'goals {rec["hg"]}-{rec["ag"]}')
            actual = ", ".join(actual)
            for j, (name, ok) in enumerate(calls):
                vals = [f'{rec["home"]} v {rec["away"]}' if j == 0 else "",
                        name, "came off" if ok else "missed",
                        "YES" if ok else "no", actual if j == 0 else ""]
                for i, v in enumerate(vals, start=1):
                    cc = ws.cell(r, i, v)
                    cc.font = S["plain"]; cc.border = S["border"]
                    if i in (3, 4):
                        cc.alignment = S["centre"]
                    if i == 4:
                        cc.font = S["bold"]
                        cc.fill = S["hi"] if ok else S["miss"]
                    if i == 5:
                        cc.font = S["note"]
                r += 1
        r += 1

    if weeks:
        for txt in [
            "A week is Thursday to Wednesday, matching the fixture tabs.",
            "One week's percentage is a very small sample - a bad week may mean nothing, "
            "and so may a good one. The ALL WEEKS row is the number to watch.",
            "Predictions are frozen when made and never revised, so this is an honest "
            "record rather than hindsight.",
        ]:
            c = ws.cell(r, 1, txt)
            c.font = S["note"]
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
            r += 1
    widths(ws, [34, 26, 14, 9, 26])
    ws.freeze_panes = "A6"


# ------------------------------------------------------------------ main

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, WORKBOOK)

    print("\n" + "=" * 60)
    print("  Corners tracker - weekly update")
    print("=" * 60 + "\n")

    if not os.path.exists(path):
        die(f"Can't find {WORKBOOK} next to this script.\n  Looked in: {here}")
    try:
        import openpyxl
    except ImportError:
        die("Missing 'openpyxl'. Run:  pip3 install --user openpyxl")

    season = season_code()
    print(f"  Season {season[:2]}/{season[2:]}\n")

    manual = get_manual(here)
    results, failed = get_results(season)
    if manual:
        # manual first, official second: the apply loop updates rows in place,
        # so whichever is processed later wins - and that must be the official.
        results = manual + results
    if not results:
        die("Nothing downloaded. Check your connection, or the season may not\n"
            "  have started. The workbook has not been touched.")

    fixtures = get_openfootball(season)
    have = {f["league"] for f in fixtures}
    missing = [lg for lg in ("Championship", "League 1", "League 2")
               if lg not in have]
    if missing:
        fixtures.extend(get_fixturedownload(season, missing))
    extra = get_fixtures()
    if extra:
        seen = {(f["league"], f["home"], f["away"], f["date"]) for f in fixtures}
        merged = 0
        for f in extra:
            # the two sources spell clubs differently, so match on date + a
            # loose form of both names before treating it as a duplicate
            dup = any(f["date"] == g["date"]
                      and norm_loose(f["home"]) == norm_loose(g["home"])
                      and norm_loose(f["away"]) == norm_loose(g["away"])
                      for g in fixtures)
            if not dup:
                fixtures.append(f)
                merged += 1
        if merged:
            print(f"  Added {merged} more from football-data")
    # one fixture can arrive from several sources under different spellings,
    # so collapse them on date plus a loose form of both team names
    if fixtures:
        seen, unique = set(), []
        for f in sorted(fixtures, key=lambda x: (x["date"], x["home"])):
            key = (f["date"], norm_loose(f["home"]), norm_loose(f["away"]))
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)
        if len(unique) != len(fixtures):
            print(f"  Removed {len(fixtures) - len(unique)} duplicate fixture(s)")
        fixtures = unique
    if not fixtures:
        fixtures = None

    shutil.copy2(path, os.path.join(here, "corners_tracker_backup.xlsx"))
    wb = openpyxl.load_workbook(path)
    if "Match Log" not in wb.sheetnames:
        die("That workbook has no 'Match Log' tab.")
    ml = wb["Match Log"]
    S = style(wb)

    existing = read_log(ml)
    next_row = max([m["row"] for m in existing], default=FIRST - 1) + 1

    def find_existing(m):
        """
        Same fixture within a few days = the same match, so provisional
        figures get corrected. Same fixture months apart = a different match
        (cup tie, or a new season) and gets its own row.
        """
        for e in existing:
            if (e["league"], e["home"], e["away"]) != (m["league"], m["home"], m["away"]):
                continue
            if e["date"] is None or abs((e["date"] - m["date"]).days) <= 3:
                return e
        return None

    added = updated = 0
    # NB: not re-sorted - manual entries come first so official data,
    # processed after, overwrites them where the two disagree.
    for m in results:
        hit = find_existing(m)
        if hit:
            if hit["hc"] != m["hc"] or hit["ac"] != m["ac"]:
                ml.cell(hit["row"], 8, m["hc"])
                ml.cell(hit["row"], 9, m["ac"])
                if m["hg"] is not None:
                    ml.cell(hit["row"], 6, m["hg"])
                    ml.cell(hit["row"], 7, m["ag"])
                hit["hc"], hit["ac"] = m["hc"], m["ac"]
                hit["hg"], hit["ag"] = m.get("hg"), m.get("ag")
                hit["date"] = m["date"]
                updated += 1
            continue
        vals = [m["date"].strftime("%Y-%m-%d"), None, m["league"], m["home"],
                m["away"], m["hg"], m["ag"], m["hc"], m["ac"]]
        for i, v in enumerate(vals, start=1):
            c = ml.cell(next_row, i, v)
            c.font = S["blue"]
            if i in (1, 2, 6, 7, 8, 9):
                c.alignment = S["centre"]
        if not ml.cell(next_row, 10).value:
            ml.cell(next_row, 10, f'=IF(H{next_row}="","",H{next_row}+I{next_row})')
        rec = {"row": next_row, "date": m["date"], "league": m["league"],
               "home": m["home"], "away": m["away"], "hc": m["hc"], "ac": m["ac"],
               "hg": m.get("hg"), "ag": m.get("ag")}
        existing.append(rec)
        next_row += 1
        added += 1

    # renumber gameweeks per league: Thu-Mon rounds numbered, Tue/Wed as MWn
    by_league, week_numbers = {}, {}
    for m in existing:
        if m["date"]:
            by_league.setdefault(m["league"], []).append(m)
    for league, ms in by_league.items():
        labels, numbers = label_gameweeks(ms)
        week_numbers[league] = numbers
        for m in ms:
            ml.cell(m["row"], 2, labels.get(id(m)))
            ml.cell(m["row"], 2).alignment = S["centre"]

    stats = build_stats(existing)
    lines = harvest_lines(wb)
    settled = settle_lines(lines, existing)
    for s_ in settled:
        tot = s_.get("proj")
        if tot is None:
            _, _, tot, _ = project(stats, s_["home"], s_["away"])
            s_["proj"] = tot
        call, diff, result, correct = score_call(tot, s_["line"], s_["actual"])
        s_["call"] = s_.get("call") or call
        s_["result"] = result
        s_["correct"] = (None if s_["call"] is None or result in (None, "Push")
                         else s_["call"] == result)
    write_teams(wb, stats, S)
    round_desc, deferred = "", 0
    if fixtures is None:
        print("  ! Keeping previous Fixtures tab (download unavailable)")
        fixtures = []
    else:
        raw_count = len(fixtures)
        fixtures, round_desc, deferred = upcoming(fixtures, week_numbers)
        print(f"  Next {FIXTURE_WINDOW_DAYS} days: {round_desc} "
              f"- {len(fixtures)} of {raw_count} fixtures")
        by_div = {}
        for f in fixtures:
            by_div[f["league"]] = by_div.get(f["league"], 0) + 1
        for lg in ("Championship", "League 1", "League 2"):
            print(f"    {lg}: {by_div.get(lg, 0)}")
    # clear stale fixture sheets from previous runs (their names carry dates)
    for name in list(wb.sheetnames):
        if name in ("This Week", "Goals") or name.startswith(
                ("Weekend ", "Midweek ", "Goals ")) or name == "Scorecard":
            del wb[name]

    segments, unmatched, goal_rows = [], [], []
    blocks = []         # (label, raw fixtures) for each tab
    shown = []          # only the fixtures that actually reach a tab

    # Second dedupe pass, now that the team list exists: sources spell clubs
    # differently ("Sheffield Wednesday" vs "Sheff Wed"), so collapse on the
    # resolved names rather than the raw ones.
    if fixtures:
        known_teams = set(stats)
        seen, unique = set(), []
        for f in sorted(fixtures, key=lambda x: (x["date"], x["home"])):
            h = resolve_team(f["home"], known_teams) or norm_loose(f["home"])
            a = resolve_team(f["away"], known_teams) or norm_loose(f["away"])
            key = (f["date"], h, a)
            if key in seen:
                continue
            seen.add(key)
            unique.append(f)
        if len(unique) != len(fixtures):
            print(f"  Merged {len(fixtures) - len(unique)} duplicate fixture(s)")
        fixtures = unique

    if fixtures:
        for pos, (label, seg) in enumerate(segment_fixtures(fixtures)[:3]):
            for f in seg:
                f["block"] = label
            shown.extend(seg)
            blocks.append((label, seg))
            rows, um, _ = write_this_week(wb, seg, stats, S, round_desc, lines,
                                          sheet_name=label, position=pos)
            segments.append((label, rows))
            unmatched.extend(um)
    unmatched = sorted(set(unmatched))
    ranked = [r for _, rows in segments for r in rows]
    for i, (label, seg) in enumerate(blocks):
        rows = write_goals(wb, seg, stats, S,
                           position=len(blocks) + i, label=label)
        if rows:
            goal_rows.append((label, rows))

    # prediction log + scorecard
    preds = read_predictions(wb)
    new_preds = record_predictions(preds, blocks, stats)
    pred_rows = settle_predictions(preds, existing)
    weeks = weekly_breakdown(pred_rows)
    base = len(blocks) * 2
    write_predictions(wb, pred_rows, S, base)
    write_scorecard(wb, weeks, S, base + 1)
    write_lines(wb, settled, S)
    write_readme(wb, S)
    for junk in ("Fixtures", "Findings", "Team Summary"):
        if junk in wb.sheetnames:
            del wb[junk]
    # keep the running order: corners tabs, goals tabs, then reference sheets
    if "Read Me" in wb.sheetnames:
        target = min(len(blocks) * 2 + 2, len(wb.sheetnames) - 1)
        wb.move_sheet("Read Me", offset=target - wb.sheetnames.index("Read Me"))
    if "Match Log" in wb.sheetnames:
        wb.move_sheet("Match Log", offset=len(wb.sheetnames))

    wb.save(path)

    print("\n" + "=" * 66)
    print(f"  {round_desc.upper()}" if round_desc else "  NEXT ROUND")
    print("=" * 66)
    if not segments:
        print("\n  No upcoming fixtures found.\n")
    for label, rows in segments:
        done = [x for x in rows if x[1] is not None]
        print(f"\n  ===== {label.upper()} =====")
        if not done:
            print("  nothing projectable yet")
            continue
        hi, lo, both = [], [], []
        for f, ph, pa, tot, *_ in done:
            if ph >= TEAM_HIGH:
                hi.append((ph, f'{f["home"]} (h) v {f["away"]}'))
            if pa >= TEAM_HIGH:
                hi.append((pa, f'{f["away"]} (a) at {f["home"]}'))
            if ph < TEAM_LOW:
                lo.append((ph, f'{f["home"]} (h) v {f["away"]}'))
            if pa < TEAM_LOW:
                lo.append((pa, f'{f["away"]} (a) at {f["home"]}'))
            ph_p, pa_p = pois_p_at_least(ph, 4), pois_p_at_least(pa, 4)
            if ph >= BOTH_MIN and pa >= BOTH_MIN and ph_p and pa_p:
                p_both = ph_p * pa_p
                both.append((p_both,
                    f'{f["home"]} {ph:.1f} v {f["away"]} {pa:.1f}  fair {fair(p_both):.2f}'))
        if hi:
            print(f"  Teams projected {TEAM_HIGH:.0f}+:")
            for v, label2 in sorted(hi, reverse=True):
                print(f"    {label2:<46}{v:>6.1f}")
        if lo:
            print(f"  Teams projected under {TEAM_LOW:.0f}:")
            for v, label2 in sorted(lo):
                print(f"    {label2:<46}{v:>6.1f}")
        if both:
            print(f"  Both teams {BOTH_MIN:.0f}+ (P and fair price):")
            for v, label2 in sorted(both, reverse=True):
                print(f"    {label2:<50}{v*100:>5.0f}%")
        if not (hi or lo or both):
            print("  no flags this segment")
    if segments:
        print("\n  'fair' = the price matching that chance. Only prices above fair")
        print("  are worth anything; below it you are paying the bookmaker.")
        print()

    for glabel, grows in goal_rows:
        print(f"\n  ===== GOALS - {glabel.upper()} =====")
        cs = []
        for f, home, away, m in grows:
            if m["cs_home"] >= 0.40:
                cs.append((m["cs_home"], f'{home} (h) v {away}'))
            if m["cs_away"] >= 0.40:
                cs.append((m["cs_away"], f'{away} (a) at {home}'))
        if cs:
            print("  Clean sheet chances (40%+):")
            for v, label in sorted(cs, reverse=True)[:8]:
                print(f"    {label:<44}{v*100:>4.0f}%  fair {fair(v):.2f}")
        big = sorted([x for x in grows if x[3]["over25"] >= 0.55],
                     key=lambda x: -x[3]["over25"])
        if big:
            print("  3+ goals expected:")
            for f, home, away, m in big[:8]:
                print(f"    {home + ' v ' + away:<44}{m['over25']*100:>4.0f}%  "
                      f"fair {fair(m['over25']):.2f}")
        bt = sorted([x for x in grows if x[3]["btts"] >= 0.55],
                    key=lambda x: -x[3]["btts"])
        if bt:
            print("  Both teams to score (55%+):")
            for f, home, away, m in bt[:8]:
                print(f"    {home + ' v ' + away:<44}{m['btts']*100:>4.0f}%  "
                      f"fair {fair(m['btts']):.2f}")
        if not (cs or big or bt):
            print("  nothing above the thresholds this week")
        print()

    if unmatched:
        print("\n  Teams in the fixture list with no matching data:")
        for u in unmatched[:12]:
            print(f"    {u}")
        print("  If any of those look like a club you already track under a")
        print("  different spelling, send me the pair and I'll add the alias.")
    settled_n = sum(1 for x in settled if x.get("correct") is not None)
    if settled_n:
        hits = sum(1 for x in settled if x.get("correct"))
        print(f"\n  Your calls so far: {hits}/{settled_n} correct")
    print(f"\n  ({added} matches added, {updated} corrected"
          + (f", {', '.join(failed)} unavailable" if failed else "") + ")")
    if weeks:
        print("\n  ===== WEEKLY RESULTS =====")
        for w in weeks[:6]:
            pct = w["hits"] / w["calls"] * 100 if w["calls"] else 0
            bar = "#" * int(round(pct / 10)) + "." * (10 - int(round(pct / 10)))
            print(f'  {w["label"]:<14}{w["hits"]:>3}/{w["calls"]:<3} '
                  f'{bar}  {pct:>3.0f}%')
        tc = sum(w["calls"] for w in weeks)
        th = sum(w["hits"] for w in weeks)
        if tc:
            print(f'  {"ALL WEEKS":<14}{th:>3}/{tc:<3} '
                  f'{"":<10}  {th/tc*100:>3.0f}%')
        if tc < 40:
            print("  (small sample - a good or bad week may mean nothing yet)")
    if new_preds:
        print(f"\n  {new_preds} new prediction(s) recorded for later scoring")
    print("  Spreadsheet updated. Backup saved.\n")
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


if __name__ == "__main__":
    main()
