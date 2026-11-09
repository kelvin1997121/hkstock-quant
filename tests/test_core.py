"""Fast offline checks of the parts that decide what the page and alerts show.
No network: everything runs on synthetic data. Run with `python -m pytest -q`."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import pytest

from hkstock import ai_tools, alerts, data, events, indicators, levels, news, regime, tracking
from hkstock.data import Prices
from hkstock.universe import HK, US


# ---------------------------------------------------------------- helpers
def synthetic_prices(n_days=320, tickers=("AAA", "BBB", "CCC", "^IDX"), seed=1) -> Prices:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n_days)
    close = pd.DataFrame({t: 50 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n_days))) for t in tickers}, index=idx)
    spread = close * rng.uniform(0.005, 0.03, close.shape)
    high, low = close + spread, close - spread
    vol = pd.DataFrame(rng.integers(1e5, 1e6, close.shape), index=idx, columns=close.columns).astype(float)
    return Prices(close, high, low, vol)


# ---------------------------------------------------------------- price steps
@pytest.mark.parametrize("p,expected", [(0.2, 0.2), (7.986, 7.99), (15.03, 15.04), (55.07, 55.05),
                                        (150.06, 150.1), (450.3, 450.4), (1234.4, 1234.0)])
def test_hk_tick_nearest(p, expected):
    assert levels.tick_round(p, "hk") == pytest.approx(expected)


@pytest.mark.parametrize("p", [0.237, 7.985, 15.039, 99.99, 199.97, 455.55, 999.9, 4321.0])
def test_hk_tick_down_never_above_price(p):
    r = levels.tick_round(p, "hk", "down")
    assert r <= p + 1e-9
    assert p - r < 5  # never more than one step down


def test_us_tick_and_bad_input():
    assert levels.tick_round(12.345, "us", "down") == 12.34
    assert levels.tick_round(float("nan"), "us") is None
    assert levels.tick_round(-1, "hk") is None


# ---------------------------------------------------------------- buy/sell plans
def test_short_plan_levels_are_in_order():
    p = synthetic_prices()
    f = indicators.features(p)
    L = levels.frames(p, f)
    for t in ("AAA", "BBB", "CCC"):
        s = levels.plan_for(L, t, "us")["short"]
        close = p.close[t].iloc[-1]
        assert s["stop"] < s["entry_low"] <= s["entry_high"] <= close + 1e-9 < s["t1"] <= s["t2"]
        assert s["rr"] is None or s["rr"] > 0


def test_long_plan_levels_are_in_order():
    p = synthetic_prices()
    L = levels.frames(p, indicators.features(p))
    for t in ("AAA", "BBB", "CCC"):
        lp = levels.plan_for(L, t, "us")["long"]
        if lp["stop"] is not None:
            assert lp["stop"] < lp["zone_low"] <= lp["zone_high"]


# ---------------------------------------------------------------- holdings parsing (alerts + page share the format)
def test_ticker_normalising():
    assert alerts.norm("700") == "0700.HK"
    assert alerts.norm("0700.hk") == "0700.HK"
    assert alerts.norm("9988") == "9988.HK"
    assert alerts.norm("btc") == "BTC-USD"
    assert alerts.norm("brk.b") == "BRK-B"
    assert alerts.norm("nvda") == "NVDA"


def test_parse_holdings_and_margin():
    rows, margin = alerts.parse_holdings("AAPL 10 190\n# comment\n700 200 480\nbad line\n"
                                         "MARGIN AAPL,700 loan_usd=1000 maint=0.4 fx=7.8")
    assert [r["ticker"] for r in rows] == ["AAPL", "0700.HK"]
    assert rows[1] == {"ticker": "0700.HK", "qty": 200.0, "cost": 480.0}
    assert margin == {"tickers": ["AAPL", "0700.HK"], "loan_usd": 1000.0, "maint": 0.4, "fx": 7.8}


def test_margin_estimate_maths():
    m = {"qty": {"AAA": 100}, "loan_usd": 500.0, "maint": 0.5, "fx": 8.0}
    est = alerts.margin_estimate(m, {"AAA": {"close": 10.0}})  # value 1000 USD
    assert est["equity_hkd"] == pytest.approx(4000)       # (1000-500)*8
    assert est["liquidity_hkd"] == pytest.approx(0)       # 4000 - 0.5*1000*8
    assert est["leverage"] == pytest.approx(2.0)


# ---------------------------------------------------------------- Claude publish checks
@pytest.fixture
def ai_sandbox(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "docs" / "us.json").write_text(json.dumps({"asof": "2026-11-09", "stocks": {"NVDA": {}, "PM": {}}}))
    monkeypatch.setattr(ai_tools, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(ai_tools, "DATA", tmp_path / "data")
    return tmp_path


def test_publish_rejects_bad_input(ai_sandbox):
    with pytest.raises(SystemExit):
        ai_tools.publish("us", "大升", 50, [], "text")
    with pytest.raises(SystemExit):
        ai_tools.publish("us", "偏升", 150, [], "text")
    with pytest.raises(SystemExit):
        ai_tools.publish("us", "偏升", 50, ["FAKE"], "text")
    with pytest.raises(SystemExit):
        ai_tools.publish("us", "偏升", 50, [], "字" * (ai_tools.MAX_CHARS + 1))
    assert not (ai_sandbox / "docs" / "ai.json").exists()


def test_publish_keeps_first_call_of_the_day(ai_sandbox):
    ai_tools.publish("us", "偏升", 60, ["NVDA"], "first")
    ai_tools.publish("us", "偏跌", 40, ["PM"], "second")
    log = json.loads((ai_sandbox / "data" / "claude_log.json").read_text(encoding="utf-8"))
    assert log["us"]["2026-11-09"]["call"] == "偏升"          # no hindsight edits
    ai = json.loads((ai_sandbox / "docs" / "ai.json").read_text(encoding="utf-8"))
    assert ai["us"]["text"] == "second"                         # the page shows the latest text


# ---------------------------------------------------------------- regime
def test_regime_labels():
    assert regime.label(45)[0] == "強勢"
    assert regime.label(15)[0] == "偏好"
    assert regime.label(0)[0] == "震盪"
    assert regime.label(-20)[0] == "偏淡"
    assert regime.label(-80)[0] == "弱勢"


def test_regime_score_reweights_missing_evidence():
    comp = pd.DataFrame([{"trend": 1.0, "breadth": 1.0, "momentum": np.nan, "volatility": np.nan,
                          "vix": np.nan, "rates": np.nan, "dollar": np.nan, "credit": np.nan}])
    assert regime.score(comp, US).iloc[0] == pytest.approx(100)


# ---------------------------------------------------------------- sessions
def test_market_sessions():
    hkt = timezone(timedelta(hours=8))
    assert data.market_session(HK, datetime(2026, 11, 9, 8, 0, tzinfo=hkt)) == "開市前"
    assert data.market_session(HK, datetime(2026, 11, 9, 11, 0, tzinfo=hkt)) == "盤中"
    assert data.market_session(HK, datetime(2026, 11, 9, 17, 0, tzinfo=hkt)) == "收市後"
    assert data.market_session(HK, datetime(2026, 11, 8, 11, 0, tzinfo=hkt)) == "休市"   # Sunday
    assert data.market_session(US, datetime(2026, 11, 9, 23, 0, tzinfo=hkt)) == "盤中"   # 10:00 New York
    assert data.is_active(US, datetime(2026, 11, 10, 5, 40, tzinfo=hkt))                # 16:40 New York
    assert not data.is_active(US, datetime(2026, 11, 10, 9, 0, tzinfo=hkt))            # 20:00 New York


# ---------------------------------------------------------------- news tagging
def test_english_keywords_match_whole_words():
    assert "AI軟件及雲" in news.tag_themes("AI stocks rally", US.keywords)
    assert "AI軟件及雲" not in news.tag_themes("MAIN street retail", US.keywords)
    assert news.tone("Nvidia shares surge") == 1
    assert news.tone("恒指急跌") == -1


# ---------------------------------------------------------------- scoring of logged calls
def test_claude_calls_are_scored(tmp_path, monkeypatch):
    p = synthetic_prices(n_days=40)
    day = p.close.index[20].strftime("%Y-%m-%d")
    (tmp_path / "claude_log.json").write_text(json.dumps({"us": {day: {"call": "偏升", "confidence": 60, "picks": ["AAA"]}}}))
    monkeypatch.setattr(tracking, "DATA", tmp_path)
    res = tracking.evaluate_claude(p, ["AAA", "BBB", "CCC"], "^IDX", "us")
    up = p.close["^IDX"].iloc[25] > p.close["^IDX"].iloc[20]
    assert res["n"] == 1 and res["call_hit"] == (100.0 if up else 0.0)


# ---------------------------------------------------------------- earnings calendar
def test_upcoming_earnings_window():
    from datetime import date
    cal = {"A": {"earnings": "2026-11-12"}, "B": {"earnings": "2026-12-30"}, "C": {"earnings": None}}
    up = events.upcoming(cal, {"A": "甲"}, within=7, today=date(2026, 11, 10))
    assert up == [{"ticker": "A", "name": "甲", "date": "2026-11-12", "days": 2}]
