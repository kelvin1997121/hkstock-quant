from datetime import date, datetime, timedelta, timezone

from hkstock import data, market_days
from hkstock.universe import HK, US

HKT = timezone(timedelta(hours=8))


def test_known_holidays():
    assert market_days.is_holiday("hk", date(2026, 2, 17))      # Lunar New Year
    assert market_days.is_holiday("hk", date(2026, 4, 7))       # Ching Ming (moved)
    assert market_days.is_holiday("us", date(2026, 11, 26))     # Thanksgiving
    assert market_days.is_holiday("us", date(2026, 7, 3))       # Independence Day observed
    assert not market_days.is_holiday("hk", date(2026, 11, 10))
    assert not market_days.is_holiday("us", date(2026, 11, 10))


def test_holiday_session_is_closed_and_inactive():
    # 25 Dec 2026, 11:00 HKT: HK closed for Christmas
    assert data.market_session(HK, datetime(2026, 12, 25, 11, 0, tzinfo=HKT)) == "休市"
    assert not data.is_active(HK, datetime(2026, 12, 25, 11, 0, tzinfo=HKT))
    # Thanksgiving 26 Nov 2026, 11:00 New York = 00:00 HKT 27 Nov
    assert data.market_session(US, datetime(2026, 11, 27, 0, 0, tzinfo=HKT)) == "休市"
    assert not data.is_active(US, datetime(2026, 11, 27, 0, 0, tzinfo=HKT))
    # an ordinary weekday is still open
    assert data.market_session(HK, datetime(2026, 11, 10, 11, 0, tzinfo=HKT)) == "盤中"


def test_holidays_are_weekdays():
    for market in ("hk", "us"):
        for d in market_days.HOLIDAYS[market]:
            assert date.fromisoformat(d).weekday() < 5


def test_years_past_the_lists_are_computed():
    # past COVERED_UNTIL the exchange_calendars package answers, so the system never silently treats
    # Christmas or Lunar New Year as a trading day (and no test time-bomb stops the updates)
    assert market_days.is_holiday("us", date(2029, 12, 25))
    assert market_days.is_holiday("hk", date(2029, 12, 25))
    assert not market_days.is_holiday("us", date(2029, 12, 27))
    assert market_days.is_holiday("us", date(2028, 11, 23))       # listed year: Thanksgiving 2028


def test_upcoming_window():
    assert market_days.upcoming("us", date(2026, 11, 10), 60) == ["2026-11-26", "2026-12-25", "2027-01-01"]
