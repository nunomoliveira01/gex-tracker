"""FX test — EUR/USD crowd-positioning rule (rules: claude/fx-teste-pre-registo.md, fixed 20 Nov 2026).
Run inside GitHub Actions (needs Yahoo, FRED and CFTC)."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
START, END = "2004-01-01", "2026-11-20"
PERIODS = [("2004–2010", "2004-01-01", "2010-12-31"), ("2011–2018", "2011-01-01", "2018-12-31"), ("2019–2026", "2019-01-01", "2026-12-31")]
UA = {"User-Agent": "Mozilla/5.0 (research script)"}
rng = np.random.default_rng(42)


def yahoo(sym):
    h = yf.Ticker(sym).history(start="2003-06-01", interval="1d", auto_adjust=False)["Close"]
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[~h.index.duplicated()]


def fred(series):
    import time
    for i in range(4):
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers=UA, timeout=120)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = ["date", "v"]
            df["date"] = pd.to_datetime(df["date"]); df["v"] = pd.to_numeric(df["v"], errors="coerce")
            return df.set_index("date")["v"]
        except Exception as e:
            print("FRED", series, "try", i + 1, "failed:", e); time.sleep(10)
    print("FRED unavailable, using backup source for", series)
    if series == "DTB3":
        return yahoo("^IRX")
    url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata"
    r = requests.get(url, headers=UA, timeout=120); r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    v = pd.Series(pd.to_numeric(df["OBS_VALUE"], errors="coerce").to_numpy(), index=pd.to_datetime(df["TIME_PERIOD"]))
    return v.sort_index()


def cot_euro():
    """CFTC legacy futures-only, Euro FX (099741): one row per weekly report."""
    rows = []
    try:   # public API (Socrata)
        url = ("https://publicreporting.cftc.gov/resource/6dca-aqww.csv?$limit=5000&cftc_contract_market_code=099741"
               "&$select=report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all")
        r = requests.get(url, headers=UA, timeout=60); r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if len(df) > 500:
            df.columns = ["date", "long", "short", "oi"]
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").drop_duplicates("date").set_index("date")
    except Exception as e:
        print("CFTC API failed:", e)
    for y in range(2003, 2027):   # yearly history files
        try:
            r = requests.get(f"https://www.cftc.gov/files/dea/history/deacot{y}.zip", headers=UA, timeout=60); r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content)); t = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
            code = [c for c in t.columns if "CFTC Contract Market Code" in c][0]
            t = t[t[code].astype(str).str.strip() == "099741"]
            dc = [c for c in t.columns if "YYYY-MM-DD" in c][0]
            rows.append(pd.DataFrame({"date": pd.to_datetime(t[dc]), "long": t["Noncommercial Positions-Long (All)"],
                                      "short": t["Noncommercial Positions-Short (All)"], "oi": t["Open Interest (All)"]}))
        except Exception as e:
            print("CFTC file", y, "failed:", e)
    df = pd.concat(rows).sort_values("date").drop_duplicates("date")
    return df.set_index("date")


def trades_from(signal_dir, entry, exit_):
    """signal_dir: +1 long / -1 short wanted at entry; entry/exit_: boolean Series. One position at a time."""
    out, pos = [], None
    for t in entry.index:
        if pos is None:
            if entry[t]:
                pos = {"e": t, "dir": int(signal_dir[t])}
        elif exit_[t] if not callable(exit_) else exit_(pos, t):
            pos["x"] = t; out.append(pos); pos = None
    if pos:
        pos["x"] = entry.index[-1]; pos["open"] = True; out.append(pos)
    return out


def leg_returns(r_long, tr, add_rule=None, px=None, mean20=None, sd20=None):
    """Daily account returns (1 unit = all capital; with add-ons the account holds capital for 2 units, each half)."""
    acct = pd.Series(0.0, index=r_long.index)
    per = []
    for s in tr:
        days = r_long.loc[s["e"]:s["x"]].index[1:]
        legs = [(s["e"], days)]
        if add_rule:
            seg = px.loc[s["e"]:s["x"]].index[1:]
            for t in seg:
                if t == s["x"]:
                    break
                far = (px[t] - mean20[t]) / sd20[t]
                if (s["dir"] > 0 and far <= -2) or (s["dir"] < 0 and far >= 2):
                    legs.append((t, r_long.loc[t:s["x"]].index[1:])); s["a"] = t
                    break
        w = 0.5 if add_rule else 1.0
        rets = []
        for t0, dd in legs:
            r = s["dir"] * r_long.loc[dd]
            acct.loc[dd] += w * r
            rets.append(float((1 + r).prod() - 1))
        path = (1 + s["dir"] * r_long.loc[days]).cumprod() if len(days) else pd.Series([1.0])
        per.append({"e": s["e"], "x": s["x"], "dir": s["dir"], "ret": float(np.mean(rets)), "days": len(days),
                    "worst": float(path.min() - 1), "open": s.get("open", False)})
    return acct, pd.DataFrame(per)


def summarise(acct, per, idx):
    a = acct.loc[idx]
    curve = (1 + a).cumprod()
    yrs = len(a) / 252
    done = per[~per["open"]] if len(per) else per
    out = {"trades": int(len(done)), "avg_trade_pct": round(done["ret"].mean() * 100, 2) if len(done) else None,
           "won_pct": int(round((done["ret"] > 0).mean() * 100)) if len(done) else None,
           "worst_trade_drop_pct": round(per["worst"].min() * 100, 2) if len(per) else None,
           "avg_days": round(per["days"].mean(), 1) if len(per) else None,
           "ret_yr_pct": round((curve.iloc[-1] ** (1 / yrs) - 1) * 100, 2), "vol_pct": round(a.std() * np.sqrt(252) * 100, 2),
           "sharpe": round(a.mean() / a.std() * np.sqrt(252), 2) if a.std() else None,
           "max_dd_pct": round((curve / curve.cummax() - 1).min() * 100, 2),
           "in_market_pct": round((a != 0).mean() * 100, 1)}
    out["by_period"] = {}
    for name, s, e in PERIODS:
        p = done[(done["e"] >= s) & (done["e"] <= e)] if len(done) else done
        out["by_period"][name] = (round(p["ret"].mean() * 100, 2), int(len(p))) if len(p) else (None, 0)
    return out


def alpha_vs(acct, factors, idx):
    y = acct.loc[idx].to_numpy()
    X = np.column_stack([np.ones(len(idx))] + [f.loc[idx].fillna(0).to_numpy() for f in factors])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    se = np.sqrt(np.diag(np.linalg.inv(X.T @ X)) * resid.var(ddof=X.shape[1]))
    return {"alpha_pct_yr": round(coef[0] * 252 * 100, 2), "alpha_t": round(coef[0] / se[0], 2),
            "betas": [round(c, 2) for c in coef[1:]]}


def random_p(r_long, per, reps=10000):
    done = per[~per["open"]]
    if not len(done):
        return None
    cum = np.log1p(r_long.fillna(0)).cumsum().to_numpy()
    n, h, dr = len(cum), done["days"].clip(lower=1).to_numpy(), done["dir"].to_numpy()
    target, hits = done["ret"].mean(), 0
    for _ in range(reps):
        s = rng.integers(0, n - h.max() - 1, len(h))
        r = np.expm1(dr * (cum[s + h] - cum[s]))
        hits += r.mean() >= target
    return round(hits / reps, 4)


def main():
    px = yahoo("EURUSD=X").loc[START:END]
    idx = px.index
    eur = fred("ECBDFR").reindex(idx, method="ffill") / 100
    usd = fred("DTB3").reindex(idx, method="ffill") / 100
    r_long = (px.pct_change() + (eur - usd) / 252).fillna(0)          # long EUR funded in USD, incl. carry
    dxy = yahoo("DX-Y.NYB").reindex(idx).ffill().pct_change().fillna(0)
    trend = np.sign(px / px.shift(252) - 1).shift(1).fillna(0) * r_long
    carry = np.sign(eur - usd).shift(1).fillna(0) * r_long
    factors = [dxy, trend, carry]

    cot = cot_euro()
    cot["pos"] = (cot["long"] - cot["short"]) / cot["oi"]
    prev = cot["pos"].shift(1).rolling(156, min_periods=104)
    cot["z"] = (cot["pos"] - prev.mean()) / prev.std()
    # usable from the close of the first trading day after Friday's release (report Tuesday + 3 days)
    usable = cot[["z", "pos"]].copy(); usable.index = usable.index + pd.Timedelta(days=4)
    z = usable["z"].reindex(idx, method="ffill")

    # Rule A
    want = pd.Series(np.where(z >= 1.5, -1, np.where(z <= -1.5, 1, 0)), index=idx)
    entry = want != 0
    def ex_a(pos, t):
        return (pos["dir"] < 0 and z[t] <= 0) or (pos["dir"] > 0 and z[t] >= 0)
    trA = trades_from(want, entry, ex_a)
    accA, perA = leg_returns(r_long, trA)
    resA = summarise(accA, perA, idx); resA.update(alpha_vs(accA, factors, idx)); resA["random_p"] = random_p(r_long, perA)
    # Rule B
    m20, sd20 = px.rolling(20).mean(), px.rolling(20).std()
    trB = [dict(t) for t in trA]
    accB, perB = leg_returns(r_long, trB, add_rule=True, px=px, mean20=m20, sd20=sd20)
    resB = summarise(accB, perB, idx); resB.update(alpha_vs(accB, factors, idx)); resB["adds"] = int(sum(1 for t in trB if t.get("a") is not None))
    # stretched-move twin, k matched to Rule A's trade count
    nA = resA["trades"]; best = None
    for k in np.arange(1.0, 4.01, 0.05):
        far = (px - m20) / sd20
        w = pd.Series(np.where(far >= k, -1, np.where(far <= -k, 1, 0)), index=idx)
        def ex_t(pos, t, m20=m20):
            return (pos["dir"] < 0 and px[t] <= m20[t]) or (pos["dir"] > 0 and px[t] >= m20[t])
        tr = trades_from(w, w != 0, ex_t)
        gap = abs(len([x for x in tr if not x.get("open")]) - nA)
        if best is None or gap < best[0]:
            best = (gap, k, tr)
    accT, perT = leg_returns(r_long, best[2])
    resT = summarise(accT, perT, idx); resT.update(alpha_vs(accT, factors, idx)); resT["k"] = round(float(best[1]), 2)
    resTrend = summarise(trend, pd.DataFrame(columns=["e", "x", "dir", "ret", "days", "worst", "open"]), idx)
    resCarry = summarise(carry, pd.DataFrame(columns=["e", "x", "dir", "ret", "days", "worst", "open"]), idx)

    pos_periods = sum(1 for v in resA["by_period"].values() if v[0] is not None and v[0] > 0)
    passA = bool(resA["avg_trade_pct"] is not None and resA["avg_trade_pct"] > 0 and resA["avg_trade_pct"] > (resT["avg_trade_pct"] or -1e9)
                 and resA["random_p"] is not None and resA["random_p"] < 0.10 and pos_periods >= 2 and resA["alpha_t"] >= 1.5)
    out = {"rule_A": resA, "rule_B": resB, "stretched_twin": resT, "trend": resTrend, "carry": resCarry, "pass_A": passA,
           "cot_first": str(cot.index.min().date()), "cot_last": str(cot.index.max().date()), "cot_rows": int(len(cot)),
           "z_now": round(float(z.iloc[-1]), 2), "pos_now_pct": round(float(cot["pos"].iloc[-1]) * 100, 1),
           "trades_A": [{"entry": str(t["e"].date()), "exit": str(t["x"].date()), "dir": "long EUR" if t["dir"] > 0 else "short EUR",
                         "open": t.get("open", False)} for t in trA]}
    (ROOT / "results_fx.json").write_text(json.dumps(out, indent=1, default=str))
    f = lambda v, u="%": "–" if v is None else f"{v}{u}"
    L = [f"# FX test — EUR/USD crowd-positioning rule ({START[:4]} → {str(idx[-1].date())}, daily, carry included, no costs) — Rule A: {'PASS' if passA else 'FAIL'}", "",
         f"CFTC data {out['cot_first']} → {out['cot_last']} ({out['cot_rows']} weekly reports). Speculators' net position now {out['pos_now_pct']}% of open interest, z = {out['z_now']}.", "",
         "| Rule | Trades | Avg per trade | Won | Worst drop in a trade | Days held | Return/yr | Volatility | Sharpe | Worst fall | In market | Alpha/yr after dollar, trend, carry | Alpha t |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, r in (("A — fade speculator extremes (|z| ≥ 1.5, exit at 0)", resA), ("B — A + add-on 2 std stretch", resB),
                    (f"Stretched-move twin (fade {resT['k']} std, exit at the 20-day average)", resT)):
        L.append(f"| {name} | {r['trades']} | {f(r['avg_trade_pct'])} | {f(r['won_pct'])} | {f(r['worst_trade_drop_pct'])} | {f(r['avg_days'], '')} | {r['ret_yr_pct']}% | {r['vol_pct']}% | {r['sharpe']} | {r['max_dd_pct']}% | {r['in_market_pct']}% | {r['alpha_pct_yr']}% | {r['alpha_t']} |")
    for name, r in (("Trend (12-month direction), always in", resTrend), ("Carry (higher-rate currency), always in", resCarry)):
        L.append(f"| {name} | – | – | – | – | – | {r['ret_yr_pct']}% | {r['vol_pct']}% | {r['sharpe']} | {r['max_dd_pct']}% | 100% | – | – |")
    L += ["", f"Random dates (same directions and lengths) did as well as Rule A: {resA['random_p']}. Rule B add-ons: {resB['adds']}.", "",
          "| Period | Rule A avg (trades) | Twin avg (trades) |", "|---|---|---|"]
    for name, *_ in PERIODS:
        a, t = resA["by_period"][name], resT["by_period"][name]
        L.append(f"| {name} | {f(a[0])} ({a[1]}) | {f(t[0])} ({t[1]}) |")
    (ROOT / "results_fx.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
