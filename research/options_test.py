"""Test 13 — score the call-wall short done with SPY puts vs the NQ short (rules: claude/opcoes-callwall-teste-pre-registo.md)."""
import json

import numpy as np
import pandas as pd

from backtest import ROOT


def main():
    o = pd.read_csv(ROOT / "data" / "options_trades.csv").drop_duplicates(["date", "kind"])
    nq = pd.DataFrame(json.loads((ROOT / "data" / "cw_backtest.json").read_text()))
    nq = nq.drop_duplicates("entry_date").set_index("entry_date")["pct"] / 100
    rows, L = {}, ["# Test 13 — call-wall short with SPY puts vs shorting NQ (real bid/ask, 1-minute quotes)", ""]
    sig = o[o["kind"] == "signal"].copy()
    L += [f"Signal days with option data: {len(sig)} (SPY reached the wall in the minute data on {int(sig['touched'].sum())}); random days: {int((o['kind'] == 'random').sum())}.", ""]
    variants = {"A — 0DTE (or ≤2-day), out at 15:55": ("A", "A_out_close"), "B1 — 2–3 day, out at 15:55": ("B", "B_out_close"),
                "B2 — 2–3 day, out 9:45 next day": ("B", "B_out_next")}
    L += ["| Version | Days | Trades | Avg return on premium | Median | Won | Worst | Avg P&L per $ of exposure | NQ short same days | 1% premium per trade: return/yr | Worst run | Positive years 2019–24 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, (k, out) in variants.items():
        for kind in ("signal", "random"):
            x = o[(o["kind"] == kind) & o[f"{k}_ask"].notna() & o[out].notna() & o[f"{k}_delta"].notna()].copy()
            if x.empty:
                continue
            x["ret"] = x[out] / x[f"{k}_ask"] - 1
            x["per_exp"] = (x[out] - x[f"{k}_ask"]) / (x[f"{k}_delta"].abs() * x["spy_in"])
            x["yr"] = x["date"].str[:4].astype(int)
            acct = 0.01 * x.set_index(pd.to_datetime(x["date"]))["ret"]
            curve = (1 + acct).cumprod()
            yrs = (curve.index[-1] - curve.index[0]).days / 365.25 or 1
            ry = (curve.iloc[-1] ** (1 / yrs) - 1) * 100
            dd = (curve / curve.cummax() - 1).min() * 100
            by = x.groupby("yr")["ret"].mean()
            pos_years = int(sum(1 for y in range(2019, 2025) if by.get(y, -1) > 0))
            nq_same = nq.reindex(x["date"]).dropna() if kind == "signal" else pd.Series(dtype=float)
            r = {"n": len(x), "avg_ret_pct": round(x["ret"].mean() * 100, 1), "median_ret_pct": round(x["ret"].median() * 100, 1),
                 "won_pct": int(round((x["ret"] > 0).mean() * 100)), "worst_pct": round(x["ret"].min() * 100, 1),
                 "per_exposure_pct": round(x["per_exp"].mean() * 100, 3), "nq_same_days_pct": round(nq_same.mean() * 100, 3) if len(nq_same) else None,
                 "ret_yr_1pct": round(ry, 2), "worst_run_pct": round(dd, 1), "pos_years_2019_24": pos_years,
                 "by_year": {int(a): round(b * 100, 1) for a, b in by.items()}, "avg_dte": round(x[f"{k}_dte"].mean(), 1)}
            rows[f"{kind}: {name}"] = r
            L.append(f"| {name} — {kind} | {r['avg_dte']} | {r['n']} | {r['avg_ret_pct']}% | {r['median_ret_pct']}% | {r['won_pct']}% | {r['worst_pct']}% | "
                     f"{r['per_exposure_pct']}% | {r['nq_same_days_pct'] if r['nq_same_days_pct'] is not None else '–'}{'%' if r['nq_same_days_pct'] is not None else ''} | "
                     f"{r['ret_yr_1pct']}% | {r['worst_run_pct']}% | {pos_years} of 6 |")
    passes = {}
    for name in variants:
        s, rd = rows.get(f"signal: {name}"), rows.get(f"random: {name}")
        if not s or not rd:
            continue
        passes[name] = bool(s["per_exposure_pct"] > (s["nq_same_days_pct"] or 1e9) and s["avg_ret_pct"] > 0
                            and s["avg_ret_pct"] > rd["avg_ret_pct"] and s["pos_years_2019_24"] >= 4)
    rows["pass"] = passes
    L += ["", "By year, average return on premium (signal days):", ""]
    for name in variants:
        s = rows.get(f"signal: {name}")
        if s:
            L.append(f"- {name}: " + ", ".join(f"{y}: {v:+.1f}%" for y, v in s["by_year"].items()))
    L += ["", "Pass (an option version beats the NQ short per $ of exposure after spreads, positive and above random days, ≥4 of 6 positive years): " +
          json.dumps(passes)]
    (ROOT / "results_options.json").write_text(json.dumps(rows, indent=1))
    (ROOT / "results_options.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
