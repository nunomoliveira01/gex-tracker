"""Private archive: SPY/QQQ end-of-day option chains (open interest + end-of-day quotes, every contract) for one
ticker and one year. Writes one parquet per month into ./archive_out/ (NOT committed: the repo is public). The workflow
encrypts the files before uploading them as a short-lived artifact.

  python research/archive_fetch.py SPY 2021
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

import theta_fetch as tf

OUT = Path("archive_out")
KEEP = ["expiration", "strike", "right", "oi", "open", "high", "low", "close", "volume", "bid", "bid_size", "ask", "ask_size", "count"]


def day(sym, d):
    ds = d.strftime("%Y%m%d")
    for attempt in range(3):
        try:
            oi = tf.norm(tf.get("option/history/open_interest", symbol=sym, expiration="*", date=ds))
            eod = tf.norm(tf.get("option/history/eod", symbol=sym, expiration="*", start_date=ds, end_date=ds))
            break
        except Exception as e:
            if attempt == 2:
                print(f"{sym} {d}: {str(e)[:120]}", file=sys.stderr)
                return None
            time.sleep(5)
    if eod is None or eod.empty:
        return None
    key = ["expiration", "strike", "right"]
    df = eod.merge(oi[key + ["oi"]], on=key, how="outer") if oi is not None and not oi.empty else eod.assign(oi=pd.NA)
    df = df[[c for c in KEEP if c in df.columns]].copy()
    df.insert(0, "date", pd.Timestamp(d))
    return df


def main(sym, year):
    OUT.mkdir(exist_ok=True)
    h = yf.Ticker(sym).history(start=f"{year}-01-01", end=f"{year + 1}-01-01", interval="1d")
    days = [t.date() for t in pd.to_datetime(h.index).tz_localize(None) if t.date() >= date(2018, 11, 19) and t.date() < date.today()]
    print(f"{sym} {year}: {len(days)} trading days")
    with ThreadPoolExecutor(3) as ex:
        res = list(ex.map(lambda d: day(sym, d), days))
    got = [r for r in res if r is not None]
    print(f"{sym} {year}: {len(got)} days with data")
    if not got:
        return
    all_ = pd.concat(got, ignore_index=True)
    for c in ("strike", "close", "open", "high", "low", "bid", "ask"):
        if c in all_:
            all_[c] = pd.to_numeric(all_[c], errors="coerce").astype("float32")
    for c in ("oi", "volume", "bid_size", "ask_size", "count"):
        if c in all_:
            all_[c] = pd.to_numeric(all_[c], errors="coerce").astype("Int32")
    all_["right"] = all_["right"].astype("category")
    for m, part in all_.groupby(all_["date"].dt.month):
        f = OUT / f"{sym}_{year}-{m:02d}.parquet"
        part.to_parquet(f, index=False, compression="zstd")
        print(f"  {f.name}: {len(part):,} rows, {f.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]))
