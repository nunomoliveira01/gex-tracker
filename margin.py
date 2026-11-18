"""Margin pain indicator - JP Financas' "marginal buyer" method (live of 17 Nov 2026).

Idea: FINRA publishes, every month, how much money investors have borrowed from their brokers to buy
shares (margin debt). Each month the debt goes up, that new borrowed money bought the market at roughly
that month's average price. Add the interest they pay (~7.5% a year) and you get the price at which
each slice of borrowed money breaks even. When the index trades below those prices, that money is
losing - and borrowed money that is losing is the first to be forced to sell (margin calls).

Outputs docs/margin.json, used by the live page (panel + lines on the charts) and by the quant lab.
Second signal from the same live: when the dollar rises 4% or more against the euro in 6 months,
the S&P has tended to do badly in the next 3 months. We test that claim on history too.
"""
import io
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).parent
DATA, DOCS = ROOT / "data", ROOT / "docs"
CSV = DATA / "finra_margin.csv"
OUT = DOCS / "margin.json"
FINRA_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}

DEFAULTS = {"rate": 0.075, "cycle_start": "2022-12", "recent_start": "2025-06", "fx_threshold": 0.04,
            "refresh_hours": 3}


# ---------------------------------------------------------------- FINRA data
def load_finra():
    df = pd.read_csv(CSV, dtype={"month": str})
    return df.sort_values("month").reset_index(drop=True)


def refresh_finra():
    """Download FINRA's spreadsheet; keep the saved copy if the download fails."""
    try:
        r = requests.get(FINRA_URL, headers=UA, timeout=30)
        r.raise_for_status()
        raw = pd.read_excel(io.BytesIO(r.content), engine="openpyxl")
        raw = raw.rename(columns={raw.columns[0]: "month", raw.columns[1]: "debit_musd"})
        raw["month"] = raw["month"].astype(str).str.slice(0, 7)
        raw = raw[raw["month"].str.match(r"^\d{4}-\d{2}$")][["month", "debit_musd"]].dropna()
        raw["debit_musd"] = raw["debit_musd"].astype(float).round().astype(int)
        raw = raw.sort_values("month")
        if len(raw) < 100:
            raise ValueError(f"only {len(raw)} rows")
        old = load_finra() if CSV.exists() else raw.iloc[:0]
        if len(old) and raw["month"].iloc[-1] < old["month"].iloc[-1]:
            raise ValueError("download is older than saved copy")
        raw.to_csv(CSV, index=False)
        print(f"  margin: FINRA data up to {raw['month'].iloc[-1]} ({len(raw)} months)")
        return True
    except Exception as e:
        print(f"  margin: FINRA download failed ({e}) - using saved copy")
        return False


# ---------------------------------------------------------------- prices
def _closes(symbols, start):
    import yfinance as yf
    out = {}
    for s in symbols:
        try:
            h = yf.Ticker(s).history(start=start, interval="1d", auto_adjust=False)["Close"].dropna()
            h.index = pd.to_datetime(h.index).tz_localize(None)
            if len(h):
                out[s] = h
        except Exception as e:
            print(f"  margin: no prices for {s} ({e})")
    return out


# ---------------------------------------------------------------- the method
def build_lots(fin, monthly_px, start, rate, today):
    """Each month debt rose since `start`: a slice of borrowed money bought at that month's average price.
    When debt falls, every open slice shrinks in proportion (we don't know who repaid, so spread it).
    Breakeven = entry price grown by the interest paid since then (simple interest)."""
    f = fin[fin["month"] >= start].reset_index(drop=True)
    lots = []
    for i in range(1, len(f)):
        m, d = f["month"][i], (f["debit_musd"][i] - f["debit_musd"][i - 1]) / 1000.0   # $bn
        px = monthly_px.get(m)
        if d > 0 and px is not None:
            lots.append({"month": m, "bn": d, "entry": px})
        elif d < 0:
            tot = sum(l["bn"] for l in lots)
            if tot > 0:
                k = max(0.0, 1 + d / tot)
                for l in lots:
                    l["bn"] *= k
    for l in lots:
        mid = pd.Timestamp(l["month"] + "-15")
        years = max(0.0, (today - mid).days / 365.25)
        l["breakeven"] = l["entry"] * (1 + rate * years)
    return [l for l in lots if l["bn"] > 0.05]


def summarise(lots, last):
    tot = sum(l["bn"] for l in lots)
    if not tot:
        return None
    entry = sum(l["bn"] * l["entry"] for l in lots) / tot
    be = sum(l["bn"] * l["breakeven"] for l in lots) / tot
    under = sum(l["bn"] for l in lots if l["breakeven"] > last) / tot
    return {"amount_bn": round(tot, 1), "entry": round(entry, 1), "breakeven": round(be, 1),
            "underwater_pct": round(under * 100, 1)}


def pain_levels(lots, qs=(25, 50, 75)):
    """Price at which q% of the borrowed money would be losing (sorted by breakeven, highest first)."""
    tot = sum(l["bn"] for l in lots)
    s = sorted(lots, key=lambda l: -l["breakeven"])
    out, acc = {}, 0.0
    for l in s:
        acc += l["bn"]
        for q in qs:
            if str(q) not in out and acc / tot * 100 >= q:
                out[str(q)] = round(l["breakeven"], 1)
    return out


def underwater_at(lots, price):
    tot = sum(l["bn"] for l in lots)
    return None if not tot else round(sum(l["bn"] for l in lots if l["breakeven"] > price) / tot * 100, 1)


def fx_block(px, threshold):
    """JP: dollar up >= 4% against the euro over 6 months -> S&P weak over the next 3 months. Test it."""
    out = {}
    eur, jpy, spx = px.get("EURUSD=X"), px.get("JPY=X"), px.get("^GSPC")
    for name, s, usd_up_sign in (("eurusd", eur, -1), ("usdjpy", jpy, +1)):
        if s is None or len(s) < 130:
            continue
        ago = s[s.index <= s.index[-1] - pd.Timedelta(days=182)]
        if not len(ago):
            continue
        chg = s.iloc[-1] / ago.iloc[-1] - 1
        out[name] = {"now": round(float(s.iloc[-1]), 4), "six_months_ago": round(float(ago.iloc[-1]), 4),
                     "chg_6m_pct": round(chg * 100, 2), "usd_chg_6m_pct": round(usd_up_sign * chg * 100, 2)}
    if "eurusd" in out:
        out["signal"] = bool(out["eurusd"]["usd_chg_6m_pct"] >= threshold * 100)
    # history check, month-end data
    if eur is not None and spx is not None and len(eur) > 500:
        e, sp = eur.resample("ME").last(), spx.resample("ME").last()
        j = pd.concat({"e": e, "s": sp}, axis=1).dropna()
        j["usd6"] = -(j["e"] / j["e"].shift(6) - 1)
        j["fwd3"] = j["s"].shift(-3) / j["s"] - 1
        j = j.dropna()
        sig = j[j["usd6"] >= threshold]
        if len(j):
            out["history"] = {
                "since": str(j.index[0].date())[:7], "months": int(len(j)),
                "signal_months": int(len(sig)),
                "spx_3m_avg_after_signal_pct": round(float(sig["fwd3"].mean() * 100), 2) if len(sig) else None,
                "spx_3m_down_share_after_signal_pct": round(float((sig["fwd3"] < 0).mean() * 100), 1) if len(sig) else None,
                "spx_3m_avg_all_pct": round(float(j["fwd3"].mean() * 100), 2),
                "spx_3m_down_share_all_pct": round(float((j["fwd3"] < 0).mean() * 100), 1),
            }
    return out


def build(cfg=None, refresh=True):
    c = {**DEFAULTS, **(cfg or {})}
    if refresh:
        refresh_finra()
    fin = load_finra()
    today = pd.Timestamp.now(tz="UTC").tz_localize(None)
    syms = ["^GSPC", "^NDX", "SPY", "QQQ", "ES=F", "NQ=F", "EURUSD=X", "JPY=X"]
    px = _closes(syms, "2003-12-01")
    if "^GSPC" not in px:
        raise RuntimeError("no S&P prices")
    last_m = fin["month"].iloc[-1]
    debt = fin["debit_musd"].iloc[-1] / 1000
    d21 = fin[(fin["month"] >= "2021-01") & (fin["month"] <= "2021-12")]["debit_musd"]
    peak21 = d21.max() / 1000 if len(d21) else None
    yr = fin[fin["month"] == f"{int(last_m[:4]) - 1}{last_m[4:]}"]["debit_musd"]
    out = {
        "updated_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "finra_month": last_m, "debt_bn": round(debt, 1),
        "peak_2021_bn": round(peak21, 1) if peak21 else None,
        "drop_to_2021_pct": round((peak21 / debt - 1) * 100, 1) if peak21 else None,
        "yoy_pct": round((debt / (yr.iloc[0] / 1000) - 1) * 100, 1) if len(yr) else None,
        "rate": c["rate"], "cycle_start": c["cycle_start"], "recent_start": c["recent_start"],
        "debt_series": [[m, round(v / 1000, 1)] for m, v in zip(fin["month"], fin["debit_musd"]) if m >= "2019-01"],
        "indexes": {}, "ratio": {},
    }
    for idx, kids in (("^GSPC", ("SPY", "ES=F")), ("^NDX", ("QQQ", "NQ=F"))):
        s = px.get(idx)
        if s is None:
            continue
        monthly = {str(k)[:7]: float(v) for k, v in s.groupby(s.index.strftime("%Y-%m")).mean().items()}
        last = float(s.iloc[-1])
        block = {"last": round(last, 2), "last_date": str(s.index[-1].date())}
        for key, start in (("cycle", c["cycle_start"]), ("recent", c["recent_start"])):
            lots = build_lots(fin, monthly, start, c["rate"], today)
            block[key] = summarise(lots, last)
            if key == "cycle" and lots:
                block["pain"] = pain_levels(lots)
                block["lots"] = [[l["month"], round(l["bn"], 1), round(l["entry"], 1), round(l["breakeven"], 1)] for l in lots]
        name = "SPX" if idx == "^GSPC" else "NDX"
        out["indexes"][name] = block
        for k in kids:
            ks = px.get(k)
            if ks is not None:
                j = pd.concat([ks, s], axis=1).dropna()
                if len(j):
                    out["ratio"][k.replace("=F", "")] = round(float(j.iloc[-1, 0] / j.iloc[-1, 1]), 6)
        out["ratio"][name] = 1.0
    out["ratio"]["SPX"] = 1.0
    out["fx"] = fx_block(px, c["fx_threshold"])
    out["fx"]["threshold_pct"] = c["fx_threshold"] * 100
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    sp = out["indexes"].get("SPX", {})
    print(f"  margin: debt ${debt:,.0f}bn ({last_m}); S&P cycle breakeven {sp.get('cycle', {}).get('breakeven')}, "
          f"{sp.get('cycle', {}).get('underwater_pct')}% underwater; since Jun-25 {sp.get('recent', {}).get('breakeven')}")
    return out


def maybe_build(cfg=None):
    """Rebuild at most every few hours (FINRA changes once a month; prices are applied live in the page)."""
    c = {**DEFAULTS, **(cfg or {})}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text())
            age = pd.Timestamp.now(tz="UTC") - pd.Timestamp(old["updated_utc"])
            if age < pd.Timedelta(hours=c["refresh_hours"]):
                return old
        except Exception:
            pass
    return build(c)


def load():
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return None


def spx_lots(m, index="SPX"):
    if not m:
        return []
    b = m.get("indexes", {}).get(index, {})
    return [{"month": a, "bn": x, "entry": e, "breakeven": be} for a, x, e, be in b.get("lots", [])]


if __name__ == "__main__":
    print(json.dumps(build(), indent=1)[:3000])
