"""Earnings and dividend calendar from Yahoo, cached for 3 days (one request per ticker)."""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta

from .data import DATA

CACHE = DATA / "events.json"
MAX_AGE = timedelta(days=3)


def _fetch(tickers: list[str]) -> dict:
    import yfinance as yf
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_"))
    out = {}
    for t in tickers:
        try:
            c = yf.Ticker(t).calendar or {}
        except Exception:
            c = {}
        e = c.get("Earnings Date") or []
        out[t] = {"earnings": str(e[0]) if e else None,
                  "ex_div": str(c["Ex-Dividend Date"]) if c.get("Ex-Dividend Date") else None,
                  "fetched": date.today().isoformat()}
    return out


def calendar(tickers: list[str], save: bool = True) -> dict:
    """{ticker: {earnings, ex_div}}; refreshes entries older than 3 days or missing."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    today = date.today()
    stale = [t for t in tickers
             if t not in cache or today - date.fromisoformat(cache[t]["fetched"]) > MAX_AGE
             or (cache[t]["earnings"] and date.fromisoformat(cache[t]["earnings"]) < today)]
    if stale:
        print(f"  events: refreshing {len(stale)} tickers")
        cache.update(_fetch(stale))
        if save:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    return {t: cache[t] for t in tickers if t in cache}


def days_until(d: str | None, today: date | None = None) -> int | None:
    if not d:
        return None
    return (date.fromisoformat(d) - (today or date.today())).days


def upcoming(cal: dict, names: dict, within: int = 7, today: date | None = None) -> list[dict]:
    rows = []
    for t, e in cal.items():
        n = days_until(e.get("earnings"), today)
        if n is not None and 0 <= n <= within:
            rows.append({"ticker": t, "name": names.get(t, t), "date": e["earnings"], "days": n})
    return sorted(rows, key=lambda r: (r["days"], r["ticker"]))
