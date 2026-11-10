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


@pytest.fixture
def book(tmp_path, monkeypatch):
    """An empty account and two stocks, all in a temp folder."""
    import json
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "data.json").write_text(json.dumps({"stocks": {"0700.HK": {"name": "騰訊", "close": 400.0}}}))
    (tmp_path / "docs" / "us.json").write_text(json.dumps({"stocks": {"AAPL": {"name": "蘋果", "close": 300.0},
        "MSFT": {"name": "微軟", "close": 400.0}, "NVDA": {"name": "輝達", "close": 200.0}}}))
    view = {"equity": 100000.0, "cash": 100000.0, "fx": 7.8, "positions": [], "orders": []}
    (tmp_path / "docs" / "paper.json").write_text(json.dumps(view))
    (tmp_path / "lots.json").write_text(json.dumps({"0700.HK": 100}))
    monkeypatch.setattr(paper, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(paper, "VIEW", tmp_path / "docs" / "paper.json")
    monkeypatch.setattr(paper, "ORDERS", tmp_path / "orders.json")
    monkeypatch.setattr(paper, "LOTS", tmp_path / "lots.json")
    return tmp_path


def test_order_checks(book):
    with pytest.raises(SystemExit, match="倍數"):
        paper.place_order("0700.HK", "buy", 150, stop=380, reason="測試每手")
    with pytest.raises(SystemExit, match="止蝕"):
        paper.place_order("0700.HK", "buy", 100, reason="冇止蝕測試")
    paper.place_order("0700.HK", "buy", 100, stop=380, reason="一手 4 萬,准許")          # 40% via the one-lot rule
    with pytest.raises(SystemExit, match="上限"):                                       # same-run order counts
        paper.place_order("0700.HK", "buy", 100, stop=380, reason="再買一手")
    paper.place_order("AAPL", "buy", 10, stop=290, reason="2.3 萬港元")
    with pytest.raises(SystemExit, match="上限"):
        paper.place_order("AAPL", "buy", 1, stop=290, reason="2.3 萬 + 2340 > 2.5 萬")
    paper.place_order("MSFT", "buy", 6, stop=380, reason="1.9 萬港元,仲有現金")
    with pytest.raises(SystemExit, match="現金唔夠"):                                   # 40k + 23.4k + 18.7k already out
        paper.place_order("NVDA", "buy", 12, stop=190, reason="1.9 萬港元,現金唔夠")
    with pytest.raises(SystemExit, match="可以沽"):
        paper.place_order("AAPL", "sell", 1, reason="冇持倉測試")


def test_cancel_an_order_from_the_same_run(book):
    e = paper.place_order("AAPL", "buy", 10, stop=290, reason="測試取消")
    paper.cancel(e["id"], "改變主意")
    with pytest.raises(SystemExit):
        paper.cancel(e["id"], "再取消")


# ---------------------------------------------------------------- learning
from hkstock import paper_learn  # noqa: E402


def test_closed_trade_carries_setup_context_and_r():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=95.0, setup="突破", ctx={"regime": "偏好", "short_score": 80})]
    b = bars({"0700.HK": {"2026-11-03": (97, 98, 93, 94), "2026-11-04": (96, 99, 95, 98)}})
    final, _ = run(e, b, datetime(2026, 11, 13, 0, 0, tzinfo=UTC))
    t = final["trades"][0]
    assert t["id"] == 1 and t["setup"] == "突破" and t["ctx"]["regime"] == "偏好"
    assert -1.25 < t["r"] < -1.0                       # a stop-out loses about 1R, a bit more after costs
    x = paper_learn.trade_extras(t, b)
    assert x["mae"] == pytest.approx(93 / t["entry"] - 1, abs=1e-4)
    assert x["after5"] is not None and x["held"] == 2


def _trades(n, r, setup="突破"):
    return [{"id": i, "ticker": "X", "market": "us", "setup": setup, "why": "止蝕" if r < 0 else "到目標",
             "pnl_hkd": 100 * r, "pnl_pct": 0.05 * r, "r": r + (0.1 if i % 2 else -0.1), "entry": 10, "exit": 10,
             "ctx": {"regime": "偏好"}} for i in range(n)]


def test_groups_stay_humble_on_small_samples():
    g = {(x["dim"], x["key"]): x for x in paper_learn.groups(_trades(4, 1.5))}
    assert g[("買入類型", "突破")]["verdict"] == "樣本唔夠"
    g = {(x["dim"], x["key"]): x for x in paper_learn.groups(_trades(12, 1.5) + _trades(12, -1.0, "回調"))}
    assert g[("買入類型", "突破")]["verdict"] == "有跡象賺"
    assert g[("買入類型", "回調")]["verdict"] == "有跡象蝕"


def test_lessons_need_evidence_before_adoption(book):
    import json
    view = json.loads((book / "docs" / "paper.json").read_text(encoding="utf-8"))
    view["trades"] = [{"id": i} for i in range(1, 7)]
    (book / "docs" / "paper.json").write_text(json.dumps(view))
    paper.add_lesson("業績前兩日唔好開新倉", [1, 2], None, None)
    with pytest.raises(SystemExit, match="最少"):
        paper.add_lesson(None, [3], "採用", "L1", "想採用")
    with pytest.raises(SystemExit, match="唔存在"):
        paper.add_lesson(None, [99], "採用", "L1", "想採用")
    paper.add_lesson(None, [3, 4, 5], "採用", "L1", "再多三單都係咁")
    L = paper_learn.fold_lessons(json.loads((book / "orders.json").read_text(encoding="utf-8"))["entries"])[0]
    assert L["id"] == "L1" and L["status"] == "採用" and L["evidence"] == [1, 2, 3, 4, 5]
    paper.add_review(2, "止蝕太近,開市波動已經掃走")
    with pytest.raises(SystemExit, match="檢討過"):
        paper.add_review(2, "再寫一次檢討應該唔得")
