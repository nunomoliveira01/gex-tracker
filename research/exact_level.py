"""JP + Put/Call rule with entries/exits at the exact gamma level instead of the close.

Entry: on the first day gamma reaches <= -10bn, buy at the SPY price where gamma = -10bn that day (found by
interpolating gamma between the day's high, close and low, same open interest), or at the open if SPY gapped below it.
Exit: on the first later day gamma reaches >= 0 (gamma at the day's high), sell at the flip price (gamma = 0),
or at the open if SPY gapped above it. Add-on unchanged: at the close, below the entry, put/call z >= 1 (vs 60 days).
NQ prices for intraday fills: NQ at that day's close moved by NQ's beta to SPY times SPY's move from the fill to the close.
Compared with the close-only version on the same data. 2022-01-03 -> 2026-11-17, no costs."""
import json

import numpy as np
import pandas as pd
import yfinance as yf

from backtest import ROOT
from metrics import stats as acct_stats

START, END, LEVEL = "2022-01-03", "2026-11-17", -10.0


def yahoo(sym, adjust=False):
    h = yf.Ticker(sym).history(start="2021-10-01", interval="1d", auto_adjust=adjust)
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h


def level_price(r, target):
    """SPY price where gamma = target, by linear interpolation over (low, close, high). None if not crossed."""
    pts = sorted([(r["spy_low"], r["gex_low"]), (r["spy_close"], r["gex_close"]), (r["spy_high"], r["gex_high"])])
    for (p0, g0), (p1, g1) in zip(pts, pts[1:]):
        if (g0 - target) * (g1 - target) <= 0 and g0 != g1:
            return p0 + (target - g0) * (p1 - p0) / (g1 - g0)
    return None


def trades(d, exact):
    prev = d["pc_volume"].shift(1).rolling(60)
    z = (d["pc_volume"] - prev.mean()) / prev.std()
    out, pos = [], None
    for i, (t, r) in enumerate(d.iterrows()):
        if pos is None:
            if r["gex_low"] <= LEVEL:
                if exact:
                    lp = level_price(r, LEVEL)
                    px = r["open"] if (lp is None or lp >= r["open"]) else lp      # gap down -> fill at the open
                    px = min(max(px, r["spy_low"]), r["spy_high"])
                else:
                    px = r["spy_close"]
                pos = {"e": t, "e_px": px, "a": None, "a_px": None}
            continue
        hit = (r["gex_high"] >= 0) if exact else (r["gex_close"] >= 0)
        if hit:
            if exact:
                fp = r["flip"] if pd.notna(r["flip"]) else level_price(r, 0.0)
                px = r["open"] if (fp is None or fp <= r["open"]) else fp          # gap up -> fill at the open
                px = min(max(px, r["spy_low"]), r["spy_high"])
            else:
                px = r["spy_close"]
            out.append({**pos, "x": t, "x_px": px, "open": False}); pos = None
            continue
        if pos["a"] is None and r["spy_close"] < pos["e_px"] and z.iloc[i] >= 1:
            pos["a"], pos["a_px"] = t, r["spy_close"]
    if pos:
        out.append({**pos, "x": d.index[-1], "x_px": d["spy_close"].iloc[-1], "open": True})
    return [s for s in out if s["e"] >= pd.Timestamp(START)]


def daily_account(d, tr, inst_close, to_inst):
    """Daily return of a 2-unit account (each unit = half) holding the legs; fills at intraday prices."""
    r = pd.Series(0.0, index=d.index)
    c = inst_close
    for s in tr:
        legs = [(s["e"], to_inst(s["e"], s["e_px"]))] + ([(s["a"], to_inst(s["a"], s["a_px"]))] if s["a"] is not None else [])
        x, x_px = s["x"], to_inst(s["x"], s["x_px"])
        for t0, p0 in legs:
            days = d.index[(d.index >= t0) & (d.index <= x)]
            for k, day in enumerate(days):
                start = p0 if k == 0 else c[days[k - 1]]
                end = x_px if day == x else c[day]
                r[day] += 0.5 * (end / start - 1)
    return r


def per_trade(tr, to_inst):
    rows = []
    for s in tr:
        legs = [to_inst(s["e"], s["e_px"])] + ([to_inst(s["a"], s["a_px"])] if s["a"] is not None else [])
        x = to_inst(s["x"], s["x_px"])
        rows.append({"ret": np.mean([x / p - 1 for p in legs]), "open": s["open"]})
    df = pd.DataFrame(rows); done = df[~df["open"]]
    return {"n": int(len(done)), "avg_pct": round(done["ret"].mean() * 100, 2), "won_pct": int(round((done["ret"] > 0).mean() * 100))}


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index().loc[:END]
    spy_y = yahoo("SPY")
    d["open"] = spy_y["Open"].reindex(d.index)
    nq_y = yahoo("NQ=F").reindex(d.index).ffill()
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = (yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252)
    nq_ret, spy_ret = nq_y["Close"].pct_change(), d["spy_close"].pct_change()
    beta_nq = float(np.cov(nq_ret.loc[START:END].dropna(), spy_ret.loc[START:END].reindex(nq_ret.loc[START:END].dropna().index).fillna(0))[0, 1]
                    / spy_ret.loc[START:END].var())
    to_spy = lambda t, px: px
    to_nq = lambda t, px: nq_y.at[t, "Close"] * (1 + beta_nq * (px / d.at[t, "spy_close"] - 1))
    idx = d.loc[START:END].index
    rf = rf_all.loc[idx]
    spy_ex = spy_tr.pct_change().loc[idx] - rf
    out = {"beta_nq_to_spy": round(beta_nq, 2)}
    L = ["# Entries and exits at the exact gamma level vs at the close (JP + Put/Call rule, 2022-01-03 → 2026-11-17, no costs)", "",
         f"Exact level: entry at the SPY price where gamma = −10bn that day (or the open on a gap), exit at the flip price (gamma = 0) or the open on a gap. NQ fills = NQ close moved by beta {round(beta_nq, 2)} × SPY's move from the fill to the close.", "",
         "| Version | Instrument | Trades | Avg per trade | Won | Return/yr | Volatility | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    keep = {}
    for exact in (False, True):
        tr = trades(d, exact)
        name = "Exact level" if exact else "Close (current backtest)"
        for inst, close, conv, cash_part in (("SPY", d["spy_close"], to_spy, True), ("NQ futures", nq_y["Close"], to_nq, False)):
            acct = daily_account(d, tr, close, conv).loc[idx]
            units = pd.Series(0.0, index=d.index)
            for s in tr:
                units.loc[s["e"]:s["x"]] += 0.5
                if s["a"] is not None:
                    units.loc[s["a"]:s["x"]] += 0.5
            w = units.loc[idx].clip(upper=1)
            ex = acct - (w * rf if cash_part else 0)                       # futures: no cash cost on the invested part
            st = acct_stats(ex, spy_ex, rf)
            pt = per_trade(tr, conv)
            out[f"{name} — {inst}"] = {**st, **pt}
            if exact:
                keep[inst] = (ex, w, conv, tr)
            L.append(f"| {name} | {inst} | {pt['n']} | {pt['avg_pct']}% | {pt['won_pct']}% | {st['cagr_pct']}% | {st['vol_pct']}% | {st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} |")
        out[f"{name} trades"] = [{"entry": str(s["e"].date()), "entry_px": round(s["e_px"], 2), "add": str(s["a"].date()) if s["a"] is not None else None,
                                  "exit": str(s["x"].date()), "exit_px": round(s["x_px"], 2), "open": s["open"]} for s in tr]
    # the live page's scorecard and backtest lists use the exact-level version (it matches how the tracker trades)
    pd.DataFrame({"rf": rf, "spy_ex": spy_ex, "ours_spy_ex": keep["SPY"][0], "ours_nq_ex": keep["NQ futures"][0],
                  "units": keep["SPY"][1] * 2}).round(8).rename_axis("date").to_csv(ROOT / "data" / "strategy_daily.csv")
    tr = keep["SPY"][3]
    for fname, conv in (("spy_pc_backtest.json", to_spy), ("nq_pc_backtest.json", to_nq)):
        rows = []
        for s_ in tr:
            e, a, x = conv(s_["e"], s_["e_px"]), (conv(s_["a"], s_["a_px"]) if s_["a"] is not None else None), conv(s_["x"], s_["x_px"])
            units = [e] + ([a] if a is not None else [])
            pts = sum(x - u for u in units)
            rows.append({"entry_date": str(s_["e"].date()), "entry": round(e, 2), "add_date": str(s_["a"].date()) if s_["a"] is not None else None,
                         "add": round(a, 2) if a is not None else None, "exit_date": str(s_["x"].date()), "exit": round(x, 2),
                         "pnl": round(pts, 2), "pnl_pts": round(pts, 2), "pct": round(pts / sum(units) * 100, 2), "open": s_["open"]})
        (ROOT / "data" / fname).write_text(json.dumps(rows, indent=1))
    (ROOT / "results_exact.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_exact.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
