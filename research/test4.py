"""Test 4 — Nuno's "relative −10bn" theory (rules in claude/thetadata-teste-pre-registo.md, added 19 Nov 2026)."""
import json

import numpy as np
import pandas as pd
import yfinance as yf

import backtest as bt

CAL = ("2024-01-01", "2026-11-17")


def yahoo(sym, col="Close"):
    h = yf.Ticker(sym).history(start="2018-01-01", interval="1d", auto_adjust=False)
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[col]


def twin_table(d, trades, extra_twins):
    n = len(trades)
    out = {}
    out["VIX ≥ level"] = bt.match_twin(d, n, {"grid": np.arange(15, 60, 0.5), "enter": lambda th: d["vix"] >= th, "exit": d["vix"] <= d["vix_ma20"]})
    out["Price drop"] = bt.match_twin(d, n, {"grid": np.arange(0.01, 0.25, 0.0025), "enter": lambda th: d["spy_close"] <= (1 - th) * d["hi20"],
                                             "exit": d["spy_close"] > d["ma20"]})
    out.update(extra_twins(n))
    return out


def evaluate(d, level, name):
    tr = bt.jp_rule(d, level)
    twins = twin_table(d, tr, lambda n: {"VIX ÷ VIX3M ≥ k": bt.match_twin(d, n, {"grid": np.arange(0.85, 1.4, 0.005),
                                         "enter": lambda th: d["vix_ratio"] >= th, "exit": d["vix"] <= d["vix_ma20"]})})
    old = lambda ts: [t for t in ts if t["entry_date"] <= pd.Timestamp("2021-12-31")]
    jp_old = bt.stats(old(tr))
    res = {"name": name, "all": bt.stats(tr), "2018_21": jp_old, "periods": bt.period_means(tr), "random_p": bt.random_p(d, tr),
           "twins": {}, "trades": [{"entry": str(t["entry_date"].date()), "exit": str(t["exit_date"].date()), "ret_pct": round(t["ret"] * 100, 2)} for t in tr]}
    beats_old = jp_old.get("n", 0) > 0 and jp_old["mean_pct"] > 0
    for k, (th, tt) in twins.items():
        s_old = bt.stats(old(tt))
        res["twins"][k] = {"threshold": round(float(th), 4), "all": bt.stats(tt), "2018_21": s_old, "periods": bt.period_means(tt),
                           "overlap_pct": bt.overlap(tr, tt)}
        if s_old.get("n", 0) and jp_old.get("n", 0) and jp_old["mean_pct"] <= s_old["mean_pct"]:
            beats_old = False
    res["pass"] = bool(beats_old and res["random_p"] is not None and res["random_p"] < 0.10)
    return res


def main():
    d = bt.load()
    vol = yahoo("SPY", "Volume")
    d["dollar_vol20"] = (d["spy_close"] * vol.reindex(d.index)).rolling(20).mean() / 1e9   # $bn per day
    d["vix_ratio"] = d["vix"] / yahoo("^VIX3M").reindex(d.index).ffill()
    cal = d.loc[CAL[0]:CAL[1]]
    s_ref = float(cal["spy_close"].median())
    r = float((-10.0 / cal["dollar_vol20"]).median())
    lvl_a = -10.0 * (d["spy_close"] / s_ref) ** 2
    lvl_b = r * d["dollar_vol20"]
    out = {"s_ref": round(s_ref, 2), "R": round(r, 4),
           "examples": {str(y): {"price_adj_bn": round(float(lvl_a.loc[str(y)].median()), 2), "liquidity_adj_bn": round(float(lvl_b.loc[str(y)].median()), 2)}
                        for y in range(2019, 2027)},
           "A": evaluate(d, lvl_a, "A — price-adjusted −10bn"), "B": evaluate(d, lvl_b, "B — liquidity-adjusted −10bn")}
    (bt.ROOT / "results_test4.json").write_text(json.dumps(out, indent=1, default=str))
    L = ["# Test 4 — relative −10bn (Nuno's theory)", "", f"S_ref (median SPY 2024–26) = {out['s_ref']}; R = {out['R']} (gamma ÷ daily $ volume that −10bn represents in 2024–26)", "",
         "| Year | −10bn translated by price | by liquidity |", "|---|---|---|"]
    for y, v in out["examples"].items():
        L.append(f"| {y} | {v['price_adj_bn']}bn | {v['liquidity_adj_bn']}bn |")
    for key in ("A", "B"):
        e = out[key]
        L += ["", f"## {e['name']}: {'PASS' if e['pass'] else 'FAIL'}", "", f"Random entries doing as well (full period): {e['random_p']}", "",
              "| Rule | Trades 2018–21 | Mean 2018–21 | Wins 2018–21 | Worst 2018–21 | Trades all | Mean all | Wins all | Worst all |", "|---|---|---|---|---|---|---|---|---|"]
        rows = [("Translated JP rule", e["2018_21"], e["all"])] + [(f"{k} ({v['threshold']}) · overlap {v['overlap_pct']}%", v["2018_21"], v["all"]) for k, v in e["twins"].items()]
        for nm, o, a in rows:
            L.append(f"| {nm} | {o.get('n', 0)} | {bt.f(o.get('mean_pct'))} | {bt.f(o.get('win_pct'))} | {bt.f(o.get('worst_pct'))} | {a.get('n', 0)} | {bt.f(a.get('mean_pct'))} | {bt.f(a.get('win_pct'))} | {bt.f(a.get('worst_pct'))} |")
        L += ["", "| Period | Translated JP | " + " | ".join(e["twins"]) + " |", "|---|---|" + "---|" * len(e["twins"])]
        for p, v in e["periods"].items():
            L.append(f"| {p} | {bt.f(v[0])} ({v[1]}) | " + " | ".join(f"{bt.f(t['periods'][p][0])} ({t['periods'][p][1]})" for t in e["twins"].values()) + " |")
    (bt.ROOT / "results_test4.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
