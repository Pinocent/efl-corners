"""
Manager changes, from each season's Wikipedia page ("2023-24 EFL League One",
section "Managerial changes"). A new manager can change how a side plays
quickly, so the ratings lean less on a club's matches from before its latest
change for a while afterwards (model.py, cards.py: `mgr_weight`, `mgr_days`).

Every date of vacancy and of appointment counts as a change: a caretaker is a
change too. Finished seasons are saved in managers.csv and never fetched
again; the current season is fetched each run, falling back on the saved copy.
"""

import csv
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from html.parser import HTMLParser

from . import sources
from .teams import TEAMS, canon

FILE = "managers.csv"
FIELDS = ["season", "league", "team", "date", "kind"]
DIVISIONS = {"Championship": "EFL Championship", "League 1": "EFL League One", "League 2": "EFL League Two"}
API = "https://en.wikipedia.org/w/api.php?action=parse&format=json&redirects=1&prop=text&page="
UA = "efl-corners-board/1.0 (https://github.com/Pinocent/efl-corners)"


def page_title(season, league):
    a = 2000 + int(season[:2])
    return f"{a}–{(a + 1) % 100:02d} {DIVISIONS[league]}"


class _Tables(HTMLParser):
    """Every wikitable as a grid of cell texts, rowspan and colspan filled in."""

    def __init__(self):
        super().__init__()
        self.tables, self._stack = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append({"rows": [], "row": None, "cell": None, "span": {}})
        elif not self._stack:
            return
        t = self._stack[-1]
        if tag == "tr":
            t["row"] = []
        elif tag in ("td", "th") and t["row"] is not None:
            t["cell"] = {"text": "", "rs": int(re.sub(r"\D", "", a.get("rowspan", "1")) or 1),
                         "cs": int(re.sub(r"\D", "", a.get("colspan", "1")) or 1)}
        elif tag == "sup" and t["cell"] is not None:
            t["cell"]["skip"] = t["cell"].get("skip", 0) + 1     # drop footnote markers

    def handle_endtag(self, tag):
        if not self._stack:
            return
        t = self._stack[-1]
        if tag == "sup" and t["cell"] is not None and t["cell"].get("skip"):
            t["cell"]["skip"] -= 1
        elif tag in ("td", "th") and t["cell"] is not None and t["row"] is not None:
            t["row"].append(t["cell"])
            t["cell"] = None
        elif tag == "tr" and t["row"] is not None:
            t["rows"].append(t["row"])
            t["row"] = None
        elif tag == "table":
            done = self._stack.pop()
            self.tables.append(_grid(done["rows"]))

    def handle_data(self, data):
        if self._stack and self._stack[-1]["cell"] is not None and not self._stack[-1]["cell"].get("skip"):
            self._stack[-1]["cell"]["text"] += data


def _grid(rows):
    out, pending = [], {}            # pending: column -> (text, rows left)
    for row in rows:
        line, col, cells = [], 0, list(row)
        while cells or col in pending:
            if col in pending:
                text, left = pending[col]
                line.append(text)
                pending[col] = (text, left - 1)
                if left - 1 <= 0:
                    del pending[col]
                col += 1
                continue
            c = cells.pop(0)
            text = " ".join(html.unescape(c["text"]).split())
            for _ in range(c["cs"]):
                line.append(text)
                if c["rs"] > 1:
                    pending[col] = (text, c["rs"] - 1)
                col += 1
        out.append(line)
    return out


def _date(text):
    m = re.search(r"(\d{1,2}) (January|February|March|April|May|June|July|August|September|"
                  r"October|November|December) (\d{4})", text)
    return datetime.strptime(" ".join(m.groups()), "%d %B %Y").date() if m else None


def parse(page_html, league, season):
    """[(team, date, kind)] from a season page's managerial changes table."""
    p = _Tables()
    p.feed(page_html)
    for grid in p.tables:
        head = [h.lower() for h in (grid[0] if grid else [])]
        if not any("date of vacancy" in h for h in head) or not any("appointment" in h for h in head):
            continue
        ti = next(i for i, h in enumerate(head) if h.startswith("team"))
        vi = next(i for i, h in enumerate(head) if "date of vacancy" in h)
        ai = next(i for i, h in enumerate(head) if "date of appointment" in h)
        out = []
        for row in grid[1:]:
            if len(row) <= max(ti, vi, ai):
                continue
            team = canon(row[ti])
            if team not in TEAMS:
                continue
            for i, kind in ((vi, "vacancy"), (ai, "appointment")):
                d = _date(row[i])
                if d:
                    out.append({"season": season, "league": league, "team": team, "date": d, "kind": kind})
        return out
    return []


def fetch_season(season, league):
    url = API + urllib.parse.quote(page_title(season, league))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8"))
    if "parse" not in d:
        raise ValueError(d.get("error", {}).get("info", "page not found"))
    return parse(d["parse"]["text"]["*"], league, season)


def get_changes(seasons, cache=None, log=print, current=None):
    """
    Every manager change for `seasons` (codes like '2324'), as
    {team: sorted [dates]}. Seasons already in `cache` are read from it,
    except `current`, which is fetched again (keeping the saved rows if that fails).
    """
    saved = []
    if cache and os.path.exists(cache):
        with open(cache, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                r["date"] = date.fromisoformat(r["date"])
                saved.append(r)
    have = {(r["season"], r["league"]) for r in saved}
    fresh, got = [], set()
    for s in seasons:
        for lg in DIVISIONS:
            if (s, lg) in have and s != current:
                continue
            try:
                rows = fetch_season(s, lg)
            except Exception as e:
                log(f"  ! manager changes {lg} {s}: {e}")
                continue
            fresh += rows
            got.add((s, lg))
            if not rows:
                log(f"  ! manager changes {lg} {s}: none found")
    rows = [r for r in saved if (r["season"], r["league"]) not in got] + fresh
    if cache and fresh:
        rows.sort(key=lambda r: (r["season"], r["league"], r["date"], r["team"]))
        sources.write_csv(cache, rows, FIELDS)
    out = {}
    for r in rows:
        out.setdefault(r["team"], set()).add(r["date"])
    return {t: sorted(ds) for t, ds in out.items()}
