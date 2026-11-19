"""Beta, Sharpe and alpha of the JP + Put/Call rule (SPY signals), traded in SPY and in NQ futures, 2022-01-03 → 2026-11-17.

Account convention: capital sized for the maximum position (2 units). 1 unit = half the account, the add-on uses the other
half; idle cash earns the 3-month T-bill rate. Daily closes, no costs. Futures returns are already over cash, so the NQ
account earns T-bill + units/2 × NQ futures return."""
import json

import numpy as np
import pandas as pd
import yfinance as yf

from backtest import ROOT
from nq_test import signals

START, END = "2022-01-03", "2026-11-17"


def yahoo(sym, adjust=False):
    h = yf.Ticker(sym).history(start="2021-10-01", interval="1d", auto_adjust=adjust)["Close"]
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h


def units_series(index, sig, with_adds=True):
    u = pd.Series(0.0, index=index)
    for s in sig:
        u.loc[index[index.get_loc(s["e"]) + 1]:s["x"]] += 1        # held from the day after the entry close
        if with_adds and s["a"] is not None:
            u.loc[index[index.get_loc(s["a"]) + 1]:s["x"]] += 1
    return u


def stats(excess, bench_ex, rf):
    """excess: account return over cash. bench_ex: benchmark return over cash."""
    total = excess + rf
    yrs = len(total) / 252
    curve = (1 + total).cumprod()
    X = np.column_stack([np.ones(len(bench_ex)), bench_ex.to_numpy()])
    coef, res, *_ = np.linalg.lstsq(X, excess.to_numpy(), rcond=None)
    resid = excess.to_numpy() - X @ coef
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ X)) * resid.var(ddof=2))
    corr = np.corrcoef(excess, bench_ex)[0, 1]
    return {"cagr_pct": round((curve.iloc[-1] ** (1 / yrs) - 1) * 100, 2), "vol_pct": round(total.std() * np.sqrt(252) * 100, 2),
            "sharpe": round(excess.mean() / excess.std() * np.sqrt(252), 2),
            "max_dd_pct": round((curve / curve.cummax() - 1).min() * 100, 2),
            "beta": round(coef[1], 3), "alpha_pct_yr": round(coef[0] * 252 * 100, 2), "alpha_t": round(coef[0] / se[0], 2),
            "corr": round(corr, 2), "time_in_market_pct": None}


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index().loc[:END]
    sig = [s for s in signals(d) if s["e"] >= pd.Timestamp("2022-01-01")]
    idx = d.loc[START:END].index
    spy = yahoo("SPY", adjust=True).reindex(d.index).ffill()           # with dividends
    nq = yahoo("NQ=F").reindex(d.index).ffill()
    qqq = yahoo("QQQ", adjust=True).reindex(d.index).ffill()
    rf = (yahoo("^IRX").reindex(d.index).ffill() / 100 / 252).loc[idx]
    r_spy, r_nq, r_qqq = (x.pct_change().loc[idx] for x in (spy, nq, qqq))
    spy_ex, qqq_ex = r_spy - rf, r_qqq - rf
    rows = {}
    for name, with_adds in (("with put/call add-on", True), ("JP entry/exit only, 1 unit", False)):
        u = units_series(d.index, sig, with_adds).loc[idx]
        w = u / (2 if with_adds else 1)                                # share of the account invested
        for inst, ex in (("SPY", w * spy_ex), ("NQ futures", w * r_nq)):
            s = stats(ex, spy_ex, rf)
            s["time_in_market_pct"] = round((u > 0).mean() * 100, 1)
            s["avg_exposure_pct"] = round(w.mean() * 100, 1)
            if inst == "NQ futures":
                s2 = stats(ex, qqq_ex, rf)
                s["beta_vs_nasdaq"], s["alpha_vs_nasdaq_pct_yr"], s["alpha_vs_nasdaq_t"] = s2["beta"], s2["alpha_pct_yr"], s2["alpha_t"]
            rows[f"{inst} — {name}"] = s
    rows["Buy & hold SPY"] = {**stats(spy_ex, spy_ex, rf), "time_in_market_pct": 100.0, "avg_exposure_pct": 100.0}
    rows["Buy & hold NQ futures"] = {**stats(r_nq, spy_ex, rf), "time_in_market_pct": 100.0, "avg_exposure_pct": 100.0}
    (ROOT / "results_metrics.json").write_text(json.dumps(rows, indent=1))
    L = [f"# Beta, Sharpe, alpha — JP + Put/Call rule ({START} → {END}, daily, no costs; benchmark SPY with dividends; cash = 3-month T-bill)", "",
         "| Strategy | Return/yr | Volatility | Sharpe | Max drawdown | Beta vs SPY | Alpha/yr vs SPY | Alpha t-stat | Time in market | Avg exposure |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for k, s in rows.items():
        L.append(f"| {k} | {s['cagr_pct']}% | {s['vol_pct']}% | {s['sharpe']} | {s['max_dd_pct']}% | {s['beta']} | {s['alpha_pct_yr']}% | {s['alpha_t']} | {s['time_in_market_pct']}% | {s['avg_exposure_pct']}% |")
    L += ["", "NQ vs the Nasdaq (QQQ) as benchmark: " + "; ".join(f"{k}: beta {v['beta_vs_nasdaq']}, alpha {v['alpha_vs_nasdaq_pct_yr']}%/yr (t {v['alpha_vs_nasdaq_t']})"
                                                       for k, v in rows.items() if "beta_vs_nasdaq" in v)]
    (ROOT / "results_metrics.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
