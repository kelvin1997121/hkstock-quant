"""Live forward test: every after-close run logs the day's calls; later runs score them.

This is the only test that cannot be flattered by hindsight, because each call is
written down before its outcome exists (the git history of data/signals_log.json
shows when).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .data import DATA

LOG = DATA / "signals_log.json"


def load() -> dict:
    return json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {}


def record(date: str, regime: dict, short: list[str], long: list[str]) -> None:
    log = load()
    log[date] = {"regime_score": regime["score"], "label": regime["label"], "short": short, "long": long}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def evaluate(prices, stock_cols: list[str], horizon: int = 5) -> dict:
    log = load()
    c = prices.close
    idx = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(c.index)}
    rows = []
    for d, e in sorted(log.items()):
        i = idx.get(d)
        if i is None or i + horizon >= len(c):
            continue
        fwd = c.iloc[i + horizon] / c.iloc[i] - 1
        uni = fwd[stock_cols].mean()
        top = fwd[[t for t in e["short"] if t in fwd]].mean()
        rows.append({"date": d, "label": e["label"], "score": e["regime_score"],
                     "short_ret": round(float(top) * 100, 2), "universe_ret": round(float(uni) * 100, 2),
                     "hsi_ret": round(float(fwd["^HSI"]) * 100, 2)})
    if not rows:
        return {"n": 0, "pending": len(log), "rows": []}
    df = pd.DataFrame(rows)
    ex = df["short_ret"] - df["universe_ret"]
    agree = np.sign(df["score"]) == np.sign(df["hsi_ret"])
    return {"n": len(df), "pending": len(log) - len(df), "horizon": horizon,
            "short_excess": round(float(ex.mean()), 2), "short_hit": round(float((ex > 0).mean() * 100), 1),
            "regime_hit": round(float(agree.mean() * 100), 1), "rows": rows[-20:][::-1]}
