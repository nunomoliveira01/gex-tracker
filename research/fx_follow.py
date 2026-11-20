"""FX test 2 — GBP/USD follow the crowd, enter on a pullback (rules: claude/fx-gbp-teste-pre-registo.md, fixed 20 Nov 2026)."""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from fx_test import PERIODS, UA, alpha_vs, leg_returns, random_p, summarise, yahoo

ROOT = Path(__file__).resolve().parent
START, END = "2004-01-01", "2026-11-20"
CUR = {"GBP": ("GBPUSD=X", "096742")}
MAXD, ZIN, PB, ADD = 60, 1.5, 1.0, 2.0
notes = []


def fred_quick(series):
    for i in range(2):
        try:
            r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers=UA, timeout=40)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text)); df.columns = ["d", "v"]
            return pd.Series(pd.to_numeric(df["v"], errors="coerce").to_numpy(), index=pd.to_datetime(df["d"])).dropna()
        except Exception as e:
            print("FRED", series, "failed:", e); time.sleep(5)
    return None


def us_rate():
    s = fred_quick("DTB3")
    if s is None:
        notes.append("US rate from Yahoo ^IRX (FRED unavailable)."); s = yahoo("^IRX")
    return s


def uk_rate():
    try:
        url = ("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2000"
               "&Dateto=now&SeriesCodes=IUDBEDR&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N")
        r = requests.get(url, headers={**UA, "Accept": "text/csv"}, timeout=60); r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy(), index=pd.to_datetime(df.iloc[:, 0], format="mixed")).dropna()
        if len(s) > 1000:
            notes.append("UK rate: Bank of England Bank Rate."); return s.sort_index()
    except Exception as e:
        print("BoE failed:", e)
    s = fred_quick("IRSTCI01GBM156N")
    if s is not None:
        notes.append("UK rate: OECD UK interbank rate via FRED (BoE unavailable)."); return s
    notes.append("UK rate unavailable: carry set to 0."); return None


def cot(code):
    try:
        url = ("https://publicreporting.cftc.gov/resource/6dca-aqww.csv?$limit=5000&cftc_contract_market_code=" + code +
               "&$select=report_date_as_yyyy_mm_dd,noncomm_positions_long_all,noncomm_positions_short_all,open_interest_all")
        r = requests.get(url, headers=UA, timeout=90); r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if len(df) > 500:
            df.columns = ["date", "long", "short", "oi"]; df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date").drop_duplicates("date").set_index("date")
    except Exception as e:
        print("CFTC API failed:", e)
    rows = []
    for y in range(2000, 2027):
        try:
            r = requests.get(f"https://www.cftc.gov/files/dea/history/deacot{y}.zip", headers=UA, timeout=60); r.raise_for_status()
            z = zipfile.ZipFile(io.BytesIO(r.content)); t = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
            cc = [c for c in t.columns if "CFTC Contract Market Code" in c][0]; t = t[t[cc].astype(str).str.strip() == code]
            dc = [c for c in t.columns if "YYYY-MM-DD" in c][0]
            rows.append(pd.DataFrame({"date": pd.to_datetime(t[dc]), "long": t["Noncommercial Positions-Long (All)"],
                                      "short": t["Noncommercial Positions-Short (All)"], "oi": t["Open Interest (All)"]}))
        except Exception as e:
            print("CFTC file", y, "failed:", e)
    return pd.concat(rows).sort_values("date").drop_duplicates("date").set_index("date")


def simulate(idx, want_dir, entry_ok, exit_fn):
    """want_dir(t) -> +1/-1/0 ; entry_ok(t, d) -> bool ; exit_fn(pos, t, held) -> bool. Max hold MAXD days."""
    out, pos = [], None
    for i, t in enumerate(idx):
        if pos is not None:
            held = i - pos["i"]
            if held >= MAXD or exit_fn(pos, t, held):
                pos["x"] = t; out.append(pos); pos = None
            continue
        d = want_dir(t)
        if d and entry_ok(t, d):
            pos = {"e": t, "i": i, "dir": int(d)}
    if pos:
        pos["x"] = idx[-1]; pos["open"] = True; out.append(pos)
    return out


def add_on(trs, px, m20, sd20, r_long, idx):
    """Rule D: account returns with a second unit at a 2 std stretch (capital for two units)."""
    acct = pd.Series(0.0, index=idx); per = []; n = 0
    for s in trs:
        days = r_long.loc[s["e"]:s["x"]].index[1:]
        legs = [days]
        for t in days:
            if t == s["x"]:
                break
            far = (px[t] - m20[t]) / sd20[t]
            if (s["dir"] > 0 and far <= -ADD) or (s["dir"] < 0 and far >= ADD):
                legs.append(r_long.loc[t:s["x"]].index[1:]); n += 1; break
        rets = []
        for dd in legs:
            r = s["dir"] * r_long.loc[dd]; acct.loc[dd] += 0.5 * r; rets.append(float((1 + r).prod() - 1))
        path = (1 + s["dir"] * r_long.loc[days]).cumprod() if len(days) else pd.Series([1.0])
        per.append({"e": s["e"], "x": s["x"], "dir": s["dir"], "ret": float(np.mean(rets)), "days": len(days),
                    "worst": float(path.min() - 1), "open": s.get("open", False)})
    return acct, pd.DataFrame(per), n


def run(cur):
    ysym, code = CUR[cur]
    px = yahoo(ysym).loc[START:END]; idx = px.index
    us = us_rate().reindex(idx, method="ffill") / 100
    uk = uk_rate()
    fx_rate = uk.reindex(idx, method="ffill") / 100 if uk is not None else us
    r_long = (px.pct_change() + (fx_rate - us) / 252).fillna(0)
    dxy = yahoo("DX-Y.NYB").reindex(idx).ffill().pct_change().fillna(0)
    tr_sign = np.sign(px / px.shift(252) - 1).shift(1).fillna(0)
    trend = tr_sign * r_long
    carry = np.sign(fx_rate - us).shift(1).fillna(0) * r_long
    factors = [dxy, trend, carry]

    c = cot(code); c["pos"] = (c["long"] - c["short"]) / c["oi"]
    prev = c["pos"].shift(1).rolling(156, min_periods=104)
    c["z"] = (c["pos"] - prev.mean()) / prev.std()
    u = c[["z", "pos"]].copy(); u.index = u.index + pd.Timedelta(days=4)
    z = u["z"].reindex(idx, method="ffill")
    m20, sd20 = px.rolling(20).mean(), px.rolling(20).std()
    far = (px - m20) / sd20

    crowd = lambda t: 1 if z[t] >= ZIN else (-1 if z[t] <= -ZIN else 0)
    pullback = lambda t, d: (d > 0 and far[t] <= -PB) or (d < 0 and far[t] >= PB)
    z_exit = lambda p, t, h: (p["dir"] > 0 and z[t] <= 0) or (p["dir"] < 0 and z[t] >= 0)

    res = {}
    trC = simulate(idx, crowd, pullback, z_exit)
    accC, perC = leg_returns(r_long, trC)
    res["C"] = summarise(accC, perC, idx); res["C"].update(alpha_vs(accC, factors, idx)); res["C"]["random_p"] = random_p(r_long, perC)
    accD, perD, nadd = add_on([dict(t) for t in trC], px, m20, sd20, r_long, idx)
    res["D"] = summarise(accD, perD, idx); res["D"].update(alpha_vs(accD, factors, idx)); res["D"]["adds"] = nadd
    trN = simulate(idx, crowd, lambda t, d: True, z_exit)
    accN, perN = leg_returns(r_long, trN)
    res["N"] = summarise(accN, perN, idx); res["N"].update(alpha_vs(accN, factors, idx)); res["N"]["random_p"] = random_p(r_long, perN)
    trT = simulate(idx, lambda t: int(tr_sign[t]), pullback, lambda p, t, h: tr_sign[t] != p["dir"])
    accT, perT = leg_returns(r_long, trT)
    res["T"] = summarise(accT, perT, idx); res["T"].update(alpha_vs(accT, factors, idx)); res["T"]["random_p"] = random_p(r_long, perT)

    C = res["C"]; ok = lambda v: v is not None
    pos_p = sum(1 for v in C["by_period"].values() if ok(v[0]) and v[0] > 0)
    passC = bool(ok(C["avg_trade_pct"]) and C["avg_trade_pct"] > 0 and C["avg_trade_pct"] > (res["N"]["avg_trade_pct"] or -1e9)
                 and C["avg_trade_pct"] > (res["T"]["avg_trade_pct"] or -1e9) and ok(C["random_p"]) and C["random_p"] < 0.10
                 and pos_p >= 2 and C["alpha_t"] >= 1.5)
    out = {"currency": cur, "pass_C": passC, "results": res, "notes": notes, "cot_first": str(c.index.min().date()),
           "cot_last": str(c.index.max().date()), "z_now": round(float(z.iloc[-1]), 2), "pos_now_pct": round(float(c["pos"].iloc[-1]) * 100, 1),
           "trades_C": [{"entry": str(t["e"].date()), "exit": str(t["x"].date()), "dir": "long" if t["dir"] > 0 else "short",
                         "open": t.get("open", False)} for t in trC]}
    (ROOT / f"results_fx_{cur.lower()}.json").write_text(json.dumps(out, indent=1, default=str))
    f = lambda v, u="%": "–" if v is None else f"{v}{u}"
    L = [f"# FX test 2 — {cur}/USD follow the crowd, enter on a 1 std pullback ({START[:4]} → {str(idx[-1].date())}) — Rule C: {'PASS' if passC else 'FAIL'}", "",
         f"CFTC {out['cot_first']} → {out['cot_last']}. Speculators now {out['pos_now_pct']}% of open interest, z = {out['z_now']}. " + " ".join(notes), "",
         "| Rule | Trades | Avg per trade | Won | Worst drop in a trade | Days held | Return/yr | Volatility | Sharpe | Worst fall | In market | Alpha/yr | Alpha t | Random p |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    names = {"C": "C — crowd direction + 1 std pullback", "D": "D — C + add-on at 2 std", "N": "Crowd, no pullback",
             "T": "Price trend + pullback, no CFTC"}
    for k in "CDNT":
        r = res[k]
        L.append(f"| {names[k]} | {r['trades']} | {f(r['avg_trade_pct'])} | {f(r['won_pct'])} | {f(r['worst_trade_drop_pct'])} | {f(r['avg_days'], '')} | "
                 f"{r['ret_yr_pct']}% | {r['vol_pct']}% | {r['sharpe']} | {r['max_dd_pct']}% | {r['in_market_pct']}% | {r['alpha_pct_yr']}% | {r['alpha_t']} | {f(r.get('random_p'), '')} |")
    L += ["", f"Rule D add-ons: {res['D']['adds']}.", "", "| Period | C avg (trades) | Crowd no pullback | Trend + pullback |", "|---|---|---|---|"]
    for name, *_ in PERIODS:
        g = lambda k: f"{f(res[k]['by_period'][name][0])} ({res[k]['by_period'][name][1]})"
        L.append(f"| {name} | {g('C')} | {g('N')} | {g('T')} |")
    (ROOT / f"results_fx_{cur.lower()}.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "GBP")
