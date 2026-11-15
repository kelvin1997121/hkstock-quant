"""Forward record of the system's short-term trade plans (買賣計劃實測).

Every trading day the after-close candidates' buy zone, stop and target are written down, and each
plan is then followed as a pretend trade at real prices. Nothing is bought, in the simulated account
or anywhere else. The rules (order, fill, exit, costs, statistics, verdict) are fixed in
research/PREREG_2026-11-15_plan_trades.md and must not be changed here without a new registration.

  data/plan_log.json   the plans and their outcomes (GitHub Actions only; first record of a day stands)
  docs/plans.json      what the page shows

  python -m hkstock.plans update      # GitHub Actions, every run
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta

from . import paper

LOG, VIEW = paper.DATA / "plan_log.json", paper.DOCS / "plans.json"
LISTS = ("short", "tech")                       # candidate lists in docs/data.json and docs/us.json
VALID_DAYS, HOLD_DAYS = 3, 10                   # sessions the order works; sessions held at most
NOTIONAL = {"hk": 20_000.0, "us": 2_500.0}      # per trade, in the stock's own currency
MIN_DAYS, MIN_TRADES, T_PASS = 30, 150, 2.0     # the verdict is taken once, at this sample
RULES = (f"每個交易日收市後,系統將短炒候選同科技股候選嘅買入區、止蝕同目標價記低,之後當成一單假交易跟住計:"
         f"用買入區頂做限價買入,{VALID_DAYS} 個交易日內有效;跌到止蝕就沽,升穿目標價就沽,兩樣都冇就喺第 {HOLD_DAYS} 個交易日收市沽;"
         "每單當 2 萬港元(美股 2,500 美元),扣富途收費,計埋股息。冇真係買。"
         f"同一日嘅候選會一齊升跌,所以每個訊號日只當一個樣本。要有 {MIN_DAYS} 個訊號日同 {MIN_TRADES} 單完成交易先判斷一次:"
         "平均扣費後要賺,而且要跑贏指數,兩樣都要唔似係好彩(t 值 2 或以上)先算合格。規則喺睇到結果之前已經寫死。")


def candidates(d: dict) -> list[dict]:
    """Today's short-term candidates with their plans, each ticker once."""
    seen, out = set(), []
    for k in LISTS:
        for p in d.get(k) or []:
            t = p["ticker"]
            if t in seen:
                continue
            seen.add(t)
            s = (p.get("plan") or {}).get("short") or {}
            rec = {"t": t, "name": p.get("name", t), "src": k, "theme": p.get("theme"), "close": p.get("close"),
                   "score": None if p.get("score") is None else round(p["score"], 1),
                   "el": s.get("entry_low"), "eh": s.get("entry_high"), "stop": s.get("stop"), "t1": s.get("t1")}
            levels = [rec["stop"], rec["eh"], rec["t1"]]
            if not all(isinstance(v, (int, float)) and v == v for v in levels) or not levels[0] < levels[1] < levels[2]:
                rec["out"] = {"status": "invalid", "final": True}
            out.append(rec)
    return out


def record(log: dict, m: str, d: dict, today: date, now: datetime) -> bool:
    """Write down today's plans once, only from the after-close analysis of today's data."""
    days = log.setdefault(m, {})
    asof = d.get("asof")
    if d.get("session") != "收市後" or asof != today.isoformat() or asof in days:
        return False
    days[asof] = {"logged": paper.iso(now), "regime": (d.get("regime") or {}).get("label"), "plans": candidates(d)}
    return True


def _index_ret(bars, m: str, d_in: date, d_out: date):
    a, b = bars.get(paper.INDEX[m], d_in), bars.get(paper.INDEX[m], d_out)
    return None if a is None or b is None else round(b["c"] / a["o"] - 1, 5)


def _net(m: str, entry: float, exit_px: float, div: float) -> float:
    sh = NOTIONAL[m] / entry
    fees = paper.fee(m, "buy", sh, sh * entry) + paper.fee(m, "sell", sh, sh * exit_px)
    return ((exit_px - entry) * sh - fees + div * sh) / (sh * entry)


def _held(bars, t: str, m: str, days: list[date]):
    """Net dividends per share over days[1:], or None when a split falls in the window."""
    div = 0.0
    for d in days:
        dv, sp = bars.actions(t, d)
        if sp and sp != 1:
            return None
        div += dv * (1 - paper.DIV_TAX[m])
    return div


def _baseline(bars, t: str, m: str, days: list[date]):
    """The same stock bought at the next open and held to the 10th close, same size and fees."""
    if len(days) < HOLD_DAYS:
        return None
    hold = days[:HOLD_DAYS]
    div = _held(bars, t, m, hold[1:])
    if div is None or (bars.actions(t, hold[0])[1] or 1) != 1:
        return {"split": True}
    entry = bars.get(t, hold[0])["o"] * (1 + paper.SLIP[m])
    exit_px = bars.get(t, hold[-1])["c"] * (1 - paper.SLIP[m])
    return {"net": round(_net(m, entry, exit_px, div), 5), "idx": _index_ret(bars, m, hold[0], hold[-1])}


def simulate(rec: dict, bars, m: str, d0: date, last_done: date):
    """Follow one plan from the signal date d0 through the finished sessions up to last_done.
    Returns the outcome, or None when the stock has no prices in this run (try again next time).
    "final" means later prices cannot change it."""
    t = rec["t"]
    if t not in bars.frames:
        return None
    days = [ts.date() for ts in bars.frames[t].index if d0 < ts.date() <= last_done and bars.get(t, ts.date())]
    eh, stop, t1, slip = rec["eh"], rec["stop"], rec["t1"], paper.SLIP[m]
    base = _baseline(bars, t, m, days)
    if base and base.get("split"):
        return {"status": "split", "final": True}
    done = base is not None                                   # the comparison needs 10 sessions too

    fill = None
    for i, d in enumerate(days[:VALID_DAYS]):
        b = bars.get(t, d)
        if (bars.actions(t, d)[1] or 1) != 1:
            return {"status": "split", "final": True}
        if b["o"] <= stop:                                    # opened at or below the stop: plan void
            return {"status": "void", "final": done, "base": base}
        px = paper._limit_buy(b, eh)
        if px is not None:
            fill = (i, d, px, px == b["o"])
            break
    if fill is None:
        waiting = len(days) < VALID_DAYS
        return {"status": "waiting" if waiting else "unfilled", "final": done and not waiting, "base": base}

    i, d_in, entry, at_open = fill
    hold = days[i:i + HOLD_DAYS]
    exit_px = why = d_out = None
    for j, d in enumerate(hold):
        b = bars.get(t, d)
        if b["l"] <= stop:
            exit_px, why = min(b["o"], stop) * (1 - slip), "止蝕"
        elif (j > 0 or at_open) and paper._limit_sell(b, t1) is not None:   # no target on an intraday fill day
            exit_px, why = paper._limit_sell(b, t1), "到目標"
        if exit_px is not None:
            d_out = d
            break
    if exit_px is None:
        if len(hold) < HOLD_DAYS:
            return {"status": "open", "final": False, "in": d_in.isoformat(), "entry": round(entry, 4), "base": base}
        d_out = hold[-1]
        exit_px, why = bars.get(t, d_out)["c"] * (1 - slip), "到期"
    div = _held(bars, t, m, hold[1:hold.index(d_out) + 1])
    if div is None:
        return {"status": "split", "final": True}
    net = _net(m, entry, exit_px, div)
    return {"status": "done", "final": done, "in": d_in.isoformat(), "out": d_out.isoformat(), "entry": round(entry, 4),
            "exit": round(exit_px, 4), "why": why, "at_open": at_open, "net": round(net, 5),
            "r": round(net * entry / (entry - stop), 3), "idx": _index_ret(bars, m, d_in, d_out), "base": base}


def _mean_t(vals: list[float]):
    n = len(vals)
    if not n:
        return None, None
    mean = sum(vals) / n
    if n < 3:
        return mean, None
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return mean, (mean / sd * math.sqrt(n) if sd > 0 else None)


def summarise(days: dict) -> dict:
    """Running figures for one market. A signal day counts once (the average of its completed trades)."""
    count = {"plans": 0, "invalid": 0, "void": 0, "unfilled": 0, "split": 0, "pending": 0, "done": 0}
    net_d, excess_d, vs_base_d, base_all_d, trades = [], [], [], [], []
    for day, e in sorted(days.items()):
        done, base_all = [], []
        for rec in e["plans"]:
            o = rec.get("out") or {}
            st = o.get("status")
            count["plans"] += 1
            if st in ("invalid", "void", "unfilled", "split"):
                count[st] += 1
            elif st == "done":
                count["done"] += 1
                done.append(o)
            else:
                count["pending"] += 1
            if (o.get("base") or {}).get("net") is not None:
                base_all.append(o["base"]["net"])
        if base_all:
            base_all_d.append(sum(base_all) / len(base_all))
        if not done:
            continue
        trades += done
        net_d.append(sum(o["net"] for o in done) / len(done))
        ex = [o["net"] - o["idx"] for o in done if o.get("idx") is not None]
        if ex:
            excess_d.append(sum(ex) / len(ex))
        vb = [o["net"] - o["base"]["net"] for o in done if (o.get("base") or {}).get("net") is not None]
        if vb:
            vs_base_d.append(sum(vb) / len(vb))
    rnd = lambda v, n=4: None if v is None else round(v, n)
    mean, t = _mean_t(net_d)
    ex_mean, ex_t = _mean_t(excess_d)
    vb_mean, vb_t = _mean_t(vs_base_d)
    by_exit = {}
    for o in trades:
        g = by_exit.setdefault(o["why"], [])
        g.append(o["net"])
    fillable = count["done"] + count["unfilled"] + count["void"]
    return {**count, "days_logged": len(days), "signal_days": len(net_d),
            "fill_rate": rnd(count["done"] / fillable, 3) if fillable else None,
            "win_rate": rnd(sum(o["net"] > 0 for o in trades) / len(trades), 3) if trades else None,
            "avg_net": rnd(sum(o["net"] for o in trades) / len(trades)) if trades else None,
            "day_mean": rnd(mean), "t": rnd(t, 2), "excess_mean": rnd(ex_mean), "excess_t": rnd(ex_t, 2),
            "vs_base_mean": rnd(vb_mean), "vs_base_t": rnd(vb_t, 2),
            "base_all_mean": rnd(_mean_t(base_all_d)[0]),
            "by_exit": {k: {"n": len(v), "avg": rnd(sum(v) / len(v))} for k, v in sorted(by_exit.items())}}


def verdict(s: dict):
    """The registered verdict, or None before the registered sample is reached."""
    if s["signal_days"] < MIN_DAYS or s["done"] < MIN_TRADES:
        return None
    ok = all(v is not None for v in (s["day_mean"], s["t"], s["excess_mean"], s["excess_t"])) \
        and s["day_mean"] > 0 and s["t"] >= T_PASS and s["excess_mean"] > 0 and s["excess_t"] >= T_PASS
    keep = ("signal_days", "done", "day_mean", "t", "excess_mean", "excess_t", "win_rate", "fill_rate", "vs_base_mean")
    return {"pass": ok, **{k: s[k] for k in keep}}


def last_finished(m: str, now: datetime) -> date:
    d = now.astimezone(paper.TZ[m]).date()
    for _ in range(15):
        if paper.is_trading(m, d) and paper.finished_at(m, d) <= now:
            return d
        d -= timedelta(days=1)
    raise RuntimeError("no finished session in the last two weeks")


def score(log: dict, m: str, now: datetime, download=None) -> bool:
    """Follow the unfinished plans of one market, once per finished session."""
    done = last_finished(m, now)
    pend = [(day, rec) for day, e in log.get(m, {}).items() for rec in e["plans"] if not (rec.get("out") or {}).get("final")]
    if not pend or log.setdefault("evaluated", {}).get(m) == done.isoformat():
        return False
    start = date.fromisoformat(min(day for day, _ in pend)) - timedelta(days=7)
    bars = (download or paper.download)(sorted({rec["t"] for _, rec in pend} | {paper.INDEX[m]}), start)
    if bars.get(paper.INDEX[m], done) is None:
        print(f"  plans {m}: no index bar for {done} yet")           # Yahoo not caught up; next run
        return False
    for day, rec in pend:
        out = simulate(rec, bars, m, date.fromisoformat(day), done)
        if out is not None:
            rec["out"] = out
    log["evaluated"][m] = done.isoformat()
    return True


def build_view(log: dict, now: datetime) -> dict:
    markets = {}
    for m in ("hk", "us"):
        days = log.get(m, {})
        s = summarise(days)
        frozen = log.setdefault("verdict", {}).get(m)
        if frozen is None:
            v = verdict(s)
            if v is not None:
                frozen = log["verdict"][m] = {"taken": paper.hkt_str(now), **v}      # taken once, kept for good
        recent = []
        for day, e in sorted(days.items(), reverse=True):
            for rec in e["plans"]:
                o = rec.get("out") or {}
                recent.append({"day": day, "t": rec["t"], "name": rec["name"], "eh": rec["eh"], "stop": rec["stop"],
                               "t1": rec["t1"], "status": o.get("status", "waiting"), "in": o.get("in"), "out": o.get("out"),
                               "entry": o.get("entry"), "exit": o.get("exit"), "why": o.get("why"), "net": o.get("net"),
                               "idx": o.get("idx"), "base": (o.get("base") or {}).get("net")})
            if len(recent) >= 60:
                break
        markets[m] = {**s, "verdict": frozen, "recent": recent[:60]}
    return {"updated": paper.hkt_str(now), "rules": RULES, "min_days": MIN_DAYS, "min_trades": MIN_TRADES, "markets": markets}


def update(now: datetime | None = None) -> dict:
    now = now or datetime.now(paper.UTC)
    log = paper.read_json(LOG, {})
    for m in ("hk", "us"):
        d = paper.read_json(paper.DOCS / paper.STOCK_FILES[m], {})
        if record(log, m, d, now.astimezone(paper.TZ[m]).date(), now):
            print(f"  plans {m}: recorded {len(log[m][d['asof']]['plans'])} plans for {d['asof']}")
    for m in ("hk", "us"):
        try:
            if score(log, m, now):
                print(f"  plans {m}: scored through {log['evaluated'][m]}")
        except Exception as e:                    # a bad download must not lose today's record
            print(f"::warning::plans {m} not scored: {e!r}")
    view = build_view(log, now)
    paper.write_json(LOG, log, compact=True)
    paper.write_json(VIEW, view, compact=True)
    return view


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] != ["update"]:
        raise SystemExit("usage: python -m hkstock.plans update")
    v = update()
    for m, s in v["markets"].items():
        print(f"plans {m}: {s['days_logged']} days logged, {s['plans']} plans, {s['done']} completed trades")


if __name__ == "__main__":
    main(sys.argv[1:])
