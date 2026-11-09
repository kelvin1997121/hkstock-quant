"""Short-term (短炒, about 1-2 weeks) and long-term (長揸, 3-12 months) stock scores.

The weights were set by hand before any backtest and are NOT fitted to data, so
the backtest in backtest.py is a test of these fixed rules, not a curve fit.
Changing a weight after looking at backtest numbers turns the backtest into
in-sample evidence; record any change and judge it on dates after the change.

Both scores are cross-sectional: 0-100 = percentile within today's universe,
so they say "stronger than the others today", not "will rise".
"""
from __future__ import annotations

import pandas as pd


def _rank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True)


SHORT_WEIGHTS = {  # fixed 2026-11-09
    "ret20": 0.25,      # 1-month strength
    "cmf5": 0.20,       # money flowing in this week
    "turn_ratio": 0.20, # turnover pick-up vs its own 20-day norm
    "trend_ok": 0.15,   # close > MA20 > MA50
    "breakout": 0.10,   # new 20-day closing high
    "not_hot": 0.10,    # RSI below 75 (not yet overheated)
}

LONG_WEIGHTS = {  # fixed 2026-11-09
    "mom_12_1": 0.35,     # 12-month momentum skipping last month
    "above_ma200": 0.20,  # long-term uptrend
    "low_vol": 0.25,      # lower volatility
    "near_high": 0.20,    # small drawdown from 52-week high
}


def short_score(f: dict[str, pd.DataFrame], cols: list[str]) -> pd.DataFrame:
    w = SHORT_WEIGHTS
    s = (w["ret20"] * _rank(f["ret20"][cols])
         + w["cmf5"] * _rank(f["cmf5"][cols])
         + w["turn_ratio"] * _rank(f["turn_ratio"][cols])
         + w["trend_ok"] * f["trend_ok"][cols]
         + w["breakout"] * f["breakout"][cols]
         + w["not_hot"] * (f["rsi14"][cols] < 75).astype(float))
    s = s - 0.15 * (f["rsi14"][cols] > 82).astype(float)  # overheated: chase risk
    return (s.where(f["ma50"][cols].notna()) * 100).clip(0, 100)


def long_score(f: dict[str, pd.DataFrame], cols: list[str]) -> pd.DataFrame:
    w = LONG_WEIGHTS
    s = (w["mom_12_1"] * _rank(f["mom_12_1"][cols])
         + w["above_ma200"] * f["above_ma200"][cols]
         + w["low_vol"] * _rank(-f["vol60"][cols])
         + w["near_high"] * _rank(f["drawdown"][cols]))
    return (s.where(f["ma200"][cols].notna() & f["mom_12_1"][cols].notna()) * 100).clip(0, 100)


def short_reasons(f, t: str, i=-1) -> list[str]:
    r = []
    g = lambda k: f[k][t].iloc[i]
    if g("trend_ok") == 1:
        r.append("股價企穩20天及50天線之上,均線向上排列")
    if g("breakout") == 1:
        r.append("收市創20日新高(突破)")
    if g("turn_ratio") >= 1.5:
        r.append(f"成交額係平時 {g('turn_ratio'):.1f} 倍,有資金追入")
    if g("cmf5") > 0.15:
        r.append("近5日資金流(CMF)明顯流入")
    if g("ret20") > 0.10:
        r.append(f"一個月升 {g('ret20') * 100:.0f}%,動力強")
    if g("rsi14") > 75:
        r.append(f"RSI {g('rsi14'):.0f},短線偏熱,唔好追高")
    return r


def long_reasons(f, t: str, i=-1) -> list[str]:
    r = []
    g = lambda k: f[k][t].iloc[i]
    if g("above_ma200") == 1:
        r.append("企穩200天線,長期趨勢向上")
    if g("mom_12_1") > 0.2:
        r.append(f"過去一年(扣除最近一個月)升 {g('mom_12_1') * 100:.0f}%")
    if g("vol60") < 0.30:
        r.append(f"波幅低(年化 {g('vol60') * 100:.0f}%),走勢穩")
    if g("drawdown") > -0.10:
        r.append("貼近52週高位")
    elif g("drawdown") < -0.30:
        r.append(f"距52週高位仍低 {-g('drawdown') * 100:.0f}%")
    return r
