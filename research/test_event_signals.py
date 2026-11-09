"""Runs research/PREREG_2026-11-09b_event_signals.md exactly. Do not change definitions here
without a new pre-registration.

  python -m research.test_event_signals fetch    # download earnings / analyst records (cached)
  python -m research.test_event_signals dev      # development period 2014-01-01..2021-10-31
  python -m research.test_event_signals locked   # locked period, development passers only, once
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from hkstock import data
from hkstock.universe import HK, US

warnings.filterwarnings("ignore")
HERE = Path(__file__).parent
CACHE = HERE / "cache"
OUT = HERE / "results_2026-11-09b.json"
DEV = ("2014-01-01", "2021-10-31")
LOCK = ("2021-11-01", None)
COST = {"hk": 0.003, "us": 0.001}
H, STEP, OVERLAP = 20, 5, 2.0  # horizon, sampling step, t divisor (sqrt of 4x overlap)
LOCAL_TZ = {"hk": "Asia/Hong_Kong", "us": "America/New_York"}


# ---------------------------------------------------------------- data
def fetch() -> None:
    import yfinance as yf
    yf.set_tz_cache_location(tempfile.mkdtemp())
    CACHE.mkdir(parents=True, exist_ok=True)
    for m in (HK, US):
        earn, acts = {}, {}
        for i, t in enumerate(m.names()):
            tk = yf.Ticker(t)
            try:
                e = tk.get_earnings_dates(limit=60)
                if e is not None and len(e):
                    earn[t] = [[ts.isoformat(), None if pd.isna(s) else float(s)]
                               for ts, s in zip(e.index, e["Surprise(%)"])]
            except Exception as ex:
                print(f"  {t} earnings: {str(ex)[:60]}")
            if m.key == "us":
                try:
                    u = tk.upgrades_downgrades
                    if u is not None and len(u):
                        acts[t] = [[ts.isoformat(), a, p] for ts, a, p in
                                   zip(u.index, u["Action"].fillna(""), u["priceTargetAction"].fillna(""))]
                except Exception as ex:
                    print(f"  {t} analyst: {str(ex)[:60]}")
            if i % 20 == 0:
                print(f"  {m.key} {i}/{len(m.names())}")
        (CACHE / f"earnings_{m.key}.json").write_text(json.dumps(earn), encoding="utf-8")
        if m.key == "us":
            (CACHE / "analyst_us.json").write_text(json.dumps(acts), encoding="utf-8")
        print(f"{m.key}: earnings for {len(earn)} stocks, analyst actions for {len(acts)}")
    prices = {}
    for m in (HK, US):
        tick = list(m.names())
        p = data._split(data._download(tick, "max"), tick)
        p.close.loc["2013-06-01":].to_pickle(CACHE / f"close_{m.key}.pkl")
        prices[m.key] = p.close.shape
    print("prices", prices)


def usable_index(dates: pd.DatetimeIndex, local_day: pd.Timestamp) -> int:
    """Position of the first trading day strictly after local_day."""
    return int(dates.searchsorted(local_day, side="right"))


def local_date(iso: str, market: str) -> pd.Timestamp:
    ts = pd.Timestamp(iso)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return pd.Timestamp(ts.tz_convert(LOCAL_TZ[market]).date())


# ---------------------------------------------------------------- signals
def signal_e1(close: pd.DataFrame, earn: dict, market: str) -> pd.DataFrame:
    dates = close.index
    sig = pd.DataFrame(np.nan, index=dates, columns=close.columns)
    for t in close.columns:
        rows = sorted((local_date(ts, market), s) for ts, s in earn.get(t, []) if s is not None)
        col = np.full(len(dates), np.nan)
        for d, s in rows:
            u = usable_index(dates, d)
            if u < len(dates):
                col[u:u + 40] = float(np.clip(s, -50, 50))  # later reports overwrite earlier ones
        sig[t] = col
    return sig


def signal_e2(close: pd.DataFrame, acts: dict, sample_pos: list[int]) -> pd.DataFrame:
    dates = close.index
    sig = pd.DataFrame(np.nan, index=dates[sample_pos], columns=close.columns)
    for t in close.columns:
        rows = []
        for ts, a, p in acts.get(t, []):
            d = local_date(ts, "us")
            s = 1 if a == "up" else -1 if a == "down" else 1 if p == "Raises" else -1 if p == "Lowers" else 0
            rows.append((d, usable_index(dates, d), s))
        if not rows:
            sig[t] = 0.0
            continue
        ds = np.array([r[0].value for r in rows]); us = np.array([r[1] for r in rows]); ss = np.array([r[2] for r in rows])
        vals = []
        for pos in sample_pos:
            day = dates[pos]
            win = (ds > (day - pd.Timedelta(days=30)).value) & (ds <= day.value) & (us <= pos)
            vals.append(ss[win].sum() / win.sum() if win.any() else 0.0)
        sig[t] = vals
    return sig


# ---------------------------------------------------------------- test
def evaluate(sig: pd.DataFrame, close: pd.DataFrame, period, cost: float):
    fwd = close.shift(-H) / close - 1
    start, end = period
    ics, ex = [], []
    for day in sig.index:
        if day < pd.Timestamp(start) or (end and day > pd.Timestamp(end)):
            continue
        s, r = sig.loc[day], fwd.loc[day]
        ok = s.notna() & r.notna()
        if ok.sum() < 8 or r.notna().sum() < 20:
            continue
        s, rr = s[ok], r[ok]
        ic = s.rank().corr(rr.rank())
        if np.isnan(ic):
            continue
        k = math.ceil(len(s) / 5)
        top = rr[s.sort_values(ascending=False).index[:k]].mean()
        ics.append(ic)
        ex.append(top - r.dropna().mean())
    if len(ics) < 20:
        return None
    ics, ex = np.array(ics), np.array(ex)
    t = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics))) / OVERLAP
    net = ex.mean() * 100 - cost * 100
    return {"n": len(ics), "ic": round(float(ics.mean()), 4), "t": round(float(t), 2),
            "top_excess_net": round(float(net), 2), "hit": round(float((ex > 0).mean() * 100), 1),
            "pass": bool(t >= 2.0 and net > 0)}


def run(phase: str) -> None:
    results = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    if phase == "locked":
        if "locked" in results:
            raise SystemExit("locked period already run once")
        todo = [k for k, r in results.get("dev", {}).items() if r and r["pass"]]
        if not todo:
            raise SystemExit("nothing passed development: the locked period stays closed")
    period = DEV if phase == "dev" else LOCK
    rows = {}
    for m in (HK, US):
        close = pd.read_pickle(CACHE / f"close_{m.key}.pkl")
        # Yahoo's full-history download can stamp one trading day at two different times;
        # keep one row per calendar date and drop days with almost no prices (holidays)
        close = close.groupby(close.index.normalize()).last()
        close = close[close.notna().sum(axis=1) >= 0.5 * close.notna().sum(axis=1).max()]
        print(f"{m.key}: {len(close)} trading days {close.index[0].date()}~{close.index[-1].date()}")
        earn =json.loads((CACHE / f"earnings_{m.key}.json").read_text(encoding="utf-8"))
        all_pos = list(range(0, len(close) - H, STEP))
        e1 = signal_e1(close, earn, m.key).iloc[all_pos]
        tests = {f"E1-{m.key}": e1}
        if m.key == "us":
            acts = json.loads((CACHE / "analyst_us.json").read_text(encoding="utf-8"))
            tests["E2-us"] = signal_e2(close, acts, all_pos)
        for k, sig in tests.items():
            if phase == "locked" and k not in todo:
                continue
            rows[k] = evaluate(sig, close, period, COST[m.key])
            r = rows[k]
            print(f"{k:6} {period[0]}~{period[1] or 'now'}: " + (
                f"n={r['n']} IC={r['ic']:+.4f} t={r['t']:+.2f} 頂五分一淨跑贏={r['top_excess_net']:+.2f}% "
                f"勝率={r['hit']}% {'PASS' if r['pass'] else 'FAIL'}" if r else "not enough data"))
    results[phase] = rows
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("written", OUT)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dev"
    fetch() if cmd == "fetch" else run(cmd)
