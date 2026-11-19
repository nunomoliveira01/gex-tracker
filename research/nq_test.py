"""SPY signals, Nasdaq instrument: same entries/add-ons/exits as 'JP + Put/Call Ratio' (from SPY gamma and SPY put/call),
but the position is in Nasdaq (QQQ / NQ futures) instead of SPY. Daily closes, 2022–2026, no costs."""
import json

import numpy as np
import pandas as pd
import yfinance as yf

from backtest import ROOT

rng = np.random.default_rng(11)


def closes(sym):
    h = yf.Ticker(sym).history(start="2018-01-01", interval="1d", auto_adjust=False)["Close"]
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h


def signals(d, level=-10.0, zmin=1.0):
    prev = d["pc_volume"].shift(1).rolling(60)
    d["z"] = (d["pc_volume"] - prev.mean()) / prev.std()
    out, pos = [], None
    for t, r in d.iterrows():
        if pos is None:
            if r["gex_low"] <= level:
                pos = {"e": t, "a": None}
            continue
        if r["gex_close"] >= 0:
            out.append({**pos, "x": t, "open": False}); pos = None; continue
        if pos["a"] is None and r["spy_close"] < d.at[pos["e"], "spy_close"] and r["z"] >= zmin:
            pos["a"] = t
    if pos:
        out.append({**pos, "x": d.index[-1], "open": True})
    return out


def run(px, sig):
    """Per trade: % return on the capital used (1 unit, 2 if added), worst point during the trade."""
    rows = []
    for s in sig:
        legs = [s["e"]] + ([s["a"]] if s["a"] is not None else [])
        ret = np.mean([px[s["x"]] / px[l] - 1 for l in legs])
        path = px.loc[s["e"]:s["x"]]
        worst = min((path.loc[l:] / px[l] - 1).min() for l in legs)
        rows.append({"entry": s["e"], "ret": ret, "worst": worst, "days": len(path) - 1, "add": s["a"] is not None, "open": s["open"]})
    return pd.DataFrame(rows)


def daily_overlay(px, sig, cash=0.03 / 252):
    """Daily returns while in a trade (units held) vs other days: is the timing better than average?"""
    r = px.pct_change().fillna(0)
    units = pd.Series(0.0, index=px.index)
    for s in sig:
        units.loc[px.index[px.index.get_loc(s["e"]) + 1]:s["x"]] += 1
        if s["a"] is not None:
            units.loc[px.index[px.index.get_loc(s["a"]) + 1]:s["x"]] += 1
    ex = r - cash
    on, off = ex[units > 0], ex[units == 0]
    sh = lambda x: float(x.mean() / x.std() * np.sqrt(252)) if len(x) > 2 else None
    return {"days_in": int((units > 0).sum()), "in_mean_pct": round(on.mean() * 100, 3), "in_vol_pct": round(on.std() * np.sqrt(252) * 100, 1),
            "in_sharpe": round(sh(on), 2), "out_sharpe": round(sh(off), 2), "all_sharpe": round(sh(ex), 2)}


def random_p(px, res, reps=5000):
    hold = res["days"].clip(lower=1).to_numpy()
    a = px.to_numpy()
    target, hits = res["ret"].mean(), 0
    for _ in range(reps):
        h = rng.choice(hold, len(hold)); s = rng.integers(0, len(a) - h.max() - 1, len(hold))
        hits += (a[s + h] / a[s] - 1).mean() >= target
    return round(hits / reps, 3)


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    sig = [s for s in signals(d) if s["e"] >= pd.Timestamp("2022-01-01")]
    inst = {"SPY": d["spy_close"], "QQQ (Nasdaq 100 ETF)": closes("QQQ").reindex(d.index).ffill(),
            "NQ futures (front month)": closes("NQ=F").reindex(d.index).ffill(), "ES futures (front month)": closes("ES=F").reindex(d.index).ffill()}
    out, L = {}, ["# SPY signals traded in Nasdaq vs S&P (JP + Put/Call Ratio rule, 2022–26, daily closes, no costs)", "",
                  "| Instrument | Trades | Avg per trade | Median | Won | Worst during a trade | Avg ÷ worst | Random did as well | Sharpe in-trade days | Sharpe other days | Sharpe always held |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, px in inst.items():
        px = px.dropna().loc["2022-01-01":]   # same window as the signals (random dates and "other days" too)
        res = run(px, sig); done = res[~res["open"]]
        o = daily_overlay(px, sig)
        stats = {"n": len(done), "mean": round(done["ret"].mean() * 100, 2), "median": round(done["ret"].median() * 100, 2),
                 "won": round((done["ret"] > 0).mean() * 100), "worst": round(res["worst"].min() * 100, 2),
                 "random_p": random_p(px, done), **o,
                 "per_year": {str(y): round(g["ret"].mean() * 100, 2) for y, g in done.groupby(done["entry"].dt.year)},
                 "open_trade_pct": round(res[res["open"]]["ret"].iloc[0] * 100, 2) if res["open"].any() else None}
        stats["mean_over_worst"] = round(stats["mean"] / abs(stats["worst"]), 3)
        out[name] = stats
        L.append(f"| {name} | {stats['n']} | {stats['mean']}% | {stats['median']}% | {stats['won']}% | {stats['worst']}% | {stats['mean_over_worst']} | {stats['random_p']} | {o['in_sharpe']} | {o['out_sharpe']} | {o['all_sharpe']} |")
    L += ["", "| Year | " + " | ".join(out) + " |", "|---|" + "---|" * len(out)]
    years = sorted({y for v in out.values() for y in v["per_year"]})
    for y in years:
        L.append(f"| {y} | " + " | ".join(f"{v['per_year'].get(y, '–')}%" for v in out.values()) + " |")
    L += ["", "Open trade now: " + ", ".join(f"{k} {v['open_trade_pct']}%" for k, v in out.items())]
    (ROOT / "results_nq.md").write_text("\n".join(L))
    (ROOT / "results_nq.json").write_text(json.dumps(out, indent=1, default=str))
    print("\n".join(L))


if __name__ == "__main__":
    main()
