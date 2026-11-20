"""Tests 6-8 — market-neutral versions of the JP + Put/Call rule (rules: claude/thetadata-teste-pre-registo.md, 20 Nov 2026).

Signals: exact-level fills (entry at the -10bn price, exit at the flip, add-on at the close below the entry when SPY put/call
z >= 1), from research/exact_level.py. Instruments: NQ=F and ES=F (Yahoo), intraday fills mapped from SPY by beta.
2022-01-03 -> 2026-11-17, daily, no costs. Futures returns are already over cash.
Run:  python neutral.py          (Test 6 only)
      python neutral.py --all    (Tests 6, 7 and 8)"""
import json
import sys

import numpy as np
import pandas as pd

from backtest import ROOT
from exact_level import trades, yahoo
from metrics import stats as acct_stats

START, END = "2022-01-03", "2026-11-17"
rng = np.random.default_rng(20)


def trailing_beta(y, x, n=60):
    """beta of y on x over the previous n days (known before each day)."""
    cov = (y * x).rolling(n).mean() - y.rolling(n).mean() * x.rolling(n).mean()
    return (cov / x.rolling(n).var(ddof=0)).shift(1)


def leg_daily(d, tr, close, fill):
    """Per-day return of each leg (weight 0.5 per unit), as a DataFrame column per leg, plus leg metadata."""
    cols, meta = {}, []
    for k, s in enumerate(tr):
        for j, (t0, px0) in enumerate([(s["e"], s["e_px"])] + ([(s["a"], s["a_px"])] if s["a"] is not None else [])):
            p0, x, px1 = fill(t0, px0), s["x"], fill(s["x"], s["x_px"])
            days = d.index[(d.index >= t0) & (d.index <= x)]
            r = pd.Series(0.0, index=d.index)
            for i, day in enumerate(days):
                a = p0 if i == 0 else close[days[i - 1]]
                b = px1 if day == x else close[day]
                r[day] = b / a - 1
            cols[f"{k}_{j}"] = r
            meta.append({"key": f"{k}_{j}", "trade": k, "start": t0, "end": x, "ret": px1 / p0 - 1, "days": len(days) - 1})
    return pd.DataFrame(cols), meta


def pair_test(d, tr, nq, es, fill_nq, fill_es, beta_series, fixed=None):
    Ln, meta = leg_daily(d, tr, nq["Close"], fill_nq)
    Le, _ = leg_daily(d, tr, es["Close"], fill_es)
    daily = pd.Series(0.0, index=d.index)
    per_trade = {}
    for m in meta:
        b = fixed if fixed is not None else float(beta_series.get(m["start"], 1.25))
        daily += 0.5 * (Ln[m["key"]] - b * Le[m["key"]])
        nret = Ln[m["key"]].add(1).prod() - 1
        eret = Le[m["key"]].add(1).prod() - 1
        per_trade.setdefault(m["trade"], []).append((nret - b * eret, m["days"], b))
    trade_ret = [np.mean([x[0] for x in v]) for v in per_trade.values()]
    holds = [max(1, v[0][1]) for v in per_trade.values()]
    return daily, trade_ret, holds, [tr[k] for k in per_trade]


def random_pair(nq_c, es_c, beta_series, holds, target, reps=10000):
    n, a, e, b = len(nq_c), nq_c.to_numpy(), es_c.to_numpy(), beta_series.fillna(1.25).to_numpy()
    hits = 0
    h_arr = np.array(holds)
    for _ in range(reps):
        h = rng.choice(h_arr, len(h_arr)); s = rng.integers(60, n - h.max() - 1, len(h_arr))
        r = (a[s + h] / a[s] - 1) - b[s] * (e[s + h] / e[s] - 1)
        hits += r.mean() >= target
    return round(hits / reps, 4)


def yearly(trade_ret, tlist):
    df = pd.DataFrame({"y": [t["e"].year for t in tlist], "r": trade_ret})
    return {str(y): round(g["r"].mean() * 100, 2) for y, g in df.groupby("y")}


def main(run_all):
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index().loc[:END]
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill(); es = yahoo("ES=F").reindex(d.index).ffill()
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = (yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252)
    r_nq, r_es, r_spy = nq["Close"].pct_change(), es["Close"].pct_change(), d["spy_close"].pct_change()
    w = r_spy.loc[START:END]
    b_nq_spy = float(np.cov(r_nq.loc[START:END].fillna(0), w.fillna(0))[0, 1] / w.var())
    b_es_spy = float(np.cov(r_es.loc[START:END].fillna(0), w.fillna(0))[0, 1] / w.var())
    fill_nq = lambda t, px: nq.at[t, "Close"] * (1 + b_nq_spy * (px / d.at[t, "spy_close"] - 1))
    fill_es = lambda t, px: es.at[t, "Close"] * (1 + b_es_spy * (px / d.at[t, "spy_close"] - 1))
    beta_ne = trailing_beta(r_nq.fillna(0), r_es.fillna(0))
    tr = trades(d, True)
    idx = d.loc[START:END].index
    rf = rf_all.loc[idx]
    spy_ex = spy_tr.pct_change().loc[idx] - rf
    out, L = {"beta_nq_to_spy": round(b_nq_spy, 2), "beta_es_to_spy": round(b_es_spy, 2)}, []

    # ---------------- Test 6: hedged pair during signals
    L += ["# Market-neutral versions of the JP + Put/Call rule (2022-01-03 → 2026-11-17, daily, no costs)", "",
          "## Test 6 — long MNQ / short MES during signals (hedge = NQ's 60-day beta to ES at entry)", "",
          "| Hedge | Trades | Avg pair return per trade | Won | Random did as well | Beta vs SPY | Pair return/yr | Sharpe | Worst fall | Alpha t |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    res6 = {}
    for name, fixed in (("60-day beta (rule)", None), ("fixed 1.25", 1.25)):
        daily, tret, holds, tl = pair_test(d, tr, nq, es, fill_nq, fill_es, beta_ne, fixed)
        done = [r for r, t in zip(tret, tl) if not t["open"]]
        bser = beta_ne if fixed is None else pd.Series(fixed, index=d.index)
        p = random_pair(nq["Close"], es["Close"], bser, holds, np.mean(done))
        st = acct_stats(daily.loc[idx], spy_ex, rf)
        yr = yearly(done, [t for t in tl if not t["open"]])
        res6[name] = {"n": len(done), "avg_pct": round(np.mean(done) * 100, 2), "won_pct": int(round(np.mean(np.array(done) > 0) * 100)),
                      "random_p": p, "per_year": yr, **st, "avg_hedge": round(float(np.mean([beta_ne.get(t["e"], 1.25) for t in tl])), 2) if fixed is None else 1.25}
        r = res6[name]
        L.append(f"| {name} | {r['n']} | {r['avg_pct']}% | {r['won_pct']}% | {r['random_p']} | {r['beta']} | {r['cagr_pct']}% | {r['sharpe']} | {r['max_dd_pct']}% | {r['alpha_t']} |")
    r = res6["60-day beta (rule)"]
    yrs_pos = sum(1 for v in r["per_year"].values() if v > 0)
    pass6 = bool(r["avg_pct"] > 0 and r["random_p"] < 0.10 and yrs_pos >= 3 and -0.2 <= r["beta"] <= 0.2)
    L += ["", "Average pair return per trade by entry year: " + ", ".join(f"{y}: {v}%" for y, v in r["per_year"].items()),
          f"Average hedge ratio used: {r['avg_hedge']}. Positive years: {yrs_pos} of {len(r['per_year'])}.", "",
          f"**Test 6: {'PASS' if pass6 else 'FAIL'}**"]
    out["test6"] = res6; out["test6_pass"] = pass6

    if run_all:
        # strategy NQ legs (weights 0.5 per unit) — same as the live scorecard
        Ln, meta = leg_daily(d, tr, nq["Close"], fill_nq)
        strat = (0.5 * Ln).sum(axis=1)
        units = pd.Series(0.0, index=d.index)
        for m in meta:
            units.loc[m["start"]:m["end"]] += 0.5
        expo = units * beta_ne.fillna(1.25)                       # ES-equivalent exposure each day
        hist = expo.loc[START:].shift(1).expanding().mean()
        roll = expo.loc[START:].shift(1).rolling(252, min_periods=60).mean()
        prior = 0.33 * 0.5 * 1.25
        short = roll.fillna(hist.where(hist.index.to_series().rank() > 60)).fillna(prior).reindex(d.index).fillna(prior)

        def evaluate(ex, label):
            st = acct_stats(ex.loc[idx], spy_ex, rf)
            yr = {}
            for y in sorted(set(idx.year)):
                m = idx.year == y
                e, s_ = ex.loc[idx][m], spy_ex[m]
                yr[str(y)] = round(float((e.mean() - st["beta"] * s_.mean()) * 252 * 100), 2)
            return {**st, "alpha_by_year": yr}

        # ---------------- Test 7: neutral on average
        ex7 = strat - short * r_es.fillna(0)
        res7 = evaluate(ex7, "t7")
        # random: shift the signal windows to random start dates (same lengths), same short rule
        target = ex7.loc[idx].mean(); hits = 0; reps = 1000
        pos_idx = np.arange(len(idx))
        for _ in range(reps):
            u = pd.Series(0.0, index=idx); s_r = pd.Series(0.0, index=idx)
            for m in meta:
                h = max(1, m["days"]); st0 = rng.integers(0, len(idx) - h - 1)
                u.iloc[st0 + 1:st0 + h + 1] += 0.5
            rr = u * r_nq.loc[idx].fillna(0) - short.loc[idx] * r_es.loc[idx].fillna(0)
            hits += rr.mean() >= target
        res7["random_p"] = round(hits / reps, 3)
        res7["avg_short"] = round(float(short.loc[idx].mean()), 3)
        yp7 = sum(1 for v in res7["alpha_by_year"].values() if v > 0)
        pass7 = bool(res7["alpha_pct_yr"] > 0 and res7["alpha_t"] >= 1.5 and res7["random_p"] < 0.10 and yp7 >= 3)
        out["test7"] = res7; out["test7_pass"] = pass7

        # ---------------- Test 8: steady 1x ES + strategy NQ on top
        ex8 = r_es.fillna(0) + strat
        res8 = evaluate(ex8, "t8")
        es_only = acct_stats(r_es.fillna(0).loc[idx], spy_ex, rf)
        res8["sharpe_es_only"] = es_only["sharpe"]; res8["avg_leverage"] = round(float(1 + units.loc[idx].mean()), 2)
        yp8 = sum(1 for v in res8["alpha_by_year"].values() if v > 0)
        pass8 = bool(res8["alpha_pct_yr"] > 0 and res8["alpha_t"] >= 1.5 and res8["sharpe"] > es_only["sharpe"] and yp8 >= 3)
        out["test8"] = res8; out["test8_pass"] = pass8
        out["strategy_alone"] = evaluate(strat, "s")

        L += ["", "## Test 7 — neutral on average (strategy NQ + permanent ES short = trailing 12-month average exposure)", "",
              "| Version | Return/yr | Volatility | Sharpe | Worst fall | Beta vs SPY | Alpha/yr | Alpha t | Random did as well |", "|---|---|---|---|---|---|---|---|---|",
              f"| Neutral on average | {res7['cagr_pct']}% | {res7['vol_pct']}% | {res7['sharpe']} | {res7['max_dd_pct']}% | {res7['beta']} | {res7['alpha_pct_yr']}% | {res7['alpha_t']} | {res7['random_p']} |",
              "", f"Average permanent short: {res7['avg_short']}× ES. Alpha by year: " + ", ".join(f"{y}: {v}%" for y, v in res7["alpha_by_year"].items()),
              f"**Test 7: {'PASS' if pass7 else 'FAIL'}**", "",
              "## Test 8 — steady 1× ES long + strategy NQ on top", "",
              "| Version | Return/yr | Volatility | Sharpe | Worst fall | Beta vs SPY | Alpha/yr | Alpha t |", "|---|---|---|---|---|---|---|---|",
              f"| 1× ES + NQ signals | {res8['cagr_pct']}% | {res8['vol_pct']}% | {res8['sharpe']} | {res8['max_dd_pct']}% | {res8['beta']} | {res8['alpha_pct_yr']}% | {res8['alpha_t']} |",
              f"| 1× ES only | {es_only['cagr_pct']}% | {es_only['vol_pct']}% | {es_only['sharpe']} | {es_only['max_dd_pct']}% | {es_only['beta']} | {es_only['alpha_pct_yr']}% | {es_only['alpha_t']} |",
              "", f"Average leverage: {res8['avg_leverage']}×. Alpha by year: " + ", ".join(f"{y}: {v}%" for y, v in res8["alpha_by_year"].items()),
              f"**Test 8: {'PASS' if pass8 else 'FAIL'}**"]
    (ROOT / "results_neutral.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_neutral.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main("--all" in sys.argv)
