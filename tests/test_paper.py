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


def test_limit_needs_the_price_to_trade_through_not_just_touch():
    now = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", limit=98.0, stop=90.0)]
    touch, _ = run(e, bars({"0700.HK": {"2026-11-02": (100, 101, 98.0, 99)}}), now)   # low exactly at the limit
    assert touch["orders"]["1"]["status"] == "expired" and not touch["fills"]
    through, _ = run(e, bars({"0700.HK": {"2026-11-02": (100, 101, 97.9, 99)}}), now)
    assert through["fills"][0]["price"] == pytest.approx(98.0)
    gap, _ = run(e, bars({"0700.HK": {"2026-11-02": (96, 99, 95, 97)}}), now)         # opened below the limit
    assert gap["fills"][0]["price"] == pytest.approx(96.0)


def test_target_needs_a_high_above_it():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=90.0, target=105.0)]
    touch, _ = run(e, bars({"0700.HK": {"2026-11-03": (101, 105.0, 100, 104)}}), datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert "0700.HK" in touch["positions"]
    hit, _ = run(e, bars({"0700.HK": {"2026-11-03": (101, 105.5, 100, 104)}}), datetime(2026, 11, 4, 0, 0, tzinfo=UTC))
    assert hit["trades"][0]["why"] == "到目標" and hit["trades"][0]["exit"] == pytest.approx(105.0)


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
    monkeypatch.setattr(paper, "_correlations", lambda t, others: {})          # offline
    return tmp_path


def test_order_checks(book):
    with pytest.raises(SystemExit, match="倍數"):
        paper.place_order("0700.HK", "buy", 150, stop=390, reason="測試每手")
    with pytest.raises(SystemExit, match="止蝕"):
        paper.place_order("0700.HK", "buy", 100, reason="冇止蝕測試")
    with pytest.raises(SystemExit, match="風險太大"):                                   # 100 x 20 = 2% of equity
        paper.place_order("0700.HK", "buy", 100, stop=380, reason="止蝕太闊")
    e = paper.place_order("0700.HK", "buy", 100, stop=390, reason="一手 4 萬,准許")      # 40% via the one-lot rule
    assert e["check"]["risk_hkd"] == 1000 and e["check"]["size_pct"] == 0.4
    with pytest.raises(SystemExit, match="上限"):                                       # same-run order counts
        paper.place_order("0700.HK", "buy", 100, stop=390, reason="再買一手")
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
    # the exit day's low of 93 came after the stop sold at 95: only the exit price counts that day
    assert x["mae"] == pytest.approx(95 * 0.999 / t["entry"] - 1, abs=1e-4)
    assert x["mfe"] == pytest.approx(102 / t["entry"] - 1, abs=1e-4)   # bought at the open: whole first day counts
    assert x["after5"] is not None and x["held"] == 2


def test_mfe_ignores_the_entry_days_high_for_a_limit_fill():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", limit=99.0, stop=90.0),
         order(2, "2026-11-03T09:00:00Z", "0700.HK", side="sell")]
    b = bars({"0700.HK": {"2026-11-02": (100, 110, 97, 100), "2026-11-04": (101, 103, 100, 102)}})
    final, _ = run(e, b, datetime(2026, 11, 13, 0, 0, tzinfo=UTC))
    t = final["trades"][0]
    assert t["entry_at_open"] is False
    x = paper_learn.trade_extras(t, b)
    # the 110 high may have come before the 99 limit fill; the first day counts only at its close (100)
    assert x["mfe"] == pytest.approx(max(100, 102, 98 * 1.02, 101 * 0.9995) / t["entry"] - 1, abs=1e-4)


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


def test_earnings_group_keeps_today_and_unknown_apart():
    key = paper_learn.DIMENSIONS["業績"]
    label = lambda d: key({"ctx": {"earn_days": d}})
    assert label(0) == "7日內有業績" and label(7) == "7日內有業績"
    assert label(30) == "7日後先有業績"
    assert label(None) == "無數據" and label(-14) == "無數據"


def _with_actions(b, ticker, day, div=0.0, split=0.0):
    f = b.frames[ticker]
    f["Dividends"], f["Stock Splits"] = 0.0, 0.0
    f.loc[pd.Timestamp(day), ["Dividends", "Stock Splits"]] = [div, split]
    return b


def test_dividend_is_credited_to_holders_and_counts_in_the_trade():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=90.0),
         order(2, "2026-11-04T09:00:00Z", "0700.HK", side="sell")]               # sold at the 11-05 open
    b = _with_actions(bars(), "0700.HK", "2026-11-04", div=2.0)
    final, _ = run(e, b, datetime(2026, 11, 6, 0, 0, tzinfo=UTC))
    d = final["dividends"][0]
    assert d["kind"] == "div" and d["net_hkd"] == pytest.approx(200.0)
    t = final["trades"][0]
    assert t["div_hkd"] == pytest.approx(200.0)
    no_div, _ = run(e, bars(), datetime(2026, 11, 6, 0, 0, tzinfo=UTC))
    assert t["pnl_hkd"] == pytest.approx(no_div["trades"][0]["pnl_hkd"] + 200.0)
    assert final["cash"] == pytest.approx(no_div["cash"] + 200.0)


def test_no_dividend_when_bought_on_the_ex_date_and_us_tax_withheld():
    hk = [order(1, "2026-11-03T09:00:00Z", "0700.HK", stop=90.0)]               # first fills at the 11-04 open
    final, _ = run(hk, _with_actions(bars(), "0700.HK", "2026-11-04", div=2.0), datetime(2026, 11, 5, 0, 0, tzinfo=UTC))
    assert not final["dividends"]
    us = [order(1, "2026-11-02T09:30:00Z", "AAPL", shares=10, stop=90.0)]
    final, _ = run(us, _with_actions(bars(), "AAPL", "2026-11-04", div=1.0), datetime(2026, 11, 6, 0, 0, tzinfo=UTC))
    assert final["dividends"][0]["net_hkd"] == pytest.approx(10 * 1.0 * 0.7 * 7.8)


def test_split_rescales_the_position_and_cancels_waiting_orders():
    e = [order(1, "2026-11-02T00:30:00Z", "0700.HK", stop=90.0, target=120.0),
         order(2, "2026-11-03T09:00:00Z", "0700.HK", limit=95.0, stop=90.0, days=3)]
    after = {d: (50, 51, 49, 50) for d in ("2026-11-04", "2026-11-05", "2026-11-06")}   # prices after the split
    b = _with_actions(bars({"0700.HK": after}), "0700.HK", "2026-11-04", split=2.0)
    final, _ = run(e, b, datetime(2026, 11, 5, 0, 0, tzinfo=UTC))
    p = final["positions"]["0700.HK"]
    assert p["shares"] == 200 and p["stop"] == pytest.approx(45.0) and p["target"] == pytest.approx(60.0)
    assert final["orders"]["2"]["status"] == "cancelled"


def test_next_ex_dividend_estimate():
    from datetime import date
    today = date(2026, 11, 12)
    hist = {date(2025, 12, 4): 0.156, date(2026, 5, 4): 0.194, date(2026, 10, 9): 0.177}
    assert paper.next_ex_dividend(hist, "2026-10-09", today) == {"date": "2026-12-03", "est": True, "per_share": 0.156}
    assert paper.next_ex_dividend(hist, "2026-11-20", today)["est"] is False
    assert paper.next_ex_dividend({date(2026, 3, 1): 1.0}, None, today) is None


def test_theme_correlation_and_cash_limits(book, monkeypatch):
    import json
    stocks = {t: {"name": t, "close": 100.0, "theme": th} for t, th in
              (("AMD", "AI晶片"), ("NVDA", "AI晶片"), ("AVGO", "AI晶片"), ("KO", "消費"), ("PEP", "消費"), ("XOM", "能源"))}
    (book / "docs" / "us.json").write_text(json.dumps({"stocks": stocks}))
    paper.place_order("AMD", "buy", 25, stop=95, reason="AI 晶片第一隻")                 # 19.5k
    paper.place_order("NVDA", "buy", 25, stop=95, reason="同板塊第二隻")                 # 39k in the theme
    with pytest.raises(SystemExit, match="板塊太集中"):
        paper.place_order("AVGO", "buy", 10, stop=95, reason="第三隻超過 40%")
    monkeypatch.setattr(paper, "_correlations", lambda t, others: {"AMD": 0.8, "NVDA": 0.7})
    with pytest.raises(SystemExit, match="走勢太相似"):
        paper.place_order("XOM", "buy", 10, stop=95, reason="唔同板塊但走勢一樣")
    monkeypatch.setattr(paper, "_correlations", lambda t, others: {})
    paper.place_order("KO", "buy", 25, stop=95, reason="消費 19.5k")
    paper.place_order("XOM", "buy", 25, stop=95, reason="能源 19.5k")
    with pytest.raises(SystemExit, match="10% 現金"):                                  # 78k out, 15.6k more leaves 6k
        paper.place_order("PEP", "buy", 20, stop=98, reason="用到剩唔夠一成現金")


def test_exit_reason_cannot_claim_a_locked_profit_below_cost(book):
    import json
    view = json.loads((book / "docs" / "paper.json").read_text())
    view["positions"] = [{"ticker": "AAPL", "shares": 2, "cost": 1061.0, "last": 1087.6}]
    (book / "docs" / "paper.json").write_text(json.dumps(view))
    with pytest.raises(SystemExit, match="低過成本"):
        paper.set_exit("AAPL", stop=1055.0, reason="上移止蝕鎖定盈利")
    e = paper.set_exit("AAPL", stop=1055.0, reason="上移止蝕減少風險")
    assert e["check"]["stop_vs_cost"] < 0 and e["check"]["pnl_at_stop_hkd"] < 0
    assert paper.set_exit("AAPL", stop=1062.0, reason="上移止蝕保本")["check"]["stop_vs_cost"] > 0
