"""Test 12 — call-wall short in NQ + long defensive sectors (rules: claude/defensivos-teste-pre-registo.md)."""
import json

import numpy as np
import pandas as pd

import callwall_test as CW
from backtest import ROOT
from metrics import stats as acct_stats
from walls_oos import legs_account, per_trade, yahoo

PERIODS = {"2018–23 (out of sample)": ("2018-11-19", "2023-07-31"), "2023–26": ("2023-11-17", "2026-11-17"), "2018–26 all": ("2018-11-19", "2026-11-17")}
SECTORS = {"XLP": "consumer staples", "XLV": "healthcare", "XLE": "energy"}
rng = np.random.default_rng(12)


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(cw[["call_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    sec = {s: yahoo(s, adjust=True)["Close"].reindex(d.index).ffill() for s in SECTORS}
    spy = d["spy_close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    assert nq.loc["2018-11-19":].notna().all() and all(v.loc["2018-11-19":].notna().all() for v in sec.values())
    spy_ret = spy.pct_change()
    b60 = lambda x: (x.pct_change().rolling(60).cov(spy_ret) / spy_ret.rolling(60).var()).shift(1)
    nq_b60, sec_b60 = b60(nq), {s: b60(v) for s, v in sec.items()}
    out, L = {}, ["# Test 12 — short NQ at the call wall, alone vs paired with long defensive sectors (daily, no costs)", ""]
    for per, (a, b) in PERIODS.items():
        idx = d.loc[a:b].index
        beta_of = lambda x: float(np.cov(x.pct_change().loc[a:b].fillna(0), spy_ret.loc[a:b].fillna(0))[0, 1] / spy_ret.loc[a:b].var())
        bn, bs = beta_of(nq), {s: beta_of(v) for s, v in sec.items()}
        mk = lambda inst, beta: (lambda t, px: inst.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1)))
        cnq, csec = mk(nq, bn), {s: mk(sec[s], bs[s]) for s in SECTORS}
        rf = rf_all.loc[idx].fillna(0)
        spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
        CW.START = a
        tr = CW.trades_short(d.loc[:b], "call_wall")
        done = [s for s in tr if not s["open"]]
        days = np.array([max(1, d.index.get_loc(s["x"]) - d.index.get_loc(s["e"])) for s in done])
        third = lambda s: 1 / 3

        def betaw(s):   # basket sized to match NQ's S&P exposure, betas known at entry
            bb = np.nanmean([sec_b60[k].get(s["e"], np.nan) for k in SECTORS])
            nb = nq_b60.get(s["e"], np.nan)
            return float(nb / bb) / 3 if np.isfinite(nb) and np.isfinite(bb) and bb > 0.1 else 1 / 3

        versions = {"S — short NQ only": [],
                    "D — short NQ + long XLP/XLV/XLE (same $)": [(k, third) for k in SECTORS],
                    "D-beta — basket sized to cancel market exposure": [(k, betaw) for k in SECTORS],
                    **{f"Short NQ + long {k} ({v})": [(k, lambda s: 1.0)] for k, v in SECTORS.items()},
                    "Basket alone — long XLP/XLV/XLE during S": None}
        lnq = np.log(nq.loc[a:b]).to_numpy(); lsec = {k: np.log(sec[k].loc[a:b]).to_numpy() for k in SECTORS}
        L += [f"## {per}: {a} → {b}  (betas to SPY: NQ {bn:.2f}, " + ", ".join(f"{k} {bs[k]:.2f}" for k in SECTORS) + ")", "",
              "| Version | Trades | Avg per trade | Won | Worst trade | Return/yr | Volatility | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, basket in versions.items():
            if basket is None:     # basket alone, long
                acct = sum(legs_account(d.index, tr, sec[k], csec[k], 1, third) for k in SECTORS)
                legs = [(csec[k], 1, third) for k in SECTORS]
            else:
                acct = legs_account(d.index, tr, nq, cnq, -1)
                legs = [(cnq, -1, lambda s: 1.0)]
                for k, w in basket:
                    acct = acct + legs_account(d.index, tr, sec[k], csec[k], 1, w)
                    legs.append((csec[k], 1, w))
            acct = acct.loc[idx].fillna(0)
            st = acct_stats(acct, spy_ex, rf)
            rets = np.array([r for _, r in per_trade(tr, legs)])
            p = None
            if len(done) and name.startswith("D —"):
                hits = 0
                for _ in range(10000):
                    k0 = rng.integers(0, len(lnq) - days.max() - 1, len(days))
                    rr = -np.expm1(lnq[k0 + days] - lnq[k0]) + np.mean([np.expm1(lsec[k][k0 + days] - lsec[k][k0]) for k in SECTORS], axis=0)
                    hits += rr.mean() >= rets.mean()
                p = round(hits / 10000, 4)
            res = {**st, "n": len(rets), "avg_pct": round(rets.mean() * 100, 2), "won_pct": int(round((rets > 0).mean() * 100)),
                   "worst_trade_pct": round(rets.min() * 100, 2), "random_p": p}
            out[f"{per}: {name}"] = res
            L.append(f"| {name} | {res['n']} | {res['avg_pct']}% | {res['won_pct']}% | {res['worst_trade_pct']}% | {st['cagr_pct']}% | {st['vol_pct']}% | "
                     f"{st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {p if p is not None else '–'} |")
        L.append("")
    g = lambda per, n: out[f"{per}: {n}"]
    S, D = "S — short NQ only", "D — short NQ + long XLP/XLV/XLE (same $)"
    passD = bool(g("2018–26 all", D)["sharpe"] > g("2018–26 all", S)["sharpe"] and g("2018–26 all", D)["alpha_t"] >= 2
                 and (g("2018–26 all", D)["random_p"] if g("2018–26 all", D)["random_p"] is not None else 1) < 0.10
                 and all(g(p, D)["sharpe"] >= g(p, S)["sharpe"] for p in ("2018–23 (out of sample)", "2023–26")))
    out["pass_D"] = passD
    L += [f"Rule D (long-short better than just shorting NQ): {'PASS' if passD else 'FAIL'}."]
    (ROOT / "results_defensive.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_defensive.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
