"""Earnings and dividend calendar from Yahoo, cached for 3 days.

The next results date comes from Yahoo's earnings history when it lists a future date. Many HK
companies only appear with past dates, so the next date is then estimated as one year after the
results date of the same season last year (HK companies keep a steady results calendar); those rows
carry earnings_est = True and the page marks them as estimates."""
from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta

from .data import DATA

CACHE = DATA / "events.json"
MAX_AGE = timedelta(days=3)


SCHEMA = 2


def next_earnings(dates: list[date], today: date) -> tuple[str | None, bool]:
    """(next results date, estimated?) from a list of known results dates."""
    future = [d for d in dates if d >= today]
    if future:
        return min(future).isoformat(), False
    guesses = [d + timedelta(days=364) for d in dates]          # same weekday a year on
    guesses = [g for g in guesses if today <= g <= today + timedelta(days=200)]
    return (min(guesses).isoformat(), True) if guesses else (None, False)


def _fetch(tickers: list[str]) -> dict:
    import yfinance as yf
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_"))
    out, today = {}, date.today()
    for t in tickers:
        tk = yf.Ticker(t)
        try:
            c = tk.calendar or {}
        except Exception:
            c = {}
        try:
            h = tk.get_earnings_dates(limit=12)
            dates = [] if h is None else sorted({x.date() for x in h.index})
        except Exception:
            dates = []
        cal = [d.date() if isinstance(d, datetime) else d for d in (c.get("Earnings Date") or []) if isinstance(d, date)]
        nxt, est = next_earnings(dates + cal, today)
        out[t] = {"earnings": nxt, "earnings_est": est,
                  "ex_div": str(c["Ex-Dividend Date"]) if c.get("Ex-Dividend Date") else None,
                  "fetched": today.isoformat(), "v": SCHEMA}
    return out


def calendar(tickers: list[str], save: bool = True) -> dict:
    """{ticker: {earnings, ex_div}}; refreshes entries older than 3 days or missing."""
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    today = date.today()
    def is_stale(e: dict) -> bool:
        if e.get("v") != SCHEMA or today - date.fromisoformat(e["fetched"]) > MAX_AGE:
            return True
        # a results date that has passed: look again, but at most once a day (not every 15-minute run)
        return bool(e["earnings"]) and date.fromisoformat(e["earnings"]) < today and e["fetched"] < today.isoformat()
    stale = [t for t in tickers if t not in cache or is_stale(cache[t])]
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
            rows.append({"ticker": t, "name": names.get(t, t), "date": e["earnings"], "days": n,
                         "est": bool(e.get("earnings_est"))})
    return sorted(rows, key=lambda r: (r["days"], r["ticker"]))
