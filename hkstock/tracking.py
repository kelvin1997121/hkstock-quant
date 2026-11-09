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



def _log(suffix: str):
    return DATA / f"signals_log{suffix}.json"


def load(suffix: str = "") -> dict:
    p = _log(suffix)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def record(date: str, regime: dict, short: list[str], long: list[str], suffix: str = "") -> None:
    log = load(suffix)
    LOG = _log(suffix)
    log[date] = {"regime_score": regime["score"], "label": regime["label"], "short": short, "long": long}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


def evaluate(prices, stock_cols: list[str], index: str, suffix: str = "", horizon: int = 5) -> dict:
    log = load(suffix)
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
                     "index_ret": round(float(fwd[index]) * 100, 2)})
    if not rows:
        return {"n": 0, "pending": len(log), "rows": []}
    df = pd.DataFrame(rows)
    ex = df["short_ret"] - df["universe_ret"]
    agree = np.sign(df["score"]) == np.sign(df["index_ret"])
    return {"n": len(df), "pending": len(log) - len(df), "horizon": horizon,
            "short_excess": round(float(ex.mean()), 2), "short_hit": round(float((ex > 0).mean() * 100), 1),
            "regime_hit": round(float(agree.mean() * 100), 1), "rows": rows[-20:][::-1],
            "series": [{"date": r["date"], "excess": round(r["short_ret"] - r["universe_ret"], 2),
                        "regime_ok": bool(np.sign(r["score"]) == np.sign(r["index_ret"]))} for r in rows[-120:]]}


def evaluate_claude(prices, stock_cols: list[str], index: str, market: str, horizon: int = 5) -> dict:
    """Score Claude's logged calls: 偏升 right if the index rose over the next 5 days, 偏跌 if it fell,
    震盪 if it moved less than 1% either way; picks scored against the universe average."""
    p = DATA / "claude_log.json"
    log = (json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}).get(market, {})
    c = prices.close
    idx = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(c.index)}
    hits, ex, series = [], [], []
    for d, e in sorted(log.items()):
        i = idx.get(d)
        if i is None or i + horizon >= len(c):
            continue
        fwd = c.iloc[i + horizon] / c.iloc[i] - 1
        r = float(fwd[index])
        ok = {"偏升": r > 0, "偏跌": r < 0, "震盪": abs(r) < 0.01}.get(e["call"], False)
        hits.append(ok)
        picks = [t for t in e.get("picks", []) if t in fwd and not np.isnan(fwd[t])]
        pex = float(fwd[picks].mean() - fwd[stock_cols].mean()) * 100 if picks else None
        if pex is not None:
            ex.append(pex)
        series.append({"date": d, "call": e["call"], "index_ret": round(r * 100, 2), "ok": bool(ok),
                       "excess": None if pex is None else round(pex, 2)})
    if not hits:
        return {"n": 0, "pending": len(log)}
    return {"n": len(hits), "pending": len(log) - len(hits), "call_hit": round(float(np.mean(hits)) * 100, 1),
            "pick_excess": round(float(np.mean(ex)), 2) if ex else None, "series": series[-120:]}
