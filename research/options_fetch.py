"""Test 13 data: SPY put prices (real bid/ask, 1-minute) on call-wall days and on random days
(rules: claude/opcoes-callwall-teste-pre-registo.md). Only derived numbers are saved: research/data/options_trades.csv.

  python research/options_fetch.py --hours 5
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import theta_fetch as tf

OUT = tf.ROOT / "data" / "options_trades.csv"
D = tf.ROOT / "data"


def quotes(**kw):
    q = tf.get("option/history/quote", symbol="SPY", interval="1m", **kw)
    if q is None or len(q) == 0:
        return pd.DataFrame()
    q = tf.norm(q)
    q["t"] = pd.to_datetime(q["timestamp"])
    q = q[(q["bid"] > 0) & (q["ask"] > 0) & (q["ask"] >= q["bid"])]
    q["mid"] = (q["bid"] + q["ask"]) / 2
    return q


def price_path(q):
    """SPY's price each minute from put-call parity: strike + call mid - put mid, at the strike where they're closest."""
    c = q[q["right"] == "C"].set_index(["t", "strike"])["mid"]
    p = q[q["right"] == "P"].set_index(["t", "strike"])["mid"]
    both = pd.concat([c.rename("c"), p.rename("p")], axis=1).dropna().reset_index()
    both["gap"] = (both["c"] - both["p"]).abs()
    best = both.loc[both.groupby("t")["gap"].idxmin()]
    return pd.Series((best["strike"] + best["c"] - best["p"]).to_numpy(), index=best["t"]).sort_index()


def at(q, strike, when, field, after=True):
    """First quote at/after `when` (or last at/before) for the put at `strike`."""
    s = q[(q["right"] == "P") & (np.isclose(q["strike"], strike))].sort_values("t")
    s = s[s["t"] >= when] if after else s[s["t"] <= when]
    if s.empty:
        return None, None
    r = s.iloc[0] if after else s.iloc[-1]
    return float(r[field]), r["t"]


def delta_put(S, K, T, price):
    iv = tf.implied_vol(np.array([price]), S, np.array([K]), np.array([T]), np.array([False]))[0]
    if not np.isfinite(iv):
        return None, None
    d1 = (np.log(S / K) + (tf.RATE + 0.5 * iv ** 2) * T) / (iv * np.sqrt(T))
    return float(tf.ncdf(d1) - 1), float(iv)


def main(hours):
    t_end = time.time() + hours * 3600
    days_all = pd.read_csv(D / "daily.csv", parse_dates=["date"])["date"].drop_duplicates().sort_values().reset_index(drop=True)
    cal = list(days_all.dt.date)
    pos = {d: i for i, d in enumerate(cal)}
    cw = pd.read_csv(D / "callwall.csv", parse_dates=["date"]).set_index("date")
    sig = [pd.Timestamp(t["entry_date"]) for t in json.loads((D / "cw_backtest.json").read_text())]
    sig = sorted(set(s for s in sig if s.date() in pos and pos[s.date()] + 1 < len(cal)))
    rng = np.random.default_rng(13)
    pool = [d for d in days_all if d.year >= 2019 and d not in set(sig) and pos[d.date()] + 1 < len(cal)]
    rnd = sorted(rng.choice(np.array(pool, dtype="datetime64[ns]"), 200, replace=False))
    jobs = [(d, "signal") for d in sig] + [(pd.Timestamp(d), "random") for d in rnd]
    done = set(map(tuple, pd.read_csv(OUT)[["date", "kind"]].astype(str).values)) if OUT.exists() else set()
    jobs = [(d, k) for d, k in jobs if (d.strftime("%Y-%m-%d"), k) not in done]
    exps = sorted(pd.to_datetime(tf.norm(tf.get("option/list/expirations", symbol="SPY"))["expiration"]).dt.date.unique())
    print(f"{len(done)} done, {len(jobs)} to go; {len(exps)} SPY expirations")

    def one(job):
        d, kind = job
        day = d.date(); ds = d.strftime("%Y%m%d")
        nxt = cal[pos[day] + 1]
        e0 = next((e for e in exps if e >= day), None)
        if e0 is None:
            return None, "no expiry"
        q0 = quotes(expiration=e0.strftime("%Y%m%d"), date=ds)
        if q0.empty:
            return None, "no quotes"
        path = price_path(q0)
        path = path[(path.index.time >= pd.Timestamp("09:31").time()) & (path.index.time <= pd.Timestamp("15:55").time())]
        if path.empty:
            return None, "no price path"
        if kind == "signal":
            wall = float(cw.at[d, "call_wall"]) if d in cw.index else None
            if wall is None:
                return None, "no wall"
            hit = path[path >= wall - 0.02]
            touched = not hit.empty
            t_in = hit.index[0] if touched else path.idxmax()
            K = wall
        else:
            wall, touched = None, True
            t_in = path.index[path.index.time >= pd.Timestamp("11:00").time()][0]
            K = None
        S_in = float(path.loc[t_in])
        strikes = np.sort(q0[q0["right"] == "P"]["strike"].unique())
        K = float(strikes[np.abs(strikes - (K if K is not None else S_in)).argmin()])
        tdist = lambda e: sum(1 for c in cal if day < c <= e)
        eA = day if day in exps else next((e for e in exps if e > day and tdist(e) <= 2), None)
        eB = next((e for e in exps if 2 <= tdist(e) <= 4), None)
        row = {"date": d.strftime("%Y-%m-%d"), "kind": kind, "entry_time": t_in.strftime("%H:%M"), "touched": touched,
               "spy_in": round(S_in, 3), "wall": wall, "strike": K, "spy_1555": round(float(path.iloc[-1]), 3)}
        t1555 = pd.Timestamp(f"{day} 15:55")
        for tag, e in (("A", eA), ("B", eB)):
            if e is None:
                continue
            qe = q0 if e == e0 else quotes(expiration=e.strftime("%Y%m%d"), strike=f"{K:.3f}", right="put", date=ds)
            ask, ta = at(qe, K, t_in, "ask")
            mid, _ = at(qe, K, t_in, "mid")
            out, _ = at(qe, K, t1555, "bid", after=False)
            if ask is None or out is None:
                continue
            T = max((pd.Timestamp(f"{e} 16:00") - ta).total_seconds(), 600) / (365 * 86400)
            dl, iv = delta_put(S_in, K, T, mid)
            row.update({f"{tag}_exp": str(e), f"{tag}_dte": tdist(e), f"{tag}_ask": ask, f"{tag}_out_close": out, f"{tag}_delta": dl, f"{tag}_iv": iv})
            if tag == "B":
                qn = quotes(expiration=e.strftime("%Y%m%d"), strike=f"{K:.3f}", right="put", date=nxt.strftime("%Y%m%d"))
                nb, _ = at(qn, K, pd.Timestamp(f"{nxt} 09:45"), "bid") if not qn.empty else (None, None)
                row["B_out_next"] = nb
        return row, None

    cols = ["date", "kind", "entry_time", "touched", "spy_in", "wall", "strike", "spy_1555",
            "A_exp", "A_dte", "A_ask", "A_out_close", "A_delta", "A_iv", "B_exp", "B_dte", "B_ask", "B_out_close", "B_delta", "B_iv", "B_out_next"]
    n = 0
    with ThreadPoolExecutor(3) as ex:
        for i in range(0, len(jobs), 15):
            if time.time() > t_end:
                break
            for job, (row, err) in zip(jobs[i:i + 15], ex.map(lambda j: (lambda r: r)(_safe(one, j)), jobs[i:i + 15])):
                if err:
                    print(f"{job[0].date()} {job[1]}: {err}", file=sys.stderr)
                    continue
                pd.DataFrame([row], columns=cols).to_csv(OUT, mode="a", header=not OUT.exists(), index=False)
                n += 1
            print(f"  {n} days this run")
    left = len(jobs) - n
    print(f"done this run: {n}; left: {left}")
    (D / "remaining.txt").write_text(str(max(left, 0) if n else 0))


def _safe(f, job):
    try:
        return f(job)
    except Exception as e:
        return None, str(e)[:160]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.0)
    main(ap.parse_args().hours)
