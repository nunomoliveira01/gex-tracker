"""Test 5 — NQ futures timed by QQQ's own signals (native) vs by SPY's signals (cross).
Rules fixed in claude/thetadata-teste-pre-registo.md (19 Nov 2026) before the QQQ option data was downloaded."""
import json

import numpy as np
import pandas as pd
import yfinance as yf

from backtest import ROOT
from nq_test import daily_overlay, random_p, run

START, END = "2022-01-01", "2026-11-17"


def yahoo(sym, col="Close"):
    h = yf.Ticker(sym).history(start="2018-01-01", interval="1d", auto_adjust=False)
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[col]


def load(name):
    d = pd.read_csv(ROOT / "data" / name, parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    prev = d["pc_volume"].shift(1).rolling(60)
    d["z"] = (d["pc_volume"] - prev.mean()) / prev.std()
    return d


def signals(d, level, zmin=1.0):
    """Enter at the close of a day gamma at the low <= level; add once at a close below the entry with put/call z >= 1;
    exit at the close of the first day gamma >= 0."""
    out, pos = [], None
    for t, r in d.iterrows():
        lv = level[t] if isinstance(level, pd.Series) else level
        if pos is None:
            if r["gex_low"] <= lv:
                pos = {"e": t, "a": None}
            continue
        if r["gex_close"] >= 0:
            out.append({**pos, "x": t, "open": False}); pos = None; continue
        if pos["a"] is None and r["spy_close"] < d.at[pos["e"], "spy_close"] and r["z"] >= zmin:
            pos["a"] = t
    if pos:
        out.append({**pos, "x": d.index[-1], "open": True})
    return [s for s in out if s["e"] >= pd.Timestamp(START)]


def score(px, sig):
    px = px.dropna().loc[START:]
    res = run(px, sig); done = res[~res["open"]]
    o = daily_overlay(px, sig)
    s = {"n": int(len(done)), "mean": round(done["ret"].mean() * 100, 2), "median": round(done["ret"].median() * 100, 2),
         "won": int(round((done["ret"] > 0).mean() * 100)), "worst": round(res["worst"].min() * 100, 2), "random_p": random_p(px, done),
         "adds": int(res["add"].sum()), "avg_days": round(res["days"].mean(), 1), **o,
         "per_year": {str(y): round(g["ret"].mean() * 100, 2) for y, g in done.groupby(done["entry"].dt.year)},
         "open_pct": round(res[res["open"]]["ret"].iloc[0] * 100, 2) if res["open"].any() else None,
         "entries": [str(x["e"].date()) for x in sig]}
    s["mean_over_worst"] = round(s["mean"] / abs(s["worst"]), 3)
    return s


def main():
    spy, qqq = load("daily.csv").loc[:END], load("daily_qqq.csv").loc[:END]   # same last day for both
    nq = yahoo("NQ=F").reindex(spy.index).ffill()
    dv_spy = (yahoo("SPY") * yahoo("SPY", "Volume")).rolling(20).mean()
    dv_qqq = (yahoo("QQQ") * yahoo("QQQ", "Volume")).rolling(20).mean()
    ratio = (dv_qqq / dv_spy).loc[START:END].median()
    L_liq = round(-10.0 * float(ratio), 2)
    share = float((spy.loc[START:END, "gex_low"] <= -10).mean())
    L_freq = round(float(qqq.loc[START:END, "gex_low"].quantile(share)), 2)

    # NQ moves are priced on SPY's trading days; QQQ signals are read on QQQ's (same calendar)
    cross = score(nq, signals(spy, -10.0))
    native = score(nq, signals(qqq, L_liq))
    native_freq = score(nq, signals(qqq, L_freq))
    years = sorted(set(cross["per_year"]) | set(native["per_year"]))
    yrs_won = sum(1 for y in years if native["per_year"].get(y) is not None and cross["per_year"].get(y) is not None
                  and native["per_year"][y] > cross["per_year"][y])
    outperforms = bool(native["mean_over_worst"] > cross["mean_over_worst"] and native["in_sharpe"] > cross["in_sharpe"]
                       and native["mean"] >= cross["mean"] and yrs_won >= 3)
    overlap = len(set(native["entries"]) & set(cross["entries"]))
    out = {"L_liquidity_bn": L_liq, "qqq_to_spy_dollar_volume": round(float(ratio), 3), "L_frequency_bn": L_freq,
           "cross": cross, "native": native, "native_frequency_level": native_freq, "native_years_won": yrs_won,
           "same_entry_days": overlap, "native_outperforms": outperforms}
    (ROOT / "results_nq_native.json").write_text(json.dumps(out, indent=1, default=str))
    L = [f"# Test 5 — NQ futures: QQQ's own signals vs SPY's signals (2022–26, daily closes, no costs) — native outperforms: {'YES' if outperforms else 'NO'}", "",
         f"QQQ level (liquidity-matched): {L_liq}bn — QQQ trades {round(float(ratio) * 100)}% of SPY's daily $ volume. Frequency-matched level (robustness): {L_freq}bn.", "",
         "| Signals | Trades | Add-ons | Avg per trade | Median | Won | Worst during a trade | Avg ÷ worst | Sharpe in-trade days | Random did as well | Days held | Open trade now |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for nm, s in (("Cross: SPY gamma −10bn + SPY put/call", cross), (f"Native: QQQ gamma {L_liq}bn + QQQ put/call", native),
                  (f"Native, frequency level {L_freq}bn", native_freq)):
        L.append(f"| {nm} | {s['n']} | {s['adds']} | {s['mean']}% | {s['median']}% | {s['won']}% | {s['worst']}% | {s['mean_over_worst']} | {s['in_sharpe']} | {s['random_p']} | {s['avg_days']} | {s['open_pct']}% |")
    L += ["", "| Year | Cross | Native | Native (frequency level) |", "|---|---|---|---|"]
    for y in years:
        L.append(f"| {y} | {cross['per_year'].get(y, '–')}% | {native['per_year'].get(y, '–')}% | {native_freq['per_year'].get(y, '–')}% |")
    L += ["", f"Native beat cross in {yrs_won} of {len(years)} years. Entry days in common: {overlap}."]
    (ROOT / "results_nq_native.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
