"""Backtest every block strategy in config.json on the daily ThetaData history (Nov 2018 -> Nov 2026).
Runs automatically when config.json changes (workflow blocks-backtest.yml); skips strategies whose blocks didn't change.
Same blocks as the live engine (gex.py strategy_signals); daily bars, fills at the exact levels, no costs.
Writes research/data/<backtest_file> (trade list for the site) and research/data/bt_summary.json (scores)."""
import hashlib
import json
import sys

import numpy as np
import pandas as pd

from backtest import ROOT
from exact_level import level_price
from metrics import stats as acct_stats
from walls_oos import legs_account, per_trade, yahoo

REPO = ROOT.parent
PERIODS = {"oos": ("2018-11-19", "2023-07-31"), "is": ("2023-11-17", "2026-11-17"), "all": ("2018-11-19", "2026-11-17")}
YAHOO = {"NQ": "NQ=F", "ES": "ES=F", "QQQ": "QQQ", "SPY": "SPY"}
rng = np.random.default_rng(12)


def clip(px, r):
    return float(min(max(px, r["spy_low"]), r["spy_high"]))


def trades(d, s, start):
    long = s.get("side") == "long"
    sign = 1 if long else -1
    e, filters, exits, add = s.get("entry", {}), s.get("filters", []), s.get("exits") or [{"type": "flip"}], s.get("add_on") or {}
    prev = d["pc_volume"].shift(1).rolling(60)
    z = (d["pc_volume"] - prev.mean()) / prev.std()
    out, pos = [], None
    pclose = d["spy_close"].shift(1)
    for i, (t, r) in enumerate(d.iterrows()):
        if pos is None:
            if any((f.get("type") == "gamma_negative" and not r["gex_low"] < 0) or (f.get("type") == "gamma_positive" and not r["gex_high"] > 0) for f in filters):
                continue
            et, px, lvl = e.get("type"), None, None
            if et in ("put_wall", "flip_down"):
                lvl = r["put_wall"] if et == "put_wall" else r["flip"]
                ok = pd.notna(lvl) and r["spy_low"] <= lvl and (et == "put_wall" or pclose.iloc[i] > lvl)
                px = (r["open"] if r["open"] < lvl else lvl) if ok else None
            elif et in ("call_wall", "flip_up"):
                lvl = r["call_wall"] if et == "call_wall" else r["flip"]
                ok = pd.notna(lvl) and r["spy_high"] >= lvl and (et == "call_wall" or pclose.iloc[i] < lvl)
                px = (r["open"] if r["open"] > lvl else lvl) if ok else None
            elif et == "gamma_below" and r["gex_low"] <= float(e.get("bn", -10)):
                lp = level_price(r, float(e.get("bn", -10)))
                px = r["open"] if (lp is None or lp >= r["open"]) else lp
            elif et == "gamma_above" and r["gex_high"] >= float(e.get("bn", 0)):
                lp = level_price(r, float(e.get("bn", 0)))
                px = r["open"] if (lp is None or lp <= r["open"]) else lp
            if px is not None and pd.notna(px):
                pos = {"e": t, "i": i, "e_px": clip(px, r), "a": None, "a_px": None}
            continue
        e_px, fav, stop, timed = pos["e_px"], [], None, False
        for x in exits:
            xt = x.get("type")
            if xt == "opposite_wall":
                w = r["call_wall" if long else "put_wall"]
                if pd.notna(w) and ((long and r["spy_high"] >= w) or (not long and r["spy_low"] <= w)):
                    fav.append((w, "wall"))
            elif xt in ("flip", "gamma_level"):
                bn = 0.0 if xt == "flip" else float(x.get("bn", 0))
                if (long and r["gex_high"] >= bn) or (not long and r["gex_low"] <= bn):
                    fp = r["flip"] if (xt == "flip" and pd.notna(r["flip"])) else level_price(r, bn)
                    fav.append((fp if fp is not None else r["open"], "flip" if xt == "flip" else "gamma"))
            elif xt == "target_pct":
                lv = e_px * (1 + sign * float(x.get("pct", 1)) / 100)
                if (long and r["spy_high"] >= lv) or (not long and r["spy_low"] <= lv):
                    fav.append((lv, "target"))
            elif xt == "stop_pct":
                lv = e_px * (1 - sign * float(x.get("pct", 1)) / 100)
                if (long and r["spy_low"] <= lv) or (not long and r["spy_high"] >= lv):
                    stop = lv
            elif xt == "days" and i - pos["i"] >= int(x.get("n", 5)):
                timed = True
        if stop is not None:                                   # a stop wins a tie (the cautious read)
            px = r["open"] if ((long and r["open"] <= stop) or (not long and r["open"] >= stop)) else stop
            out.append({**pos, "x": t, "x_px": clip(px, r), "open": False, "why": "stop"}); pos = None
            continue
        if fav:
            lv, why = min(fav) if long else max(fav)            # the level SPY reaches first
            px = r["open"] if ((long and r["open"] >= lv) or (not long and r["open"] <= lv)) else lv
            out.append({**pos, "x": t, "x_px": clip(px, r), "open": False, "why": why}); pos = None
            continue
        if timed:
            out.append({**pos, "x": t, "x_px": float(r["spy_close"]), "open": False, "why": "time"}); pos = None
            continue
        if add.get("on") and pos["a"] is None and z.iloc[i] == z.iloc[i]:
            zz = float(add.get("z", 1.0))
            if (long and r["spy_close"] < e_px and z.iloc[i] >= zz) or (not long and r["spy_close"] > e_px and z.iloc[i] <= -zz):
                pos["a"], pos["a_px"] = t, float(r["spy_close"])
    if pos:
        out.append({**pos, "x": d.index[-1], "x_px": float(d["spy_close"].iloc[-1]), "open": True, "why": "open"})
    return [s for s in out if s["e"] >= pd.Timestamp(start)]


def spec_hash(s):
    keep = {k: s.get(k) for k in ("side", "trade", "entry", "filters", "exits", "add_on")}
    return hashlib.sha1(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def main():
    cfg = json.loads((REPO / "config.json").read_text())
    specs = [s for s in cfg.get("strategies", []) if s.get("on", True)]
    sum_path = ROOT / "data" / "bt_summary.json"
    summary = json.loads(sum_path.read_text()) if sum_path.exists() else {}
    force = "--force" in sys.argv
    todo = [s for s in specs if force or summary.get(s["id"], {}).get("hash") != spec_hash(s)]
    if not todo:
        print("No strategy changed; nothing to backtest.")
        return
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall"]]).join(cw[["call_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    spy = d["spy_close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    inst_cache = {}
    for s in todo:
        tk = s.get("trade", "NQ")
        if tk not in inst_cache:
            inst_cache[tk] = spy if tk == "SPY" else yahoo(YAHOO.get(tk, tk)).reindex(d.index).ffill()["Close"]
        inst = inst_cache[tk]
        sign = 1 if s.get("side") == "long" else -1
        res, web = {"hash": spec_hash(s), "name": s.get("name", s["id"]), "periods": {}}, []
        for per, (a, b) in PERIODS.items():
            idx = d.loc[a:b].index
            i_ret, s_ret = inst.pct_change(), spy.pct_change()
            win = i_ret.loc[a:b].dropna().index
            beta = 1.0 if tk == "SPY" else float(np.cov(i_ret.loc[win], s_ret.loc[win].fillna(0))[0, 1] / s_ret.loc[win].var())
            conv = lambda t, px, beta=beta: inst.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
            rf = rf_all.loc[idx].fillna(0)
            spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
            tr = trades(d.loc[:b], s, a)
            done = [x for x in tr if not x["open"]]
            if not done:
                res["periods"][per] = {"n": 0}
                continue
            acct = legs_account(d.index, tr, inst, conv, sign, lambda x: 2.0).loc[idx].fillna(0)
            st = acct_stats(acct, spy_ex, rf)
            rets = np.array([r for _, r in per_trade(tr, [(conv, sign, lambda x: 1.0)])])
            days = np.array([max(1, d.index.get_loc(x["x"]) - d.index.get_loc(x["e"])) for x in done])
            ci = np.log(inst.loc[a:b]).to_numpy()
            hits = sum(np.expm1(sign * (ci[k + days] - ci[k])).mean() >= rets.mean()
                       for k in (rng.integers(0, len(ci) - days.max() - 1, len(days)) for _ in range(5000)))
            res["periods"][per] = {"n": len(done), "ev_pct": round(rets.mean() * 100, 2), "won_pct": int(round((rets > 0).mean() * 100)),
                                   "avg_days": round(float(days.mean()), 1), "max_days": int(days.max()), "worst_pct": round(rets.min() * 100, 2),
                                   "cagr_pct": st["cagr_pct"], "sharpe": st["sharpe"], "max_dd_pct": st["max_dd_pct"], "beta": st["beta"],
                                   "alpha_pct_yr": st["alpha_pct_yr"], "alpha_t": st["alpha_t"], "random_p": round(hits / 5000, 4)}
            if per in ("oos", "is"):
                for x in tr:
                    if x["open"]:
                        continue
                    units = [conv(x["e"], x["e_px"])] + ([conv(x["a"], x["a_px"])] if x["a"] is not None else [])
                    xp = conv(x["x"], x["x_px"])
                    pts = sum(sign * (xp - u) for u in units)
                    web.append({"period": per, "entry_date": str(x["e"].date()), "entry_spy": round(x["e_px"], 2), "entry": round(units[0], 2),
                                "add_date": str(x["a"].date()) if x["a"] is not None else None, "add": round(units[1], 2) if len(units) > 1 else None,
                                "exit_date": str(x["x"].date()), "exit_spy": round(x["x_px"], 2), "exit": round(xp, 2), "why": x["why"],
                                "pnl_pts": round(pts, 2), "pct": round(pts / sum(units) * 100, 2)})
        summary[s["id"]] = res
        (ROOT / "data" / s.get("backtest_file", f"bt_{s['id']}.json")).write_text(json.dumps(web, indent=1))
        print(s["id"], json.dumps(res["periods"]))
    sum_path.write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
