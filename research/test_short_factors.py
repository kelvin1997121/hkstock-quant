"""Runs the pre-registered test in research/PREREG_2026-11-09_short_factors.md. Do not change
the definitions or rules here without a new pre-registration.

  python -m research.test_short_factors dev      # development period, picks finalists
  python -m research.test_short_factors locked   # locked period, finalists only, run once

Results are written to research/results_2026-11-09.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from hkstock import backtest, data, indicators, scoring
from hkstock.universe import HK, US

DEV_END = "2024-10-31"
LOCK_START = "2024-11-01"
COST = {"hk": 0.003, "us": 0.001}
NAMES = {"F1": "reversal_5d 短線反彈", "F2": "pullback_uptrend 升勢中回調", "F3": "near_52w_high 貼近52週高",
         "F4": "sector_momentum 板塊動量", "F5": "high_volume_drift 放量日漂移", "F6": "macd_hist MACD柱",
         "F7": "bollinger_reversion 布林帶回歸", "REF": "現有短炒分(參考)"}
OUT = Path(__file__).with_name("results_2026-11-09.json")


def candidates(prices, f, m, cols) -> dict[str, pd.DataFrame]:
    c, t = prices.close[cols], prices.turnover[cols]
    out = {"F1": -f["ret5"][cols]}
    rsi, ma200 = f["rsi14"][cols], f["ma200"][cols]
    out["F2"] = (100 - rsi).where(c > ma200, -1.0).where(ma200.notna() & rsi.notna())
    out["F3"] = f["drawdown"][cols]
    theme_of, r20 = m.theme_of(), f["ret20"][cols]
    sec = pd.DataFrame(np.nan, index=c.index, columns=cols)
    for th in {theme_of[x] for x in cols}:
        mem = [x for x in cols if theme_of[x] == th]
        avg = r20[mem].mean(axis=1)
        for x in mem:
            sec[x] = avg
    out["F4"] = sec
    base = t.shift(1).rolling(20, min_periods=10).mean()
    hv = t >= 2.5 * base
    out["F5"] = f["ret1"][cols].where(hv, 0.0).rolling(20, min_periods=20).sum().where(base.notna())
    ema = lambda x, n: x.ewm(span=n, adjust=False, min_periods=n).mean()
    macd = ema(c, 12) - ema(c, 26)
    out["F6"] = (macd - ema(macd, 9)) / c
    ma, sd = c.rolling(20, min_periods=20).mean(), c.rolling(20, min_periods=20).std()
    out["F7"] = -((c - (ma - 2 * sd)) / (4 * sd))
    out["REF"] = scoring.short_score(f, cols)
    return out


def evaluate(score: pd.DataFrame, fwd: pd.DataFrame, start: str | None, end: str | None, cost: float):
    s, r = score.loc[start:end], fwd.loc[start:end]
    res = backtest._ic_test(s, r, step=5)
    if res:
        res["top_excess_net"] = round(res["top_excess"] - cost * 100, 2)
        res["pass"] = bool(res["t"] >= 2.0 and res["top_excess_net"] > 0)
    return res


def load_market(m):
    prices, _ = data.load(m)
    cols = [x for x in prices.close.columns if x in m.names()]
    f = indicators.features(prices)
    c = prices.close[cols]
    return candidates(prices, f, m, cols), c.shift(-5) / c - 1


def main(phase: str) -> None:
    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if phase == "locked":
        if "locked" in results:
            raise SystemExit("locked period already run once; the pre-registration allows one run only")
        if not any(results.get("dev", {}).get(k, {}).get("finalists") for k in ("hk", "us")):
            raise SystemExit("no finalists from the development period: the locked period stays closed")
    for m in (HK, US):
        print(f"\n=== {m.name} ===")
        cand, fwd = load_market(m)
        if phase == "dev":
            rows = {}
            for k, sc in cand.items():
                rows[k] = evaluate(sc, fwd, None, DEV_END, COST[m.key])
                r = rows[k]
                print(f"{k:4} {NAMES[k]:28} " + (f"n={r['n']:3} IC={r['ic']:+.3f} t={r['t']:+.2f} "
                      f"頭10淨跑贏={r['top_excess_net']:+.2f}% 勝率={r['hit']}% {'PASS' if r['pass'] else '-'}"
                      f"  ({r['start']}~{r['end']})" if r else "not enough data"))
            passers = sorted([k for k, r in rows.items() if k != "REF" and r and r["pass"]],
                             key=lambda k: -rows[k]["t"])
            finalists = passers[:2]
            print("development passers:", passers or "none", "| finalists:", finalists or "none")
            results.setdefault("dev", {})[m.key] = {"rows": rows, "passers": passers, "finalists": finalists}
        else:
            finalists = results["dev"][m.key]["finalists"]
            rows = {}
            for k in finalists:
                rows[k] = evaluate(cand[k], fwd, LOCK_START, None, COST[m.key])
                r = rows[k]
                print(f"{k:4} {NAMES[k]:28} n={r['n']:3} IC={r['ic']:+.3f} t={r['t']:+.2f} "
                      f"頭10淨跑贏={r['top_excess_net']:+.2f}% {'PASS' if r['pass'] else 'FAIL'}  ({r['start']}~{r['end']})")
            if not finalists:
                print("no finalists for this market")
            results.setdefault("locked", {})[m.key] = rows
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwritten {OUT}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "dev")
