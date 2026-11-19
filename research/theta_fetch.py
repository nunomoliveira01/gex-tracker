"""Rebuild daily history from ThetaData (Options Standard) for the pre-registered tests.

Runs inside GitHub Actions next to the Theta Terminal (localhost:25503). Raw trades are NEVER saved or
printed: each day is reduced to a handful of derived numbers (dealer gamma, XSP put premium buckets)
and appended to research/data/daily.csv. Resumable: days already in the file are skipped.

  python research/theta_fetch.py probe            # check endpoints and column names (no data printed)
  python research/theta_fetch.py fetch --hours 5  # work through missing days for up to N hours
"""
import argparse
import io
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "http://127.0.0.1:25503/v3"
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "daily.csv"
SYMBOL = "SPY"   # set by --symbol; anything other than SPY skips the XSP part and writes daily_<sym>.csv
START = date(2018, 11, 19)          # Standard plan: ~8 years of history
RATE = 0.03                          # flat rate for implied vol; small effect on gamma
COLS = ["date", "spy_close", "spy_low", "spy_high", "gex_close", "gex_low", "gex_high", "flip", "gex_week",
        "pc_volume", "n_contracts", "xsp_jp_prem", "xsp_jp_n", "xsp_proxy_prem", "xsp_small_ask_prem",
        "xsp_put_prem_all", "xsp_put_trades"]


# ------------------------------------------------------------------ terminal access
def get(path, **params):
    params.setdefault("format", "csv")
    for attempt in range(4):
        try:
            r = requests.get(f"{BASE}/{path}", params=params, timeout=300)
            if r.status_code == 200:
                return pd.read_csv(io.StringIO(r.text)) if r.text.strip() else pd.DataFrame()
            if r.status_code in (204, 404, 472) or "no data" in r.text.lower():
                return pd.DataFrame()
            msg = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            msg = str(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{path} failed: {msg}")


def norm(df):
    """Lower-case column names and map the usual variants to one name."""
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    ren = {"exp": "expiration", "open_interest": "oi", "openinterest": "oi", "vol": "volume"}
    df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
    if "right" in df.columns:
        df["right"] = df["right"].astype(str).str.upper().str[0]
    if "strike" in df.columns and df["strike"].abs().max() > 100000:   # thousandths of a dollar
        df["strike"] = df["strike"] / 1000.0
    if "expiration" in df.columns:
        df["expiration"] = pd.to_datetime(df["expiration"].astype(str).str.replace("-", "").str[:8], format="%Y%m%d")
    return df


# ------------------------------------------------------------------ maths
def ncdf(x):
    from scipy.special import ndtr
    return ndtr(x)


def bs_price(S, K, T, sig, call):
    d1 = (np.log(S / K) + (RATE + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    d2 = d1 - sig * np.sqrt(T)
    c = S * ncdf(d1) - K * np.exp(-RATE * T) * ncdf(d2)
    return np.where(call, c, c - S + K * np.exp(-RATE * T))


def implied_vol(price, S, K, T, call):
    lo, hi = np.full_like(price, 0.01), np.full_like(price, 4.0)
    for _ in range(40):                                   # bisection: robust and vectorised
        mid = (lo + hi) / 2
        too_high = bs_price(S, K, T, mid, call) > price
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
    iv = (lo + hi) / 2
    intrinsic = np.where(call, np.maximum(S - K, 0), np.maximum(K - S, 0))
    return np.where((price > intrinsic + 1e-4) & (iv > 0.011) & (iv < 3.9), iv, np.nan)


def gamma(S, K, T, sig):
    d1 = (np.log(S / K) + (RATE + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    return np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * sig * np.sqrt(T))


def net_gex(S, K, T, iv, oi, sign):
    """Dealer gamma in $bn per 1% move (calls +, puts -), same convention as the live tracker."""
    return float(np.nansum(sign * gamma(S, K, T, iv) * oi * 100 * S * S * 0.01) / 1e9)


# ------------------------------------------------------------------ one day
def spy_day(d, S, lo, hi):
    ds = d.strftime("%Y%m%d")
    oi = norm(get("option/history/open_interest", symbol=SYMBOL, expiration="*", date=ds))
    eod = norm(get("option/history/eod", symbol=SYMBOL, expiration="*", start_date=ds, end_date=ds))
    if oi.empty or eod.empty:
        return None
    key = ["expiration", "strike", "right"]
    df = eod.merge(oi[key + ["oi"]], on=key, how="inner")
    bid, ask = df.get("bid", pd.Series(0, index=df.index)), df.get("ask", pd.Series(0, index=df.index))
    mid = np.where((bid > 0) & (ask > 0), (bid + ask) / 2, df.get("close", np.nan))
    df["px"] = mid
    df["T"] = ((df["expiration"] - pd.Timestamp(d)).dt.days.clip(lower=0) + 0.0) / 365.0
    df = df[(df["T"] > 0) & (df["oi"] > 0) & (df["px"] > 0)].copy()      # same-day expiries are gone at the close
    call = (df["right"] == "C").to_numpy()
    df["iv"] = implied_vol(df["px"].to_numpy(float), S, df["strike"].to_numpy(float), df["T"].to_numpy(float), call)
    df = df.dropna(subset=["iv"])
    K, T, iv, o, sign = (df["strike"].to_numpy(float), df["T"].to_numpy(float), df["iv"].to_numpy(float),
                         df["oi"].to_numpy(float), np.where(df["right"] == "C", 1.0, -1.0))
    grid = np.linspace(S * 0.9, S * 1.1, 81)
    prof = np.array([net_gex(x, K, T, iv, o, sign) for x in grid])
    flip = None
    for i in range(len(grid) - 1):
        if prof[i] < 0 <= prof[i + 1] or prof[i] >= 0 > prof[i + 1]:
            if flip is None or abs(grid[i] - S) < abs(flip - S):
                flip = float(grid[i] + (grid[i + 1] - grid[i]) * (-prof[i]) / (prof[i + 1] - prof[i]))
    friday = pd.Timestamp(d) + pd.Timedelta(days=(4 - d.weekday()) % 7)
    wk = (df["expiration"] <= friday).to_numpy()
    vol = eod.get("volume")
    pc = None
    if vol is not None:
        pv, cv = eod.loc[eod["right"] == "P", "volume"].sum(), eod.loc[eod["right"] == "C", "volume"].sum()
        pc = float(pv / cv) if cv else None
    return {"gex_close": net_gex(S, K, T, iv, o, sign), "gex_low": net_gex(lo, K, T, iv, o, sign),
            "gex_high": net_gex(hi, K, T, iv, o, sign), "flip": flip,
            "gex_week": net_gex(S, K[wk], T[wk], iv[wk], o[wk], sign[wk]), "pc_volume": pc, "n_contracts": int(len(df))}


def xsp_day(d):
    ds = d.strftime("%Y%m%d")
    tq = norm(get("option/history/trade_quote", symbol="XSP", expiration="*", date=ds))
    if tq.empty:
        return {"xsp_jp_prem": 0.0, "xsp_jp_n": 0, "xsp_proxy_prem": 0.0, "xsp_small_ask_prem": 0.0,
                "xsp_put_prem_all": 0.0, "xsp_put_trades": 0}
    p = tq[tq["right"] == "P"].copy()
    price = p["price"].astype(float)
    size = p["size"].astype(float)
    at_ask = price >= p["ask"].astype(float) - 1e-9
    prem = price * size * 100
    dte = (p["expiration"] - pd.Timestamp(d)).dt.days
    jp = (size == 1) & at_ask
    return {"xsp_jp_prem": float(prem[jp].sum()), "xsp_jp_n": int(jp.sum()),
            "xsp_proxy_prem": float(prem[dte >= 5].sum()),
            "xsp_small_ask_prem": float(prem[(size <= 10) & at_ask].sum()),
            "xsp_put_prem_all": float(prem.sum()), "xsp_put_trades": int(len(p))}


# ------------------------------------------------------------------ main
def spy_prices():
    import yfinance as yf
    h = yf.Ticker(SYMBOL).history(start=START - timedelta(days=5), interval="1d", auto_adjust=False)
    h.index = pd.to_datetime(h.index).tz_localize(None).normalize()
    return h[["Close", "Low", "High"]]


def probe():
    """Show which endpoints answer and their column names, for one recent day. No values are printed."""
    d = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).date()
    while d.weekday() > 4:
        d -= timedelta(days=1)
    ds = d.strftime("%Y%m%d")
    for name, path, kw in [
        ("SPY open interest", "option/history/open_interest", dict(symbol="SPY", expiration="*", date=ds)),
        ("SPY eod", "option/history/eod", dict(symbol="SPY", expiration="*", start_date=ds, end_date=ds)),
        ("XSP trade_quote", "option/history/trade_quote", dict(symbol="XSP", expiration="*", date=ds)),
        ("SPY 2019 eod", "option/history/eod", dict(symbol="SPY", expiration="*", start_date="20190103", end_date="20190103")),
    ]:
        t0 = time.time()
        try:
            df = get(path, **kw)
            print(f"{name}: {len(df)} rows, {time.time() - t0:.1f}s, columns={list(df.columns)}")
        except Exception as e:
            print(f"{name}: FAILED {e}")


def fetch(hours):
    t_end = time.time() + hours * 3600
    done = set(pd.read_csv(OUT)["date"]) if OUT.exists() else set()
    px = spy_prices()
    days = [d.date() for d in px.index if d.date() >= START and d.strftime("%Y-%m-%d") not in done
            and d.date() < datetime.now(timezone.utc).replace(tzinfo=None).date()]
    print(f"{len(done)} days done, {len(days)} to go")
    OUT.parent.mkdir(exist_ok=True)
    from concurrent.futures import ThreadPoolExecutor

    def one(d):   # the terminal allows 4 requests at once: run 3 days side by side
        S, lo, hi = (float(px.loc[pd.Timestamp(d), c]) for c in ("Close", "Low", "High"))
        try:
            sp = spy_day(d, S, lo, hi)
            if sp is None:
                return d, None, "no SPY data"
            row = {"date": d.isoformat(), "spy_close": S, "spy_low": lo, "spy_high": hi, **sp, **(xsp_day(d) if SYMBOL == "SPY" else {})}
            return d, row, None
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
            print(f"  {n} days this run (last {d})")
    left = len(days) - n
    print(f"done this run: {n}; left: {left}")
    # no progress this run (days with no data) -> stop the chain instead of looping
    (ROOT / "data" / "remaining.txt").write_text(str(max(left, 0) if n else 0))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["probe", "fetch"])
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--symbol", default="SPY")
    a = ap.parse_args()
    if a.symbol.upper() != "SPY":
        SYMBOL = a.symbol.upper()
        OUT = ROOT / "data" / f"daily_{SYMBOL.lower()}.csv"   # columns named spy_* hold this symbol's prices
    probe() if a.mode == "probe" else fetch(a.hours)
