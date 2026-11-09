"""Buy zone, stop-loss and targets for every stock, from support/resistance and ATR.

Rules fixed 2026-11-09, before any test of them (see test_short_plan):

Short trade (1-2 weeks), a = 14-day ATR in price terms:
  entry high = close, or close - 0.5a if RSI >= 70 (don't chase a hot stock)
  entry low  = the highest support (MA20, 10-day low) between close - 2a and entry high - 0.2a;
               if none, entry high - a
  stop       = entry low - a
  target 1   = nearest resistance (20/60/250-day high) at least close + a, else close + 2a
  target 2   = next resistance at least target 1 + a, else target 1 + 1.5a

Long hold (3-12 months):
  zone high  = min(close, MA50 + 0.5a)
  zone low   = max(min(MA200, zone high - a), zone high - 3a)
  stop       = min(zone low - 1.5a, MA200 x 0.97)
  target     = max(52-week high, close x 1.15)

Prices are rounded to the exchange tick (HK spread table; US 0.01).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

HK_TICKS = [(0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05), (200, 0.1),
            (500, 0.2), (1000, 0.5), (2000, 1), (5000, 2), (math.inf, 5)]


def tick_round(p: float, market: str) -> float | None:
    if p is None or not np.isfinite(p) or p <= 0:
        return None
    if market == "us":
        return round(p, 2)
    step = next(s for lim, s in HK_TICKS if p < lim)
    return round(round(p / step) * step, 3)


def frames(prices, f: dict) -> dict[str, pd.DataFrame]:
    """All plan levels as date x ticker frames (causal: day t uses data to t)."""
    c, a = prices.close, f["atr_pct"] * prices.close
    rsi = f["rsi14"]
    low10 = prices.low.rolling(10, min_periods=10).min()
    hi = {n: prices.high.rolling(n, min_periods=min(n, 120)).max() for n in (20, 60, 250)}

    eh = c.where(rsi < 70, c - 0.5 * a)
    el = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    for sup in (f["ma20"], low10):
        ok = (sup <= eh - 0.2 * a) & (sup >= c - 2 * a)
        el = el.where(~ok | (el >= sup), sup.where(ok))
    el = el.fillna(eh - a)
    stop = el - a

    t1 = pd.DataFrame(np.inf, index=c.index, columns=c.columns)
    for r in hi.values():
        ok = r >= c + a
        t1 = t1.where(~ok | (t1 <= r), r)
    t1 = t1.where(np.isfinite(t1), c + 2 * a)
    t2 = pd.DataFrame(np.inf, index=c.index, columns=c.columns)
    for r in hi.values():
        ok = r >= t1 + a
        t2 = t2.where(~ok | (t2 <= r), r)
    t2 = t2.where(np.isfinite(t2), t1 + 1.5 * a)

    zh = np.minimum(c, f["ma50"] + 0.5 * a)
    zl = np.maximum(np.minimum(f["ma200"], zh - a), zh - 3 * a)
    lstop = np.minimum(zl - 1.5 * a, f["ma200"] * 0.97)
    ltarget = np.maximum(hi[250], c * 1.15)
    return {"entry_low": el, "entry_high": eh, "stop": stop, "t1": t1, "t2": t2, "atr": a,
            "support": low10, "res20": hi[20], "res250": hi[250],
            "zone_low": zl, "zone_high": zh, "long_stop": lstop, "long_target": ltarget}


def plan_for(L: dict, t: str, market: str, i: int = -1) -> dict:
    g = lambda k: float(L[k][t].iloc[i]) if not pd.isna(L[k][t].iloc[i]) else None
    r = lambda k: tick_round(g(k), market)
    el, eh, st, t1 = g("entry_low"), g("entry_high"), g("stop"), g("t1")
    mid = (el + eh) / 2 if el and eh else None
    rr = (t1 - mid) / (mid - st) if mid and st and t1 and mid > st else None
    return {
        "short": {"entry_low": r("entry_low"), "entry_high": r("entry_high"), "stop": r("stop"),
                  "t1": r("t1"), "t2": r("t2"), "rr": round(rr, 2) if rr else None,
                  "stop_pct": round((st / mid - 1) * 100, 1) if mid and st else None,
                  "t1_pct": round((t1 / mid - 1) * 100, 1) if mid and t1 else None},
        "long": {"zone_low": r("zone_low"), "zone_high": r("zone_high"), "stop": r("long_stop"),
                 "target": r("long_target")},
        "support": r("support"), "resistance": r("res20"), "high52": r("res250"),
    }


def test_short_plan(prices, short_score: pd.DataFrame, L: dict, days: int = 10, top: int = 10) -> dict | None:
    """Every 5th day buy the top-10 short picks at the close, then walk forward up to `days` days:
    target 1 first = +R (reward/risk), stop first = -1R (both in one day counts as stop),
    neither = the move at day `days` in R. Trades on one date move together, so t is taken over
    per-date average R and divided by sqrt(days / 5) for the overlapping holds.
    PASS if mean R > 0 with that t >= 2 (rule fixed 2026-11-09)."""
    c, hi, lo = prices.close, prices.high, prices.low
    res = []
    dates = short_score.index[:-days][::5]
    pos = {d: k for k, d in enumerate(c.index)}
    for d in dates:
        s = short_score.loc[d].dropna()
        if len(s) < 20:
            continue
        k = pos[d]
        for t in s.nlargest(top).index:
            e, st, tg = c[t].iloc[k], L["stop"][t].iloc[k], L["t1"][t].iloc[k]
            if not (np.isfinite(e) and np.isfinite(st) and np.isfinite(tg)) or e <= st or tg <= e:
                continue
            risk = e - st
            out = None
            for j in range(k + 1, k + days + 1):
                if lo[t].iloc[j] <= st:
                    out = ("stop", -1.0)
                    break
                if hi[t].iloc[j] >= tg:
                    out = ("target", (tg - e) / risk)
                    break
            if out is None:
                out = ("open", (c[t].iloc[k + days] - e) / risk)
            res.append((d, *out))
    if len(res) < 50:
        return None
    rs = np.array([x[2] for x in res])
    kinds = [x[1] for x in res]
    per_date = pd.Series(rs, index=[x[0] for x in res]).groupby(level=0).mean()
    t = per_date.mean() / (per_date.std(ddof=1) / np.sqrt(len(per_date))) / np.sqrt(days / 5)
    return {"n": len(rs), "target_first": round(kinds.count("target") / len(rs) * 100, 1),
            "stop_first": round(kinds.count("stop") / len(rs) * 100, 1),
            "neither": round(kinds.count("open") / len(rs) * 100, 1),
            "mean_r": round(float(rs.mean()), 3), "t": round(float(t), 2), "pass": bool(t >= 2 and rs.mean() > 0),
            "rule": f"每5日買短炒頭{top}隻(收市價),{days}日內先到目標1定先到止蝕;平均R > 0 且 t ≥ 2 先算合格"}
