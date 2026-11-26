"""Test 12 — exit at the opposite wall instead of the gamma flip (rules: claude/saida-nas-walls-teste-pre-registo.md).
NQ, no add-on, daily, no costs. Periods as Test 11."""
import json

import numpy as np
import pandas as pd

from backtest import ROOT
from exact_level import level_price
from metrics import stats as acct_stats
from walls_oos import PERIODS, legs_account, per_trade, yahoo

rng = np.random.default_rng(12)


def clip(px, r):
    return float(min(max(px, r["spy_low"]), r["spy_high"]))


def trades(d, side, mode, start):
    """side 'P' (long at the put wall) or 'S' (short at the call wall); mode flip / wall / first / wall10."""
    long = side == "P"
    ent, opp = ("put_wall", "call_wall") if long else ("call_wall", "put_wall")
    out, pos = [], None
    for i, (t, r) in enumerate(d.iterrows()):
        if pos is None:
            w = r[ent]
            if pd.notna(w) and ((long and r["spy_low"] <= w) or (not long and r["spy_high"] >= w)):
                px = r["open"] if ((long and r["open"] < w) or (not long and r["open"] > w)) else w
                pos = {"e": t, "i": i, "e_px": clip(px, r), "a": None, "a_px": None}
            continue
        lv = []                                   # exit levels live today
        if mode in ("flip", "first"):
            if (long and r["gex_high"] >= 0) or (not long and r["gex_low"] <= 0):
                fp = r["flip"] if pd.notna(r["flip"]) else level_price(r, 0.0)
                lv.append(fp if fp is not None else r["open"])
        if mode in ("wall", "first", "wall10") and pd.notna(r[opp]):
            w = r[opp]
            if (long and r["spy_high"] >= w) or (not long and r["spy_low"] <= w):
                lv.append(w)
        if lv:
            x = min(lv) if long else max(lv)      # the level price reaches first
            px = r["open"] if ((long and r["open"] >= x) or (not long and r["open"] <= x)) else x
            out.append({**pos, "x": t, "x_px": clip(px, r), "open": False}); pos = None
            continue
        if mode == "wall10" and i - pos["i"] >= 10:
            out.append({**pos, "x": t, "x_px": float(r["spy_close"]), "open": False}); pos = None
    if pos:
        out.append({**pos, "x": d.index[-1], "x_px": float(d["spy_close"].iloc[-1]), "open": True})
    return [s for s in out if s["e"] >= pd.Timestamp(start)]


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall"]]).join(cw[["call_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    spy = d["spy_close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    nq_ret, spy_ret = nq.pct_change(), spy.pct_change()
    names = {"flip": "Flip (current)", "wall": "Opposite wall", "first": "Wall or flip, first", "wall10": "Wall, 10-day limit"}
    out, L = {}, ["# Test 12 — exit at the opposite wall vs the gamma flip (NQ, no add-on, daily, no costs)", ""]
    for per, (a, b) in PERIODS.items():
        idx = d.loc[a:b].index
        win = nq_ret.loc[a:b].dropna().index
        beta = float(np.cov(nq_ret.loc[win], spy_ret.loc[win].fillna(0))[0, 1] / spy_ret.loc[win].var())
        conv = lambda t, px, beta=beta: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
        rf = rf_all.loc[idx].fillna(0)
        spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
        cnq = np.log(nq.loc[a:b]).to_numpy()
        dd = d.loc[:b]
        L += [f"## {'Out of sample' if per == 'oos' else 'Original period'}: {a} → {b}", "",
              "| Strategy | Exit | Trades | EV per trade | Won | Avg days | Longest | Worst trade | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for side, sign, label in (("P", 1, "Put wall long"), ("S", -1, "Call wall short")):
            for mode, nm in names.items():
                tr = trades(dd, side, mode, a)
                acct = legs_account(d.index, tr, nq, conv, sign, lambda s: 2.0).loc[idx].fillna(0)
                st = acct_stats(acct, spy_ex, rf)
                rets = np.array([r for _, r in per_trade(tr, [(conv, sign, lambda s: 1.0)])])
                done = [s for s in tr if not s["open"]]
                days = np.array([max(1, d.index.get_loc(s["x"]) - d.index.get_loc(s["e"])) for s in done])
                hits = 0
                for _ in range(10000):
                    k = rng.integers(0, len(cnq) - days.max() - 1, len(days))
                    hits += np.expm1(sign * (cnq[k + days] - cnq[k])).mean() >= rets.mean()
                res = {**st, "n": len(done), "ev_pct": round(rets.mean() * 100, 2), "won_pct": int(round((rets > 0).mean() * 100)),
                       "avg_days": round(float(days.mean()), 1), "max_days": int(days.max()), "worst_pct": round(rets.min() * 100, 2),
                       "random_p": round(hits / 10000, 4), "still_open": len(tr) - len(done)}
                out[f"{per}|{side}|{mode}"] = res
                L.append(f"| {label} | {nm} | {res['n']} | {res['ev_pct']}% | {res['won_pct']}% | {res['avg_days']} | {res['max_days']} | {res['worst_pct']}% | "
                         f"{st['cagr_pct']}% | {st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {res['random_p']} |")
        L.append("")
    v = {}
    for side in ("P", "S"):
        for mode in ("wall", "first", "wall10"):
            wins = [out[f"{p}|{side}|{mode}"]["sharpe"] > out[f"{p}|{side}|flip"]["sharpe"] and
                    out[f"{p}|{side}|{mode}"]["alpha_t"] > out[f"{p}|{side}|flip"]["alpha_t"] for p in PERIODS]
            v[f"{side}|{mode}"] = "beats the flip" if all(wins) else ("mixed: keep the flip" if any(wins) else "worse: keep the flip")
    out["verdicts"] = v
    L += ["## Verdicts (must beat the flip on Sharpe AND alpha t in both periods)", ""] + [f"- {k}: {x}" for k, x in v.items()]
    (ROOT / "results_exit_walls.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_exit_walls.md").write_text("\n".join(L))
    print("\n".join(L))




def combined():
    """Put wall long + call wall short in one account, half the capital each, for each pair of exit rules."""
    import sys
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall"]]).join(cw[["call_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    nq_ret, spy_ret = nq.pct_change(), d["spy_close"].pct_change()
    pairs = [("flip", "flip", "Both flip (current)"), ("first", "first", "Both wall-or-flip first"),
             ("wall", "first", "Long: call wall only · Short: first"), ("wall", "flip", "Long: call wall only · Short: flip")]
    per_all = {**PERIODS, "all": ("2018-11-19", "2026-11-17")}
    out, L = {}, ["# Test 12b — both wall strategies in ONE account (half the capital each), NQ, no add-on, daily, no costs", "",
                  "| Period | Exits | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Days in a trade | Days long AND short |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
    for per, (a, b) in per_all.items():
        idx = d.loc[a:b].index
        win = nq_ret.loc[a:b].dropna().index
        beta = float(np.cov(nq_ret.loc[win], spy_ret.loc[win].fillna(0))[0, 1] / spy_ret.loc[win].var())
        conv = lambda t, px, beta=beta: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
        rf = rf_all.loc[idx].fillna(0)
        spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
        dd = d.loc[:b]
        for mp, ms, nm in pairs:
            P, S = trades(dd, "P", mp, a), trades(dd, "S", ms, a)
            acct = (legs_account(d.index, P, nq, conv, 1) + legs_account(d.index, S, nq, conv, -1)).loc[idx].fillna(0)
            st = acct_stats(acct, spy_ex, rf)
            inp = lambda tr: pd.Series(False, index=idx) | pd.Series(idx, index=idx).apply(lambda t: any(s["e"] <= t <= s["x"] for s in tr))
            ip, is_ = inp(P), inp(S)
            res = {**st, "in_trade_pct": round(float((ip | is_).mean() * 100)), "both_pct": round(float((ip & is_).mean() * 100))}
            out[f"{per}|{nm}"] = res
            L.append(f"| {per} | {nm} | {st['cagr_pct']}% | {st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {res['in_trade_pct']}% | {res['both_pct']}% |")
    (ROOT / "results_exit_walls_combined.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_exit_walls_combined.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    import sys
    combined() if "combined" in sys.argv else main()
