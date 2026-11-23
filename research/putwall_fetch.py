"""Test 9 data: SPY's put wall for each day, rebuilt from ThetaData (rules: claude/putwall-teste-pre-registo.md).

Put wall of day d = strike with the largest put gamma (gamma x open interest, puts only) between 95% and 100% of the
PREVIOUS close, using day d's start-of-day open interest. Also the open-interest-only wall. Only these derived
numbers are saved (research/data/putwall.csv); raw data is never written. Resumable.

  python research/putwall_fetch.py --hours 5
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import theta_fetch as tf

START = date(2023, 8, 1)
SIDE = "put"   # --side call: call wall (calls between 100% and 105% of the previous close) -> callwall.csv
OUT = tf.ROOT / "data" / "putwall.csv"
COLS = ["date", "prev_close", "put_wall", "put_wall_oi", "put_wall_gamma_bn"]


def day(d, S0):
    ds = d.strftime("%Y%m%d")
    oi = tf.norm(tf.get("option/history/open_interest", symbol="SPY", expiration="*", date=ds))
    eod = tf.norm(tf.get("option/history/eod", symbol="SPY", expiration="*", start_date=ds, end_date=ds))
    if oi.empty or eod.empty:
        return None
    key = ["expiration", "strike", "right"]
    df = eod.merge(oi[key + ["oi"]], on=key, how="inner")
    if SIDE == "call":
        df = df[(df["right"] == "C") & (df["strike"] >= S0) & (df["strike"] <= S0 * 1.05)].copy()
    else:
        df = df[(df["right"] == "P") & (df["strike"] >= S0 * 0.95) & (df["strike"] <= S0)].copy()
    bid, ask = df.get("bid", pd.Series(0, index=df.index)), df.get("ask", pd.Series(0, index=df.index))
    df["px"] = np.where((bid > 0) & (ask > 0), (bid + ask) / 2, df.get("close", np.nan))
    # start-of-day view: options expiring on day d are still alive in the morning -> give them a few hours
    df["T"] = ((df["expiration"] - pd.Timestamp(d)).dt.days.clip(lower=0) + 0.25) / 365.0
    df = df[(df["oi"] > 0) & (df["px"] > 0)]
    if df.empty:
        return None
    iv = tf.implied_vol(df["px"].to_numpy(float), S0, df["strike"].to_numpy(float), df["T"].to_numpy(float),
                        np.full(len(df), SIDE == "call"))
    df["iv"] = np.where(np.isnan(iv), np.nanmedian(iv) if np.isfinite(np.nanmedian(iv)) else 0.2, iv)
    df["g"] = tf.gamma(S0, df["strike"].to_numpy(float), df["T"].to_numpy(float), df["iv"].to_numpy(float)) \
        * df["oi"] * 100 * S0 * S0 * 0.01 / 1e9
    by = df.groupby("strike").agg(g=("g", "sum"), oi=("oi", "sum"))
    return {f"{SIDE}_wall": float(by["g"].idxmax()), f"{SIDE}_wall_oi": float(by["oi"].idxmax()),
            f"{SIDE}_wall_gamma_bn": round(float(by["g"].max()), 3)}


def main(hours):
    t_end = time.time() + hours * 3600
    done = set(pd.read_csv(OUT)["date"]) if OUT.exists() else set()
    h = yf.Ticker("SPY").history(start=START - timedelta(days=10), interval="1d", auto_adjust=False)
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    prev = h["Close"].shift(1)
    today = datetime.now(timezone.utc).date()
    days = [t.date() for t in h.index if t.date() >= START and t.date() < today and t.strftime("%Y-%m-%d") not in done]
    print(f"{len(done)} days done, {len(days)} to go")

    def one(d):
        try:
            S0 = float(prev.loc[pd.Timestamp(d)])
            r = day(d, S0)
            return d, (None if r is None else {"date": d.isoformat(), "prev_close": round(S0, 2), **r}), None if r else "no data"
        except Exception as e:
            return d, None, str(e)[:150]

    n = 0
    with ThreadPoolExecutor(3) as ex:
        for i in range(0, len(days), 30):
            if time.time() > t_end:
                break
            for d, row, err in ex.map(one, days[i:i + 30]):
                if err:
                    print(f"{d}: {err}", file=sys.stderr)
                    continue
                pd.DataFrame([row], columns=COLS).to_csv(OUT, mode="a", header=not OUT.exists(), index=False)
                n += 1
            print(f"  {n} days this run")
    left = len(days) - n
    print(f"done this run: {n}; left: {left}")
    (tf.ROOT / "data" / "remaining.txt").write_text(str(max(left, 0) if n else 0))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--side", default="put", choices=["put", "call"])
    a = ap.parse_args()
    if a.side == "call":
        SIDE, OUT = "call", tf.ROOT / "data" / "callwall.csv"
        COLS = ["date", "prev_close", "call_wall", "call_wall_oi", "call_wall_gamma_bn"]
    main(a.hours)
