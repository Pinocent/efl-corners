"""
Gameweek detection - worked out from the fixture dates, not a fixed rule.

The old script assumed Thu-Mon = weekend and Tue/Wed = midweek. That breaks
when a round starts on a Friday and a midweek round follows on the Monday, or
when Christmas rounds land on odd days. Two facts are always true instead:

  1. a round's matches sit on consecutive (or nearly consecutive) days
  2. no club plays twice in the same round

So: chain match days together while the gap is at most 2 days, then, if a
chain contains a club twice, cut it where that clash disappears. Whatever is
left is a round. It works across all three divisions at once, so one "round"
covers the Championship, League 1 and League 2 matches played together.
"""

from datetime import timedelta

MAX_GAP_DAYS = 2        # Thu -> Sat stays together; Sat -> Tue does not


def _clashes(items):
    seen, n = set(), 0
    for it in items:
        for t in ((it["league"], it["home"]), (it["league"], it["away"])):
            if t in seen:
                n += 1
            seen.add(t)
    return n


def _split(days):
    """days: list of (date, [items]) in date order -> list of such lists."""
    if len(days) <= 1:
        return [days]
    flat = [it for _, its in days for it in its]
    if _clashes(flat) == 0 and (days[-1][0] - days[0][0]).days <= 6:
        return [days]
    best = None
    for k in range(1, len(days)):
        left = [it for _, its in days[:k] for it in its]
        right = [it for _, its in days[k:] for it in its]
        gap = (days[k][0] - days[k - 1][0]).days
        # fewest clashes, then the widest gap, then the most even split
        score = (_clashes(left) + _clashes(right), -gap,
                 -min(len(left), len(right)))
        if best is None or score < best[0]:
            best = (score, k)
    k = best[1]
    return _split(days[:k]) + _split(days[k:])


def detect_rounds(items):
    """
    items: dicts with date, league, home, away (played and unplayed mixed).
    Sets it["round"] to a round id (ISO date of the round's first day) and
    returns {round_id: info} in date order.
    """
    by_day = {}
    for it in items:
        if it.get("date"):
            by_day.setdefault(it["date"], []).append(it)
    days = sorted(by_day.items())

    chains, cur = [], []
    for d, its in days:
        if cur and (d - cur[-1][0]).days > MAX_GAP_DAYS:
            chains.append(cur)
            cur = []
        cur.append((d, its))
    if cur:
        chains.append(cur)

    rounds = {}
    for chain in chains:
        for part in _split(chain):
            start, end = part[0][0], part[-1][0]
            rid = start.isoformat()
            members = [it for _, its in part for it in its]
            for it in members:
                it["round"] = rid
            # named by where most of its matches fall: Tue-Thu = midweek
            mid = sum(len(its) for d, its in part if d.weekday() in (1, 2, 3))
            kind = "Midweek" if mid * 2 >= len(members) else "Weekend"
            rounds[rid] = {"id": rid, "start": start, "end": end, "kind": kind,
                           "matches": len(members), "label": _label(kind, start, end)}
    ordered = dict(sorted(rounds.items()))
    for i, r in enumerate(ordered.values(), 1):
        r["n"] = i
    return ordered


def _label(kind, a, b):
    if a == b:
        return f"{kind} · {a.strftime('%a %-d %b')}"
    return f"{kind} · {a.strftime('%a %-d')} – {b.strftime('%a %-d %b')}" \
        if a.month == b.month else \
        f"{kind} · {a.strftime('%a %-d %b')} – {b.strftime('%a %-d %b')}"


def round_of(rounds, d):
    for r in rounds.values():
        if r["start"] <= d <= r["end"]:
            return r
    return None


def next_rounds(rounds, today, n=2):
    """The next n rounds that still have something to play (today counts)."""
    ahead = [r for r in rounds.values() if r["end"] >= today]
    return ahead[:n]


def last_completed(rounds, today):
    done = [r for r in rounds.values() if r["end"] < today]
    return done[-1] if done else None


def week_span(d):
    return d - timedelta(days=d.weekday())
