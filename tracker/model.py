"""
The prediction model.

Old approach (v2): home side's corners = average of (its home corners won,
opponent's away corners conceded). Every match counts equally, one match of
data is trusted as much as twenty, and there's no allowance for who the
team has already played.

This version fits team ratings the way bookmakers' base models do:

  expected home corners = league home average x home attack x away defence
  expected away corners = league away average x away attack x home defence

  * attack / defence are fitted together, so beating weak sides counts for less
  * each team gets a home and an away adjustment, shrunk towards its overall
    level until there's enough evidence the venue really matters for them
  * ratings start from last season's (regressed halfway to average), so the
    first few weeks aren't guesswork, and fade as this season's data arrives
  * recent matches weigh more (half-life in days)
  * corner ratings are blended with shot-volume ratings - shots are a steadier
    read on territorial pressure, which is what produces corners
  * goals use the same rating structure on a blend of goals, xG and shots on
    target, with heavier pulling towards average than corners need. Goals
    alone swing too much: replaying the last two seasons, "60%+" goals calls
    landed only 52-57% of the time; with shots on target and more shrinkage
    they land 61-67%, and every goals market scores better.

Bookmaker odds are deliberately not used: every number is the model's own,
from match statistics alone.

All the blend weights live in PARAMS and were chosen by backtest.py on the
whole of last season, then checked on this season without re-tuning.
"""

import math

from . import markets

PARAMS = {
    "half_life": 60,        # days; a match 2 months ago counts half
    "k_team": 12.0,           # prior strength, in matches, for team ratings
    "k_venue": 40.0,         # prior strength for home/away adjustments
    "prior_regress": 0.5,    # last season's rating kept at this fraction
    "shot_blend": 0.4,       # weight of shot ratings in corner ratings
    "nb_size": 10.0,         # per-side corner spread, used by the warnings
    "dc_rho": -0.08,         # Dixon-Coles low-score correction
    # goals
    "goal_mix": (0.2, 0.4, 0.4),  # goals, xG, shots on target x conversion
    "goal_k_team": 25.0,     # goals need more pulling towards average
    "goal_half_life": 120,
}


def _w(age, half_life):
    return 0.5 ** (max(age, 0) / half_life) if half_life else 1.0


class Ratings:
    """Multiplicative attack/defence ratings for one statistic, per league."""

    def __init__(self, hkey, akey, p=PARAMS):
        self.hkey, self.akey, self.p = hkey, akey, p
        self.mu = {}                 # league -> (home mean, away mean)
        self.att, self.dfn = {}, {}  # team -> rating
        self.ah, self.aa, self.dh, self.da = {}, {}, {}, {}  # venue adjustments
        self.n = {}                  # team -> matches used
        self.league_of = {}

    def fit(self, matches, asof, priors=None, league_prior=None):
        p = self.p
        rows = []
        for m in matches:
            x, y = m.get(self.hkey), m.get(self.akey)
            if x is None or y is None or m["date"] >= asof:
                continue
            rows.append((m["league"], m["home"], m["away"], x, y,
                         _w((asof - m["date"]).days, p["half_life"])))
        teams = {}
        for lg, h, a, *_ in rows:
            teams[h] = lg
            teams[a] = lg
            self.n[h] = self.n.get(h, 0) + 1
            self.n[a] = self.n.get(a, 0) + 1
        self.league_of = teams

        # league means, leaning on last season's until ~40 matches are in.
        # A league with no matches yet (gameweek 1) uses last season's.
        for lg in set(teams.values()) | set(league_prior or {}):
            lr = [r for r in rows if r[0] == lg]
            sw = sum(r[5] for r in lr)
            mh = sum(r[3] * r[5] for r in lr) / sw if sw else 0
            ma = sum(r[4] * r[5] for r in lr) / sw if sw else 0
            if league_prior and lg in league_prior:
                ph_, pa_ = league_prior[lg]
                k = 40.0
                mh = (mh * sw + ph_ * k) / (sw + k)
                ma = (ma * sw + pa_ * k) / (sw + k)
            self.mu[lg] = (max(mh, 0.05), max(ma, 0.05))

        # a club only inherits last season's rating if it's in the same
        # division - a promoted side's League 1 numbers mean little in the
        # Championship
        rg = p["prior_regress"]
        self.prior = {}
        for t, (pa_, pd_, plg) in (priors or {}).items():
            self.prior[t] = (plg, (1 + rg * (pa_ - 1), 1 + rg * (pd_ - 1)))
        prior = {}
        for t in teams:
            plg, v = self.prior.get(t, (None, (1.0, 1.0)))
            prior[t] = v if plg == teams[t] else (1.0, 1.0)
        att = {t: prior[t][0] for t in teams}
        dfn = {t: prior[t][1] for t in teams}
        ah = {t: 1.0 for t in teams}
        aa = {t: 1.0 for t in teams}
        dh = {t: 1.0 for t in teams}
        da = {t: 1.0 for t in teams}
        kt, kv = p["k_team"], p["k_venue"]

        for _ in range(12):
            num = {t: [0.0] * 8 for t in teams}   # obs / exp for att, dfn, ah, aa, dh, da
            for lg, h, a, x, y, w in rows:
                mh, ma = self.mu[lg]
                eh_base = mh * dfn[a] * da[a]            # before home attack
                ea_base = ma * dfn[h] * dh[h]
                num[h][0] += w * x; num[h][1] += w * eh_base * ah[h]
                num[a][0] += w * y; num[a][1] += w * ea_base * aa[a]
                # defence: what the side concedes vs what the attacker would expect
                num[a][2] += w * x; num[a][3] += w * mh * att[h] * ah[h] * da[a]
                num[h][2] += w * y; num[h][3] += w * ma * att[a] * aa[a] * dh[h]
                # venue adjustments
                num[h][4] += w * x; num[h][5] += w * eh_base * att[h]
                num[a][6] += w * y; num[a][7] += w * ea_base * att[a]
            for t, v in num.items():
                lg = teams[t]
                mbar = sum(self.mu[lg]) / 2
                att[t] = (v[0] + kt * mbar * prior[t][0]) / (v[1] + kt * mbar)
                dfn[t] = (v[2] + kt * mbar * prior[t][1]) / (v[3] + kt * mbar)
                ah[t] = (v[4] + kv * mbar) / (v[5] + kv * mbar)
                aa[t] = (v[6] + kv * mbar) / (v[7] + kv * mbar)
            # defensive venue adjustments, same pattern
            vd = {t: [0.0] * 4 for t in teams}
            for lg, h, a, x, y, w in rows:
                mh, ma = self.mu[lg]
                vd[a][0] += w * x; vd[a][1] += w * mh * att[h] * ah[h] * dfn[a]
                vd[h][2] += w * y; vd[h][3] += w * ma * att[a] * aa[a] * dfn[h]
            for t, v in vd.items():
                mbar = sum(self.mu[teams[t]]) / 2
                da[t] = (v[0] + kv * mbar) / (v[1] + kv * mbar)
                dh[t] = (v[2] + kv * mbar) / (v[3] + kv * mbar)
            # keep each league's average rating at 1 so the league mean means something
            for lg in self.mu:
                ts = [t for t in teams if teams[t] == lg]
                if not ts:
                    continue
                for d in (att, dfn, ah, aa, dh, da):
                    g = math.exp(sum(math.log(d[t]) for t in ts) / len(ts))
                    for t in ts:
                        d[t] /= g

        self.att, self.dfn = att, dfn
        self.ah, self.aa, self.dh, self.da = ah, aa, dh, da
        return self

    def expect(self, league, home, away):
        mh, ma = self.mu.get(league, (None, None))
        if mh is None:
            return None, None

        def base(t, i):
            # a club with no matches yet: last season's rating if same division
            plg, v = getattr(self, "prior", {}).get(t, (None, (1.0, 1.0)))
            return v[i] if plg == league else 1.0

        def g(d, t, i=None):
            return d[t] if t in d else (base(t, i) if i is not None else 1.0)
        eh = mh * g(self.att, home, 0) * g(self.ah, home) * g(self.dfn, away, 1) * g(self.da, away)
        ea = ma * g(self.att, away, 0) * g(self.aa, away) * g(self.dfn, home, 1) * g(self.dh, home)
        return eh, ea

    def table(self):
        return {t: {"att": self.att[t], "dfn": self.dfn[t], "home_att": self.ah[t],
                    "away_att": self.aa[t], "home_dfn": self.dh[t],
                    "away_dfn": self.da[t], "n": self.n.get(t, 0)}
                for t in self.att}


def season_priors(rat):
    """Final ratings from last season, keyed by team, for next season's start."""
    return ({t: (rat.att[t], rat.dfn[t], rat.league_of[t]) for t in rat.att},
            dict(rat.mu))


def _goal_signal(matches, mix):
    """
    Per match, each side's goal signal: a weighted blend of goals scored, xG,
    and shots on target converted at the league's goals-per-shot-on-target
    rate. Whatever is missing (xG before 2026-27) is left out and the rest
    re-weighted.
    """
    conv = {}
    for lg in {m["league"] for m in matches}:
        rs = [m for m in matches if m["league"] == lg and m.get("hst") is not None
              and m.get("hg") is not None]
        sot = sum(m["hst"] + m["ast"] for m in rs)
        conv[lg] = sum(m["hg"] + m["ag"] for m in rs) / sot if sot else 0.3
    wg, wx, ws = mix
    out = []
    for m in matches:
        m = dict(m)
        for side, g, xg, st in (("h", "hg", "hxg", "hst"), ("a", "ag", "axg", "ast")):
            if m.get(g) is None:
                m["gs" + side] = None
                continue
            parts = [(wg, m[g]), (wx, m.get(xg)),
                     (ws, m[st] * conv[m["league"]] if m.get(st) is not None else None)]
            parts = [(w, v) for w, v in parts if v is not None and w]
            m["gs" + side] = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
        out.append(m)
    return out


class Model:
    """Everything needed to predict one round, fitted as of a date."""

    def __init__(self, p=PARAMS):
        self.p = p

    def fit(self, matches, asof, prior_model=None):
        p = self.p
        pri = prior_model or {}
        self.corners = Ratings("hc", "ac", p).fit(
            matches, asof, *pri.get("corners", (None, None)))
        self.shots = Ratings("hs", "as_", p).fit(
            matches, asof, *pri.get("shots", (None, None)))
        gp = dict(p, k_team=p["goal_k_team"], half_life=p["goal_half_life"])
        self.goals = Ratings("gsh", "gsa", gp).fit(
            _goal_signal(matches, p["goal_mix"]), asof, *pri.get("goals", (None, None)))
        return self

    def priors(self):
        return {"corners": season_priors(self.corners),
                "shots": season_priors(self.shots),
                "goals": season_priors(self.goals)}

    def predict(self, fx):
        """fx: fixture dict (league, home, away). -> dict or None."""
        p = self.p
        lg, h, a = fx["league"], fx["home"], fx["away"]
        ch, ca = self.expect_corners(fx)
        if ch is None:
            return None
        corners = markets.corner_markets(ch, ca, lg)
        return self._with_goals(fx, corners)

    def expect_corners(self, fx):
        """Expected corners for each side, after the shots blend."""
        p = self.p
        lg, h, a = fx["league"], fx["home"], fx["away"]
        ch, ca = self.corners.expect(lg, h, a)
        if ch is None:
            return None, None
        sh, sa = self.shots.expect(lg, h, a)
        mh, ma = self.corners.mu[lg]
        smh, sma = self.shots.mu.get(lg, (None, None))
        b = p["shot_blend"]
        if sh and smh:
            # same relative strength, read from shots instead of corners
            ch = mh * (ch / mh) ** (1 - b) * (sh / smh) ** b
            ca = ma * (ca / ma) ** (1 - b) * (sa / sma) ** b

        return ch, ca

    def _with_goals(self, fx, corners):
        p = self.p
        lg, h, a = fx["league"], fx["home"], fx["away"]
        gh, ga = self.goals.expect(lg, h, a)
        goals = markets.goal_markets(max(gh, 0.15), max(ga, 0.15), p["dc_rho"])
        n = min(self.corners.n.get(h, 0), self.corners.n.get(a, 0))
        return {"corners": corners, "goals": goals, "sample": n}

