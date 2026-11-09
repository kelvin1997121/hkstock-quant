"""Honest check of the fixed rules on past data.

Pass rules, written 2026-11-09 BEFORE the first run and not to be changed after
seeing results:
  * Short score: every 5th trading day (non-overlapping), Spearman IC between the
    score and the next 5-day return. PASS if mean IC t-stat >= 2.0 AND the top 10
    beat the universe average AFTER 0.3% round-trip cost.
  * Long score: every 20th day, next 60-day return; t-stat divided by sqrt(3) for the
    overlap. PASS if adjusted t >= 2.0 AND top 10 beat the universe average.
  * Regime: every 5th day, correlation of score with the next 5-day HSI return.
    PASS if t-stat >= 2.0.

Known biases, stated on the page: the universe is today's names (survivorship bias
flatters long-only results), and news/southbound-by-theme have no history so the
theme heat overlay is not tested here. The live log (tracking.py) is the real test.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COST = 0.003  # HK round trip: stamp duty 0.1% x 2 + fees, rounded up


def _ic_test(score: pd.DataFrame, fwd: pd.DataFrame, step: int, overlap: float = 1.0, top: int = 10):
    dates = score.index[::step]
    ics, ex, hits = [], [], []
    for d in dates:
        s, r = score.loc[d], fwd.loc[d]
        ok = s.notna() & r.notna()
        if ok.sum() < 20:
            continue
        s, r = s[ok], r[ok]
        ics.append(s.rank().corr(r.rank()))
        topn = r[s.nlargest(top).index].mean()
        ex.append(topn - r.mean())
        hits.append(topn > r.mean())
    if len(ics) < 10:
        return None
    ics, ex = np.array(ics), np.array(ex)
    t = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) / np.sqrt(overlap)
    return {"n": len(ics), "ic": round(float(ics.mean()), 3), "t": round(float(t), 2),
            "top_excess": round(float(ex.mean()) * 100, 2), "hit": round(float(np.mean(hits)) * 100, 1),
            "start": str(dates[0].date()), "end": str(dates[-1].date())}


def run(prices, short: pd.DataFrame, long: pd.DataFrame, regime_score: pd.Series) -> dict:
    c = prices.close[short.columns]
    fwd5 = c.shift(-5) / c - 1
    fwd60 = c.shift(-60) / c - 1
    out = {}

    r = _ic_test(short, fwd5, step=5)
    if r:
        r["top_excess_net"] = round(r["top_excess"] - COST * 100, 2)
        r["pass"] = bool(r["t"] >= 2.0 and r["top_excess_net"] > 0)
        r["rule"] = "每5日揀分數最高10隻,持5日;IC t值 ≥ 2 及扣0.3%成本後跑贏平均先算合格"
    out["short"] = r

    r = _ic_test(long, fwd60, step=20, overlap=3)
    if r:
        r["pass"] = bool(r["t"] >= 2.0 and r["top_excess"] > 0)
        r["rule"] = "每20日揀分數最高10隻,持60日;重疊調整後 t ≥ 2 及跑贏平均先算合格"
    out["long"] = r

    hsi = prices.close["^HSI"]
    f5 = (hsi.shift(-5) / hsi - 1)
    df = pd.DataFrame({"s": regime_score, "r": f5}).dropna().iloc[::5]
    if len(df) > 30:
        corr = df["s"].corr(df["r"])
        t = corr * np.sqrt((len(df) - 2) / max(1 - corr ** 2, 1e-9))
        bull = df.loc[df["s"] >= 15, "r"]
        bear = df.loc[df["s"] <= -15, "r"]
        out["regime"] = {"n": len(df), "corr": round(float(corr), 3), "t": round(float(t), 2),
                         "bull_n": int(len(bull)), "bull_ret": round(float(bull.mean() * 100), 2) if len(bull) else None,
                         "bear_n": int(len(bear)), "bear_ret": round(float(bear.mean() * 100), 2) if len(bear) else None,
                         "all_ret": round(float(df["r"].mean() * 100), 2),
                         "start": str(df.index[0].date()), "end": str(df.index[-1].date()),
                         "pass": bool(t >= 2.0),
                         "rule": "型勢分數同之後5日恒指回報嘅相關;t ≥ 2 先算合格"}
    return out
