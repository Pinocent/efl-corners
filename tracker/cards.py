"""
Cards (yellow + red shown to each side), with the referee.

  expected home cards = league home average x home side's card rating
                        x away side's "gets opponents booked" rating
                        x home/away adjustments x referee factor

The team ratings use the same machinery as corners (tracker/model.py), fitted
on each match's cards after dividing out that match's referee - a side that
happened to get three strict referees in a row shouldn't look dirty.

Referee factor: cards shown compared with the league average in the matches
they refereed, over every season since 2017-18 (referees work across all three
divisions), recent matches weighted more, and pulled towards 1.0 until there's
enough evidence. Replaying 2024-26 with factors learned from 2017-24, the
strictest quarter of referees saw over 3.5 cards in 57% of matches and the most
lenient quarter in 42%.

Cards on the two sides move together (heated matches get both sides booked;
correlation +0.15, the opposite of corners), so the chances come from the same
total-then-split model as corners with the spread set the other way: a total
that varies more than chance, split fairly evenly.

When a match's referee hasn't been announced, the factor is 1.0 (an average
referee) and the page says so. football-data publishes appointments about a
day before; the Cowork task (referees.csv) can supply them earlier.
"""

import math
from datetime import timedelta

from . import markets
from .model import Ratings, _w

# chosen by `backtest.py cards-tune` on 2018-19 and 2021-24, checked on 2024-26
CARD_PARAMS = {
    "ref_k": 24.0,           # prior strength for a referee, in matches
    "ref_half_life": 365,    # days; a referee's matches a year ago count half
    "k_team": 12.0,          # prior strength for team card ratings
    "k_venue": 80.0,
    "half_life": 120,
    "prior_regress": 0.7,
    "foul_blend": 0.3,       # weight of foul ratings in the team part
    "total_size": 50.0,      # spread of the match total
    "split_kappa": 150.0,    # spread of the split (high = even split)
    "red_k": 80.0,           # referees' red-card rates barely persist: shrink hard
}
LINES = (2.5, 3.5, 4.5, 5.5, 6.5)


def cards_of(m, side):
    y, r = (m.get("hy"), m.get("hr")) if side == "h" else (m.get("ay"), m.get("ar"))
    return None if y is None else y + (r or 0)


def _league_means(matches):
    """(season or year, league) -> mean total cards; used as each match's baseline."""
    acc = {}
    for m in matches:
        h, a = cards_of(m, "h"), cards_of(m, "a")
        if h is None:
            continue
        key = (m.get("season") or _season(m["date"]), m["league"])
        v = acc.setdefault(key, [0, 0.0, 0])
        v[0] += h + a; v[1] += 1; v[2] += (m.get("hr") or 0) + (m.get("ar") or 0)
    return {k: (v[0] / v[1], v[2] / v[1]) for k, v in acc.items()}


def _season(d):
    return d.year if d.month >= 7 else d.year - 1


class RefereeFactors:
    """How strict each referee is, relative to the league average."""

    def fit(self, matches, asof, p=CARD_PARAMS):
        rows = [m for m in matches if m["date"] < asof and m.get("ref")
                and cards_of(m, "h") is not None]
        means = _league_means(rows)
        acc = {}
        for m in rows:
            base, rbase = means[(m.get("season") or _season(m["date"]), m["league"])]
            w = _w((asof - m["date"]).days, p["ref_half_life"])
            v = acc.setdefault(m["ref"], [0.0, 0.0, 0.0, 0.0, 0])
            v[0] += w * (cards_of(m, "h") + cards_of(m, "a")); v[1] += w * base
            v[2] += w * ((m.get("hr") or 0) + (m.get("ar") or 0)); v[3] += w * rbase; v[4] += 1
        mbar = sum(b for b, _ in means.values()) / max(len(means), 1) or 3.7
        rbar = sum(r for _, r in means.values()) / max(len(means), 1) or 0.13
        k, kr = p["ref_k"], p["red_k"]
        self.factor = {r: (v[0] + k * mbar) / (v[1] + k * mbar) for r, v in acc.items()}
        self.red = {r: (v[2] + kr * rbar) / (v[3] + kr * rbar) for r, v in acc.items()}
        self.n = {r: v[4] for r, v in acc.items()}
        return self

    def get(self, ref):
        return self.factor.get(ref, 1.0) if ref else 1.0


def _adjusted(matches, refs):
    """Each match's cards and fouls with the referee's influence divided out."""
    out = []
    for m in matches:
        h, a = cards_of(m, "h"), cards_of(m, "a")
        if h is None:
            continue
        f = refs.get(m.get("ref"))
        out.append({**m, "khx": h / f, "kax": a / f})
    return out


class CardModel:
    def __init__(self, p=CARD_PARAMS):
        self.p = p

    def fit(self, matches, asof, history, prior=None):
        """
        matches: this season's results. history: every earlier season's results
        (for the referees). prior: last season's card ratings (season_priors).
        """
        p = self.p
        self.refs = RefereeFactors().fit(list(history) + list(matches), asof, p)
        adj = _adjusted(matches, self.refs)
        pr = prior or {}
        self.cards = Ratings("khx", "kax", p).fit(adj, asof, *pr.get("cards", (None, None)))
        self.fouls = Ratings("hf", "af", p).fit(matches, asof, *pr.get("fouls", (None, None)))
        means = _league_means([m for m in matches if m["date"] < asof] or list(history))
        self.red_base = {lg: r for (_, lg), (_, r) in means.items()}
        return self

    def priors(self):
        from .model import season_priors
        return {"cards": season_priors(self.cards), "fouls": season_priors(self.fouls)}

    def expect(self, fx, ref=None):
        """Expected cards for each side (before the referee), and after it."""
        p = self.p
        lg, h, a = fx["league"], fx["home"], fx["away"]
        kh, ka = self.cards.expect(lg, h, a)
        if kh is None:
            return None
        fh, fa = self.fouls.expect(lg, h, a)
        mh, ma = self.cards.mu[lg]
        fmh, fma = self.fouls.mu.get(lg, (None, None))
        b = p["foul_blend"]
        if fh and fmh:
            # the same relative tendency, read from fouls (steadier than cards)
            kh = mh * (kh / mh) ** (1 - b) * (fh / fmh) ** b
            ka = ma * (ka / ma) ** (1 - b) * (fa / fma) ** b
        f = self.refs.get(ref)
        return kh * f, ka * f, f

    def predict(self, fx, ref=None, adjust=None):
        e = self.expect(fx, ref)
        if not e:
            return None
        kh, ka, f = e
        raw = [kh, ka]
        if adjust:                        # the self-review's corrections, if any
            kh, ka = adjust("cards", fx["league"], kh, ka)
        mk = card_markets(kh, ka, self.p["total_size"], self.p["split_kappa"])
        mk["raw"] = raw
        base = self.red_base.get(fx["league"], 0.13)
        red_rate = base * (self.refs.red.get(ref, 1.0) if ref else 1.0) * (kh + ka) / max(sum(self.cards.mu[fx["league"]]), 0.1)
        mk.update(ref=ref or "", ref_factor=f, ref_n=self.refs.n.get(ref, 0) if ref else 0,
                  red=1 - math.exp(-red_rate))
        return mk


def card_markets(kh, ka, total_size=CARD_PARAMS["total_size"], kappa=CARD_PARAMS["split_kappa"]):
    joint = markets.corner_joint(kh, ka, total_size, kappa)
    tot = [0.0] * 30
    ph, pa = [0.0] * 30, [0.0] * 30
    for (h, a), q in joint.items():
        if h + a < 30:
            tot[h + a] += q
        ph[min(h, 29)] += q
        pa[min(a, 29)] += q
    totals = {l: sum(tot[int(l) + 1:]) for l in LINES}
    return {"kh": kh, "ka": ka, "kt": kh + ka, "ktotals": totals, "ktot": tot[:16],
            "home2": sum(ph[2:]), "away2": sum(pa[2:]),
            "kph": [round(x, 4) for x in ph[:9]] + [round(sum(ph[9:]), 4)],
            "kpa": [round(x, 4) for x in pa[:9]] + [round(sum(pa[9:]), 4)]}
