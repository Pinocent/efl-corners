"""
The EFL's referee appointments.

The EFL publishes each round's officials as a news article ("Referee
appointments: 3-6 October") one to five days before the matches, earlier than
football-data's fixtures file has them. The article is also the best early
sign of a postponement: the fixture list keeps a called-off match on its old
date, but the appointments list only the matches going ahead. On 26 Sep they
listed exactly the 14 of 24 fixtures that were played.

efl.com is a Nuxt app, but its server-rendered pages carry each article's
body as a JavaScript string in window.__NUXT__, so plain HTTP is enough: the
news page for links to the appointment articles, then each article.

Every article read is saved to efl_referees.csv, so the appointments outlive
the article dropping off the news page, and a refused download falls back on
them (as with the fixture list: fixturedownload.com has refused the cloud).
"""

import csv
import html
import json
import os
import re
from datetime import date, datetime, timedelta

from . import sources
from .teams import TEAMS, canon

NEWS_URL = "https://www.efl.com/news/"
SITE = "https://www.efl.com"
FILE = "efl_referees.csv"
FIELDS = ["date", "time", "league", "home", "away", "referee", "article", "published"]
KEEP_DAYS = 400             # saved appointments older than this are dropped

_LINK = re.compile(r"/news/\d{4}/[a-z]+/\d{1,2}/referee-appointments[a-z0-9-]*/")
_MATCH = re.compile(r"^(.+?)\s+v\s+(.+?)(?:\s*\((\d{1,2})[:.](\d{2})\))?$")
_DAY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(January|February|March|April|May|June|July|"
                  r"August|September|October|November|December)(?:\s+(\d{4}))?", re.I)
_JS_STR = r'"((?:[^"\\]|\\.)*)"'


# ------------------------------------------------------------------ reading a page

def article_links(page):
    """Referee-appointment article URLs linked from an efl.com page."""
    return sorted({SITE + p for p in _LINK.findall(page)})


def _js_string(s):
    """The value of a JavaScript string literal's body."""
    s = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", s).replace("\\'", "'")
    try:
        return json.loads(f'"{s}"', strict=False)
    except ValueError:
        return s.replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/")


def article_body(page):
    """The article's text blocks, as HTML. (Other `content:` strings on the page are CSS.)"""
    blocks = re.findall(r'widgetType:"TextBlockWidget"[^{]*widgetData:\{[^}]*?content:' + _JS_STR, page)
    return "\n".join(_js_string(b) for b in blocks)


def _meta(page, key):
    m = re.search(key + r":" + _JS_STR, page)
    return _js_string(m.group(1)) if m else ""


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _day(text, published=""):
    """The date in a heading like 'Saturday, 3 October 2026', else None."""
    m = _DAY.search(text)
    if not m:
        return None
    pub = sources.parse_date(published[:10]) if published else None
    year = int(m.group(3)) if m.group(3) else (pub or date.today()).year
    try:
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {year}", "%d %B %Y").date()
    except ValueError:
        return None
    if not m.group(3) and pub and d < pub - timedelta(days=60):
        d = d.replace(year=year + 1)          # published in December for January
    return d


def _league(heading):
    """Board division for a competition heading; None for cups and anything else."""
    t = heading.lower()
    if any(w in t for w in ("trophy", "cup", "play-off", "playoff")):
        return None
    if "championship" in t:
        return "Championship"
    if re.search(r"\bleague (one|1)\b", t):
        return "League 1"
    if re.search(r"\bleague (two|2)\b", t):
        return "League 2"
    return None


def parse(body, published=""):
    """
    The league matches in an appointments article: [{date, time, league, home,
    away, referee, known}]. Headings give the date and the competition, in
    either order ('Saturday, 3 October 2026' then 'Sky Bet League One', or
    'Jersey Mike's Trophy' then the dates); a heading clears those below its
    level. Each match is a paragraph: 'Home v Away (15:00)', then the referee,
    assistants and fourth official on separate lines. `known` is False when a
    club name isn't one of the board's.
    """
    out = []
    ctx = []                                   # [(level, kind, value)], outermost first
    for tag, inner in re.findall(r"<(h[1-6]|p)\b[^>]*>(.*?)</\1>", body, re.S | re.I):
        level = int(tag[1]) if tag[0] in "hH" else None
        if level is None and "<br" not in inner.lower() and not _MATCH.match(_text(inner)) \
                and re.fullmatch(r"\s*<(strong|b)\b[^>]*>.*?</\1>\s*", inner, re.S | re.I):
            level = 7                          # a bold line as a sub-heading: '<p><strong>Sky Bet League One</strong></p>'
        if level is not None:
            text = _text(inner)
            d = _day(text, published)
            ctx = [c for c in ctx if c[0] < level] + [(level, "date" if d else "comp", d or text)]
            continue
        day = next((v for _, k, v in reversed(ctx) if k == "date"), None)
        league = _league(next((v for _, k, v in reversed(ctx) if k == "comp"), ""))
        lines = [t for t in (_text(x) for x in re.split(r"<br\s*/?>", inner, flags=re.I)) if t]
        m = _MATCH.match(lines[0]) if lines else None
        if not (m and day and league and len(lines) > 1):
            continue
        home, away = canon(m.group(1)), canon(m.group(2))
        ref = re.sub(r"^referee\s*:?\s*", "", lines[1], flags=re.I).strip()
        if re.search(r"\b(tbc|tba|to be (confirmed|announced))\b", ref, re.I):
            ref = ""                           # listed, so going ahead, but no referee yet
        if ref.isupper():
            ref = ref.title()                  # 'NEIL HAIR' -> 'Neil Hair', "O'CONNOR" -> "O'Connor"
        out.append({"date": day, "time": f"{int(m.group(3)):02d}:{m.group(4)}" if m.group(3) else "",
                    "league": league, "home": home, "away": away, "referee": ref,
                    "known": home in TEAMS and away in TEAMS})
    return out


# ------------------------------------------------------------------ the saved copy

def _read(path):
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = sources.parse_date(r.get("date"))
            if d and r.get("home") and r.get("away"):
                r = {k: r.get(k, "") for k in FIELDS}
                r.update(date=d, known=r["home"] in TEAMS and r["away"] in TEAMS)
                out.append(r)
    return out


def _write(path, rows):
    rows = sorted(rows, key=lambda r: (r["date"], r["league"], r["home"]))
    sources.write_csv(path, [{k: r[k] for k in FIELDS} for r in rows], FIELDS)


def get_appointments(cache=None, log=print, status=None, today=None, fetch=None):
    """
    Every appointment known: the saved ones in `cache`, updated from the
    appointment articles linked on efl.com's news page. An article is read
    again while any of its matches is still to come, so a late change of
    referee comes through. Returns rows (FIELDS plus `known`, `date` a date).
    `status` (a dict) gets what happened, for the page.
    """
    fetch = fetch or sources.fetch
    today = today or date.today()
    saved = _read(cache)
    finished = ({r["article"] for r in saved}
                - {r["article"] for r in saved if r["date"] >= today})
    st = {"source": "efl.com", "articles": [], "unknown": []}
    fresh, read = [], set()
    try:
        links = article_links(fetch(NEWS_URL))
    except Exception as e:
        log(f"  ! EFL news page unavailable ({e}); using the saved referee appointments")
        links, st["source"] = [], "saved copy"
    for url in links:
        if url in finished:
            continue
        try:
            page = fetch(url)
        except Exception as e:
            log(f"  ! {url}: unavailable ({e})")
            continue
        pub = _meta(page, "publishedDateTime")
        rows = parse(article_body(page), pub)
        if not rows:
            log(f"  ! {url}: no league appointments found")
            continue
        for r in rows:
            r.update(article=url, published=pub)
        fresh += rows
        read.add(url)
        st["articles"].append({"url": url, "title": _meta(page, "postTitle"), "published": pub,
                               "matches": len(rows)})
    # a re-read article replaces what was saved from it
    keep = [r for r in saved if r["article"] not in read and r["date"] >= today - timedelta(days=KEEP_DAYS)]
    rows = keep + fresh
    if cache and fresh:
        _write(cache, rows)
    st["unknown"] = sorted({t for r in rows if not r["known"] and r["date"] >= today - timedelta(days=7)
                            for t in (r["home"], r["away"]) if t not in TEAMS})
    if not st["articles"] and st["source"] == "efl.com":
        st["source"] = "saved copy"
    if status is not None:
        status.update(st)
    return rows


# ------------------------------------------------------------------ using them

def referees(apps, today, known=()):
    """
    {(home, away): referee} for matches from three days ago to three weeks
    ahead, spelt the way the rest of the board does ('N Hair'). `known` is
    every referee name already on the board, so 'R Mcilravey' comes out as the
    'R McIlravey' football-data writes.
    """
    spelling = {k.lower(): k for k in known if k}
    out = {}
    for r in sorted(apps, key=lambda r: r["published"]):
        if today - timedelta(days=3) <= r["date"] <= today + timedelta(days=21) and r["referee"]:
            key = sources.ref_key(r["referee"])
            out[(r["home"], r["away"])] = spelling.get(key.lower(), key)
    return out


def postponed(apps, fixtures, played=()):
    """
    {(home, away, date)} for fixtures the appointments leave out. When an
    article lists a division's matches for a date, a fixture on that date in
    that division that isn't listed (on that date or within two weeks of it)
    has been called off or moved. The date is part of the key because a
    rearranged match keeps its pair of clubs. A date and division where any
    club name wasn't recognised is left alone rather than trusted. `fixtures`
    need date (a date or 'YYYY-MM-DD'), league, home and away.
    """
    covered, doubtful, listed = set(), set(), {}
    for r in apps:
        (covered if r["known"] else doubtful).add((r["date"], r["league"]))
        listed.setdefault((r["home"], r["away"]), []).append(r["date"])
    out = set()
    for f in fixtures:
        d = f["date"] if isinstance(f["date"], date) else date.fromisoformat(f["date"])
        pair = (f["home"], f["away"])
        if (d, f["league"]) not in covered or (d, f["league"]) in doubtful or pair in played:
            continue
        if any(abs((x - d).days) <= 14 for x in listed.get(pair, [])):
            continue
        out.add(pair + (d,))
    return out
