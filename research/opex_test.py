"""Test 13 — win rate in opex weeks, and double size in opex weeks (rules: claude/opex-teste-pre-registo.md).
The three site strategies through the blocks backtest engine; NQ, daily, no costs."""
import json
import math

import numpy as np
import pandas as pd

import blocks_backtest as BB
from backtest import ROOT
from metrics import stats as acct_stats
from walls_oos import legs_account, per_trade, yahoo

PERIODS = {"all": ("2018-11-19", "2026-11-17"), "oos": ("2018-11-19", "2023-07-31"), "is": ("2023-11-17", "2026-11-17")}
SPECS = {
    "My strategy (−10bn)": {"side": "long", "trade": "NQ", "entry": {"type": "gamma_below", "bn": -10}, "filters": [],
                            "exits": [{"type": "flip"}], "add_on": {"on": True, "z": 1.0}},
    "Put wall long": {"side": "long", "trade": "NQ", "entry": {"type": "put_wall"}, "filters": [],
                      "exits": [{"type": "opposite_wall"}, {"type": "flip"}], "add_on": {"on": False}},
    "Call wall short": {"side": "short", "trade": "NQ", "entry": {"type": "call_wall"}, "filters": [],
                        "exits": [{"type": "opposite_wall"}, {"type": "flip"}], "add_on": {"on": False}},
}


def third_fri(y, m):
    x = pd.Timestamp(y, m, 15)
    return x + pd.Timedelta(days=(4 - x.weekday()) % 7)


OPEX = {third_fri(y, m).to_period("W-FRI"): m for y in range(2018, 2027) for m in range(1, 13)}


def kind(t):
    m = OPEX.get(pd.Timestamp(t).to_period("W-FRI"))
    return None if m is None else ("quarterly" if m in (3, 6, 9, 12) else "monthly")


def two_prop_p(w1, n1, w2, n2):
    if min(n1, n2) == 0:
        return None
    p = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2)) or 1e-9
    z = (w1 / n1 - w2 / n2) / se
    return round(math.erfc(abs(z) / math.sqrt(2)), 3)


def main():
    d = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date").sort_index()
    pw = pd.read_csv(ROOT / "data" / "putwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    cw = pd.read_csv(ROOT / "data" / "callwall.csv", parse_dates=["date"]).drop_duplicates("date").set_index("date")
    d = d.join(pw[["put_wall"]]).join(cw[["call_wall"]])
    d["open"] = yahoo("SPY")["Open"].reindex(d.index)
    nq = yahoo("NQ=F").reindex(d.index).ffill()["Close"]
    spy = d["spy_close"]
    spy_tr = yahoo("SPY", adjust=True)["Close"].reindex(d.index).ffill()
    rf_all = yahoo("^IRX")["Close"].reindex(d.index).ffill() / 100 / 252
    out, L = {"winrates": {}, "sized": {}}, ["# Test 13 — opex weeks: win rate, and double size in opex weeks (NQ, daily, no costs)", ""]

    # ---------- part 1: win rates (full period)
    a, b = PERIODS["all"]
    win = nq.pct_change().loc[a:b].dropna().index
    beta_all = float(np.cov(nq.pct_change().loc[win], spy.pct_change().loc[win].fillna(0))[0, 1] / spy.pct_change().loc[win].var())
    conv_all = lambda t, px: nq.at[t] * (1 + beta_all * (px / d.at[t, "spy_close"] - 1))
    rows = {}
    L += ["## 1. Win rate by type of week (Nov 2018 → Nov 2026)", "",
          "| Strategy | Week | Trades | Win rate | Avg per trade | p (vs other weeks) |", "|---|---|---|---|---|---|"]
    confirmed = True
    for name, s in SPECS.items():
        tr = [x for x in BB.trades(d.loc[:b], s, a) if not x["open"]]
        rets = [r for _, r in per_trade(tr, [(conv_all, 1 if s["side"] == "long" else -1, lambda x: 1.0)])]
        df = pd.DataFrame({"e": [x["e"] for x in tr], "r": rets})
        df["k"] = df["e"].map(kind)
        df["opex"] = df["k"].notna()
        df["yr"] = df["e"].dt.year
        rows[name] = df
        res = {}
        for lab, sub in (("Opex weeks", df[df.opex]), ("Other weeks", df[~df.opex]), ("Quarterly opex", df[df.k == "quarterly"]),
                         ("Monthly-only opex", df[df.k == "monthly"]), ("December opex", df[(df.k == "quarterly") & (df.e.dt.month == 12)])):
            n, w = len(sub), int((sub.r > 0).sum())
            p = two_prop_p(w, n, int((df[~df.opex].r > 0).sum()), int((~df.opex).sum())) if lab != "Other weeks" else None
            res[lab] = {"n": n, "win_pct": round(100 * w / n, 1) if n else None, "avg_pct": round(100 * sub.r.mean(), 2) if n else None, "p": p}
            L.append(f"| {name} | {lab} | {n} | {res[lab]['win_pct']}% | {res[lab]['avg_pct']}% | {p if p is not None else ''} |")
        res["by_year_opex"] = {int(y): {"n": len(g), "win_pct": round(100 * (g.r > 0).mean(), 1)} for y, g in df[df.opex].groupby("yr")}
        res["by_year_other"] = {int(y): {"n": len(g), "win_pct": round(100 * (g.r > 0).mean(), 1)} for y, g in df[~df.opex].groupby("yr")}
        out["winrates"][name] = res
        if not (res["Opex weeks"]["win_pct"] or 0) > (res["Other weeks"]["win_pct"] or 0):
            confirmed = False
    L += ["", "## 2. Opex-week win rate by year (other weeks in brackets)", "",
          "| Year | " + " | ".join(SPECS) + " |", "|---|" + "---|" * len(SPECS)]
    for y in range(2018, 2027):
        cells = []
        for name in SPECS:
            o, x = out["winrates"][name]["by_year_opex"].get(y), out["winrates"][name]["by_year_other"].get(y)
            cells.append((f"{o['win_pct']}% ({o['n']})" if o else "–") + (f" [{x['win_pct']}%]" if x else ""))
        L.append(f"| {y} | " + " | ".join(cells) + " |")
    out["confirmed"] = confirmed
    L += ["", f"**Confirmed (every strategy wins more often in opex weeks): {confirmed}**", ""]

    # ---------- part 2: double size in opex weeks (only if confirmed)
    if confirmed:
        L += ["## 3. Double size in opex weeks vs equal size", "",
              "| Period | Strategy | Sizing | Trades | Win rate | EV per trade (sized) | Return/yr | Sharpe | Worst fall | Beta | Alpha/yr | Alpha t |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for per, (a, b) in PERIODS.items():
            idx = d.loc[a:b].index
            win = nq.pct_change().loc[a:b].dropna().index
            beta = float(np.cov(nq.pct_change().loc[win], spy.pct_change().loc[win].fillna(0))[0, 1] / spy.pct_change().loc[win].var())
            conv = lambda t, px, beta=beta: nq.at[t] * (1 + beta * (px / d.at[t, "spy_close"] - 1))
            rf = rf_all.loc[idx].fillna(0)
            spy_ex = spy_tr.pct_change().loc[idx].fillna(0) - rf
            combo = {}
            for name, s in SPECS.items():
                sign = 1 if s["side"] == "long" else -1
                tr = BB.trades(d.loc[:b], s, a)
                done = [x for x in tr if not x["open"]]
                n_op = sum(kind(x["e"]) is not None for x in done)
                k = len(done) / (len(done) - n_op + 1.5 * n_op) if done else 1
                sizings = {"equal (1 unit)": lambda x: 1.0, "double in opex": lambda x: 2.0 if kind(x["e"]) else 1.0,
                           "same exposure (opex 1.5×)": lambda x, k=k: (1.5 * k) if kind(x["e"]) else k}
                for sz, f in sizings.items():
                    acct = legs_account(d.index, tr, nq, conv, sign, lambda x, f=f: 2.0 * f(x)).loc[idx].fillna(0)
                    combo.setdefault(sz, []).append(acct)
                    st = acct_stats(acct, spy_ex, rf)
                    rets = np.array([r for _, r in per_trade(tr, [(conv, sign, lambda x: 1.0)])])
                    sizes = np.array([f(x) for x in done])
                    res = {**st, "n": len(done), "win_pct": round(100 * (rets > 0).mean(), 1), "ev_sized_pct": round(100 * (rets * sizes).mean(), 2)}
                    out["sized"][f"{per}|{name}|{sz}"] = res
                    L.append(f"| {per} | {name} | {sz} | {res['n']} | {res['win_pct']}% | {res['ev_sized_pct']}% | {st['cagr_pct']}% | {st['sharpe']} | "
                             f"{st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} |")
            for sz, accts in combo.items():   # all three in one account, a third of the capital each
                acct = sum(accts) / 3
                st = acct_stats(acct, spy_ex, rf)
                out["sized"][f"{per}|Combined (1/3 each)|{sz}"] = st
                L.append(f"| {per} | **Combined (1/3 each)** | {sz} | | | | {st['cagr_pct']}% | {st['sharpe']} | {st['max_dd_pct']}% | {st['beta']} | {st['alpha_pct_yr']}% | {st['alpha_t']} |")
    (ROOT / "results_opex.json").write_text(json.dumps(out, indent=1, default=str))
    (ROOT / "results_opex.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
