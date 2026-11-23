"""Test 9 — NQ entered at SPY's put wall instead of at −10bn gamma (rules: claude/putwall-teste-pre-registo.md).
Exit and add-on unchanged (exact-level exit at the flip; add-on at the close below the entry with put/call z >= 1).
17 Nov 2023 -> 17 Nov 2026, daily, no costs."""
import json

import numpy as np
import pandas as pd

from backtest import ROOT
from exact_level import daily_account, level_price, per_trade, trades as trades_10bn, yahoo
from metrics import stats as acct_stats

START, END = "2023-11-17", "2026-11-17"
rng = np.random.default_rng(9)


def trades_wall(d, col, need_neg=False, add=True):
    prev = d["pc_volume"].shift(1).rolling(60)
    z = (d["pc_volume"] - prev.mean()) / prev.std()
    out, pos = [], None
    for i, (t, r) in enumerate(d.iterrows()):
        if pos is None:
            w = r[col]
            if pd.notna(w) and r["spy_low"] <= w and (not need_neg or r["gex_low"] < 0):
                px = r["open"] if r["open"] < w else w
                pos = {"e": t, "e_px": float(min(max(px, r["spy_low"]), r["spy_high"])), "a": None, "a_px": None}
            continue
        if r["gex_high"] >= 0:
            fp = r["flip"] if pd.notna(r["flip"]) else level_price(r, 0.0)
            px = r["open"] if (fp is None or fp <= r["open"]) else fp
            out.append({**pos, "x": t, "x_px": float(min(max(px, r["spy_low"]), r["spy_high"])), "open": False}); pos = None
            continue
        if add and pos["a"] is None and r["spy_close"] < pos["e_px"] and z.iloc[i] >= 1:
            pos["a"], pos["a_px"] = t, r["spy_close"]
    if pos:
        out.append({**pos, "x": d.index[-1], "x_px": d["spy_close"].iloc[-1], "open": True})
    return [s for s in out if s["e"] >= pd.Timestamp(START)]


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index().loc[:END]
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall", "put_wall_oi"]])
    spy_y = yahoo("SPY")
    d["open"] = spy_y["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    nq_ret, spy_ret = nq.pct_change(), d["spy_close"].pct_change()
    win = nq_ret.loc["2022-01-03":END].dropna().index
    beta = float(np.cov(nq_ret.loc[win], spy_ret.loc[win].fillna(0))[0, 1] / spy_ret.loc[win].var())
    to_nq = lambda t, px: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
    idx = d.loc[START:END].index
    rf, spy_ex = rf_all.loc[idx], spy_tr.pct_change().loc[idx] - rf_all.loc[idx]
    have = d.loc[START:END, "put_wall"].notna().mean()

    versions = {"P — enter at the put wall": trades_wall(d, "put_wall"),
                "P-neg — put wall, only if gamma < 0": trades_wall(d, "put_wall", True),
                "P-OI — put wall by open interest": trades_wall(d, "put_wall_oi"),
                "Current rule — enter at −10bn": [s for s in trades_10bn(d, True) if s["e"] >= pd.Timestamp(START)]}
    cum = np.log(nq.loc[START:END]).to_numpy()
    out, L = {"beta_nq_to_spy": round(beta, 2), "put_wall_days_covered": round(float(have) * 100, 1)}, []
    L += [f"# Test 9 — NQ entered at SPY's put wall vs at −10bn ({START} → {END}, daily, no costs)", "",
          f"Put wall found for {out['put_wall_days_covered']}% of days. NQ fills = NQ close moved by {round(beta, 2)} × SPY's move from the fill to the close.", "",
          "| Version | Trades | Avg per trade | Won | Avg days | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, tr in versions.items():
        acct = daily_account(d, tr, nq, to_nq).loc[idx]
        st = acct_stats(acct, spy_ex, rf)          # futures: invested part needs no cash
        pt = per_trade(tr, to_nq)
        done = [s for s in tr if not s["open"]]
        days = np.array([max(1, d.index.get_loc(s["x"]) - d.index.get_loc(s["e"])) for s in done])
        rets = []
        for s in done:
            legs = [to_nq(s["e"], s["e_px"])] + ([to_nq(s["a"], s["a_px"])] if s["a"] is not None else [])
            x = to_nq(s["x"], s["x_px"])
            rets.append(np.mean([x / p - 1 for p in legs]))
        p = None
        if len(done):
            target, hits = np.mean(rets), 0
            for _ in range(10000):
                st0 = rng.integers(0, len(cum) - days.max() - 1, len(days))
                hits += np.expm1(cum[st0 + days] - cum[st0]).mean() >= target
            p = round(hits / 10000, 4)
        out[name] = {**st, **pt, "avg_days": round(float(days.mean()), 1) if len(days) else None, "random_p": p,
                     "trades": [{"entry": str(s["e"].date()), "entry_spy": round(s["e_px"], 2), "add": str(s["a"].date()) if s["a"] is not None else None,
                                 "exit": str(s["x"].date()), "exit_spy": round(s["x_px"], 2), "open": s["open"]} for s in tr]}
        L.append(f"| {name} | {pt['n']} | {pt['avg_pct']}% | {pt['won_pct']}% | {out[name]['avg_days']} | {st['cagr_pct']}% | {st['sharpe']} | "
                 f"{st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {p} |")
    P, C = out["P — enter at the put wall"], out["Current rule — enter at −10bn"]
    passP = bool(P["alpha_pct_yr"] > 0 and P["alpha_t"] >= 2 and P["random_p"] is not None and P["random_p"] < 0.10)
    better = bool(passP and P["alpha_t"] > C["alpha_t"])
    out["pass_P"], out["better_than_current"] = passP, better
    L += ["", f"Rule P: {'PASS' if passP else 'FAIL'} (alpha t ≥ 2 and random p < 0.10). Better than the current rule: {'yes' if better else 'no'}."]
    (ROOT / "results_putwall.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_putwall.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
