"""Exchange holidays (weekdays with no trading), 2026-2027.

Generated on 2026-11-10 from the `exchange_calendars` package (XHKG, XNYS) and checked against
the days the Hang Seng Index and S&P 500 actually traded from 2026-01-01 to 2026-11-09: every
2026 holiday so far matched. Typhoon / black-rainstorm closures in Hong Kong cannot be listed
in advance. Extend the lists before 2028 (a test fails when the year is not covered).
"""
from __future__ import annotations

from datetime import date, timedelta

COVERED_UNTIL = date(2027, 12, 31)

HOLIDAYS = {
    "hk": {
        "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19", "2026-04-03", "2026-04-06", "2026-04-07",
        "2026-05-01", "2026-05-25", "2026-06-19", "2026-07-01", "2026-10-01", "2026-10-19", "2026-12-25",
        "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-26", "2027-03-29", "2027-04-05", "2027-05-13",
        "2027-06-09", "2027-07-01", "2027-09-16", "2027-10-01", "2027-10-08", "2027-12-27",
    },
    "us": {
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
        "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
        "2027-09-06", "2027-11-25", "2027-12-24",
    },
}

# half-day sessions (HK closes 12:00, US closes 13:00 local time)
EARLY_CLOSE = {
    "hk": {"2026-02-16", "2026-12-24", "2026-12-31", "2027-02-05", "2027-12-24", "2027-12-31"},
    "us": {"2026-11-27", "2026-12-24", "2027-11-26"},
}


def is_holiday(market: str, day: date) -> bool:
    return day.isoformat() in HOLIDAYS[market]


def is_early_close(market: str, day: date) -> bool:
    return day.isoformat() in EARLY_CLOSE[market]


def upcoming(market: str, today: date, days: int = 60) -> list[str]:
    end = today + timedelta(days=days)
    return sorted(d for d in HOLIDAYS[market] if today.isoformat() <= d <= end.isoformat())
