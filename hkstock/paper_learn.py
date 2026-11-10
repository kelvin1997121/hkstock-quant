"""Learning from the simulated account: score every closed trade, group the results, and keep
Claude's post-mortems and lessons.

How the loop works:
  1. Each buy records its setup type (突破, 回調, ...) and what the data said at the time (regime,
     theme verdict, short score, RSI, trend, days to earnings, stop distance).
  2. When a position closes, the trade gets an R multiple (profit / first stop distance), how far it
     went for and against us while held (MFE / MAE), and what the price did in the 5 sessions after exit.
  3. `groups` compares results by setup, market, regime, score band and so on. With few trades every
     group says 樣本唔夠; a difference is only flagged (有跡象) with 10+ trades and |t| >= 2, and even then
     it is a lead to check on later trades, not a proven rule.
  4. Claude writes a post-mortem for every closed trade and keeps a short list of lessons. A lesson starts
     as 試行 and can become 採用 (a hard rule for later trades) only with at least MIN_ADOPT closed trades
     cited as evidence; lessons the later trades contradict are moved to 放棄.
The system's own scores are never changed from these results directly: a pattern that holds up here
still needs a pre-registered test before it changes the code (see research/).
"""
from __future__ import annotations

import math

MIN_N, T_FLAG = 10, 2.0


def trade_extras(t: dict, bars) -> dict:
    """MFE/MAE while held (vs entry cost) and the move in the 5 sessions after the exit."""
    import pandas as pd
    out = {"mfe": None, "mae": None, "after5": None, "held": None}
    f = bars.frames.get(t["ticker"]) if bars else None
    if f is None or f.empty:
        return out
    o, c = pd.Timestamp(t["opened"]), pd.Timestamp(t["closed"])
    w = f[(f.index >= o) & (f.index <= c)].dropna()
    if not w.empty:
        out["mfe"] = round(float(w["High"].max()) / t["entry"] - 1, 4)
        out["mae"] = round(float(w["Low"].min()) / t["entry"] - 1, 4)
        out["held"] = int(len(w))
    after = f[f.index > c]["Close"].dropna()
    if len(after) >= 5:
        out["after5"] = round(float(after.iloc[4]) / t["exit"] - 1, 4)
    return out


def _band(v, cuts, labels):
    if v is None:
        return "無數據"
    for c, lab in zip(cuts, labels):
        if v < c:
            return lab
    return labels[-1]


DIMENSIONS = {
    "買入類型": lambda t: t.get("setup") or "其他",
    "市場": lambda t: "港股" if t.get("market") == "hk" else "美股",
    "當時大市型勢": lambda t: (t.get("ctx") or {}).get("regime") or "無數據",
    "板塊狀態": lambda t: (t.get("ctx") or {}).get("theme_verdict") or "無數據",
    "短炒分": lambda t: _band((t.get("ctx") or {}).get("short_score"), (50, 75), ("50以下", "50-75", "75以上")),
    "RSI": lambda t: _band((t.get("ctx") or {}).get("rsi"), (50, 70), ("50以下", "50-70", "70以上")),
    "長線趨勢": lambda t: {True: "200天線上", False: "200天線下"}.get((t.get("ctx") or {}).get("above_ma200"), "無數據"),
    "業績": lambda t: "7日內有業績" if 0 <= ((t.get("ctx") or {}).get("earn_days") or 99) <= 7 else "冇",
    "點樣平倉": lambda t: t["why"] if t["why"] in ("止蝕", "到目標") else "自己沽",
}


def _summary(ts: list[dict]) -> dict:
    n = len(ts)
    rs = [t["r"] for t in ts if t.get("r") is not None]
    wins = sum(t["pnl_hkd"] > 0 for t in ts)
    mean = sum(rs) / len(rs) if rs else None
    tval = None
    if len(rs) >= 3:
        sd = math.sqrt(sum((x - mean) ** 2 for x in rs) / (len(rs) - 1))
        tval = round(mean / sd * math.sqrt(len(rs)), 2) if sd > 0 else None
    if n < MIN_N:
        verdict = "樣本唔夠"
    elif tval is not None and abs(tval) >= T_FLAG:
        verdict = "有跡象賺" if tval > 0 else "有跡象蝕"
    else:
        verdict = "未見分別"
    return {"n": n, "win_rate": round(wins / n, 3) if n else None, "avg_r": None if mean is None else round(mean, 2),
            "pnl_hkd": round(sum(t["pnl_hkd"] for t in ts), 0), "t": tval, "verdict": verdict}


def groups(trades: list[dict]) -> list[dict]:
    out = []
    for dim, key in DIMENSIONS.items():
        buckets: dict[str, list] = {}
        for t in trades:
            buckets.setdefault(key(t), []).append(t)
        for k, ts in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            out.append({"dim": dim, "key": k, **_summary(ts)})
    return out


def diagnostics(trades: list[dict]) -> list[str]:
    """Plain-language checks on stops and targets, each with its sample size."""
    notes = []
    stopped = [t for t in trades if t["why"] == "止蝕" and t.get("after5") is not None]
    if stopped:
        back = sum(t["exit"] * (1 + t["after5"]) > t["entry"] for t in stopped)
        notes.append(f"止蝕嘅 {len(stopped)} 單入面,{back} 單喺 5 個交易日內返上成本價以上"
                     + ("(超過一半:止蝕可能太近)" if len(stopped) >= MIN_N and back / len(stopped) > 0.5 else ""))
    hit = [t for t in trades if t["why"] == "到目標" and t.get("after5") is not None]
    if hit:
        more = sum(t["after5"] > 0.03 for t in hit)
        notes.append(f"到目標嘅 {len(hit)} 單入面,{more} 單之後 5 日再升多過 3%"
                     + ("(超過一半:目標可能太近)" if len(hit) >= MIN_N and more / len(hit) > 0.5 else ""))
    gave_back = [t for t in trades if t["pnl_hkd"] <= 0 and t.get("mfe") is not None and t.get("ctx", {}).get("stop_pct")]
    if gave_back:
        k = sum(t["mfe"] >= -t["ctx"]["stop_pct"] for t in gave_back)   # was up at least 1R at some point
        notes.append(f"蝕錢嘅 {len(gave_back)} 單入面,{k} 單曾經賺過 1R 以上先輸返"
                     + ("(值得考慮上移止蝕保本)" if len(gave_back) >= MIN_N and k / len(gave_back) > 0.3 else ""))
    return notes


def fold_lessons(entries: list[dict]) -> list[dict]:
    """Current state of every lesson, from the append-only order book."""
    lessons: dict[str, dict] = {}
    for e in entries:
        if e["type"] != "lesson":
            continue
        lid = e.get("lesson")
        if not lid:
            lid = f"L{sum(1 for x in lessons) + 1}"
            lessons[lid] = {"id": lid, "text": e["text"], "status": e.get("status", "試行"),
                            "evidence": sorted(set(e.get("evidence", []))), "created": e["t"], "history": []}
        elif lid in lessons:
            L = lessons[lid]
            if e.get("status"):
                L["status"] = e["status"]
            L["evidence"] = sorted(set(L["evidence"]) | set(e.get("evidence", [])))
        else:
            continue
        lessons[lid]["history"].append({"t": e["t"], "status": lessons[lid]["status"], "note": e.get("note", "")})
        lessons[lid]["updated"] = e["t"]
    order = {"採用": 0, "試行": 1, "放棄": 2}
    return sorted(lessons.values(), key=lambda L: (order.get(L["status"], 3), L["id"]))


def reviews(entries: list[dict]) -> dict[int, dict]:
    out = {}
    for e in entries:
        if e["type"] == "review" and e["trade"] not in out:
            out[e["trade"]] = {"text": e["text"], "t": e["t"]}
    return out


def overall(trades: list[dict]) -> dict:
    s = _summary(trades)
    gain = sum(t["pnl_hkd"] for t in trades if t["pnl_hkd"] > 0)
    loss = -sum(t["pnl_hkd"] for t in trades if t["pnl_hkd"] <= 0)
    s["profit_factor"] = round(gain / loss, 2) if loss > 0 else None
    return s
