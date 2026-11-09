import numpy as np

from hkstock import quality
from tests.test_core import synthetic_prices

NAMES = {"AAA", "BBB", "CCC"}


def prev_with(n_stocks=3, asof="2025-01-01"):
    return {"asof": asof, "stocks": {t: {} for t in list(NAMES)[:n_stocks]}}


def test_clean_data_passes():
    p = synthetic_prices()
    assert quality.check(p, "^IDX", NAMES, prev_with()) == []


def test_missing_index_fails():
    p = synthetic_prices()
    p.close["^IDX"] = np.nan
    assert quality.check(p, "^IDX", NAMES, prev_with())


def test_impossible_index_jump_fails():
    p = synthetic_prices()
    p.close.iloc[-1, p.close.columns.get_loc("^IDX")] *= 1.5
    assert any("±30%" in x for x in quality.check(p, "^IDX", NAMES, prev_with()))


def test_many_stocks_missing_fails():
    p = synthetic_prices()
    p.close.iloc[-1, [p.close.columns.get_loc("AAA"), p.close.columns.get_loc("BBB")]] = np.nan
    assert any("八成" in x for x in quality.check(p, "^IDX", NAMES, prev_with()))


def test_older_than_published_fails():
    p = synthetic_prices()
    assert any("舊過" in x for x in quality.check(p, "^IDX", NAMES, prev_with(asof="2099-01-01")))
