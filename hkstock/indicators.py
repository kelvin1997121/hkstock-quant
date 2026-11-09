"""Technical features. Every feature on day t uses data up to and including t only,
so the same code runs the live scan and the backtest without look-ahead."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .data import Prices


def rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr_pct(p: Prices, n: int = 14) -> pd.DataFrame:
    prev = p.close.shift(1)
    tr = pd.concat([(p.high - p.low), (p.high - prev).abs(), (p.low - prev).abs()]).groupby(level=0).max()
    tr = tr.reindex(p.close.index)
    return tr.rolling(n, min_periods=n).mean() / p.close


def cmf(p: Prices, n: int) -> pd.DataFrame:
    """Chaikin money flow: turnover weighted by where the close sits in the day's range.
    +1 = every dollar traded closed at the high (buyers in control), -1 = at the low."""
    rng = (p.high - p.low).replace(0, np.nan)
    mfm = ((p.close - p.low) - (p.high - p.close)) / rng
    t = p.turnover
    return (mfm.fillna(0) * t).rolling(n, min_periods=n).sum() / t.rolling(n, min_periods=n).sum()


def features(p: Prices) -> dict[str, pd.DataFrame]:
    c = p.close
    t = p.turnover
    f = {
        "ret1": c.pct_change(1, fill_method=None),
        "ret5": c.pct_change(5, fill_method=None),
        "ret20": c.pct_change(20, fill_method=None),
        "ret60": c.pct_change(60, fill_method=None),
        "mom_12_1": c.shift(20) / c.shift(250) - 1,  # 12-month momentum skipping the last month
        "ma20": c.rolling(20, min_periods=20).mean(),
        "ma50": c.rolling(50, min_periods=50).mean(),
        "ma200": c.rolling(200, min_periods=200).mean(),
        "rsi14": rsi(c),
        "atr_pct": atr_pct(p),
        "vol60": c.pct_change(fill_method=None).rolling(60, min_periods=40).std() * np.sqrt(252),
        "turn_ratio": t.rolling(3, min_periods=1).mean() / t.shift(3).rolling(20, min_periods=10).mean(),
        "high20_prev": c.shift(1).rolling(20, min_periods=20).max(),
        "high250": c.rolling(250, min_periods=120).max(),
        "cmf5": cmf(p, 5),
        "cmf20": cmf(p, 20),
        "turnover20": t.rolling(20, min_periods=10).mean(),
    }
    f["dist_ma20"] = c / f["ma20"] - 1
    f["drawdown"] = c / f["high250"] - 1
    f["breakout"] = (c > f["high20_prev"]).astype(float).where(f["high20_prev"].notna())
    f["trend_ok"] = ((c > f["ma20"]) & (f["ma20"] > f["ma50"])).astype(float).where(f["ma50"].notna())
    f["above_ma200"] = (c > f["ma200"]).astype(float).where(f["ma200"].notna())
    return f
