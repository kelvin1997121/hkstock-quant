"""Forward record of trade plans: recording once, the fixed trade rules, and the one-off verdict. Offline."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from hkstock import paper, plans

UTC = timezone.utc
DAYS = pd.bdate_range("2026-11-02", "2026-12-04")
D0 = date(2026, 11, 2)
LAST = date(2026, 12, 4)


def bars(rows: dict | None = None, ticker: str = "0700.HK"):
    """Flat bars (open 100, high 101, low 99.5, close 100) unless a date is overridden."""
    def frame(over):
        data = {d: (100.0, 101.0, 99.5, 100.0) for d in DAYS}
        for d, ohlc in over.items():
            data[pd.Timestamp(d)] = ohlc
        f = pd.DataFrame([dict(zip(("Open", "High", "Low", "Close"), v)) for v in data.values()], index=list(data))
        f["Dividends"], f["Stock Splits"] = 0.0, 0.0
        return f
    return paper.Bars({ticker: frame(rows or {}), "^HSI": frame({}), "^GSPC": frame({})})


PLAN = {"t": "0700.HK", "eh": 99.0, "stop": 95.0, "t1": 105.0}


def run(rows=None, plan=PLAN, last=LAST):
    return plans.simulate(plan, bars(rows), "hk", D0, last)


def test_touch_is_not_a_fill_and_the_order_lapses_after_three_sessions():
    o = run({"2026-11-03": (100, 101, 99.0, 100)})                     # low exactly at the limit
    assert o["status"] == "unfilled" and o["final"]
    assert run(last=date(2026, 11, 4))["status"] == "waiting"          # only two sessions seen so far
    late = run({"2026-11-06": (100, 101, 98, 100)})                    # the 4th session: too late
    assert late["status"] == "unfilled"


def test_fill_then_time_exit_with_costs_and_index():
    o = run({"2026-11-04": (100, 101, 98.5, 100)})                     # trades through 99 on the 2nd session
    assert o["status"] == "done" and o["in"] == "2026-11-04" and o["entry"] == 99.0 and not o["at_open"]
    assert o["why"] == "到期" and o["out"] == "2026-11-17"              # the 10th session counting the fill day
    exit_px = 100 * 0.999
    sh = 20_000 / 99.0
    fees = paper.fee("hk", "buy", sh, 20_000) + paper.fee("hk", "sell", sh, sh * exit_px)
    assert o["net"] == pytest.approx(((exit_px - 99.0) * sh - fees) / 20_000, abs=1e-5)
    assert o["idx"] == pytest.approx(0.0) and o["base"]["net"] < 0     # flat index; buying at the open only pays fees


def test_opening_below_the_stop_voids_the_plan_and_gap_fills_at_the_open():
    assert run({"2026-11-03": (94, 96, 93, 95)})["status"] == "void"
    o = run({"2026-11-03": (97, 98, 96.5, 97.5)})
    assert o["entry"] == 97.0 and o["at_open"]


def test_stop_and_target_rules():
    stopped = run({"2026-11-03": (98, 99, 97, 98), "2026-11-04": (97, 106, 94, 100)})    # both touched: stop
    assert stopped["why"] == "止蝕" and stopped["exit"] == pytest.approx(95 * 0.999, abs=1e-4)
    hit = run({"2026-11-03": (98, 99, 97, 98), "2026-11-05": (100, 106, 99, 104)})
    assert hit["why"] == "到目標" and hit["exit"] == 105.0 and hit["net"] > 0
    # a limit filled during the day cannot claim that day's high as reaching the target
    same_day = run({"2026-11-03": (104, 106, 98, 100)})
    assert same_day["entry"] == 99.0 and same_day["why"] == "到期"
    at_open = run({"2026-11-03": (98, 106, 97, 100)})                  # filled at the open: the high came after
    assert at_open["why"] == "到目標"


def test_dividend_counts_and_a_split_drops_the_record():
    b = bars({"2026-11-03": (98, 99, 97, 98)})
    b.frames["0700.HK"].loc[pd.Timestamp("2026-11-05"), "Dividends"] = 1.0
    with_div = plans.simulate(PLAN, b, "hk", D0, LAST)
    assert with_div["net"] == pytest.approx(run({"2026-11-03": (98, 99, 97, 98)})["net"] + 1.0 / 98.0, abs=1e-4)
    b.frames["0700.HK"].loc[pd.Timestamp("2026-11-06"), "Stock Splits"] = 2.0
    assert plans.simulate(PLAN, b, "hk", D0, LAST)["status"] == "split"


def test_plans_are_recorded_once_and_only_after_the_close():
    pick = {"ticker": "0700.HK", "name": "騰訊", "theme": "科網", "score": 80.0, "close": 100.0,
            "plan": {"short": {"entry_low": 97.0, "entry_high": 99.0, "stop": 95.0, "t1": 105.0}}}
    bad = {**pick, "ticker": "0005.HK", "plan": {"short": {"entry_high": 99.0, "stop": 99.5, "t1": 105.0}}}
    d = {"session": "收市後", "asof": "2026-11-02", "short": [pick, bad], "tech": [pick], "regime": {"label": "震盪"}}
    log, now = {}, datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    assert not plans.record(log, "hk", {**d, "session": "盤中"}, D0, now)
    assert not plans.record(log, "hk", d, date(2026, 11, 3), now)       # yesterday's data, read too late
    assert plans.record(log, "hk", d, D0, now)
    recs = log["hk"]["2026-11-02"]["plans"]
    assert [r["t"] for r in recs] == ["0700.HK", "0005.HK"]             # the duplicate in `tech` counts once
    assert recs[1]["out"]["status"] == "invalid"
    assert not plans.record(log, "hk", {**d, "short": []}, D0, now)     # the first record stands
    assert len(log["hk"]["2026-11-02"]["plans"]) == 2


def _done(net, idx=0.0, base=0.0):
    return {"status": "done", "final": True, "why": "到期", "net": net, "idx": idx, "base": {"net": base}}


def test_summary_counts_a_signal_day_once_and_the_verdict_waits_for_the_sample():
    days = {f"2026-11-{i:02d}": {"plans": [{"out": _done(0.02)}, {"out": _done(0.04)}, {"out": {"status": "unfilled", "final": True}}]}
            for i in range(1, 11)}
    s = plans.summarise(days)
    assert s["signal_days"] == 10 and s["done"] == 20 and s["unfilled"] == 10
    assert s["day_mean"] == pytest.approx(0.03) and s["fill_rate"] == pytest.approx(0.667, abs=1e-3)
    assert plans.verdict(s) is None                                      # 10 days: far too few
    wins = {f"d{i:02d}": {"plans": [{"out": _done(0.01 + 0.001 * (i % 5), idx=-0.005)} for _ in range(5)]} for i in range(30)}
    v = plans.verdict(plans.summarise(wins))
    assert v["pass"] and v["signal_days"] == 30 and v["done"] == 150
    lag = {f"d{i:02d}": {"plans": [{"out": _done(0.01 + 0.001 * (i % 5), idx=0.02)} for _ in range(5)]} for i in range(30)}
    assert plans.verdict(plans.summarise(lag))["pass"] is False          # made money, but less than the index


def test_scoring_runs_once_per_finished_session_and_the_verdict_is_frozen():
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)                      # Fri 20:00 HKT, the session is finished
    log = {"hk": {"2026-11-02": {"plans": [dict(PLAN, name="騰訊")]}}}
    calls = []

    def download(tickers, start):
        calls.append(tickers)
        return bars({"2026-11-03": (98, 99, 97, 98)})

    assert plans.score(log, "hk", now, download)
    assert log["hk"]["2026-11-02"]["plans"][0]["out"]["status"] == "done" and log["evaluated"]["hk"] == "2026-11-20"
    assert not plans.score(log, "hk", now, download) and len(calls) == 1     # nothing new to follow
    view = plans.build_view(log, now)
    assert view["markets"]["hk"]["done"] == 1 and view["markets"]["hk"]["verdict"] is None
    log["verdict"]["hk"] = {"taken": "x", "pass": False}
    assert plans.build_view(log, now)["markets"]["hk"]["verdict"]["taken"] == "x"      # never recomputed
