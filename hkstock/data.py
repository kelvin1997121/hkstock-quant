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
from zoneinfo import ZoneInfo

import pandas as pd

from .universe import Market

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


def load(m: Market, period: str = "5y") -> tuple[Prices, pd.DataFrame]:
    """Returns (stock + benchmark prices, context closes)."""
    tickers = list(m.names()) + list(m.extras) + list(m.benchmarks)
    prices = _split(_download(tickers, period), tickers)
    # drop non-trading days (rows where the main index has no print)
    rows = prices.close[m.index].notna()
    prices = Prices(*(getattr(prices, f)[rows] for f in ("close", "high", "low", "volume")))
    try:
        g = _download(list(m.context), period)["Close"]
        g.index = pd.to_datetime(g.index).tz_localize(None)
    except Exception as e:
        print(f"  context data failed: {e}")
        g = pd.DataFrame()
    return prices, g


def now_hkt() -> datetime:
    return datetime.now(HKT)


def local_now(m: Market) -> datetime:
    return datetime.now(ZoneInfo(m.tz))


def market_session(m: Market, ts: datetime | None = None) -> str:
    ts = (ts or now_hkt()).astimezone(ZoneInfo(m.tz))
    if ts.weekday() >= 5:
        return "休市"
    hm = ts.hour * 100 + ts.minute
    if hm < 930:
        return "開市前"
    lag = 15 if m.key == "hk" else 5  # Yahoo delay before the closing print settles
    if hm < 1600 + lag:
        return "盤中"
    return "收市後"


def is_active(m: Market, ts: datetime | None = None) -> bool:
    """Weekday, between 08:00 and 18:30 exchange time: worth a scheduled run."""
    ts = (ts or now_hkt()).astimezone(ZoneInfo(m.tz))
    return ts.weekday() < 5 and 800 <= ts.hour * 100 + ts.minute <= 1830


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")
