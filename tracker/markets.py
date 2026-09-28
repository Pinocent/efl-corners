"""
Turning expected counts into market probabilities.

Corners: a joint model of both sides at once. The match total is drawn
first, then split between the sides:

    total          ~ negative binomial around the expected total
    home's share   ~ beta-binomial around the expected share

Treating the two sides as independent (the obvious approach, and what this
file used to do) gets two things wrong. The sides' counts pull against each
other (when one dominates, the other wins fewer; correlation about -0.2 in
every EFL division), so the total is much less spread out than two
independent counts would suggest - independence overstated its spread by
about a third. Replaying six seasons, the joint model puts "over 7.5" at
76% (it happened 77.5%) and "over 13.5" at 17.5% (16.4%). The two spread parameters are tuned with the rest of the model
(PARAMS in model.py); the defaults here match.

Goals: Poisson with the Dixon-Coles low-score correction, which fixes the
well-known under-count of 0-0 and 1-1 draws - and so directly affects both
teams to score and clean sheet numbers.
"""

import math

MAX_CORNERS = 30      # per side
MAX_TOTAL = 45
MAX_GOALS = 10
TOTAL_LINES = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5)
SIDE_KEEP = 16        # per-side chances kept for the dashboard (0..15 corners)

TOTAL_SIZE = 40.0     # spread of the match total (higher = closer to Poisson)
SPLIT_KAPPA = 18.0    # spread of the split (lower = more lopsided matches)


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
    out[-1] += max(1 - sum(out), 0.0)
    return out


def pois_pmf(mu, kmax):
    out, term = [], math.exp(-mu)
    for k in range(kmax + 1):
        out.append(term)
        term *= mu / (k + 1)
    out[-1] += max(1 - sum(out), 0.0)
    return out


def _beta_binomial(t, a, b):
    """P(home takes h of the t corners), h = 0..t."""
    lb = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    lt = math.lgamma(t + 1)
    return [math.exp(lt - math.lgamma(h + 1) - math.lgamma(t - h + 1)
                     + math.lgamma(h + a) + math.lgamma(t - h + b)
                     - math.lgamma(t + a + b) - lb)
            for h in range(t + 1)]


def corner_joint(eh, ea, total_size=TOTAL_SIZE, kappa=SPLIT_KAPPA):
    """{(home, away): probability} for one match."""
    eh, ea = max(eh, 0.05), max(ea, 0.05)
    et, share = eh + ea, eh / (eh + ea)
    joint = {}
    for t, pt in enumerate(nb_pmf(et, total_size, MAX_TOTAL)):
        if pt < 1e-10:
            continue
        for h, q in enumerate(_beta_binomial(t, share * kappa, (1 - share) * kappa)):
            joint[(h, t - h)] = pt * q
    return joint


def corner_markets(eh, ea, league=None, total_size=TOTAL_SIZE, kappa=SPLIT_KAPPA):
    """Everything the dashboard and the calls need for one fixture's corners."""
    joint = corner_joint(eh, ea, total_size, kappa)
    ph, pa = [0.0] * (MAX_TOTAL + 1), [0.0] * (MAX_TOTAL + 1)
    tot = [0.0] * (MAX_TOTAL + 1)
    both4 = win = lose = 0.0
    for (h, a), p in joint.items():
        ph[h] += p
        pa[a] += p
        tot[h + a] += p
        if h >= 4 and a >= 4:
            both4 += p
        if h > a:
            win += p
        elif a > h:
            lose += p
    totals = {line: sum(tot[int(line) + 1:]) for line in TOTAL_LINES}
    return {
        "eh": eh, "ea": ea, "et": eh + ea,
        "totals": totals,
        "main_line": min(totals, key=lambda l: abs(totals[l] - 0.5)),
        "home4": sum(ph[4:]), "away4": sum(pa[4:]), "both4": both4,
        "home_more": win / (win + lose),           # ignoring level counts
        "home_u3": sum(ph[:3]), "away_u3": sum(pa[:3]),
        # each side's own count, 0..15 (anything higher folded into 15)
        "ph": ph[:SIDE_KEEP - 1] + [sum(ph[SIDE_KEEP - 1:])],
        "pa": pa[:SIDE_KEEP - 1] + [sum(pa[SIDE_KEEP - 1:])],
    }


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
    return {
        "lh": lh, "la": la, "lt": lh + la,
        "home": p_home, "draw": 1 - p_home - p_away, "away": p_away,
        "btts": sum(m[i][j] for i in rng for j in rng if i and j),
        "o25": sum(m[i][j] for i in rng for j in rng if i + j >= 3),
        "cs_home": sum(m[i][0] for i in rng),     # away fail to score
        "cs_away": sum(m[0][j] for j in rng),     # home fail to score
    }


def fair(p):
    return round(1 / p, 2) if p and p > 0.01 else None
