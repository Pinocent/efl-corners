"""
Turning expected counts into market probabilities.

Corners: negative binomial, not Poisson. Corner counts are more spread out
than a Poisson allows (a side averaging 5 hits 0-1 and 10+ more often than
Poisson says), and ignoring that makes every "safe" over/under look safer than
it is. The spread parameter is fitted by backtest.py.

Goals: Poisson with the Dixon-Coles low-score correction, which fixes the
well-known under-count of 0-0 and 1-1 draws - and so directly affects both
teams to score and clean sheet numbers.
"""

import math

MAX_CORNERS = 30
MAX_GOALS = 10


def nb_pmf(mu, r, kmax=MAX_CORNERS):
    """Negative binomial with mean mu and size r (variance mu + mu^2/r)."""
    mu = max(mu, 0.05)
    if r is None or r > 1e5:
        return pois_pmf(mu, kmax)
    p = r / (r + mu)
    out = []
    logp, log1p = math.log(p), math.log(1 - p)
    for k in range(kmax + 1):
        lg = math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        out.append(math.exp(lg + r * logp + k * log1p))
    tail = 1 - sum(out)
    out[-1] += max(tail, 0.0)
    return out


def pois_pmf(mu, kmax):
    out, term = [], math.exp(-mu)
    for k in range(kmax + 1):
        out.append(term)
        term *= mu / (k + 1)
    out[-1] += max(1 - sum(out), 0.0)
    return out


def p_at_least(pmf, k):
    return sum(pmf[k:])


def corner_markets(eh, ea, r, league):
    """
    Everything the dashboard shows for one fixture's corners.
    League 2 books only offer overs, so no unders or handicaps there.
    """
    ph, pa = nb_pmf(eh, r), nb_pmf(ea, r)
    n = len(ph)
    tot = [0.0] * (2 * n)
    diff = {}
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            p = x * y
            tot[i + j] += p
            diff[i - j] = diff.get(i - j, 0.0) + p

    totals = {}
    for line in (7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5):
        over = sum(tot[int(line) + 1:])
        totals[line] = over

    # main line: the .5 line closest to a coin flip, as a bookmaker would set it
    main = min(totals, key=lambda l: abs(totals[l] - 0.5))

    out = {
        "eh": eh, "ea": ea, "et": eh + ea,
        "totals": totals, "main_line": main,
        "home4": p_at_least(ph, 4), "away4": p_at_least(pa, 4),
    }
    out["both4"] = out["home4"] * out["away4"]
    win = sum(p for d, p in diff.items() if d > 0)
    lose = sum(p for d, p in diff.items() if d < 0)
    out["home_more"] = win / (win + lose)      # ignoring level counts
    out["level"] = diff.get(0, 0.0)

    if league != "League 2":
        hcap = {}
        for h in (-4.5, -3.5, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 4.5):
            # home covers the handicap h when home + h > away
            hcap[h] = sum(p for d, p in diff.items() if d + h > 0)
        out["hcap"] = hcap
        out["hcap_main"] = min(hcap, key=lambda l: abs(hcap[l] - 0.5))
    return out


def dc_matrix(lh, la, rho=-0.08):
    """Score matrix with the Dixon-Coles adjustment on 0-0, 1-0, 0-1, 1-1."""
    ph, pa = pois_pmf(lh, MAX_GOALS), pois_pmf(la, MAX_GOALS)
    m = [[ph[i] * pa[j] for j in range(MAX_GOALS + 1)] for i in range(MAX_GOALS + 1)]
    m[0][0] *= 1 - lh * la * rho
    m[0][1] *= 1 + lh * rho
    m[1][0] *= 1 + la * rho
    m[1][1] *= 1 - rho
    s = sum(map(sum, m))
    return [[x / s for x in row] for row in m]


def goal_markets(lh, la, rho=-0.08):
    m = dc_matrix(lh, la, rho)
    rng = range(MAX_GOALS + 1)
    p_home = sum(m[i][j] for i in rng for j in rng if i > j)
    p_away = sum(m[i][j] for i in rng for j in rng if i < j)
    cs_home = sum(m[i][0] for i in rng)           # away fail to score
    cs_away = sum(m[0][j] for j in rng)           # home fail to score
    return {
        "lh": lh, "la": la, "lt": lh + la,
        "home": p_home, "draw": 1 - p_home - p_away, "away": p_away,
        "btts": sum(m[i][j] for i in rng for j in rng if i and j),
        "o15": sum(m[i][j] for i in rng for j in rng if i + j >= 2),
        "o25": sum(m[i][j] for i in rng for j in rng if i + j >= 3),
        "o35": sum(m[i][j] for i in rng for j in rng if i + j >= 4),
        "cs_home": cs_home, "cs_away": cs_away,
    }


# ---------------------------------------------------------------- odds

def implied(oh, od, oa):
    """Bookmaker 1X2 odds -> probabilities with the margin removed."""
    if not (oh and od and oa):
        return None
    inv = [1 / oh, 1 / od, 1 / oa]
    s = sum(inv)
    return [x / s for x in inv]


def market_goals(oh, od, oa, oo25, ou25):
    """
    Back out the expected goals the bookmakers' prices imply. The total comes
    from the over/under 2.5 price (a sum of Poissons is Poisson, so this is a
    one-dimensional search), then the split between the sides from 1X2.
    """
    p = implied(oh, od, oa)
    if not p or not (oo25 and ou25):
        return None
    po = (1 / oo25) / (1 / oo25 + 1 / ou25)

    def p_over(t):
        return 1 - math.exp(-t) * (1 + t + t * t / 2)

    lo, hi = 0.3, 6.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if p_over(mid) < po else (lo, mid)
    total = (lo + hi) / 2

    target = p[0] - p[2]

    def edge(s):
        ph, pa = pois_pmf(total * s, MAX_GOALS), pois_pmf(total * (1 - s), MAX_GOALS)
        w = sum(ph[i] * pa[j] for i in range(len(ph)) for j in range(len(pa)) if i > j)
        l = sum(ph[i] * pa[j] for i in range(len(ph)) for j in range(len(pa)) if i < j)
        return w - l

    lo, hi = 0.05, 0.95
    for _ in range(30):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if edge(mid) < target else (lo, mid)
    s = (lo + hi) / 2
    return total * s, total * (1 - s)


def fair(p):
    return round(1 / p, 2) if p and p > 0.01 else None
