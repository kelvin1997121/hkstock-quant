import json

from hkstock import tracking
from tests.test_core import synthetic_prices


def test_system_record_series(tmp_path, monkeypatch):
    p = synthetic_prices(n_days=60)
    days = [p.close.index[i].strftime("%Y-%m-%d") for i in (10, 20, 30, 58)]  # the last has no 5-day result yet
    log = {d: {"regime_score": 20, "label": "偏好", "short": ["AAA"], "long": []} for d in days}
    (tmp_path / "signals_log_us.json").write_text(json.dumps(log))
    monkeypatch.setattr(tracking, "DATA", tmp_path)
    res = tracking.evaluate(p, ["AAA", "BBB", "CCC"], "^IDX", "_us")
    assert res["n"] == 3 and res["pending"] == 1
    assert [x["date"] for x in res["series"]] == days[:3]          # oldest first, for the chart
    c = p.close
    i = 10
    fwd = c.iloc[i + 5] / c.iloc[i] - 1
    expect = round((fwd["AAA"] - fwd[["AAA", "BBB", "CCC"]].mean()) * 100, 2)
    assert abs(res["series"][0]["excess"] - expect) < 0.02
    assert res["series"][0]["regime_ok"] == bool(fwd["^IDX"] > 0)


def test_claude_record_series(tmp_path, monkeypatch):
    p = synthetic_prices(n_days=40)
    d = p.close.index[20].strftime("%Y-%m-%d")
    (tmp_path / "claude_log.json").write_text(json.dumps({"us": {d: {"call": "偏跌", "confidence": 50, "picks": []}}}))
    monkeypatch.setattr(tracking, "DATA", tmp_path)
    res = tracking.evaluate_claude(p, ["AAA", "BBB", "CCC"], "^IDX", "us")
    s = res["series"][0]
    assert s["date"] == d and s["call"] == "偏跌" and s["excess"] is None
    assert s["ok"] == (s["index_ret"] < 0)
