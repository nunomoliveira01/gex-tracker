"""Two-wall portfolio vs three-strategy portfolio, same engine (blocks backtest), NQ, daily, no costs."""
import json
import numpy as np
import pandas as pd
import blocks_backtest as BB
from backtest import ROOT
from metrics import stats as acct_stats
from walls_oos import legs_account, per_trade, yahoo
from opex_test import SPECS

PERIODS = {"oos": ("2018-11-19", "2023-07-31"), "is": ("2023-11-17", "2026-11-17"), "all": ("2018-11-19", "2026-11-17"), "2022+": ("2022-01-03", "2026-11-17")}
PORTS = {"Two walls (1/2 each)": ["Put wall long", "Call wall short"],
         "Three strategies (1/3 each)": ["My strategy (−10bn)", "Put wall long", "Call wall short"],
         "Put wall long alone": ["Put wall long"], "Call wall short alone": ["Call wall short"], "My strategy alone": ["My strategy (−10bn)"]}


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall"]]).join(cw[["call_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    qqq = yahoo("QQQ", adjust=True)["Close"].reindex(d.index).ffill()
    spy = d["spy_close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    L = ["# Two-wall portfolio vs three-strategy portfolio (NQ, daily, no costs, 1 unit of each strategy = its share of capital)", "",
         "| Period | Portfolio | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Days in a trade | % months up |", "|---|---|---|---|---|---|---|---|---|---|"]
    out = {}
    for per, (a, b) in PERIODS.items():
        idx = d.loc[a:b].index
        win = nq.pct_change().loc[a:b].dropna().index
        beta = float(np.cov(nq.pct_change().loc[win], spy.pct_change().loc[win].fillna(0))[0, 1] / spy.pct_change().loc[win].var())
        conv = lambda t, px, beta=beta: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
        rf = rf_all.loc[idx].fillna(0)
        spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
        accts, intr = {}, {}
        for name, s in SPECS.items():
            sign = 1 if s["side"] == "long" else -1
            tr = BB.trades(d.loc[:b], s, a)
            accts[name] = legs_account(d.index, tr, nq, conv, sign, lambda x: 2.0).loc[idx].fillna(0)   # 1 unit = all capital
            intr[name] = pd.Series(idx, index=idx).apply(lambda t: any(x["e"] <= t <= x["x"] for x in tr))
        for pname, members in PORTS.items():
            acct = sum(accts[m] for m in members) / len(members)
            st = acct_stats(acct, spy_ex, rf)
            busy = pd.concat([intr[m] for m in members], axis=1).any(axis=1).mean()
            mon = (1 + acct).groupby(acct.index.to_period("M")).prod() - 1
            res = {**st, "in_trade_pct": round(100 * busy), "months_up_pct": round(100 * (mon > 0).mean())}
            out[f"{per}|{pname}"] = res
            L.append(f"| {per} | {pname} | {st['cagr_pct']}% | {st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {res['in_trade_pct']}% | {res['months_up_pct']}% |")
        for bn, ser in (("SPY (hold)", spy_tr), ("QQQ (hold)", qqq)):
            r = ser.pct_change().loc[idx].fillna(0)
            st = acct_stats(r, spy_ex, rf)
            L.append(f"| {per} | {bn} | {st['cagr_pct']}% | {st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | 100% | |")
        # correlation of daily P&L
        c = pd.DataFrame(accts).corr().round(2)
        out[f"{per}|corr"] = c.to_dict()
        L.append(f"| {per} | daily P&L correlation | " + "; ".join(f"{x[:10]}–{y[:10]} {c.at[x, y]}" for i, x in enumerate(c) for y in list(c)[i + 1:]) + " | | | | | | | |")
    (ROOT / "results_portfolio.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_portfolio.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
