"""Simulated account: fills, stops, cash limits and the no-hindsight timing rule. Offline."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from hkstock import paper

UTC = timezone.utc
START = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)          # Mon 08:00 HKT
DAYS = pd.bdate_range("2026-10-26", "2026-11-13")


def bars(overrides=None, base=None):
    """Flat bars (open 100, high 102, low 98, close 100) unless overridden per ticker and date."""
    base = base or {"0700.HK": 100.0, "AAPL": 100.0, "^HSI": 25000.0, "^GSPC": 7000.0, "HKD=X": 7.8}
    frames = {}
    for t, p in base.items():
        rows = {d: (p, p * 1.02, p * 0.98, p) for d in DAYS}
        for d, ohlc in (overrides or {}).get(t, {}).items():
            rows[pd.Timestamp(d)] = ohlc
        frames[t] = pd.DataFrame([dict(zip(("Open", "High", "Low", "Close"), v)) for v in rows.values()],
                                 index=list(rows.keys()))
    return paper.Bars(frames)


def order(i, t, ticker, side="buy", shares=100, **kw):
    e = {"id": i, "t": t, "type": side, "side": side, "ticker": ticker, "shares": shares, "days": 1, "reason": "test"}
    if ticker.endswith(".HK"):
        e["lot"] = 100
    e.update(kw)
    return e


def run(entries, b, now):
    return paper.advance(paper.new_state(START), entries, b, now)


def test_fees_are_plausible():
    hk = paper.fee("hk", "buy", 1000, 20_000)
    assert 35 < hk < 50                   # about 0.2% on a HK$20k trade, mostly stamp duty and platform fee
    us = paper.fee("us", "buy", 10, 3_500)
    assert us == pytest.approx(1.99)
    assert paper.fee("us", "sell", 10, 3_500) > us


def test_market_buy_fills_at_next_open_with_costs():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=90.0)]
    final, live = run(e, bars({"0700.HK": {"2026-11-02": (101, 103, 99, 102)}}), datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    p = final["positions"]["0700.HK"]
    price = 101 * 1.001
    assert p["shares"] == 100
    assert p["cost"] == pytest.approx((price * 100 + paper.fee("hk", "buy", 100, price * 100)) / 100)
    assert final["cash"] == pytest.approx(paper.CAPITAL - p["cost"] * 100)
    assert final["orders"]["1"]["status"] == "filled"


def test_order_after_the_open_waits_for_the_next_session():
    e = [order(1, "2026-11-02T02:00:00Z", "0700.HK", stop=90.0)]           # 10:00 HKT, market already open
    b = bars({"0700.HK": {"2026-11-02": (90, 110, 89.5, 105), "2026-11-03": (104, 106, 103, 105)}})
    final, _ = run(e, b, datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert final["fills"][0]["date"] == "2026-11-03"
    assert final["fills"][0]["price"] == pytest.approx(104 * 1.001, abs=1e-3)


def test_us_order_fills_in_us_session_at_that_days_fx():
    e = [order(1, "2026-11-02T09:30:00Z", "AAPL", shares=10, stop=90.0)]     # 17:30 HKT
    b = bars({"HKD=X": {"2026-11-02": (7.77, 7.78, 7.76, 7.77)}})
    final, _ = run(e, b, datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    f = final["fills"][0]
    assert f["date"] == "2026-11-02" and f["fx"] == pytest.approx(7.77)
    cost = (100 * 1.0005 * 10 + paper.fee("us", "buy", 10, 100 * 1.0005 * 10)) * 7.77
    assert final["cash"] == pytest.approx(paper.CAPITAL - cost)


def test_stop_sells_whole_position_and_records_trade():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=95.0, target=120.0)]
    b = bars({"0700.HK": {"2026-11-03": (97, 98, 93, 94)}})
    final, _ = run(e, b, datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert "0700.HK" not in final["positions"]
    t = final["trades"][0]
    assert t["why"] == "止蝕" and t["exit"] == pytest.approx(95 * 0.999) and t["pnl_hkd"] < 0


def test_gap_below_stop_sells_at_the_open():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=95.0)]
    final, _ = run(e, bars({"0700.HK": {"2026-11-03": (90, 91, 88, 89)}}), datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert final["trades"][0]["exit"] == pytest.approx(90 * 0.999)


def test_stop_wins_when_stop_and_target_both_touch():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=95.0, target=105.0)]
    final, _ = run(e, bars({"0700.HK": {"2026-11-03": (100, 106, 94, 100)}}), datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert final["trades"][0]["why"] == "止蝕"


def test_limit_buy_expires_when_not_reached():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", limit=90.0, stop=85.0, days=2)]
    final, _ = run(e, bars(), datetime(2026, 11, 5, 0, 0, tzinfo=UTC))
    assert final["orders"]["1"]["status"] == "expired" and not final["fills"]


def test_buy_is_cut_to_whole_lots_the_cash_pays_for():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", shares=2000, stop=90.0)]   # HK$200k wanted, HK$100k cash
    final, _ = run(e, bars(), datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    sh = final["positions"]["0700.HK"]["shares"]
    assert sh % 100 == 0 and 900 <= sh < 1000 and final["cash"] >= 0
    assert "現金不足" in final["orders"]["1"]["note"]


def test_cancel_before_open_stops_the_fill():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=90.0),
         {"id": 2, "t": "2026-11-02T00:40:00Z", "type": "cancel", "order": 1, "reason": "test"}]
    final, _ = run(e, bars(), datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    assert final["orders"]["1"]["status"] == "cancelled" and not final["positions"]


def test_session_in_progress_is_live_only():
    e = [order(1, "2026-11-02T20:00:00Z", "0700.HK", stop=90.0)]            # placed Tue 04:00 HKT
    now = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)                           # Tue 11:00 HKT, HK in session
    final, live = run(e, bars(), now)
    assert not final["positions"] and "0700.HK" in live["positions"]
    assert final["through"] == "2026-11-02T14:30:00Z"                       # Monday's US session finished


def test_sell_order_and_missing_position():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=90.0),
         order(2, "2026-11-02T09:00:00Z", "0700.HK", side="sell"),
         order(3, "2026-11-02T09:00:00Z", "AAPL", side="sell", shares=5)]
    final, _ = run(e, bars({"0700.HK": {"2026-11-03": (110, 111, 109, 110)}}), datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert final["trades"][0]["exit"] == pytest.approx(110 * 0.999) and final["trades"][0]["pnl_hkd"] > 0
    assert final["orders"]["3"]["status"] == "rejected"
