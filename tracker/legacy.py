"""The v2 method, reproduced exactly, so backtest.py can score old against new."""

from . import markets


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

    def overall(rows, team, for_):
        v = [(m["hc"] if (m["home"] == team) == for_ else m["ac"]) for m in rows]
        return sum(v) / len(v)

    h_for = avg(hh, "hc") if hh else overall(ha, h, True)
    h_ag = avg(hh, "ac") if hh else overall(ha, h, False)
    a_for = avg(aa, "ac") if aa else overall(ab, a, True)
    a_ag = avg(aa, "hc") if aa else overall(ab, a, False)
    ch = max((h_for + a_ag) / 2, 0.5)
    ca = max((a_for + h_ag) / 2, 0.5)

    def g_overall(rows, team, for_):
        v = [(m["hg"] if (m["home"] == team) == for_ else m["ag"]) for m in rows]
        return sum(v) / len(v)
    gh_for = avg(hh, "hg") if hh else g_overall(ha, h, True)
    gh_ag = avg(hh, "ag") if hh else g_overall(ha, h, False)
    ga_for = avg(aa, "ag") if aa else g_overall(ab, a, True)
    ga_ag = avg(aa, "hg") if aa else g_overall(ab, a, False)
    lh = max((gh_for + ga_ag) / 2, 0.2)
    la = max((ga_for + gh_ag) / 2, 0.2)
    return {"corners": markets.corner_markets(ch, ca, None, fx["league"]),
            "goals": markets.goal_markets(lh, la, rho=0.0), "sample": 0}
