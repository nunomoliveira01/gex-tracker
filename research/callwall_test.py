"""Test 10 — SHORT NQ at SPY's call wall, exact mirror of Test 9 (rules: claude/callwall-teste-pre-registo.md).
17 Nov 2023 -> 17 Nov 2026, daily, no costs."""
import json

import numpy as np
import pandas as pd

from backtest import ROOT
from exact_level import level_price, yahoo
from metrics import stats as acct_stats
from putwall_test import trades_wall

START, END = "2023-11-17", "2026-11-17"
rng = np.random.default_rng(10)


def trades_short(d, col, need_pos=False, max_days=None):
    prev = d["pc_volume"].shift(1).rolling(60)
    z = (d["pc_volume"] - prev.mean()) / prev.std()
    out, pos = [], None
    for i, (t, r) in enumerate(d.iterrows()):
        if pos is None:
            w = r[col]
            if pd.notna(w) and r["spy_high"] >= w and (not need_pos or r["gex_high"] > 0):
                px = r["open"] if r["open"] > w else w
                pos = {"e": t, "i": i, "e_px": float(min(max(px, r["spy_low"]), r["spy_high"])), "a": None, "a_px": None}
            continue
        if r["gex_low"] <= 0:                                   # price reached the flip from above
            fp = r["flip"] if pd.notna(r["flip"]) else level_price(r, 0.0)
            px = r["open"] if (fp is None or fp >= r["open"]) else fp
            out.append({**pos, "x": t, "x_px": float(min(max(px, r["spy_low"]), r["spy_high"])), "open": False}); pos = None
            continue
        if max_days and i - pos["i"] >= max_days:
            out.append({**pos, "x": t, "x_px": float(r["spy_close"]), "open": False}); pos = None
            continue
        if pos["a"] is None and r["spy_close"] > pos["e_px"] and z.iloc[i] <= -1:
            pos["a"], pos["a_px"] = t, r["spy_close"]
    if pos:
        out.append({**pos, "x": d.index[-1], "x_px": d["spy_close"].iloc[-1], "open": True})
    return [s for s in out if s["e"] >= pd.Timestamp(START)]


def account(d, tr, inst, conv, sign):
    r = pd.Series(0.0, index=d.index)
    for s in tr:
        legs = [(s["e"], conv(s["e"], s["e_px"]))] + ([(s["a"], conv(s["a"], s["a_px"]))] if s["a"] is not None else [])
        x, x_px = s["x"], conv(s["x"], s["x_px"])
        for t0, p0 in legs:
            days = d.index[(d.index >= t0) & (d.index <= x)]
            for k, day in enumerate(days):
                start = p0 if k == 0 else inst[days[k - 1]]
                end = x_px if day == x else inst[day]
                r[day] += 0.5 * sign * (end / start - 1)
    return r


def trade_rets(tr, conv, sign):
    out = []
    for s in tr:
        if s["open"]:
            continue
        legs = [conv(s["e"], s["e_px"])] + ([conv(s["a"], s["a_px"])] if s["a"] is not None else [])
        x = conv(s["x"], s["x_px"])
        out.append(np.mean([sign * (x / p - 1) for p in legs]))
    return np.array(out)


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index().loc[:END]
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(cw[["call_wall", "call_wall_oi"]]).join(pw[["put_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    nq_ret, spy_ret = nq.pct_change(), d["spy_close"].pct_change()
    win = nq_ret.loc["2022-01-03":END].dropna().index
    beta = float(np.cov(nq_ret.loc[win], spy_ret.loc[win].fillna(0))[0, 1] / spy_ret.loc[win].var())
    conv = lambda t, px: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
    idx = d.loc[START:END].index
    rf, spy_ex = rf_all.loc[idx], spy_tr.pct_change().loc[idx] - rf_all.loc[idx]
    cum = np.log(nq.loc[START:END]).to_numpy()
    versions = {"S — short at the call wall": (trades_short(d, "call_wall"), -1),
                "S-pos — call wall, only if gamma > 0": (trades_short(d, "call_wall", need_pos=True), -1),
                "S-OI — call wall by open interest": (trades_short(d, "call_wall_oi"), -1),
                "S-5d — rule S, exit after 5 days at most": (trades_short(d, "call_wall", max_days=5), -1),
                "P — long at the put wall (Test 9)": (trades_wall(d, "put_wall"), 1)}
    out = {"beta_nq_to_spy": round(beta, 2), "call_wall_days_covered": round(float(d.loc[START:END, "call_wall"].notna().mean()) * 100, 1)}
    L = [f"# Test 10 — SHORT NQ at SPY's call wall vs long at the put wall ({START} → {END}, daily, no costs)", "",
         f"Call wall found for {out['call_wall_days_covered']}% of days. NQ fills = NQ close moved by {round(beta, 2)} × SPY's move from the fill to the close.", "",
         "| Version | Trades | Avg per trade | Won | Avg days | Worst trade | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, (tr, sign) in versions.items():
        acct = account(d, tr, nq, conv, sign).loc[idx]
        st = acct_stats(acct, spy_ex, rf)
        rets = trade_rets(tr, conv, sign)
        done = [s for s in tr if not s["open"]]
        days = np.array([max(1, d.index.get_loc(s["x"]) - d.index.get_loc(s["e"])) for s in done])
        p = None
        if len(done):
            hits = 0
            for _ in range(10000):
                st0 = rng.integers(0, len(cum) - days.max() - 1, len(days))
                hits += np.expm1(sign * (cum[st0 + days] - cum[st0])).mean() >= rets.mean()
            p = round(hits / 10000, 4)
        res = {**st, "n": len(done), "avg_pct": round(rets.mean() * 100, 2) if len(rets) else None,
               "won_pct": int(round((rets > 0).mean() * 100)) if len(rets) else None,
               "worst_trade_pct": round(rets.min() * 100, 2) if len(rets) else None,
               "avg_days": round(float(days.mean()), 1) if len(days) else None, "random_p": p,
               "adds": sum(1 for s in tr if s["a"] is not None), "open_now": any(s["open"] for s in tr),
               "trades": [{"entry": str(s["e"].date()), "entry_spy": round(s["e_px"], 2), "add": str(s["a"].date()) if s["a"] is not None else None,
                           "exit": str(s["x"].date()), "exit_spy": round(s["x_px"], 2), "open": s["open"]} for s in tr]}
        out[name] = res
        L.append(f"| {name} | {res['n']} | {res['avg_pct']}% | {res['won_pct']}% | {res['avg_days']} | {res['worst_trade_pct']}% | {st['cagr_pct']}% | "
                 f"{st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {p} |")
    S = out["S — short at the call wall"]
    passS = bool(S["alpha_pct_yr"] > 0 and S["alpha_t"] >= 2 and S["random_p"] is not None and S["random_p"] < 0.10)
    out["pass_S"] = passS
    L += ["", f"Rule S: {'PASS' if passS else 'FAIL'} (alpha t ≥ 2 and random p < 0.10). Add-ons used: {S['adds']}. Open now: {S['open_now']}."]
    (ROOT / "results_callwall.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_callwall.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
