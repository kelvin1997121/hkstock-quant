"""Exchange holidays (weekdays with no trading), 2026-2028.

Generated from the `exchange_calendars` package (XHKG, XNYS): 2026-2027 on 2026-11-10, checked against
the days the Hang Seng Index and S&P 500 actually traded from 2026-01-01 to 2026-11-09 (every 2026
holiday so far matched); 2028 on 2026-11-12, before Hong Kong gazetted its 2028 holidays, so the HK
2028 dates are the package's rule-based ones. Typhoon / black-rainstorm closures cannot be listed
in advance.

After COVERED_UNTIL the same package is asked directly (it computes lunar and moved holidays), so the
system keeps working when nobody extends the lists; if the package is missing, only weekends count
and a warning is printed once. The simulated account also treats a day with no index bar as closed.
"""
from __future__ import annotations

from datetime import date, timedelta

COVERED_UNTIL = date(2028, 12, 31)
_XC = {"hk": "XHKG", "us": "XNYS"}
_cache: dict = {}

HOLIDAYS = {
    "hk": {
        "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-04-03", "2026-04-06", "2026-04-07",
        "2026-05-01", "2026-05-25", "2026-06-19", "2026-07-01", "2026-10-01", "2026-10-19", "2026-12-25",
        "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-03-29", "2027-04-05", "2027-05-13",
        "2027-06-09", "2027-07-01", "2027-09-16", "2027-10-01", "2027-10-08", "2027-12-27",
        "2028-01-26", "2028-01-27", "2028-01-28", "2028-04-04", "2028-04-14", "2028-04-17", "2028-05-01",
        "2028-05-02", "2028-05-29", "2028-10-02", "2028-10-04", "2028-10-26", "2028-12-25", "2028-12-26",
    },
    "us": {
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
        "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
        "2027-09-06", "2027-11-25", "2027-12-24",
        "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19", "2028-07-04", "2028-09-04",
        "2028-11-23", "2028-12-25",
    },
}

# half-day sessions (HK closes 12:00, US closes 13:00 local time)
EARLY_CLOSE = {
    "hk": {"2026-02-16", "2026-12-24", "2026-12-31", "2027-02-05", "2027-12-24", "2027-12-31", "2028-01-25"},
    "us": {"2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24"},
}


def _computed(market: str, year: int) -> tuple[set, set]:
    """(holidays, early closes) for a year past the lists, from exchange_calendars; empty if unavailable."""
    key = (market, year)
    if key not in _cache:
        try:
            import exchange_calendars as xc
            import pandas as pd
            c = xc.get_calendar(_XC[market], start=f"{year - 1}-12-01", end=f"{year + 1}-01-31")
            sessions = set(c.sessions)
            days = pd.bdate_range(f"{year}-01-01", f"{year}-12-31")
            _cache[key] = ({d.date().isoformat() for d in days if d not in sessions},
                           {d.date().isoformat() for d in c.early_closes if d.year == year})
        except Exception as e:     # package missing or too old for that year
            print(f"::warning::{market} holidays for {year} unknown ({e!r}); only weekends count. "
                  "Add the year to hkstock/market_days.py.")
            _cache[key] = (set(), set())
    return _cache[key]


def is_holiday(market: str, day: date) -> bool:
    if day > COVERED_UNTIL:
        return day.isoformat() in _computed(market, day.year)[0]
    return day.isoformat() in HOLIDAYS[market]


def is_early_close(market: str, day: date) -> bool:
    if day > COVERED_UNTIL:
        return day.isoformat() in _computed(market, day.year)[1]
    return day.isoformat() in EARLY_CLOSE[market]


def upcoming(market: str, today: date, days: int = 60) -> list[str]:
    end = today + timedelta(days=days)
    known = set(HOLIDAYS[market])
    for y in range(COVERED_UNTIL.year + 1, end.year + 1):
        known |= _computed(market, y)[0]
    return sorted(d for d in known if today.isoformat() <= d <= end.isoformat())
