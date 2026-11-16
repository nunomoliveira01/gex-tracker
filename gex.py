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

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
ET = ZoneInfo("America/New_York")
CONFIG = json.loads((ROOT / "config.json").read_text())

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}
OPT_RE = re.compile(r"^(?P<root>[A-Z_]+?)(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")
HIST_COLS = ["time_et", "spot", "net_gex_bn", "call_wall", "put_wall", "flip",
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
        })
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("Cboe data had no options")
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


def analyse(spot, now, df):
    """Work out everything we want from one snapshot."""
    df = df[df["expiry"] >= now.date()].copy()
    sign = np.where(df["type"] == "C", 1.0, -1.0)
    # dollars of SPY/QQQ dealers must trade for a 1% move
    df["gex"] = sign * df["gamma"] * df["oi"] * 100 * spot * spot * 0.01
    net = df["gex"].sum()

    near = df[(df["strike"] > spot * 0.85) & (df["strike"] < spot * 1.15)]
    by_strike = near.pivot_table(index="strike", columns="type", values="gex",
                                 aggfunc="sum", fill_value=0.0)
    for c in ("C", "P"):
        if c not in by_strike:
            by_strike[c] = 0.0
    by_strike["net"] = by_strike["C"] + by_strike["P"]
    call_wall = float(by_strike["C"].idxmax()) if by_strike["C"].max() > 0 else np.nan
    put_wall = float(by_strike["P"].idxmin()) if by_strike["P"].min() < 0 else np.nan

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

    pv = df.loc[df["type"] == "P", "volume"].sum()
    cv = df.loc[df["type"] == "C", "volume"].sum()
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


def daily_put_call_z(h, lookback=60):
    """Last reading of each day -> put/call volume ratio -> z-score vs prior days."""
    if h.empty:
        return pd.DataFrame(columns=["date", "pc", "z"])
    d = h.copy()
    d["date"] = d["time_et"].dt.date
    daily = d.groupby("date").last().reset_index()[["date", "pc_volume_ratio"]]
    daily = daily.rename(columns={"pc_volume_ratio": "pc"})
    prior = daily["pc"].shift(1).rolling(lookback, min_periods=10)
    daily["z"] = (daily["pc"] - prior.mean()) / prior.std()
    return daily


def entry_level(h):
    lvl = CONFIG.get("entry_level_bn")
    if lvl is not None:
        return float(lvl), "set"
    if len(h) >= CONFIG.get("auto_level_min_rows", 150):
        return float(np.nanpercentile(h["net_gex_bn"], 15)), "auto: lowest 15% of your history"
    return None, "collecting history"


def signals(h, level):
    """Simple version of the 'enter on deep negative gamma, exit at 0' idea."""
    trades, pos = [], None
    if level is None:
        return trades, pos
    for i, r in h.iterrows():
        if pos is None and r["net_gex_bn"] <= level:
            pos = (i, r["spot"])
        elif pos is not None and r["net_gex_bn"] >= 0:
            trades.append((pos[0], pos[1], i, r["spot"]))
            pos = None
    return trades, pos


# ----------------------------------------------------------------------------- charts
def draw_timeline(t, h):
    if len(h) < 2:
        (DOCS / f"{t}_timeline.png").unlink(missing_ok=True)  # don't show an old chart
        return None
    h = h.reset_index(drop=True)
    x = np.arange(len(h))
    level, level_note = entry_level(h)
    trades, open_pos = signals(h, level)
    daily = daily_put_call_z(h)

    fig, (a1, a2, a3) = plt.subplots(3, 1, figsize=(16, 10), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1.6, 1.1]})
    pnl = sum(sp - ep for _, ep, _, sp in trades)
    sub = f"{len(trades)} trades closed | P&L {pnl:+.1f} points | entry level: "
    sub += f"{level:.2f}bn ({level_note})" if level is not None else level_note
    if open_pos is not None:
        sub += f" | OPEN long since {h['time_et'][open_pos[0]]:%d %b %H:%M} at {open_pos[1]:.2f}"
    fig.suptitle(f"{t} dealer gamma | updated {h['time_et'].iloc[-1]:%a %d %b %Y %H:%M} ET\n{sub}",
                 x=0.06, ha="left", fontsize=13, color="#0b2545")

    # price
    a1.plot(x, h["spot"], color="#0b2545", lw=1.2, label=t)
    for ie, pe, ix, px in trades:
        a1.plot([ie, ix], [pe, px], color="#2e7d5b", lw=2)
        a1.scatter(ie, pe, marker="^", color="#1f5fbf", zorder=5, s=60)
        a1.scatter(ix, px, marker="o", facecolor="white", edgecolor="#2e7d5b", zorder=5, s=60)
        a1.annotate(f"{px - pe:+.1f}", (ix, px), xytext=(0, 8), textcoords="offset points",
                    ha="center", color="#2e7d5b", fontsize=9, fontweight="bold")
    if open_pos is not None:
        a1.scatter(open_pos[0], open_pos[1], marker="^", color="#1f5fbf", zorder=5, s=60)
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

    # put/call proxy, one bar per day placed at the day's last snapshot
    h["date"] = h["time_et"].dt.date
    lasts = h.groupby("date").tail(1)
    pos_map = dict(zip(lasts["date"], lasts.index))
    for _, r in daily.iterrows():
        if pd.isna(r["z"]) or r["date"] not in pos_map:
            continue
        colr = "#c9a227" if r["z"] >= 1 else "#bbbbbb"
        a3.bar(pos_map[r["date"]], r["z"], width=max(len(h) / 60, 1), color=colr)
    a3.axhline(1, color="#8a7a1a", ls="--", lw=0.8)
    a3.axhline(0, color="#444", lw=0.6)
    a3.set_ylabel("Put/call z\n(free proxy)")
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
             "short puts, all expiries. Bottom panel is a free stand-in for retail put buying: "
             "daily put/call volume ratio, z-score vs prior 60 days. Not investment advice.",
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
<img src="{t}_timeline.png?v={int(time.time())}" onerror="this.style.display='none'">
<img src="{t}_profile.png?v={int(time.time())}" onerror="this.style.display='none'">
</section>""")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300"><title>Dealer gamma tracker</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1200px;margin:20px auto;padding:0 12px;color:#0b2545}}
img{{width:100%;margin:8px 0;border:1px solid #eee}}table{{border-collapse:collapse;margin:8px 0}}
td,th{{border:1px solid #ddd;padding:6px 10px;text-align:center}}.neg{{color:#c0392b;font-weight:600}}
.pos{{color:#2e7d5b;font-weight:600}}small{{color:#888;font-weight:400}}footer{{color:#888;font-size:12px}}</style></head>
<body><h1>Dealer gamma tracker</h1>{''.join(cards)}
<footer>Free Cboe delayed data, ~15 min delay. Estimates assume dealers are long calls and short puts.
Not investment advice. Page refreshes every 5 minutes.</footer></body></html>"""
    (DOCS / "index.html").write_text(html)


# ----------------------------------------------------------------------------- main
def market_open(now):
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60 + 20


def run_once(force=False, charts_only=False, file=None):
    DOCS.mkdir(exist_ok=True)
    now = pd.Timestamp.now(tz=ET)
    if not charts_only and not force and not file and not market_open(now):
        print(f"US market closed ({now:%a %H:%M} ET) - nothing to do. Use --force to run anyway.")
        return
    latest = {}
    for t in CONFIG["tickers"]:
        try:
            if charts_only:
                draw_timeline(t, load_history(t))
                continue
            raw = fetch_chain(CONFIG.get("cboe_symbols", {}).get(t, t),
                              file.format(t) if file else None)
            spot, snap_time, df = parse_chain(raw)
            row, by_strike = analyse(spot, snap_time, df)
            h = append_history(t, row)
            draw_timeline(t, h)
            draw_profile(t, row, by_strike)
            latest[t] = row
            print(f"  {t} {row['time_et']}: price {row['spot']}, net gamma {row['net_gex_bn']:+.2f}bn, "
                  f"flip {row['flip']}, call wall {row['call_wall']}, put wall {row['put_wall']}")
        except Exception as e:
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
