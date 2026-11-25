"""Test 14 — exit at the flip at any hour (NQ overnight) vs New York session only (rules: claude/saida-24h-teste-pre-registo.md)."""
import json

import numpy as np
import pandas as pd

from backtest import ROOT


def main():
    import yfinance as yf
    hb = yf.Ticker("NQ=F").history(period="730d", interval="1h", prepost=True)
    hb.index = hb.index.tz_convert("America/New_York").tz_localize(None)
    hb = hb[["Open", "High", "Low", "Close"]].dropna()
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    nqd = yf.Ticker("NQ=F").history(start="2024-06-01", interval="1d")["Close"]
    nqd.index = pd.to_datetime(nqd.index).tz_localize(None).normalize()
    nqd = nqd.reindex(d.index).ffill()
    w0 = hb.index.min().normalize() + pd.Timedelta(days=1)
    r_nq, r_spy = nqd.pct_change().loc[w0:], d["spy_close"].pct_change().loc[w0:]
    beta = float(np.cov(r_nq.fillna(0), r_spy.fillna(0))[0, 1] / r_spy.var())
    level = lambda day: nqd.at[day] * (1 + beta * (d.at[day, "flip"] / d.at[day, "spy_close"] - 1))
    outside = lambda t: not (570 <= t.hour * 60 + t.minute < 960)
    sets = {"Put wall long": ("pw_backtest.json", 1), "Call wall short": ("cw_backtest.json", -1), "−10bn strategy (my strategy)": ("nq_pc_backtest.json", 1)}
    L = [f"# Test 14 — exit at the flip at any hour vs New York session only (NQ hourly bars {hb.index.min().date()} → {hb.index.max().date()}, NQ beta {beta:.2f})", "",
         "| Strategy | Trades in window | Changed by overnight exit | Avg per trade: NY only | Avg: anytime | Won: NY only | Won: anytime | Worst: NY only | Worst: anytime | Days held: NY only | Days held: anytime | Changed trades better with anytime |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    out = {"beta": round(beta, 2), "window": [str(hb.index.min()), str(hb.index.max())]}
    for name, (f, sign) in sets.items():
        tr = [t for t in json.loads((ROOT / "data" / f).read_text()) if not t.get("open")]
        tr = [t for t in tr if pd.Timestamp(t["entry_date"]) >= w0 and pd.Timestamp(t["exit_date"]) <= d.index[-1]]
        rows = []
        for t in tr:
            e, x = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
            units = [t["entry"]] + ([t["add"]] if t.get("add") else [])
            orig = np.mean([sign * (t["exit"] / u - 1) for u in units])
            new_px, new_t = None, None
            sess = d.loc[e:x].index          # sessions from the entry day to the original exit day
            for k in range(len(sess) - 1):    # the overnight after session k, until the open of session k+1
                day, nxt = sess[k], sess[k + 1]
                if day not in nqd.index or pd.isna(d.at[day, "flip"]):
                    continue
                lv = level(day)
                bars = hb[(hb.index >= day + pd.Timedelta(hours=16)) & (hb.index < nxt + pd.Timedelta(hours=9, minutes=30))]
                bars = bars[[outside(b) for b in bars.index]]
                for bt, b in bars.iterrows():
                    if sign > 0 and b["High"] >= lv:
                        new_px, new_t = (b["Open"] if b["Open"] >= lv else lv), bt; break
                    if sign < 0 and b["Low"] <= lv:
                        new_px, new_t = (b["Open"] if b["Open"] <= lv else lv), bt; break
                if new_px is not None:
                    break
            if new_px is not None:
                new = np.mean([sign * (new_px / u - 1) for u in units])
                days_new = (new_t.normalize() - e).days
            else:
                new, days_new = orig, (x - e).days
            rows.append({"entry": str(e.date()), "orig": orig, "new": new, "changed": new_px is not None,
                         "days_orig": (x - e).days, "days_new": days_new, "exit_new": str(new_t) if new_t is not None else None})
        r = pd.DataFrame(rows)
        if r.empty:
            continue
        ch = r[r["changed"]]
        better = float((ch["new"] > ch["orig"]).mean()) if len(ch) else None
        res = {"n": len(r), "changed": int(len(ch)), "avg_ny": round(r["orig"].mean() * 100, 3), "avg_any": round(r["new"].mean() * 100, 3),
               "won_ny": int(round((r["orig"] > 0).mean() * 100)), "won_any": int(round((r["new"] > 0).mean() * 100)),
               "worst_ny": round(r["orig"].min() * 100, 2), "worst_any": round(r["new"].min() * 100, 2),
               "days_ny": round(r["days_orig"].mean(), 2), "days_any": round(r["days_new"].mean(), 2),
               "changed_better_share": None if better is None else round(better, 2),
               "changed_avg_ny": round(ch["orig"].mean() * 100, 3) if len(ch) else None, "changed_avg_any": round(ch["new"].mean() * 100, 3) if len(ch) else None,
               "verdict_anytime_better": bool(r["new"].mean() > r["orig"].mean() and better is not None and better > 0.5)}
        out[name] = res
        L.append(f"| {name} | {res['n']} | {res['changed']} | {res['avg_ny']}% | {res['avg_any']}% | {res['won_ny']}% | {res['won_any']}% | {res['worst_ny']}% | {res['worst_any']}% | "
                 f"{res['days_ny']} | {res['days_any']} | {'–' if better is None else f'{better*100:.0f}%'} |")
    L += [""] + [f"- {k}: changed trades avg {v['changed_avg_ny']}% (NY only) vs {v['changed_avg_any']}% (anytime); anytime better: {v['verdict_anytime_better']}"
                 for k, v in out.items() if isinstance(v, dict)]
    (ROOT / "results_exit_anytime.json").write_text(json.dumps(out, indent=1))
    (ROOT / "results_exit_anytime.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
