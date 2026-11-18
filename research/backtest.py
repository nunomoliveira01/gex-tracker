"""Pre-registered tests (claude/thetadata-teste-pre-registo.md, fixed 18 Nov 2026 before seeing data).

Input: research/data/daily.csv (derived from ThetaData) + VIX from Yahoo.
Output: research/results.md and research/results.json.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
LEVEL = -10.0
PERIODS = [("2018–21", "2018-01-01", "2021-12-31"), ("2022–23", "2022-01-01", "2023-12-31"), ("2024–26", "2024-01-01", "2026-12-31")]
rng = np.random.default_rng(7)


def load():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").sort_values("date").set_index("date")
    import yfinance as yf
    v = yf.Ticker("^VIX").history(start="2018-01-01", interval="1d")["Close"]
    v.index = pd.to_datetime(v.index).tz_localize(None).normalize()
    d["vix"] = v.reindex(d.index).ffill()
    d["ma20"] = d["spy_close"].rolling(20).mean()
    d["hi20"] = d["spy_close"].rolling(20).max()
    d["vix_ma20"] = d["vix"].rolling(20).mean()
    for c in ("xsp_jp_prem", "xsp_proxy_prem", "pc_volume", "vix"):   # z vs the previous 60 days
        prev = d[c].shift(1).rolling(60)
        d["z_" + c] = (d[c] - prev.mean()) / prev.std()
    d["p5"] = d["gex_close"].shift(1).rolling(252, min_periods=200).quantile(0.05)
    for k in (1, 3, 5):
        d[f"fwd{k}"] = d["spy_close"].shift(-k) / d["spy_close"] - 1
    return d


# ------------------------------------------------------------------ trade engines
def run_rule(d, enter, exit_):
    """enter/exit_: boolean Series. Enter at the close of an entry day when flat; exit at the close of an exit day."""
    trades, pos = [], None
    for t, row in d.iterrows():
        if pos is None:
            if enter.get(t, False):
                pos = {"entry_date": t, "entry": row["spy_close"]}
        elif exit_.get(t, False):
            pos.update(exit_date=t, exit=row["spy_close"])
            trades.append(pos); pos = None
    if pos is not None:   # still open at the end: marked at the last close
        pos.update(exit_date=d.index[-1], exit=d["spy_close"].iloc[-1], open=True); trades.append(pos)
    for tr in trades:
        seg = d.loc[tr["entry_date"]:tr["exit_date"], "spy_close"]
        tr["ret"] = tr["exit"] / tr["entry"] - 1
        tr["worst"] = seg.min() / tr["entry"] - 1
        tr["days"] = len(seg) - 1
    return trades


def jp_rule(d, level_series):
    return run_rule(d, d["gex_low"] <= level_series, d["gex_close"] >= 0)


def match_twin(d, n_target, make):
    """Pick the threshold whose trade count is closest to the JP rule's."""
    best = None
    for th in make["grid"]:
        tr = run_rule(d, make["enter"](th), make["exit"])
        gap = abs(len(tr) - n_target)
        if best is None or gap < best[0]:
            best = (gap, th, tr)
    return best[1], best[2]


def stats(trades):
    if not trades:
        return {"n": 0}
    r = np.array([t["ret"] for t in trades])
    return {"n": len(r), "mean_pct": round(r.mean() * 100, 2), "median_pct": round(float(np.median(r)) * 100, 2),
            "win_pct": round((r > 0).mean() * 100, 0), "worst_pct": round(min(t["worst"] for t in trades) * 100, 2),
            "avg_days": round(np.mean([t["days"] for t in trades]), 1)}


def period_means(trades):
    out = {}
    for name, a, b in PERIODS:
        r = [t["ret"] for t in trades if pd.Timestamp(a) <= t["entry_date"] <= pd.Timestamp(b)]
        out[name] = (round(np.mean(r) * 100, 2), len(r)) if r else (None, 0)
    return out


def random_p(d, trades, reps=10000):
    """Share of random entry sets (same holding periods) doing at least as well as the rule."""
    if not trades:
        return None
    px = d["spy_close"].to_numpy()
    hold = np.array([max(1, t["days"]) for t in trades])
    target = np.mean([t["ret"] for t in trades])
    n, hits = len(px), 0
    for _ in range(reps):
        h = rng.choice(hold, len(hold))
        s = rng.integers(0, n - h.max() - 1, len(hold))
        hits += (px[s + h] / px[s] - 1).mean() >= target
    return round(hits / reps, 4)


def overlap(a, b, days=3):
    if not a:
        return None
    eb = [t["entry_date"] for t in b]
    near = sum(any(abs((t["entry_date"] - x).days) <= days for x in eb) for t in a)
    return round(near / len(a) * 100, 0)


def test1(d, level_series, label):
    jp = jp_rule(d, level_series)
    n = len(jp)
    vix_th, vix = match_twin(d, n, {"grid": np.arange(15, 60, 0.5), "enter": lambda th: d["vix"] >= th, "exit": d["vix"] <= d["vix_ma20"]})
    dd_th, dd = match_twin(d, n, {"grid": np.arange(0.01, 0.25, 0.0025), "enter": lambda th: d["spy_close"] <= (1 - th) * d["hi20"],
                                  "exit": d["spy_close"] > d["ma20"]})
    s_jp, s_v, s_d = stats(jp), stats(vix), stats(dd)
    pj, pv, pd_ = period_means(jp), period_means(vix), period_means(dd)
    holds = {}
    for name, *_ in PERIODS:
        a, b, c = pj[name][0], pv[name][0], pd_[name][0]
        holds[name] = None if a is None else bool(a > (b if b is not None else -1e9) and a > (c if c is not None else -1e9))
    p = random_p(d, jp)
    beats = n > 0 and s_jp["mean_pct"] > s_v.get("mean_pct", -1e9) and s_jp["mean_pct"] > s_d.get("mean_pct", -1e9)
    passed = bool(beats and p is not None and p < 0.10 and sum(1 for v in holds.values() if v) >= 2)
    return {"label": label, "jp": s_jp, "vix_twin": {**s_v, "threshold": round(float(vix_th), 2)},
            "drop_twin": {**s_d, "threshold_pct": round(float(dd_th) * 100, 2)},
            "periods": {k: {"jp": pj[k], "vix": pv[k], "drop": pd_[k], "jp_best": holds[k]} for k in pj},
            "random_p": p, "overlap_vix_pct": overlap(jp, vix), "overlap_drop_pct": overlap(jp, dd),
            "beats_twins": bool(beats), "pass": passed, "trades": [
                {"entry": str(t["entry_date"].date()), "exit": str(t["exit_date"].date()), "ret_pct": round(t["ret"] * 100, 2),
                 "days": t["days"], "open": t.get("open", False)} for t in jp]}


def test2(d, level_series):
    """Add-on unit inside JP positions, per filter; plus all-days forward returns."""
    jp = jp_rule(d, level_series)
    filters = {"JP exact (1-lot XSP puts at ask)": d["z_xsp_jp_prem"] >= 1, "Free proxy (all XSP put premium)": d["z_xsp_proxy_prem"] >= 1,
               "VIX z ≥ 1": d["z_vix"] >= 1, "SPY put/call z ≥ 1": d["z_pc_volume"] >= 1, "No filter": pd.Series(True, index=d.index)}
    out = {}
    for name, f in filters.items():
        units = []
        for t in jp:
            seg = d.loc[t["entry_date"]:t["exit_date"]].iloc[1:]
            hit = seg[(seg["spy_close"] < t["entry"]) & f.reindex(seg.index).fillna(False)]
            if len(hit):
                a = hit.index[0]
                units.append({"date": a, "ret": t["exit"] / d.at[a, "spy_close"] - 1})
        per = {}
        for pname, a, b in PERIODS:
            r = [u["ret"] for u in units if pd.Timestamp(a) <= u["date"] <= pd.Timestamp(b)]
            sub = d.loc[a:b]
            fire = sub[f.reindex(sub.index).fillna(False)]
            per[pname] = {"add_mean_pct": round(np.mean(r) * 100, 2) if r else None, "adds": len(r),
                          "fwd3_pct": round(fire["fwd3"].mean() * 100, 2) if len(fire) else None,
                          "fwd5_pct": round(fire["fwd5"].mean() * 100, 2) if len(fire) else None, "days": int(len(fire))}
        fire = d[f.reindex(d.index).fillna(False)]
        out[name] = {"adds": len(units), "add_mean_pct": round(np.mean([u["ret"] for u in units]) * 100, 2) if units else None,
                     "fwd1_pct": round(fire["fwd1"].mean() * 100, 3), "fwd3_pct": round(fire["fwd3"].mean() * 100, 3),
                     "fwd5_pct": round(fire["fwd5"].mean() * 100, 3), "days": int(len(fire)), "periods": per}
    base = {"fwd1_pct": round(d["fwd1"].mean() * 100, 3), "fwd3_pct": round(d["fwd3"].mean() * 100, 3), "fwd5_pct": round(d["fwd5"].mean() * 100, 3)}
    jp_k = "JP exact (1-lot XSP puts at ask)"
    rivals = [k for k in out if k not in (jp_k, "No filter")] + ["No filter"]
    def beats(metric, src, dst):
        a = src.get(metric)
        return a is not None and all(dst[k].get(metric) is None or a > dst[k][metric] for k in rivals)
    overall = beats("add_mean_pct", out[jp_k], out) and beats("fwd3_pct", out[jp_k], out) and beats("fwd5_pct", out[jp_k], out)
    per_ok = {}
    for pname, *_ in PERIODS:
        src = out[jp_k]["periods"][pname]; dst = {k: out[k]["periods"][pname] for k in out}
        per_ok[pname] = bool(beats("fwd3_pct", src, dst) and beats("fwd5_pct", src, dst)
                             and (src["add_mean_pct"] is None or beats("add_mean_pct", src, dst)))
    return {"filters": out, "all_days": base, "jp_beats_all_overall": bool(overall), "jp_best_by_period": per_ok,
            "pass": bool(overall and sum(per_ok.values()) >= 2)}


def f(v, unit="%"):
    return "–" if v is None else f"{v}{unit}"


def write_md(res):
    t1, t2, t3 = res["test1"], res["test2"], res["test3"]
    L = [f"# ThetaData tests — results ({res['from']} to {res['to']}, {res['days']} trading days)", "",
         "Rules fixed before seeing the data: see claude/thetadata-teste-pre-registo.md. Returns are SPY points in %, no costs.", ""]
    for t in (t1, t3):
        L += [f"## {'Test 1' if t is t1 else 'Test 3'} — {t['label']}: {'PASS' if t['pass'] else 'FAIL'}", "",
              "| Rule | Trades | Mean | Median | Wins | Worst during trade | Days held |", "|---|---|---|---|---|---|---|"]
        for name, s in (("JP rule", t["jp"]), (f"VIX twin (VIX ≥ {t['vix_twin'].get('threshold')})", t["vix_twin"]),
                        (f"Drop twin ({t['drop_twin'].get('threshold_pct')}% below 20d high)", t["drop_twin"])):
            L.append(f"| {name} | {s.get('n')} | {f(s.get('mean_pct'))} | {f(s.get('median_pct'))} | {f(s.get('win_pct'))} | {f(s.get('worst_pct'))} | {f(s.get('avg_days'), '')} |")
        L += ["", f"Random entries doing as well: {t['random_p']} · JP entries within 3 days of a VIX-twin entry: {t['overlap_vix_pct']}%, of a drop-twin entry: {t['overlap_drop_pct']}%", "",
              "| Period | JP mean (n) | VIX twin (n) | Drop twin (n) | JP best? |", "|---|---|---|---|---|"]
        for k, v in t["periods"].items():
            L.append(f"| {k} | {f(v['jp'][0])} ({v['jp'][1]}) | {f(v['vix'][0])} ({v['vix'][1]}) | {f(v['drop'][0])} ({v['drop'][1]}) | {f(v['jp_best'], '')} |")
        L.append("")
    L += [f"## Test 2 — retail panic add-on: {'PASS' if t2['pass'] else 'FAIL'}", "",
          "| Filter | Add-ons | Add-on mean | Days firing | SPY +1d | +3d | +5d |", "|---|---|---|---|---|---|---|"]
    for k, v in t2["filters"].items():
        L.append(f"| {k} | {v['adds']} | {f(v['add_mean_pct'])} | {v['days']} | {f(v['fwd1_pct'])} | {f(v['fwd3_pct'])} | {f(v['fwd5_pct'])} |")
    b = t2["all_days"]
    L += [f"| Any day (baseline) | – | – | all | {b['fwd1_pct']}% | {b['fwd3_pct']}% | {b['fwd5_pct']}% |", "",
          f"JP exact best overall: {t2['jp_beats_all_overall']} · best by period: {t2['jp_best_by_period']}", ""]
    (ROOT / "results.md").write_text("\n".join(L))


def main():
    d = load()
    res = {"from": str(d.index[0].date()), "to": str(d.index[-1].date()), "days": int(len(d)),
           "gamma_min": round(float(d["gex_low"].min()), 2), "days_below_level": int((d["gex_low"] <= LEVEL).sum())}
    res["test1"] = test1(d, pd.Series(LEVEL, index=d.index), "fixed −10bn")
    res["test2"] = test2(d, pd.Series(LEVEL, index=d.index))
    res["test3"] = test1(d, d["p5"], "gamma below its own 5th percentile of the last 12 months")
    res["test2_scaled_extra"] = test2(d, d["p5"])
    (ROOT / "results.json").write_text(json.dumps(res, indent=1, default=str))
    write_md(res)
    print((ROOT / "results.md").read_text())


if __name__ == "__main__":
    main()
