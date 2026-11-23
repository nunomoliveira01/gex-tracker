"""Test 11 — wall strategies out of sample (Nov 2018 -> Jul 2023) and hedged with SPY
(rules: claude/walls-oos-teste-pre-registo.md). Also writes the backtest lists the live page shows."""
import json

import numpy as np
import pandas as pd

import callwall_test as CW
import putwall_test as PW
from backtest import ROOT
from metrics import stats as acct_stats

PERIODS = {"oos": ("2018-11-19", "2023-07-31"), "is": ("2023-11-17", "2026-11-17")}
rng = np.random.default_rng(11)


def yahoo(sym, adjust=False):
    """Daily Yahoo prices from mid-2018 (exact_level.yahoo starts in Oct 2021, too late for this test)."""
    import yfinance as yf
    h = yf.Ticker(sym).history(start="2018-06-01", interval="1d", auto_adjust=adjust)
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[~h.index.duplicated()]


def legs_account(idx_all, tr, inst, conv, sign, weight=lambda s: 1.0):
    r = pd.Series(0.0, index=idx_all)
    for s in tr:
        legs = [(s["e"], conv(s["e"], s["e_px"]))] + ([(s["a"], conv(s["a"], s["a_px"]))] if s["a"] is not None else [])
        x, x_px = s["x"], conv(s["x"], s["x_px"])
        w = weight(s)
        for t0, p0 in legs:
            days = idx_all[(idx_all >= t0) & (idx_all <= x)]
            for k, day in enumerate(days):
                start = p0 if k == 0 else inst[days[k - 1]]
                end = x_px if day == x else inst[day]
                r[day] += 0.5 * w * sign * (end / start - 1)
    return r


def per_trade(tr, legs):
    """legs: list of (conv, sign, weight_fn). Return per trade on the NQ money = mean over units of the summed legs."""
    out = []
    for s in tr:
        if s["open"]:
            continue
        units = [("e", s["e_px"])] + ([("a", s["a_px"])] if s["a"] is not None else [])
        rs = []
        for key, px in units:
            t0 = s["e"] if key == "e" else s["a"]
            rs.append(sum(w(s) * sg * (conv(s["x"], s["x_px"]) / conv(t0, px) - 1) for conv, sg, w in legs))
        out.append((s["e"], np.mean(rs)))
    return out


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall", "put_wall_oi"]]).join(cw[["call_wall", "call_wall_oi"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    spy = d["spy_close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    nq_ret, spy_ret = nq.pct_change(), spy.pct_change()
    beta60 = (nq_ret.rolling(60).cov(spy_ret) / spy_ret.rolling(60).var()).shift(1)
    out, L, web = {}, [], {"P": [], "S": [], "S5": []}
    L += ["# Test 11 — wall strategies out of sample (Nov 2018 → Jul 2023) and hedged with SPY; daily, no costs", ""]
    assert nq.loc["2018-11-19":].notna().all(), "NQ prices missing"
    for per, (a, b) in PERIODS.items():
        idx = d.loc[a:b].index
        win = nq_ret.loc[a:b].dropna().index
        beta = float(np.cov(nq_ret.loc[win], spy_ret.loc[win].fillna(0))[0, 1] / spy_ret.loc[win].var())
        conv = lambda t, px, beta=beta: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
        spy_conv = lambda t, px: px
        rf = rf_all.loc[idx].fillna(0)
        spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
        dd = d.loc[:b]
        PW.START, CW.START = a, a
        P, Pn, Po = PW.trades_wall(dd, "put_wall"), PW.trades_wall(dd, "put_wall", True), PW.trades_wall(dd, "put_wall_oi")
        S, Sp, So = CW.trades_short(dd, "call_wall"), CW.trades_short(dd, "call_wall", need_pos=True), CW.trades_short(dd, "call_wall_oi")
        Pn0, Sn0, S5n0 = PW.trades_wall(dd, "put_wall", add=False), CW.trades_short(dd, "call_wall", add=False), CW.trades_short(dd, "call_wall", max_days=5, add=False)
        bn = lambda s: float(beta60.get(s["e"], beta)) if pd.notna(beta60.get(s["e"], np.nan)) else beta
        one = lambda s: 1.0
        cnq, cspy = np.log(nq.loc[a:b]).to_numpy(), np.log(spy.loc[a:b]).to_numpy()
        versions = [("P — long NQ at the put wall", P, 1, None), ("P-neg — only if gamma < 0", Pn, 1, None), ("P-OI — open-interest wall", Po, 1, None),
                    ("S — short NQ at the call wall", S, -1, None), ("S-pos — only if gamma > 0", Sp, -1, None), ("S-OI — open-interest wall", So, -1, None),
                    ("P-hedged — long NQ + short SPY, same $", P, 1, one), ("S-hedged — short NQ + long SPY, same $", S, -1, one),
                    ("P-hedged, beta-neutral", P, 1, bn), ("S-hedged, beta-neutral", S, -1, bn),
                    ("P, no add-on (1 unit = all capital)", Pn0, 1, "solo"), ("S, no add-on (1 unit = all capital)", Sn0, -1, "solo"),
                    ("S-5d, no add-on (1 unit = all capital)", S5n0, -1, "solo")]
        L += [f"## {'Out of sample' if per == 'oos' else 'Original period'}: {a} → {b} (NQ beta to SPY {round(beta, 2)})", "",
              "| Version | Trades | Avg per trade | Won | Avg days | Worst trade | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t | Random p | Avg by year |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, tr, sign, hedge in versions:
            solo = hedge == "solo"
            if solo:
                hedge = None
            acct = legs_account(d.index, tr, nq, conv, sign, (lambda s: 2.0) if solo else one)
            legs = [(conv, sign, one)]
            if hedge is not None:
                acct = acct + legs_account(d.index, tr, spy, spy_conv, -sign, hedge)
                legs.append((spy_conv, -sign, hedge))
            acct = acct.loc[idx].fillna(0)
            st = acct_stats(acct, spy_ex, rf)
            pt = per_trade(tr, legs)
            rets = np.array([r for _, r in pt])
            done = [s for s in tr if not s["open"]]
            days = np.array([max(1, d.index.get_loc(s["x"]) - d.index.get_loc(s["e"])) for s in done])
            p = None
            if len(done):
                hb = 0.0 if hedge is None else (1.0 if hedge is one else float(np.nanmean([bn(s) for s in done])))
                hits = 0
                for _ in range(10000):
                    k = rng.integers(0, len(cnq) - days.max() - 1, len(days))
                    rr = np.expm1(sign * (cnq[k + days] - cnq[k] - hb * (cspy[k + days] - cspy[k])))
                    hits += rr.mean() >= rets.mean()
                p = round(hits / 10000, 4)
            yrs = pd.Series(rets, index=[t.year for t, _ in pt]).groupby(level=0).mean() * 100 if len(pt) else pd.Series(dtype=float)
            res = {**st, "n": len(done), "avg_pct": round(rets.mean() * 100, 2) if len(rets) else None,
                   "won_pct": int(round((rets > 0).mean() * 100)) if len(rets) else None,
                   "worst_trade_pct": round(rets.min() * 100, 2) if len(rets) else None,
                   "avg_days": round(float(days.mean()), 1) if len(days) else None, "random_p": p,
                   "by_year": {int(k): round(v, 2) for k, v in yrs.items()}}
            out[f"{per}: {name}"] = res
            L.append(f"| {name} | {res['n']} | {res['avg_pct']}% | {res['won_pct']}% | {res['avg_days']} | {res['worst_trade_pct']}% | {st['cagr_pct']}% | {st['sharpe']} | "
                     f"{st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} | {p} | {' '.join(f'{k}:{v:+.2f}' for k, v in res['by_year'].items())} |")
        # backtest lists for the live page (NQ prices)
        # the live page shows the rules WITHOUT the add-on (Nuno's choice, 23 Nov 2026)
        for key, tr, sign in (("P", Pn0, 1), ("S", Sn0, -1), ("S5", S5n0, -1)):
            for s in tr:
                if s["open"]:
                    continue
                units = [conv(s["e"], s["e_px"])] + ([conv(s["a"], s["a_px"])] if s["a"] is not None else [])
                x = conv(s["x"], s["x_px"])
                pts = sum(sign * (x - u) for u in units)
                web[key].append({"period": per, "entry_date": str(s["e"].date()), "entry_spy": round(s["e_px"], 2), "entry": round(units[0], 2),
                                 "add_date": str(s["a"].date()) if s["a"] is not None else None, "add": round(units[1], 2) if len(units) > 1 else None,
                                 "exit_date": str(s["x"].date()), "exit_spy": round(s["x_px"], 2), "exit": round(x, 2),
                                 "pnl_pts": round(pts, 2), "pct": round(pts / sum(units) * 100, 2)})
        L.append("")
    # verdicts
    v = {}
    for k in ("P — long NQ at the put wall", "S — short NQ at the call wall"):
        r = out[f"oos: {k}"]
        yrs = [y for y in range(2019, 2024) if r["by_year"].get(y, -1) > 0]
        v[k] = {"pass": bool(r["alpha_pct_yr"] > 0 and r["alpha_t"] >= 2 and (1 if r["random_p"] is None else r["random_p"]) < 0.10),
                "holds_up": bool(r["alpha_pct_yr"] > 0 and r["alpha_t"] >= 1.5 and (1 if r["random_p"] is None else r["random_p"]) < 0.10 and len(yrs) >= 3)}
    for k in ("P-hedged — long NQ + short SPY, same $", "S-hedged — short NQ + long SPY, same $"):
        r = out[f"oos: {k}"]
        yrs = [y for y in range(2019, 2024) if r["by_year"].get(y, -1) > 0]
        v[k] = {"pass": bool((r["avg_pct"] or 0) > 0 and (1 if r["random_p"] is None else r["random_p"]) < 0.10 and len(yrs) >= 3)}
    out["verdicts_oos"] = v
    L += ["## Verdicts (out of sample)", ""] + [f"- {k}: {json.dumps(x)}" for k, x in v.items()]
    (ROOT / "results_walls_oos.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_walls_oos.md").write_text("\n".join(L))
    for key, fname in (("P", "pw_backtest.json"), ("S", "cw_backtest.json"), ("S5", "cw5_backtest.json")):
        (ROOT / "data" / fname).write_text(json.dumps(web[key], indent=1))
    print("\n".join(L))


if __name__ == "__main__":
    main()
