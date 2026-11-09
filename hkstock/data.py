"""Price data from Yahoo Finance (works from Hong Kong and from GitHub's servers).

HK prices on Yahoo are delayed about 15 minutes. A run during trading hours sees
today's bar so far; the report marks that as 盤中.
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from . import universe

HKT = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"


@dataclass
class Prices:
    close: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame

    @property
    def turnover(self) -> pd.DataFrame:
        return self.close * self.volume


def _download(tickers: list[str], period: str) -> pd.DataFrame:
    import yfinance as yf
    # a private tz cache avoids yfinance's "database is locked" when threads share one
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_"))
    last = None
    for attempt in range(3):
        try:
            df = yf.download(tickers, period=period, auto_adjust=True, progress=False,
                             threads=True, group_by="column")
            if not df.empty:
                return df
        except Exception as e:  # network hiccup: wait and retry
            last = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Yahoo download failed: {last}")


def _split(df: pd.DataFrame, tickers: list[str]) -> Prices:
    def field(name):
        out = df[name] if name in df.columns.get_level_values(0) else pd.DataFrame()
        return out.reindex(columns=[t for t in tickers if t in out.columns])
    close = field("Close")
    keep = [t for t in close.columns if close[t].notna().sum() > 60]
    missing = sorted(set(tickers) - set(keep))
    if missing:
        print(f"  skipped (no data on Yahoo): {', '.join(missing)}")
    p = Prices(close[keep], field("High")[keep], field("Low")[keep], field("Volume")[keep])
    p.close.index = pd.to_datetime(p.close.index).tz_localize(None)
    for f in (p.high, p.low, p.volume):
        f.index = p.close.index
    return p


def load(period: str = "5y") -> tuple[Prices, pd.DataFrame]:
    """Returns (HK prices incl. benchmarks, global closes)."""
    hk = list(universe.stock_names()) + list(universe.BENCHMARKS)
    hk_prices = _split(_download(hk, period), hk)
    # drop non-trading days (rows where the HSI itself has no print)
    rows = hk_prices.close["^HSI"].notna()
    hk_prices = Prices(*(getattr(hk_prices, f)[rows] for f in ("close", "high", "low", "volume")))
    gl = list(universe.GLOBAL)
    try:
        g = _download(gl, "2y")["Close"]
        g.index = pd.to_datetime(g.index).tz_localize(None)
    except Exception as e:
        print(f"  global data failed: {e}")
        g = pd.DataFrame()
    return hk_prices, g


def now_hkt() -> datetime:
    return datetime.now(HKT)


def market_session(ts: datetime | None = None) -> str:
    ts = ts or now_hkt()
    if ts.weekday() >= 5:
        return "休市"
    hm = ts.hour * 100 + ts.minute
    if hm < 930:
        return "開市前"
    if hm < 1600 + 15:  # Yahoo lags ~15 min; treat until 16:15 as still forming
        return "盤中"
    return "收市後"


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")
