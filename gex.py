#!/usr/bin/env python3
"""
Free dealer gamma (GEX) tracker for SPY / QQQ using Cboe's public delayed option chains.

Each run:
  1. downloads the full option chain from Cboe (about 15 minutes delayed, free),
  2. works out dealer gamma exposure, call wall, put wall and the gamma flip level,
  3. adds one row to data/<TICKER>_history.csv,
  4. redraws the charts and the web page in docs/.

Assumption (the standard "naive" one): dealers are long the calls and short the puts.

Usage:
  python gex.py                 # one snapshot now (skips if US market is closed)
  python gex.py --force         # snapshot even if market is closed
  python gex.py --loop 15       # keep running, snapshot every 15 minutes (local use)
  python gex.py --charts-only   # just redraw charts from saved history
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

import margin as margin_mod

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
ET = ZoneInfo("America/New_York")
CONFIG = json.loads((ROOT / "config.json").read_text())
# Indices on Cboe are "_SPX", "_RUT"...; on Yahoo "^GSPC", "^RUT"...
INDEXES = {"SPX", "NDX", "RUT", "VIX", "XSP", "DJX", "OEX", "XEO", "MRUT", "XND"}
WATCHLIST = ROOT / "watchlist.json"   # the ticker pool, edited from the web page


def apply_watchlist():
    """Tickers to compute come from watchlist.json (edited on the web page); config.json is the fallback."""
    if WATCHLIST.exists():
        try:
            w = json.loads(WATCHLIST.read_text())
            ticks = []
            for t in w.get("tickers", []):
                t = re.sub(r"[^A-Z.]", "", str(t).upper())[:8]
                if t and t not in ticks and t not in CONFIG.get("futures", {}):
                    ticks.append(t)
            sig = CONFIG.get("signal_ticker", "SPY")
            if sig not in ticks:
                ticks.insert(0, sig)   # the JP rule needs it
            CONFIG["tickers"] = ticks
            if "futures" in w:
                CONFIG["futures"] = {k: v for k, v in CONFIG.get("futures", {}).items() if k in w["futures"]}
        except Exception as e:
            print(f"watchlist.json unreadable ({e}) - using config.json", file=sys.stderr)
    for t in CONFIG["tickers"]:
        if t in INDEXES:
            CONFIG.setdefault("cboe_symbols", {}).setdefault(t, "_" + t)
            CONFIG.setdefault("yahoo_symbols", {}).setdefault(t, "^" + t)


apply_watchlist()
STATUS = {}


def write_tickers():
    """docs/tickers.json: the pool and whether each ticker's last attempt worked (for the web page)."""
    path = DOCS / "tickers.json"
    try:
        old = json.loads(path.read_text()).get("status", {})
    except Exception:
        old = {}
    old.update(STATUS)
    out = {"updated_utc": pd.Timestamp.now(tz="UTC").isoformat(), "pool": CONFIG["tickers"],
           "futures": list(CONFIG.get("futures", {})), "indexes": sorted(INDEXES), "status": old}
    DOCS.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")))

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}
OPT_RE = re.compile(r"^(?P<root>[A-Z_]+?)(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")
HIST_COLS = ["time_et", "spot", "net_gex_bn", "call_wall", "put_wall", "flip", "iv30", "rv5", "rv20", "gex_week_bn",
             "pc_volume_ratio", "put_volume", "call_volume"]


# ----------------------------------------------------------------------------- data
def fetch_chain(ticker, file=None):
    """Return (raw_json) from Cboe, or from a local file for testing."""
    if file:
        return json.loads(Path(file).read_text())
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(CBOE_URL.format(ticker), headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # network hiccup -> retry
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Could not download {ticker} chain from Cboe: {last_err}")


def parse_chain(raw):
    """Turn Cboe JSON into (spot, snapshot time ET, DataFrame of options)."""
    d = raw.get("data", raw)
    spot = d.get("current_price") or d.get("close") or d.get("prev_day_close")
    if not spot:
        raise ValueError("No current price in Cboe data")
    ts = raw.get("timestamp") or d.get("last_trade_time")
    now = pd.Timestamp.now(tz=ET)
    try:
        t = pd.Timestamp(ts)
        # Cboe's timestamp is in UTC (no zone given) -> convert to New York time
        t = t.tz_localize("UTC").tz_convert(ET) if t.tzinfo is None else t.tz_convert(ET)
        if abs((t - now).total_seconds()) > 3 * 3600 and not os.environ.get("GEX_TEST"):
            t = now  # timestamp looks wrong: fall back to the clock
    except Exception:
        t = now

    rows = []
    for o in d.get("options", []):
        m = OPT_RE.match(str(o.get("option", "")))
        if not m:
            continue
        rows.append({
            "expiry": datetime.strptime(m["exp"], "%y%m%d").date(),
            "type": m["cp"],
            "strike": int(m["strike"]) / 1000.0,
            "oi": float(o.get("open_interest") or 0),
            "gamma": float(o.get("gamma") or 0),
            "iv": float(o.get("iv") or 0),
            "volume": float(o.get("volume") or 0),
            "bid": float(o.get("bid") or 0),
            "ask": float(o.get("ask") or 0),
            "last": float(o.get("last_trade_price") or o.get("last") or 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("Cboe data had no options")
    # Cboe sometimes briefly serves a chain without open interest (seen 17 Nov 9:42 New York); a reading
    # built on it shows gamma 0 and would fake an exit signal, so skip it
    if (df["oi"] > 0).sum() < 50:
        raise ValueError("Cboe data incomplete (no open interest) - reading skipped")
    if df.loc[df["iv"] > 0, "iv"].median() > 3:  # volatility given in percent -> decimals
        df["iv"] = df["iv"] / 100
    return float(spot), t, df


# ----------------------------------------------------------------------------- maths
def years_to_expiry(expiry_dates, now):
    """Time to 4pm ET on expiry day, in years (at least ~1 hour)."""
    close = pd.to_datetime([datetime.combine(e, datetime.min.time()) + timedelta(hours=16)
                            for e in expiry_dates]).tz_localize(ET)
    secs = (close - now).total_seconds().to_numpy()
    return np.maximum(secs, 3600) / (365.0 * 24 * 3600)


def bs_gamma(S, K, iv, T):
    """Black-Scholes gamma, vectorised. S may be a column vector for a price grid."""
    sqrtT = np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * iv ** 2 * T) / (iv * sqrtT)
    return np.exp(-0.5 * d1 ** 2) / (np.sqrt(2 * np.pi) * S * iv * sqrtT)


def analyse(spot, now, df, reprice=False):
    """Work out everything we want from one snapshot. reprice=True recomputes each option's gamma at
    `spot` (used outside US hours, when Cboe's gamma is from the last close but futures have moved)."""
    if reprice:
        df = df.copy()
        T = years_to_expiry(df["expiry"].to_numpy(), now)
        ok = (df["iv"] > 0.01).to_numpy()
        g = np.zeros(len(df))
        g[ok] = bs_gamma(spot, df["strike"].to_numpy()[ok], df["iv"].to_numpy()[ok], T[ok])
        df["gamma"] = g
    # put/call volume uses every option that traded today (including ones expiring today)
    pv = df.loc[(df["type"] == "P") & (df["expiry"] >= now.date()), "volume"].sum()
    cv = df.loc[(df["type"] == "C") & (df["expiry"] >= now.date()), "volume"].sum()
    after_close = now.hour >= 16
    df = df[(df["expiry"] > now.date()) | ((df["expiry"] == now.date()) & (not after_close))].copy()
    sign = np.where(df["type"] == "C", 1.0, -1.0)
    # dollars of SPY/QQQ dealers must trade for a 1% move
    df["gex"] = sign * df["gamma"] * df["oi"] * 100 * spot * spot * 0.01
    net = df["gex"].sum()

    # same formula using today's traded volume instead of open interest (shows fresh activity)
    df["vgex"] = sign * df["gamma"] * df["volume"] * 100 * spot * spot * 0.01

    near = df[(df["strike"] > spot * 0.85) & (df["strike"] < spot * 1.15)]
    by_strike = pd.DataFrame(index=np.sort(near["strike"].unique()))
    for col, src, typ in [("C", "gex", "C"), ("P", "gex", "P"), ("vC", "vgex", "C"), ("vP", "vgex", "P")]:
        by_strike[col] = near[near["type"] == typ].groupby("strike")[src].sum()
    by_strike = by_strike.fillna(0.0)
    by_strike["net"] = by_strike["C"] + by_strike["P"]
    by_strike["vnet"] = by_strike["vC"] + by_strike["vP"]
    # call wall: biggest call strike above the price; put wall: biggest put strike below it
    wb = by_strike[(by_strike.index > spot * 0.95) & (by_strike.index < spot * 1.05)]  # within 5%
    above, below = wb[wb.index >= spot], wb[wb.index <= spot]
    call_wall = float(above["C"].idxmax()) if len(above) and above["C"].max() > 0 else np.nan
    put_wall = float(below["P"].idxmin()) if len(below) and below["P"].min() < 0 else np.nan

    # gamma flip: re-price gamma over a grid of prices and find where net crosses 0
    use = df[(df["oi"] > 0) & (df["iv"] > 0.01) &
             (df["strike"] > spot * 0.7) & (df["strike"] < spot * 1.3)]
    flip = np.nan
    if not use.empty:
        T = years_to_expiry(use["expiry"].to_numpy(), now)
        K = use["strike"].to_numpy()
        iv = use["iv"].to_numpy()
        w = np.where(use["type"].to_numpy() == "C", 1.0, -1.0) * use["oi"].to_numpy() * 100
        grid = np.linspace(spot * 0.85, spot * 1.15, 241)
        g = bs_gamma(grid[:, None], K[None, :], iv[None, :], T[None, :])
        curve = (g * w[None, :]).sum(axis=1) * grid ** 2 * 0.01
        cross = np.where(np.diff(np.sign(curve)) != 0)[0]
        if len(cross):
            # pick the crossing closest to the current price, interpolate
            i = cross[np.argmin(np.abs(grid[cross] - spot))]
            x0, x1, y0, y1 = grid[i], grid[i + 1], curve[i], curve[i + 1]
            flip = float(x0 - y0 * (x1 - x0) / (y1 - y0))

    # implied volatility ~30 days out, near the money (what the options market expects)
    dte = np.array([(e - now.date()).days for e in df["expiry"]])
    sel = (dte >= 20) & (dte <= 45) & (np.abs(df["strike"].to_numpy() / spot - 1) < 0.02) & (df["iv"].to_numpy() > 0.01)
    iv30 = round(float(df["iv"].to_numpy()[sel].mean()), 4) if sel.any() else np.nan
    # gamma in options that expire by this Friday (released mechanically at expiry)
    friday = now.date() + timedelta(days=(4 - now.weekday()) % 7)
    gex_week = df.loc[df["expiry"] <= friday, "gex"].sum()
    return {
        "time_et": now.strftime("%Y-%m-%d %H:%M"),
        "spot": round(spot, 2),
        "net_gex_bn": round(net / 1e9, 3),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "flip": round(flip, 2) if not np.isnan(flip) else np.nan,
        "pc_volume_ratio": round(pv / cv, 3) if cv else np.nan,
        "put_volume": int(pv),
        "call_volume": int(cv),
        "iv30": iv30,
        "gex_week_bn": round(gex_week / 1e9, 3),
    }, by_strike


# ----------------------------------------------------------------------------- history
def hist_path(t):
    return DATA / f"{t}_history.csv"


def load_history(t):
    p = hist_path(t)
    if p.exists():
        h = pd.read_csv(p)
        h["time_et"] = pd.to_datetime(h["time_et"])
        return h
    return pd.DataFrame(columns=HIST_COLS)


def append_history(t, row):
    DATA.mkdir(exist_ok=True)
    h = load_history(t)
    if len(h) and h["time_et"].iloc[-1].strftime("%Y-%m-%d %H:%M") == row["time_et"]:
        print(f"  {t}: Cboe data not updated since last snapshot, skipping save")
        return h
    new = pd.DataFrame([row])
    new["time_et"] = pd.to_datetime(new["time_et"])
    h = pd.concat([h, new], ignore_index=True) if len(h) else new
    h.to_csv(hist_path(t), index=False, date_format="%Y-%m-%d %H:%M")
    return h


# ----------------------------------------------------------------------------- retail panic gauge
RETAIL_PATH = DATA / "retail_history.csv"


def snapshot_retail(file=None):
    """Free stand-in for JP's retail signal: dollar premium of XSP (mini-SPX) puts traded today with
    5+ days to expiry. JP uses only 1-lot puts bought at the ask; free data has no trade sizes or sides,
    so this counts all XSP put premium (XSP is mostly traded by small accounts)."""
    raw = None
    for sym in CONFIG.get("retail_symbols", ["_XSP", "XSP"]):
        try:
            raw = fetch_chain(sym, file.format("XSP") if file else None)
            break
        except Exception as e:
            last_err = e
    if raw is None:
        raise RuntimeError(f"XSP chain unavailable: {last_err}")
    _, t, df = parse_chain(raw)
    px = np.where((df["bid"] > 0) & (df["ask"] > 0), (df["bid"] + df["ask"]) / 2, df["last"])
    dte = np.array([(e - t.date()).days for e in df["expiry"]])
    m = (df["type"].to_numpy() == "P") & (dte >= 5)
    row = {"time_et": t.strftime("%Y-%m-%d %H:%M"),
           "put_premium": round(float((df["volume"].to_numpy()[m] * px[m] * 100).sum()), 0),
           "put_volume": int(df["volume"].to_numpy()[m].sum())}
    DATA.mkdir(exist_ok=True)
    old = pd.read_csv(RETAIL_PATH) if RETAIL_PATH.exists() else pd.DataFrame(columns=list(row))
    if len(old) and old["time_et"].iloc[-1] == row["time_et"]:
        return row
    pd.concat([old, pd.DataFrame([row])], ignore_index=True).to_csv(RETAIL_PATH, index=False)
    return row


def daily_retail(lookback=60):
    """One value per day (last reading = whole day), z-score vs the previous `lookback` days."""
    if not RETAIL_PATH.exists():
        return pd.DataFrame(columns=["date", "premium", "mean", "std", "bar", "z", "complete"])
    r = pd.read_csv(RETAIL_PATH)
    r["time_et"] = pd.to_datetime(r["time_et"])
    r["date"] = r["time_et"].dt.date
    daily = r.groupby("date").last().reset_index()
    daily = daily.rename(columns={"put_premium": "premium"})
    daily["complete"] = daily["time_et"].dt.hour >= 16
    prior = daily["premium"].where(daily["complete"]).shift(1).rolling(lookback, min_periods=10)
    daily["mean"], daily["std"] = prior.mean(), prior.std()
    daily["bar"] = daily["mean"] + CONFIG.get("add_min_z", 1.0) * daily["std"]
    daily["z"] = (daily["premium"] - daily["mean"]) / daily["std"]
    return daily[["date", "premium", "mean", "std", "bar", "z", "complete"]]


# ----------------------------------------------------------------------------- JP rule
def entry_level(h, t=None):
    lv = CONFIG.get("entry_level_bn")
    if isinstance(lv, dict):
        lv = lv.get(t)
    if lv is not None:
        return float(lv), "fixed"
    if len(h) >= CONFIG.get("auto_level_min_rows", 450):
        return float(np.nanpercentile(h["net_gex_bn"], 15)), "auto: lowest 15% of your history"
    return None, "collecting history"


def jp_signals(h, level, retail_daily, need_level=True):
    """JP's rule: first entry when gamma <= level; add (max N) at a day's close if gamma is still
    <= level, the retail gauge z >= add_min_z AND the close is below the first entry; exit when gamma >= 0."""
    trades, pos = [], None
    if level is None or h.empty:
        return trades, pos
    zmap = dict(zip(retail_daily["date"], retail_daily["z"])) if len(retail_daily) else {}
    h = h.reset_index(drop=True)
    dates = h["time_et"].dt.date
    last_of_day = set(h.groupby(dates).tail(1).index)
    max_adds, zmin = CONFIG.get("max_adds", 1), CONFIG.get("add_min_z", 1.0)
    for i, r in h.iterrows():
        g, p = r["net_gex_bn"], r["spot"]
        if pos is not None and g >= 0:
            units = [pos["entry"]] + pos["adds"]
            trades.append({**pos, "exit": (i, p), "pnl": sum(p - u[1] for u in units)})
            pos = None
            continue
        if pos is None and g <= level:
            pos = {"entry": (i, p), "adds": []}
            continue
        is_close = i in last_of_day and r["time_et"].hour >= 16
        if pos is not None and is_close and (g <= level or not need_level) and len(pos["adds"]) < max_adds:
            z = zmap.get(dates[i])
            if z is not None and not pd.isna(z) and z >= zmin and p < pos["entry"][1]:
                pos["adds"].append((i, p))
    return trades, pos


def write_signal_json(t, h, level, note, trades, pos, retail_daily, name="signal", extra=None):
    """Status of JP's rule (or a variant of it) for the live page."""
    rd = retail_daily.iloc[-1].to_dict() if len(retail_daily) else {}
    clean = lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else v
    last = h.iloc[-1]
    out = {"ticker": t, "time_et": last["time_et"].strftime("%Y-%m-%d %H:%M"), "level": level, "level_note": note,
           "gamma": float(last["net_gex_bn"]), "spot": float(last["spot"]),
           "add_min_z": CONFIG.get("add_min_z", 1.0), "max_adds": CONFIG.get("max_adds", 1),
           "retail": {"date": str(rd.get("date", "")), "premium": clean(rd.get("premium")),
                      "bar": clean(rd.get("bar")), "z": clean(rd.get("z")),
                      "complete": bool(rd.get("complete", False)),
                      "days": int(len(retail_daily))},
           "position": None,
           "closed": [{"entry_time": h["time_et"][tr["entry"][0]].strftime("%Y-%m-%d %H:%M"), "entry": tr["entry"][1],
                       "adds": [a[1] for a in tr["adds"]],
                       "exit_time": h["time_et"][tr["exit"][0]].strftime("%Y-%m-%d %H:%M"), "exit": tr["exit"][1],
                       "pnl": round(tr["pnl"], 2)} for tr in trades]}
    if pos:
        out["position"] = {"entry_time": h["time_et"][pos["entry"][0]].strftime("%Y-%m-%d %H:%M"),
                           "entry": pos["entry"][1],
                           "adds": [{"time": h["time_et"][a[0]].strftime("%Y-%m-%d %H:%M"), "price": a[1]}
                                    for a in pos["adds"]]}
    if extra:
        out.update(extra)
    (DOCS / f"{t}_{name}.json").write_text(json.dumps(out, default=lambda o: None if o is None else float(o)))


# ----------------------------------------------------------------------------- JP + put/call ratio
PC_SEED = ROOT / "research" / "data" / "daily.csv"   # SPY put/call history rebuilt from ThetaData (to 17 Nov 2026)


def daily_pc(h, lookback=60):
    """Daily SPY put/call volume ratio: ThetaData history, then the tracker's last reading of each day.
    z = today's ratio vs the previous `lookback` complete days. Same shape as daily_retail()."""
    parts = []
    if PC_SEED.exists():
        seed = pd.read_csv(PC_SEED, usecols=["date", "pc_volume"]).rename(columns={"pc_volume": "premium"})
        seed["date"] = pd.to_datetime(seed["date"]).dt.date
        seed["complete"] = True
        parts.append(seed)
    if len(h) and "pc_volume_ratio" in h:
        live = h[["time_et", "pc_volume_ratio"]].dropna().copy()
        live["date"] = live["time_et"].dt.date
        live = live.groupby("date").last().reset_index().rename(columns={"pc_volume_ratio": "premium"})
        live["complete"] = live["time_et"].dt.hour >= 16
        if parts:
            live = live[~live["date"].isin(set(parts[0]["date"]))]
        parts.append(live[["date", "premium", "complete"]])
    if not parts:
        return pd.DataFrame(columns=["date", "premium", "mean", "std", "bar", "z", "complete"])
    d = pd.concat(parts).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    d = d[d["premium"] > 0]
    prior = d["premium"].where(d["complete"]).shift(1).rolling(lookback, min_periods=20)
    d["mean"], d["std"] = prior.mean(), prior.std()
    d["bar"] = d["mean"] + CONFIG.get("add_min_z", 1.0) * d["std"]
    d["z"] = (d["premium"] - d["mean"]) / d["std"]
    return d[["date", "premium", "mean", "std", "bar", "z", "complete"]].reset_index(drop=True)


def pc_backtest(level=-10.0, z_min=1.0):
    """Same rule on the ThetaData daily history (daily closes): enter at the close of the day gamma at the low
    <= level; add 1 unit at the first close below the entry with put/call z >= z_min; exit when gamma closes >= 0."""
    if not PC_SEED.exists():
        return []
    d = pd.read_csv(PC_SEED, parse_dates=["date"]).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    prev = d["pc_volume"].shift(1).rolling(60)
    d["z"] = (d["pc_volume"] - prev.mean()) / prev.std()
    out, pos = [], None
    for _, r in d.iterrows():
        if pos is None:
            if r["gex_low"] <= level:
                pos = {"entry_date": str(r["date"].date()), "entry": round(r["spy_close"], 2), "add_date": None, "add": None}
            continue
        if r["gex_close"] >= 0:
            units = [pos["entry"]] + ([pos["add"]] if pos["add"] else [])
            out.append({**pos, "exit_date": str(r["date"].date()), "exit": round(r["spy_close"], 2),
                        "pnl": round(sum(r["spy_close"] - u for u in units), 2), "open": False})
            pos = None
            continue
        if pos["add"] is None and r["spy_close"] < pos["entry"] and r["z"] >= z_min:
            pos.update(add_date=str(r["date"].date()), add=round(r["spy_close"], 2))
    if pos:
        r = d.iloc[-1]
        units = [pos["entry"]] + ([pos["add"]] if pos["add"] else [])
        out.append({**pos, "exit_date": str(r["date"].date()), "exit": round(r["spy_close"], 2),
                    "pnl": round(sum(r["spy_close"] - u for u in units), 2), "open": True})
    return out


def write_pc_signal(t, h, level, note):
    pcd = daily_pc(h)
    trades, pos = jp_signals(h, level, pcd, need_level=False)
    bt = pc_backtest(level if level is not None else -10.0)
    write_signal_json(t, h, level, note, trades, pos, pcd, name="pc_signal",
                      extra={"backtest": bt, "backtest_note": "ThetaData daily history, closes only, to 17 Nov 2026"})


# ----------------------------------------------------------------------------- quant lab (JP's thesis, measured)
VARIANTS = [
    ("A", "JP original", "Enters at -10bn, adds at the close on retail panic below the entry, exits at 0"),
    ("B", "Staggered entry", "1/3 at -10bn, 1/3 when gamma stops falling, 1/3 on retail panic; exits at 0"),
    ("C", "Volatility filter", "Like A, but only enters/adds if implied volatility > realised (JP's condition)"),
    ("D", "Expiry exit", "Like A, but exits at the expiry-Friday close if >=50% of the negative gamma expired that week"),
    ("E", "Volatility sizing", "Like A, but size = 15% / implied volatility (smaller when the market is more nervous)"),
]


def gamma_fuel(h, i, minutes=60):
    """Change in gamma (bn) per 1% price move over the last ~hour of readings on the same day."""
    t, d = h["time_et"].iloc[i], h["time_et"].iloc[i].date()
    prior = h[(h["time_et"].dt.date == d) & (h["time_et"] <= t - pd.Timedelta(minutes=minutes - 5))]
    if prior.empty:
        return np.nan
    j = prior.index[-1]
    dp = (np.log(h["spot"].iloc[i]) - np.log(h["spot"].iloc[j])) * 100
    if abs(dp) < 0.15:
        return np.nan
    return round(float((h["net_gex_bn"].iloc[i] - h["net_gex_bn"].iloc[j]) / dp), 2)


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def simulate_variant(h, vid, zmap, level, zmin, max_adds):
    """Paper-trade one variant over the saved readings. P&L in SPY points (sum over units x weight)."""
    dates = h["time_et"].dt.date
    last_of_day = set(h.groupby(dates).tail(1).index)
    pos, trades, realized, equity, prev_g = None, [], 0.0, [], None
    for i in range(len(h)):
        r = h.iloc[i]
        g, p, t = float(r["net_gex_bn"]), float(r["spot"]), r["time_et"]
        iv, rv = r.get("iv30", np.nan), r.get("rv20", np.nan)
        vol_ok = pd.isna(iv) or pd.isna(rv) or iv > rv
        is_close = i in last_of_day and t.hour >= 16
        exited = False
        if pos is not None:
            if t.weekday() == 4 and pos.get("fri") is None and t.date() not in pos.get("fri_skip", ()):
                gw = r.get("gex_week_bn", np.nan)
                pos["fri"] = (not pd.isna(gw)) and g < 0 and gw / g >= 0.5
            exit_now = g >= 0 or (vid == "D" and is_close and t.weekday() == 4 and pos.get("fri"))
            if exit_now:
                pnl = sum(w * (p - u) for _, u, w in pos["units"])
                realized += pnl
                trades.append({"entry_time": h["time_et"].iloc[pos["units"][0][0]].strftime("%Y-%m-%d %H:%M"),
                               "entry": pos["units"][0][1], "units": [(round(u, 2), round(w, 2)) for _, u, w in pos["units"]],
                               "exit_time": t.strftime("%Y-%m-%d %H:%M"), "exit": p, "pnl": round(pnl, 2),
                               "mae": round(pos["mae"], 2)})
                pos, exited = None, True
        if pos is None and not exited and g <= level and (vid != "C" or vol_ok):
            w = 1 / 3 if vid == "B" else (min(2.0, 0.15 / iv) if vid == "E" and not pd.isna(iv) and iv > 0 else 1.0)
            pos = {"units": [(i, p, w)], "entry_p": p, "w": w, "minG": g, "turned": False, "adds": 0, "mae": 0.0}
        elif pos is not None:
            pos["minG"] = min(pos["minG"], g)
            if vid == "B" and not pos["turned"] and g <= level and prev_g is not None and g > prev_g \
                    and g - pos["minG"] >= 0.75:
                pos["units"].append((i, p, 1 / 3))
                pos["turned"] = True
            z = zmap.get(t.date())
            add_ok = is_close and g <= level and z is not None and not pd.isna(z) and z >= zmin \
                and p < pos["entry_p"] and pos["adds"] < max_adds and (vid != "C" or vol_ok)
            if add_ok:
                pos["units"].append((i, p, 1 / 3 if vid == "B" else pos["w"]))
                pos["adds"] += 1
        if pos is not None:
            open_pnl = sum(w * (p - u) for _, u, w in pos["units"])
            pos["mae"] = min(pos["mae"], open_pnl)
        else:
            open_pnl = 0.0
        equity.append(realized + open_pnl)
        prev_g = g
    eq = np.array(equity) if equity else np.array([0.0])
    maxdd = float(np.max(np.maximum.accumulate(np.maximum(eq, 0)) - eq)) if len(eq) else 0.0
    cur = float(h["spot"].iloc[-1])
    out = {"closed": len(trades), "wins": sum(tr["pnl"] > 0 for tr in trades),
           "realized": round(realized, 2), "max_drawdown": round(maxdd, 2), "trades": trades[-10:], "open": None}
    if pos is not None:
        units = pos["units"]
        size = sum(w for _, _, w in units)
        out["open"] = {"since": h["time_et"].iloc[units[0][0]].strftime("%Y-%m-%d %H:%M"),
                       "units": [{"time": h["time_et"].iloc[k].strftime("%Y-%m-%d %H:%M"), "price": round(u, 2),
                                  "size": round(w, 2)} for k, u, w in units],
                       "size": round(size, 2), "avg": round(sum(w * u for _, u, w in units) / size, 2),
                       "pnl": round(sum(w * (cur - u) for _, u, w in units), 2), "worst": round(pos["mae"], 2)}
    out["total"] = round(realized + (out["open"]["pnl"] if out["open"] else 0), 2)
    return out


def find_cycles(h, level, zmap):
    """Every cycle 'gamma reaches level -> back to 0', with the measures at the start and the outcome."""
    dates = h["time_et"].dt.date
    days = sorted(dates.unique())
    lasts = h.groupby(dates).tail(1)
    lasts = lasts[lasts["time_et"].dt.hour >= 16]          # only finished days count as a close
    day_close = pd.Series(lasts["spot"].to_numpy(), index=lasts["time_et"].dt.date.to_numpy())
    cycles, cur = [], None
    for i in range(len(h)):
        r = h.iloc[i]
        g, p = float(r["net_gex_bn"]), float(r["spot"])
        if cur is None and g <= level:
            iv, rv = r.get("iv30", np.nan), r.get("rv20", np.nan)
            gw = r.get("gex_week_bn", np.nan)
            cur = {"start": r["time_et"].strftime("%Y-%m-%d %H:%M"), "start_px": p, "start_i": i,
                   "gamma": round(g, 2), "fuel": _num(gamma_fuel(h, i)),
                   "iv30": _num(iv), "rv20": _num(rv),
                   "vrp": round((iv - rv) * 100, 1) if not (pd.isna(iv) or pd.isna(rv)) else None,
                   "week_share": round(gw / g, 2) if not pd.isna(gw) and g < 0 else None,
                   "retail_z": _num(zmap.get(r["time_et"].date())),
                   "min_gamma": g, "low": p}
        elif cur is not None:
            cur["min_gamma"] = min(cur["min_gamma"], g)
            cur["low"] = min(cur["low"], p)
            if g >= 0:
                cur.update({"end": r["time_et"].strftime("%Y-%m-%d %H:%M"), "end_px": p})
                cycles.append(cur)
                cur = None
    if cur is not None:
        cur.update({"end": None, "end_px": float(h["spot"].iloc[-1])})
        cycles.append(cur)
    for c in cycles:
        c["return_pct"] = round((c["end_px"] / c["start_px"] - 1) * 100, 2)
        c["worst_pct"] = round((c["low"] / c["start_px"] - 1) * 100, 2)
        c["min_gamma"] = round(c["min_gamma"], 2)
        start_t = pd.Timestamp(c["start"])
        end_t = pd.Timestamp(c["end"]) if c["end"] else h["time_et"].iloc[-1]
        c["days"] = round((end_t - start_t).total_seconds() / 86400, 1)
        d0 = start_t.date()
        k0 = days.index(d0)
        for n in (1, 3, 5):
            dn = days[k0 + n] if k0 + n < len(days) else None
            c[f"fwd{n}_pct"] = round((day_close[dn] / c["start_px"] - 1) * 100, 2) if dn in day_close.index else None
        c.pop("start_i", None)
    return cycles


def write_quant(t, h):
    """Measures now + cycles table + variants A-E, for the live page (docs/<t>_quant.json)."""
    h = h.reset_index(drop=True)
    h = h[~h["net_gex_bn"].isna()].reset_index(drop=True)
    retail = daily_retail()
    zmap = dict(zip(retail["date"], retail["z"])) if len(retail) else {}
    level = entry_level(h, t)[0]
    if level is None:
        return
    zmin, max_adds = CONFIG.get("add_min_z", 1.0), CONFIG.get("max_adds", 1)
    i = len(h) - 1
    r = h.iloc[i]
    today = h[h["time_et"].dt.date == r["time_et"].date()]
    low_today = float(today["net_gex_bn"].min())
    iv, rv5, rv20, gw = r.get("iv30"), r.get("rv5"), r.get("rv20"), r.get("gex_week_bn")
    rd = retail.iloc[-1] if len(retail) else None
    now = {"time_et": r["time_et"].strftime("%Y-%m-%d %H:%M"), "spot": float(r["spot"]), "gamma": float(r["net_gex_bn"]),
           "fuel": _num(gamma_fuel(h, i)), "gamma_low_today": round(low_today, 2),
           "off_low": round(float(r["net_gex_bn"]) - low_today, 2),
           "turning": bool(i > 0 and r["net_gex_bn"] > h["net_gex_bn"].iloc[i - 1] and r["net_gex_bn"] - low_today >= 0.75),
           "iv30": _num(iv), "rv5": _num(rv5), "rv20": _num(rv20),
           "vrp20": round((iv - rv20) * 100, 1) if _num(iv) and _num(rv20) else None,
           "vrp5": round((iv - rv5) * 100, 1) if _num(iv) and _num(rv5) else None,
           "gex_week": _num(gw), "week_share": round(gw / r["net_gex_bn"], 2) if _num(gw) is not None and r["net_gex_bn"] < 0 else None,
           "retail_z": _num(rd["z"]) if rd is not None else None, "retail_days": int(len(retail))}
    cycles = find_cycles(h, level, zmap)[-15:]
    m = margin_mod.load()
    lots, ratio = margin_mod.spx_lots(m), (m or {}).get("ratio", {}).get(t)
    if lots and ratio:   # share of new margin money losing money at the price where each cycle started
        now["margin_under"] = margin_mod.underwater_at(lots, r["spot"] / ratio)
        for c in cycles:
            c["margin_under"] = margin_mod.underwater_at(lots, c["start_px"] / ratio)
    out = {"ticker": t, "level": level, "now": now,
           "variants": [{"id": vid, "name": name, "desc": desc, **simulate_variant(h, vid, zmap, level, zmin, max_adds)}
                        for vid, name, desc in VARIANTS],
           "cycles": cycles,
           "first_reading": h["time_et"].iloc[0].strftime("%Y-%m-%d %H:%M")}
    (DOCS / f"{t}_quant.json").write_text(json.dumps(out, default=lambda o: None if o is None else float(o)))


# ----------------------------------------------------------------------------- charts
def draw_timeline(t, h):
    if len(h) < 2:
        (DOCS / f"{t}_timeline.png").unlink(missing_ok=True)  # don't show an old chart
        return None
    h = h.reset_index(drop=True)
    x = np.arange(len(h))
    level, level_note = entry_level(h, t)
    daily = daily_retail()
    trades, open_pos = jp_signals(h, level, daily)
    try:
        write_signal_json(t, h, level, level_note, trades, open_pos, daily)
    except Exception as e:
        print(f"  {t}: signal file failed - {e}", file=sys.stderr)
    try:
        if t == CONFIG.get("signal_ticker", "SPY"):   # the history and put/call seed are SPY's
            write_pc_signal(t, h, level, level_note)
    except Exception as e:
        print(f"  {t}: put/call signal file failed - {e}", file=sys.stderr)

    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(16, 10), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1.6, 1.1]})
    pnl = sum(tr["pnl"] for tr in trades)
    wins = sum(tr["pnl"] > 0 for tr in trades)
    n_adds = sum(len(tr["adds"]) for tr in trades)
    sub = (f"Rule: enter at {level:g}bn, add at close if retail z >= {CONFIG.get('add_min_z', 1.0):g} and below entry, exit at 0 | "
           if level is not None else f"entry level: {level_note} | ")
    sub += f"{len(trades)} trades closed | P&L {pnl:+.1f} points | hit rate {wins}/{len(trades)} | adds {n_adds}"
    if open_pos is not None:
        units = [open_pos["entry"]] + open_pos["adds"]
        cur = h["spot"].iloc[-1]
        sub += (f"\nOPEN {len(units)}x long since {h['time_et'][open_pos['entry'][0]]:%d %b %H:%M} at "
                f"{open_pos['entry'][1]:.2f}, now {sum(cur - u[1] for u in units):+.1f} at {cur:.2f}")
    fig.suptitle(f"{t} dealer gamma | updated {h['time_et'].iloc[-1]:%a %d %b %Y %H:%M} ET\n{sub}",
                 x=0.06, ha="left", fontsize=13, color="#0b2545")

    # price
    a1.plot(x, h["spot"], color="#0b2545", lw=1.2, label=t)
    for tr in trades:
        (ie, pe), (ix, px) = tr["entry"], tr["exit"]
        colr = "#2e7d5b" if tr["pnl"] > 0 else "#c0392b"
        a1.plot([ie, ix], [pe, px], color=colr, lw=2)
        a1.scatter(ie, pe, marker="^", color="#1f5fbf", zorder=5, s=60)
        for ia, pa in tr["adds"]:
            a1.plot([ia, ix], [pa, px], color=colr, lw=1.2, alpha=0.5)
            a1.scatter(ia, pa, marker="^", color="#c9a227", zorder=5, s=60)
        a1.scatter(ix, px, marker="o", facecolor="white", edgecolor=colr, zorder=5, s=60)
        a1.annotate(f"{tr['pnl']:+.1f}", (ix, px), xytext=(0, 8), textcoords="offset points",
                    ha="center", color=colr, fontsize=9, fontweight="bold")
    if open_pos is not None:
        a1.scatter(*open_pos["entry"], marker="^", color="#1f5fbf", zorder=5, s=60)
        for ia, pa in open_pos["adds"]:
            a1.scatter(ia, pa, marker="^", color="#c9a227", zorder=5, s=60)
    a1.scatter([], [], marker="^", color="#1f5fbf", label="Entry: gamma reaches entry level")
    a1.scatter([], [], marker="^", color="#c9a227", label="Add: at close, retail z >= 1, below entry")
    a1.scatter([], [], marker="o", facecolor="white", edgecolor="#2e7d5b", label="Exit: gamma back to 0")
    a1.legend(loc="upper left", fontsize=8, ncol=3)
    a1.set_ylabel(f"{t} ($)")
    a1.grid(alpha=0.25)

    # gamma
    g = h["net_gex_bn"].astype(float)
    a2.plot(x, g, color="#222", lw=1)
    a2.fill_between(x, g, 0, where=g < 0, color="#c0392b", alpha=0.15)
    a2.fill_between(x, g, 0, where=g >= 0, color="#2e7d5b", alpha=0.15)
    a2.axhline(0, color="#444", lw=0.8)
    if level is not None:
        a2.axhline(level, color="#c0392b", ls="--", lw=1)
        a2.text(x[0], level, f"entry {level:.1f}bn ", va="bottom", color="#c0392b", fontsize=9)
    a2.text(x[0], 0, "exit 0 ", va="bottom", color="#2e7d5b", fontsize=9)
    a2.set_ylabel("Dealer gamma ($bn / 1%)")
    a2.grid(alpha=0.25)

    # retail gauge, one bar per day placed at the day's last snapshot
    h["date"] = h["time_et"].dt.date
    lasts = h.groupby("date").tail(1)
    pos_map = dict(zip(lasts["date"], lasts.index))
    for _, r in daily.iterrows():
        if pd.isna(r["z"]) or r["date"] not in pos_map:
            continue
        colr = "#c9a227" if r["z"] >= CONFIG.get("add_min_z", 1.0) else "#bbbbbb"
        a3.bar(pos_map[r["date"]], r["z"], width=max(len(h) / 60, 1), color=colr,
               hatch=None if r["complete"] else "///", edgecolor="#888" if not r["complete"] else colr)
        a3.text(pos_map[r["date"]], r["z"], f"{r['z']:.2f}", ha="center",
                va="bottom" if r["z"] >= 0 else "top", fontsize=7)
    a3.axhline(1, color="#8a7a1a", ls="--", lw=0.8)
    a3.axhline(0, color="#444", lw=0.6)
    a3.set_ylabel("Retail z\n(XSP puts)")
    if daily["z"].notna().sum() == 0:
        a3.text(0.5, 0.5, "needs ~10 trading days of history", transform=a3.transAxes,
                ha="center", va="center", color="#888")
    a3.grid(alpha=0.25)

    # x labels: one per day
    firsts = h.groupby("date").head(1)
    step = max(1, len(firsts) // 25)
    a3.set_xticks(firsts.index[::step])
    a3.set_xticklabels([d.strftime("%m-%d") for d in firsts["date"]][::step], rotation=90)
    for i in firsts.index[1:]:
        for a in (a1, a2, a3):
            a.axvline(i - 0.5, color="#eee", lw=0.6, zorder=0)

    fig.text(0.06, 0.01,
             "Free data: Cboe delayed option chains (~15 min delay). Gamma assumes dealers long calls / "
             "short puts, all expiries. Retail z: dollar premium of XSP puts (5+ days to expiry) traded that day vs "
             "the previous 60 days (free stand-in for JP's 1-lot XSP puts bought at the ask); hatched = day not finished. "
             "Not investment advice.",
             fontsize=8, color="#666")
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    out = DOCS / f"{t}_timeline.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def draw_profile(t, row, by_strike):
    spot = row["spot"]
    bs = by_strike[(by_strike.index > spot * 0.93) & (by_strike.index < spot * 1.07)]
    fig, ax = plt.subplots(figsize=(16, 5))
    w = np.median(np.diff(bs.index)) * 0.8 if len(bs) > 1 else 1
    ax.bar(bs.index, bs["C"] / 1e9, width=w, color="#2e7d5b", alpha=0.6, label="Calls")
    ax.bar(bs.index, bs["P"] / 1e9, width=w, color="#c0392b", alpha=0.6, label="Puts")
    ax.plot(bs.index, bs["net"] / 1e9, color="#0b2545", lw=1.2, label="Net")
    ax.axvline(spot, color="#0b2545", ls="-", lw=1)
    ax.text(spot, ax.get_ylim()[1], f" price {spot:.2f}", va="top")
    lo, hi = ax.get_ylim()
    for n, (k, c, lab) in enumerate([("flip", "#8e44ad", "flip"), ("call_wall", "#2e7d5b", "call wall"),
                                     ("put_wall", "#c0392b", "put wall")]):
        if not pd.isna(row[k]):
            ax.axvline(row[k], color=c, ls="--", lw=1)
            ax.text(row[k], lo + (hi - lo) * (0.04 + 0.09 * n), f" {lab} {row[k]:g}",
                    va="bottom", color=c, fontsize=9, fontweight="bold",
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.8, pad=1))
    ax.axhline(0, color="#444", lw=0.6)
    ax.set_title(f"{t} gamma by strike (all expiries) | {row['time_et']} ET | net {row['net_gex_bn']:+.2f}bn per 1%",
                 loc="left", color="#0b2545")
    ax.set_ylabel("$bn per 1% move")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    out = DOCS / f"{t}_profile.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def pine_box(t):
    p = DOCS / f"{t}_levels.pine"
    if not p.exists():
        return ""
    code = p.read_text().replace("&", "&amp;").replace("<", "&lt;")
    return (f'<details><summary>TradingView levels for {t} (copy into Pine Editor)</summary>'
            f'<textarea readonly rows="12" style="width:100%;font-family:monospace" onclick="this.select()">'
            f'{code}</textarea></details>')


def write_page(latest):
    def fmt(v):
        return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:g}"
    cards = []
    for t in CONFIG["tickers"]:
        r = latest.get(t)
        if r is None:
            h = load_history(t)
            if h.empty:
                continue
            r = h.iloc[-1].to_dict()
            r["time_et"] = pd.Timestamp(r["time_et"]).strftime("%Y-%m-%d %H:%M")
        regime = "NEGATIVE gamma: dealer hedging adds to moves" if r["net_gex_bn"] < 0 \
            else "POSITIVE gamma: dealer hedging dampens moves"
        cards.append(f"""
<section><h2>{t} <small>{r['time_et']} ET</small></h2>
<p class="{'neg' if r['net_gex_bn'] < 0 else 'pos'}">{regime}</p>
<table><tr><th>Price</th><th>Net gamma ($bn / 1%)</th><th>Gamma flip</th><th>Call wall</th><th>Put wall</th><th>Put/call volume</th></tr>
<tr><td>{fmt(r['spot'])}</td><td>{r['net_gex_bn']:+.2f}</td><td>{fmt(r['flip'])}</td><td>{fmt(r['call_wall'])}</td><td>{fmt(r['put_wall'])}</td><td>{fmt(r['pc_volume_ratio'])}</td></tr></table>
<img src="{t}_map.png?v={int(time.time())}" onerror="this.style.display='none'">
<img src="{t}_timeline.png?v={int(time.time())}" onerror="this.style.display='none'">
<img src="{t}_profile.png?v={int(time.time())}" onerror="this.style.display='none'">
{pine_box(t)}
</section>""")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300"><title>Dealer gamma tracker</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1200px;margin:20px auto;padding:0 12px;color:#0b2545}}
img{{width:100%;margin:8px 0;border:1px solid #eee}}table{{border-collapse:collapse;margin:8px 0}}
td,th{{border:1px solid #ddd;padding:6px 10px;text-align:center}}.neg{{color:#c0392b;font-weight:600}}
.pos{{color:#2e7d5b;font-weight:600}}small{{color:#888;font-weight:400}}footer{{color:#888;font-size:12px}}</style></head>
<body><h1>Dealer gamma tracker</h1><p><a href="live.html"><b>&#9654; Live page (second-by-second prices)</b></a></p>{''.join(cards)}
<footer>Free Cboe delayed data, ~15 min delay. Estimates assume dealers are long calls and short puts.
Not investment advice. Page refreshes every 5 minutes.</footer></body></html>"""
    (DOCS / "index.html").write_text(html)


# ----------------------------------------------------------------------------- levels map
def save_strikes(t, row, by_strike):
    """Keep every snapshot's per-strike gamma for today (used for the 'minutes prior' dots)."""
    spot = row["spot"]
    bs = by_strike[(by_strike.index > spot * 0.94) & (by_strike.index < spot * 1.06)]
    out = pd.DataFrame({"time_et": row["time_et"], "strike": bs.index,
                        "oi_net": bs["net"].round(0).to_numpy(), "vol_net": bs["vnet"].round(0).to_numpy()})
    d = DATA / "strikes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{t}_{row['time_et'][:10]}.csv"
    if p.exists():
        old = pd.read_csv(p)
        if (old["time_et"] == row["time_et"]).any():
            return old
        out = pd.concat([old, out], ignore_index=True)
    out.to_csv(p, index=False)
    return out


_PX_CACHE = {}
_RV_CACHE = {}


def realized_vol(t):
    """Realised (actual) volatility of the last 5 and 20 trading days, annualised, from daily closes."""
    if t in _RV_CACHE:
        return _RV_CACHE[t]
    out = (np.nan, np.nan)
    try:
        import yfinance as yf
        c = yf.Ticker(CONFIG.get("yahoo_symbols", {}).get(t, t)).history(period="3mo", interval="1d")["Close"].dropna()
        r = np.log(c).diff().dropna()
        out = (round(float(r.tail(5).std() * np.sqrt(252)), 4), round(float(r.tail(20).std() * np.sqrt(252)), 4))
    except Exception as e:
        print(f"  {t}: realised volatility unavailable ({e})")
    _RV_CACHE[t] = out
    return out


def intraday_prices(t, day, hist):
    """1-minute prices for today from Yahoo (free); falls back to our own snapshots."""
    if (t, day) in _PX_CACHE:
        return _PX_CACHE[(t, day)]
    _PX_CACHE[(t, day)] = _intraday_prices(t, day, hist)
    return _PX_CACHE[(t, day)]


def _intraday_prices(t, day, hist):
    try:
        import yfinance as yf
        sym = CONFIG.get("yahoo_symbols", {}).get(t, t)
        px = yf.Ticker(sym).history(period="1d", interval="1m")["Close"]
        px.index = px.index.tz_convert(ET)
        px = px[px.index.date == day]
        if len(px) > 5:
            return px
    except Exception as e:
        print(f"  {t}: Yahoo intraday prices unavailable ({e}); using snapshots")
    h = hist[hist["time_et"].dt.date == day]
    return pd.Series(h["spot"].to_numpy(), index=h["time_et"].dt.tz_localize(ET))


def key_strikes(row, by_strike, band):
    spot = row["spot"]
    bs = by_strike[(by_strike.index >= spot - band) & (by_strike.index <= spot + band)]
    major_neg = float(bs["net"].idxmin()) if (bs["net"] < 0).any() else np.nan
    major_pos = float(bs["net"].idxmax()) if (bs["net"] > 0).any() else np.nan
    return bs, major_pos, major_neg


def draw_levels_map(t, row, by_strike, hist, strikes_today):
    spot, now = row["spot"], pd.Timestamp(row["time_et"]).tz_localize(ET)
    band = spot * 0.03
    if not pd.isna(row["flip"]) and abs(row["flip"] - spot) < spot * 0.05:
        band = max(band, abs(row["flip"] - spot) * 1.15)
    bs, major_pos, major_neg = key_strikes(row, by_strike, band)
    if bs.empty:
        return None
    step = float(np.median(np.diff(bs.index))) if len(bs) > 1 else 1.0
    px = intraday_prices(t, now.date(), hist)

    with plt.style.context("dark_background"):
        fig, ax = plt.subplots(figsize=(13, 10))
        fig.patch.set_facecolor("black")
        ax.set_facecolor("black")
        # price over the session (bottom time axis)
        start = now.normalize() + pd.Timedelta(hours=9, minutes=30)
        end = now.normalize() + pd.Timedelta(hours=16)
        if len(px):
            ax.plot(px.index, px.to_numpy(), color="#00e5ff", lw=1.4, zorder=4)
        ax.set_xlim(start, end)
        ax.set_ylim(spot - band, spot + band)
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%H:%M", tz=ET))
        ax.set_xlabel("Time (New York)")
        ax.set_ylabel("Strike / price")

        # gamma bars by strike (top axis)
        ax2 = ax.twiny()
        oi = bs["net"] / 1e9
        vol = bs["vnet"] / 1e9
        ax2.barh(bs.index, oi.abs(), height=step * 0.8, zorder=2,
                 color=np.where(oi < 0, "#7a1c1c", "#2f7d32"), label="OI gamma")
        ax2.barh(bs.index, vol.abs(), height=step * 0.35, zorder=3,
                 color=np.where(vol < 0, "#ff5a4e", "#7ee07e"), label="Volume gamma")
        # dots: where each strike's OI gamma bar was some minutes ago
        if strikes_today is not None and len(strikes_today):
            st = strikes_today.copy()
            st["t"] = pd.to_datetime(st["time_et"]).dt.tz_localize(ET)
            times = st["t"].drop_duplicates().sort_values()
            for mins, colr in [(5, "#e8f4ff"), (15, "#7fc8ff"), (30, "#2e86de")]:
                target = now - pd.Timedelta(minutes=mins)
                near_t = times[(times - target).abs() <= pd.Timedelta(minutes=4)]
                if near_t.empty:
                    continue
                tt = near_t.iloc[(near_t - target).abs().argmin()]
                snap = st[(st["t"] == tt) & st["strike"].isin(bs.index)]
                ax2.scatter(snap["oi_net"].abs() / 1e9, snap["strike"], s=10, color=colr,
                            zorder=5, label=f"{mins} min prior")
        # scale to the open-interest bars; very large volume bars (same-day options) run off the edge
        xmax = float(max(oi.abs().max() * 1.3, vol.abs().quantile(0.9) * 1.1)) or 1.0
        ax2.set_xlim(0, xmax)
        for k, v in vol.items():
            if abs(v) > xmax:
                ax2.text(xmax * 0.99, k, f"{abs(v):.0f}►", ha="right", va="center", fontsize=7,
                         color="white", zorder=6)
        ax.set_zorder(ax2.get_zorder() + 1)  # price line on top of the bars
        ax.patch.set_visible(False)
        ax2.set_xlabel(f"{t} gamma exposure ($bn per 1% move) — red = negative, green = positive")

        # level lines
        ax.axhline(spot, color="#00e5ff", ls="--", lw=0.8)
        ax.annotate(f"{spot:.2f}", (1.005, spot), xycoords=("axes fraction", "data"), color="#00e5ff",
                    va="center", fontsize=10, bbox=dict(boxstyle="round", fc="black", ec="#00e5ff"))
        lines = [("flip", row["flip"], "#ffb000", "Zero gamma (flip)"),
                 ("pos", major_pos, "#7ee07e", "Major positive gamma"),
                 ("neg", major_neg, "#ff5a4e", "Major negative gamma")]
        for _, lvl, colr, lab in lines:
            if not pd.isna(lvl):
                ax.axhline(lvl, color=colr, ls="--", lw=0.9, label=f"{lab}: {lvl:g}")
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax2.legend(h2 + h1, l2 + l1, loc="upper right", fontsize=9, facecolor="black", framealpha=0.8)
        fig.suptitle(f"{t} gamma map | {row['time_et']} ET | net {row['net_gex_bn']:+.2f}bn | "
                     f"put wall {row['put_wall']:g} | call wall {row['call_wall']:g}",
                     color="white", fontsize=12)
        fig.text(0.01, 0.005, "Bars: dealer gamma per strike from open interest (wide, dark) and from today's "
                 "volume (thin, bright). Dots: where the OI bar was 5/15/30 min earlier. Cboe data ~15 min delayed; "
                 "price line from Yahoo.", fontsize=8, color="#999")
        fig.tight_layout(rect=(0, 0.02, 0.97, 0.97))
        out = DOCS / f"{t}_map.png"
        fig.savefig(out, dpi=100, facecolor="black")
        plt.close(fig)
    return out


def write_live_json(t, row, by_strike, hist, strikes_today, offset=0.0, prices=None, window=None, extra=None):
    """Data for the live web page (docs/live.html): gamma per strike + today's minute prices.
    For futures, `row`/`by_strike` are in index points and `offset` (the futures basis) is added to every level."""
    spot, now = row["spot"], pd.Timestamp(row["time_et"]).tz_localize(ET)
    band = spot * 0.03
    if not pd.isna(row["flip"]) and abs(row["flip"] - spot) < spot * 0.05:
        band = max(band, abs(row["flip"] - spot) * 1.15)
    bs, major_pos, major_neg = key_strikes(row, by_strike, band)
    prior = {}
    if strikes_today is not None and len(strikes_today):
        st = strikes_today.copy()
        st["t"] = pd.to_datetime(st["time_et"]).dt.tz_localize(ET)
        times = st["t"].drop_duplicates().sort_values()
        for mins in (5, 15, 30):
            target = now - pd.Timedelta(minutes=mins)
            near_t = times[(times - target).abs() <= pd.Timedelta(minutes=4)]
            if len(near_t):
                tt = near_t.iloc[(near_t - target).abs().argmin()]
                snap = st[st["t"] == tt]
                prior[mins] = dict(zip(snap["strike"], snap["oi_net"]))
    strikes = []
    for k, r in bs.iterrows():
        item = {"k": round(float(k) + offset, 2), "oi": round(r["net"] / 1e9, 4), "vol": round(r["vnet"] / 1e9, 4)}
        for mins, d in prior.items():
            if k in d:
                item[f"oi{mins}"] = round(d[k] / 1e9, 4)
        strikes.append(item)
    px = intraday_prices(t, now.date(), hist) if prices is None else prices
    clean = lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v) + offset, 2)
    if window is None:
        start = now.normalize() + pd.Timedelta(hours=9, minutes=30)
        window = (start, start + pd.Timedelta(hours=6, minutes=30))
    out = {
        "ticker": t, "time_et": row["time_et"], "date": str(now.date()),
        "updated_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "spot": round(row["spot"] + offset, 2), "net_gex_bn": row["net_gex_bn"], "flip": clean(row["flip"]),
        "call_wall": clean(row["call_wall"]), "put_wall": clean(row["put_wall"]),
        "major_pos": clean(major_pos), "major_neg": clean(major_neg),
        "x_start_ms": int(window[0].timestamp() * 1000), "x_end_ms": int(window[1].timestamp() * 1000),
        "strikes": strikes, **(extra or {}),
        "prices": [[int(ts.timestamp() * 1000), round(float(p), 2)] for ts, p in px.items()],
    }
    (DOCS / f"{t}_live.json").write_text(json.dumps(out, separators=(",", ":")))


def write_pine(t, row, by_strike):
    """TradingView indicator (paste into the Pine Editor) that draws today's levels."""
    spot = row["spot"]
    bs, major_pos, major_neg = key_strikes(row, by_strike, spot * 0.05)
    levels = []
    def add(price, name, colr, width):
        if not pd.isna(price) and all(abs(price - p) > 1e-9 for p, *_ in levels):
            levels.append((float(price), name, colr, width))
    add(row["flip"], "Zero gamma (flip)", "color.orange", 2)
    add(row["put_wall"], "Put wall", "color.red", 3)
    add(row["call_wall"], "Call wall", "color.green", 3)
    for k in bs["net"].nsmallest(6).index:
        if bs.loc[k, "net"] < 0:
            add(k, "Neg gamma", "color.new(color.red, 40)", 1)
    for k in bs["net"].nlargest(3).index:
        if bs.loc[k, "net"] > 0:
            add(k, "Pos gamma", "color.new(color.green, 40)", 1)
    body = "\n".join(
        f'hline({p:g}, "{n} {p:g}", color={c}, linestyle=hline.style_dashed, linewidth={w})\n'
        f'if barstate.islast\n    label.new(bar_index + 2, {p:g}, "{n} {p:g}", style=label.style_none, '
        f'textcolor={c}, size=size.small)'
        for p, n, c, w in levels)
    pine = (f"//@version=5\n// {t} dealer gamma levels, {row['time_et']} ET (free Cboe data). "
            f"Net gamma {row['net_gex_bn']:+.2f}bn per 1%.\n"
            f'indicator("{t} gamma levels {row["time_et"]}", overlay=true, max_labels_count=50)\n{body}\n')
    (DOCS / f"{t}_levels.pine").write_text(pine)
    return pine


# ----------------------------------------------------------------------------- main
# ----------------------------------------------------------------------------- futures (ES / NQ)
def futures_open(now):
    """CME equity futures: Sunday 18:00 to Friday 17:00 New York time, with a daily break 17:00-18:00."""
    wd, mins = now.weekday(), now.hour * 60 + now.minute
    if wd == 5 or (wd == 6 and mins < 18 * 60) or (wd == 4 and mins >= 17 * 60):
        return False
    return not (17 * 60 <= mins < 18 * 60)


def yahoo_minutes(sym, period="5d"):
    import yfinance as yf
    px = yf.Ticker(sym).history(period=period, interval="1m", prepost=True)["Close"].dropna()
    px.index = px.index.tz_convert(ET)
    return px


def _legs(df, S, now, to_fut):
    """One product's options, gamma re-priced at underlying price S, strikes expressed in futures points."""
    after_close = now.hour >= 16
    df = df[(df["expiry"] > now.date()) | ((df["expiry"] == now.date()) & (not after_close))]
    df = df[(df["oi"] > 0) | (df["volume"] > 0)]
    df = df[(df["strike"] > S * 0.7) & (df["strike"] < S * 1.3) & (df["iv"] > 0.01)].copy()
    T = years_to_expiry(df["expiry"].to_numpy(), now)
    K, iv = df["strike"].to_numpy(), df["iv"].to_numpy()
    g = bs_gamma(S, K, iv, T)
    sign = np.where(df["type"].to_numpy() == "C", 1.0, -1.0)
    return pd.DataFrame({"kf": to_fut(K), "type": df["type"].to_numpy(),
                         "gex": sign * g * df["oi"].to_numpy() * 100 * S * S * 0.01,
                         "vgex": sign * g * df["volume"].to_numpy() * 100 * S * S * 0.01,
                         "K": K, "iv": iv, "T": T, "w": sign * df["oi"].to_numpy() * 100})


def run_future(f, cfg, now):
    """Gamma map for ES/NQ in futures points, combining the index options (SPX/NDX) and the ETF options
    (SPY/QQQ). Each product's gamma is re-priced at the price the futures imply for it right now:
    index = futures - basis, ETF = futures / ratio (both measured during the last US session)."""
    fut = yahoo_minutes(cfg["yahoo"], "5d")
    if fut.empty:
        raise RuntimeError(f"no Yahoo prices for {cfg['yahoo']}")
    fut_now, fut_time = float(fut.iloc[-1]), fut.index[-1]

    def last_session_pair(other):
        both = pd.concat([fut.rename("f"), other.rename("o")], axis=1, join="inner").dropna()
        mins = both.index.hour * 60 + both.index.minute
        both = both[(mins >= 9 * 60 + 35) & (mins <= 15 * 60 + 59)]
        return both.iloc[-1]

    parts, info = [], {}
    # index options: futures = index + basis
    idx_raw = fetch_chain(CONFIG.get("cboe_symbols", {}).get(cfg["index"], cfg["index"]))
    idx_cboe, _, idx_df = parse_chain(idx_raw)
    try:
        pr = last_session_pair(yahoo_minutes(cfg["index_yahoo"], "5d"))
        basis = float(pr["f"] - pr["o"])
    except Exception:
        basis = fut_now - idx_cboe
    li = _legs(idx_df, fut_now - basis, now, lambda k: k + basis)
    parts.append(("index", li, lambda F: F - basis))
    info["basis"] = round(basis, 2)
    # ETF options: futures = ETF x ratio
    if cfg.get("etf"):
        try:
            etf_raw = fetch_chain(cfg["etf"])
            etf_cboe, _, etf_df = parse_chain(etf_raw)
            try:
                pr = last_session_pair(yahoo_minutes(cfg["etf"], "5d"))
                ratio = float(pr["f"] / pr["o"])
            except Exception:
                ratio = fut_now / etf_cboe
            le = _legs(etf_df, fut_now / ratio, now, lambda k: k * ratio)
            parts.append(("etf", le, lambda F: F / ratio))
            info["ratio"] = round(ratio, 4)
        except Exception as e:
            print(f"  {f}: {cfg['etf']} options not added - {e}", file=sys.stderr)

    allp = pd.concat([p[1] for p in parts], ignore_index=True)
    info["gex_index_bn"] = round(parts[0][1]["gex"].sum() / 1e9, 3)
    info["gex_etf_bn"] = round(parts[1][1]["gex"].sum() / 1e9, 3) if len(parts) > 1 else None
    net = allp["gex"].sum()

    # per-strike view on a common futures grid
    step = cfg.get("bin", 10)
    allp["kb"] = (allp["kf"] / step).round() * step
    near = allp[(allp["kb"] > fut_now * 0.85) & (allp["kb"] < fut_now * 1.15)]
    by_strike = pd.DataFrame(index=np.sort(near["kb"].unique()))
    for col, src, typ in [("C", "gex", "C"), ("P", "gex", "P"), ("vC", "vgex", "C"), ("vP", "vgex", "P")]:
        by_strike[col] = near[near["type"] == typ].groupby("kb")[src].sum()
    by_strike = by_strike.fillna(0.0)
    by_strike["net"], by_strike["vnet"] = by_strike["C"] + by_strike["P"], by_strike["vC"] + by_strike["vP"]
    wb = by_strike[(by_strike.index > fut_now * 0.95) & (by_strike.index < fut_now * 1.05)]
    above, below = wb[wb.index >= fut_now], wb[wb.index <= fut_now]
    call_wall = float(above["C"].idxmax()) if len(above) and above["C"].max() > 0 else np.nan
    put_wall = float(below["P"].idxmin()) if len(below) and below["P"].min() < 0 else np.nan

    # gamma flip: re-price every product over a grid of futures prices
    grid = np.linspace(fut_now * 0.85, fut_now * 1.15, 241)
    curve = np.zeros_like(grid)
    for _, lg, to_under in parts:
        S = to_under(grid)[:, None]
        curve += (bs_gamma(S, lg["K"].to_numpy()[None, :], lg["iv"].to_numpy()[None, :], lg["T"].to_numpy()[None, :])
                  * lg["w"].to_numpy()[None, :]).sum(axis=1) * (S[:, 0] ** 2) * 0.01
    flip = np.nan
    cross = np.where(np.diff(np.sign(curve)) != 0)[0]
    if len(cross):
        i = cross[np.argmin(np.abs(grid[cross] - fut_now))]
        flip = float(grid[i] - curve[i] * (grid[i + 1] - grid[i]) / (curve[i + 1] - curve[i]))

    row = {"time_et": now.strftime("%Y-%m-%d %H:%M"), "spot": round(fut_now, 2), "net_gex_bn": round(net / 1e9, 3),
           "call_wall": call_wall, "put_wall": put_wall, "flip": round(flip, 2) if not np.isnan(flip) else np.nan,
           "pc_volume_ratio": np.nan, "put_volume": 0, "call_volume": 0}
    open_ = now.normalize() + pd.Timedelta(hours=18)
    if now < open_:
        open_ -= pd.Timedelta(days=1)
    window = (open_, open_ + pd.Timedelta(hours=23))
    prices = fut[fut.index >= open_ - pd.Timedelta(hours=1)]
    h = append_history(f, row)
    st = save_strikes(f, row, by_strike)
    delay = max(0, int((pd.Timestamp.now(tz=ET) - fut_time).total_seconds() // 60))
    write_live_json(f, row, by_strike, h, st, prices=prices, window=window,
                    extra={**info, "index": cfg["index"], "etf": cfg.get("etf"),
                           "price_time_et": fut_time.strftime("%Y-%m-%d %H:%M"), "price_delay_min": delay,
                           "futures": True,
                           "prev_close": round(float(fut[fut.index < open_].iloc[-1]), 2) if (fut.index < open_).any() else None})
    print(f"  {f} {row['time_et']}: futures {fut_now:.2f} (price {delay} min old), net gamma {row['net_gex_bn']:+.2f}bn "
          f"({cfg['index']} {info['gex_index_bn']:+.2f} + {cfg.get('etf')} {info['gex_etf_bn']}), flip {row['flip']}, "
          f"put wall {put_wall}, call wall {call_wall}")
    return row


def market_open(now):
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60 + 20


def run_once(force=False, charts_only=False, file=None):
    try:
        _run_once(force, charts_only, file)
    finally:
        if not charts_only:
            write_tickers()


def _run_once(force=False, charts_only=False, file=None):
    DOCS.mkdir(exist_ok=True)
    now = pd.Timestamp.now(tz=ET)
    cash, fut_open = market_open(now), futures_open(now)
    if not charts_only and not force and not file and not (cash or fut_open):
        print(f"US market and futures closed ({now:%a %H:%M} ET) - nothing to do. Use --force to run anyway.")
        return
    latest = {}
    if not charts_only and not file:
        try:
            margin_mod.maybe_build(CONFIG.get("margin"))
        except Exception as e:
            print(f"  margin indicator FAILED - {e}", file=sys.stderr)
        for f, cfg in CONFIG.get("futures", {}).items():
            try:
                latest[f] = run_future(f, cfg, now)
                STATUS[f] = {"ok": True, "utc": pd.Timestamp.now(tz="UTC").isoformat()}
            except Exception as e:
                STATUS[f] = {"ok": False, "error": str(e)[:160], "utc": pd.Timestamp.now(tz="UTC").isoformat()}
                print(f"  {f}: FAILED - {e}", file=sys.stderr)
    if not charts_only and not force and not file and not cash:
        print("  US cash market closed: futures only this run.")
        if not latest:
            sys.exit("No futures could be updated - see errors above.")
        return
    if not charts_only:
        try:
            rr = snapshot_retail(file)
            print(f"  retail gauge {rr['time_et']}: XSP put premium ${rr['put_premium']:,.0f} ({rr['put_volume']:,} contracts)")
        except Exception as e:
            print(f"  retail gauge FAILED - {e}", file=sys.stderr)
    for t in CONFIG["tickers"]:
        if t in CONFIG.get("futures", {}):
            continue
        try:
            if charts_only:
                draw_timeline(t, load_history(t))
                continue
            raw = fetch_chain(CONFIG.get("cboe_symbols", {}).get(t, t),
                              file.format(t) if file else None)
            spot, snap_time, df = parse_chain(raw)
            row, by_strike = analyse(spot, snap_time, df)
            row["rv5"], row["rv20"] = realized_vol(t)
            h = append_history(t, row)
            draw_timeline(t, h)
            if t == CONFIG.get("signal_ticker", "SPY"):
                try:
                    write_quant(t, h)
                except Exception as e:
                    print(f"  {t}: quant lab failed - {e}", file=sys.stderr)
            draw_profile(t, row, by_strike)
            try:
                st = save_strikes(t, row, by_strike)
                draw_levels_map(t, row, by_strike, h, st)
                write_live_json(t, row, by_strike, h, st)
                write_pine(t, row, by_strike)
            except Exception as e:
                print(f"  {t}: gamma map failed - {e}", file=sys.stderr)
            latest[t] = row
            STATUS[t] = {"ok": True, "utc": pd.Timestamp.now(tz="UTC").isoformat()}
            print(f"  {t} {row['time_et']}: price {row['spot']}, net gamma {row['net_gex_bn']:+.2f}bn, "
                  f"flip {row['flip']}, call wall {row['call_wall']}, put wall {row['put_wall']}")
        except Exception as e:
            STATUS[t] = {"ok": False, "error": str(e)[:160], "utc": pd.Timestamp.now(tz="UTC").isoformat()}
            print(f"  {t}: FAILED - {e}", file=sys.stderr)
    write_page(latest)
    print(f"Open {DOCS / 'index.html'} to see the charts.")
    if not charts_only and not latest:
        sys.exit("No ticker could be updated - see errors above.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run even if the market is closed")
    ap.add_argument("--loop", type=int, metavar="MIN", help="repeat every MIN minutes")
    ap.add_argument("--charts-only", action="store_true", help="redraw from saved history")
    ap.add_argument("--file", help="read chain from a local JSON file instead (use {} for ticker)")
    a = ap.parse_args()
    if a.loop:
        while True:
            run_once(a.force, a.charts_only, a.file)
            time.sleep(a.loop * 60)
    else:
        run_once(a.force, a.charts_only, a.file)


if __name__ == "__main__":
    main()
