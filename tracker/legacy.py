"""
The v2 method (the old spreadsheet), reproduced exactly - averages and
independent Poisson counts - so backtest.py can score old against new and
run.py can mark the spreadsheet's old predictions the way they were made.
"""

from . import markets


def independent_markets(eh, ea):
    """Corner markets with the two sides treated as independent Poisson counts."""
    ph, pa = markets.pois_pmf(max(eh, .05), markets.MAX_CORNERS), markets.pois_pmf(max(ea, .05), markets.MAX_CORNERS)
    tot = [0.0] * (2 * markets.MAX_CORNERS + 1)
    both4 = win = lose = 0.0
    for i, x in enumerate(ph):
        for j, y in enumerate(pa):
            tot[i + j] += x * y
            both4 += x * y if i >= 4 and j >= 4 else 0
            win += x * y if i > j else 0
            lose += x * y if j > i else 0
    totals = {l: sum(tot[int(l) + 1:]) for l in markets.TOTAL_LINES}
    keep = markets.SIDE_KEEP
    return {"eh": eh, "ea": ea, "et": eh + ea, "totals": totals,
            "main_line": min(totals, key=lambda l: abs(totals[l] - .5)),
            "home4": sum(ph[4:]), "away4": sum(pa[4:]), "both4": both4,
            "home_more": win / (win + lose), "home_u3": sum(ph[:3]), "away_u3": sum(pa[:3]),
            "ph": ph[:keep - 1] + [sum(ph[keep - 1:])], "pa": pa[:keep - 1] + [sum(pa[keep - 1:])]}


def predict_v2(matches, asof, fx):
    h, a = fx["home"], fx["away"]
    past = [m for m in matches if m["date"] < asof and m.get("hc") is not None]

    def avg(rows, key):
        v = [r[key] for r in rows if r.get(key) is not None]
        return sum(v) / len(v) if v else None

    hh = [m for m in past if m["home"] == h]
    aa = [m for m in past if m["away"] == a]
    ha = [m for m in past if h in (m["home"], m["away"])]
    ab = [m for m in past if a in (m["home"], m["away"])]
    if not ha or not ab:
        return None

    def overall(rows, team, for_, key_h, key_a):
        v = [(m[key_h] if (m["home"] == team) == for_ else m[key_a]) for m in rows]
        return sum(v) / len(v)

    h_for = avg(hh, "hc") if hh else overall(ha, h, True, "hc", "ac")
    h_ag = avg(hh, "ac") if hh else overall(ha, h, False, "hc", "ac")
    a_for = avg(aa, "ac") if aa else overall(ab, a, True, "hc", "ac")
    a_ag = avg(aa, "hc") if aa else overall(ab, a, False, "hc", "ac")
    ch = max((h_for + a_ag) / 2, 0.5)
    ca = max((a_for + h_ag) / 2, 0.5)

    gh_for = avg(hh, "hg") if hh else overall(ha, h, True, "hg", "ag")
    gh_ag = avg(hh, "ag") if hh else overall(ha, h, False, "hg", "ag")
    ga_for = avg(aa, "ag") if aa else overall(ab, a, True, "hg", "ag")
    ga_ag = avg(aa, "hg") if aa else overall(ab, a, False, "hg", "ag")
    lh = max((gh_for + ga_ag) / 2, 0.2)
    la = max((ga_for + gh_ag) / 2, 0.2)
    return {"corners": independent_markets(ch, ca),
            "goals": markets.goal_markets(lh, la, rho=0.0), "sample": 0}
