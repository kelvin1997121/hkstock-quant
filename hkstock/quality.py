"""Sanity checks on freshly downloaded prices, run before anything is computed or saved.

If Yahoo returns something broken (no index price, half the stocks missing, an impossible
index jump, or older data than what is already published), that market is skipped this run:
the page keeps its last good data and the run log carries a warning. Nothing is logged to the
live record from a skipped run.
"""
from __future__ import annotations

import math


def check(prices, index: str, names: set[str], prev: dict | None) -> list[str]:
    problems = []
    c = prices.close
    if index not in c or c[index].dropna().empty:
        return [f"冇 {index} 指數價"]
    last = c[index].iloc[-1]
    if not (isinstance(last, (int, float)) and math.isfinite(last) and last > 0):
        problems.append(f"{index} 最新價唔正常:{last}")
    s = c[index].dropna()
    if len(s) >= 2:
        jump = s.iloc[-1] / s.iloc[-2] - 1
        if abs(jump) > 0.30:
            problems.append(f"{index} 單日變動 {jump * 100:+.0f}%,超出 ±30%")
    have = sum(1 for t in c.columns if t in names and math.isfinite(c[t].iloc[-1]))
    if prev:
        before = sum(1 for t in prev.get("stocks", {}) if t in names)
        if before and have < 0.8 * before:
            problems.append(f"有價股票得 {have} 隻,少過上次 {before} 隻嘅八成")
        asof = c.index[-1].strftime("%Y-%m-%d")
        if prev.get("asof") and asof < prev["asof"]:
            problems.append(f"數據日期 {asof} 舊過已發佈嘅 {prev['asof']}")
    elif have < 10:
        problems.append(f"有價股票得 {have} 隻")
    return problems
