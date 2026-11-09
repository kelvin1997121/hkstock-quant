"""One full run: data -> features -> regime -> flows -> news -> picks -> backtest -> page data."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from . import agent, backtest, data, events, flows, indicators, levels, news, regime, scoring, tracking
from .universe import Market


def _num(v, nd=4):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(v) or math.isinf(v) else round(v, nd)


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating, np.integer)):
        return _num(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def _fundamentals(tickers: list[str]) -> dict:
    out = {}
    try:
        import yfinance as yf
        for t in tickers:
            try:
                info = yf.Ticker(t).info
                out[t] = {"pe": _num(info.get("trailingPE"), 1),
                          "yield": _num((info.get("trailingAnnualDividendYield") or 0) * 100, 2),
                          "pb": _num(info.get("priceToBook"), 2),
                          "mcap_bn": _num((info.get("marketCap") or 0) / 1e8, 0)}
            except Exception:
                pass
    except Exception as e:
        print(f"  fundamentals skipped: {e}")
    return out


def theme_table(tf: pd.DataFrame, nstats: dict) -> list[dict]:
    tf = tf.copy()
    tf["news_count"] = [nstats.get(t, {}).get("count", 0) for t in tf.index]
    tf["news_baseline"] = [nstats.get(t, {}).get("baseline", 0) for t in tf.index]
    tf["news_surge"] = [nstats.get(t, {}).get("surge", 1) for t in tf.index]
    tf["news_tone"] = [nstats.get(t, {}).get("tone", 0) for t in tf.index]
    r = lambda col: tf[col].rank(pct=True).fillna(0.5)
    tf["heat"] = (0.30 * r("rs5") + 0.25 * r("turn_ratio") + 0.20 * r("cmf5") + 0.25 * r("news_surge")) * 100
    rows = []
    for th, x in tf.sort_values("heat", ascending=False).iterrows():
        news_hot = x["news_count"] >= 3 and x["news_surge"] >= 1.5
        flow_ok = x["cmf5"] > 0 and x["rs5"] > 0
        flow_strong = x["rs5"] > 0.02 and x["turn_ratio"] > 1.2 and x["cmf5"] > 0
        if news_hot and flow_ok:
            verdict, kind = "新聞+資金共振,最值得留意", "hot"
        elif news_hot:
            verdict, kind = "新聞熱但資金冇跟,小心炒消息", "warn"
        elif flow_strong:
            verdict, kind = "資金先行,新聞未熱", "flow"
        elif x["rs5"] < -0.02 and x["cmf5"] < 0:
            verdict, kind = "資金流出,暫避", "out"
        else:
            verdict, kind = "觀望", "none"
        rows.append({"theme": th, **{k: x[k] for k in ("ret1", "ret5", "ret20", "rs5", "rs20", "turn_ratio",
                                                       "cmf5", "up_share", "turnover_bn", "news_count",
                                                       "news_baseline", "news_surge", "news_tone", "heat")},
                     "verdict": verdict, "kind": kind})
    return rows


def fallback_text(reg: dict, themes: list[dict], short: list[dict], long: list[dict], index_name: str) -> str:
    p = [f"【今日型勢】{reg['label']}(分數 {reg['score']:+.0f})。{reg['plan']}。"]
    top = reg["evidence"][:3]
    if top:
        p.append("主要理由:" + ";".join(e["text"] for e in top) + "。")
    if reg["against"]:
        p.append("反方證據:" + ";".join(e["text"] for e in reg["against"][:2]) + "。")
    for n in reg["notes"][:3]:
        p.append(n + "。")
    hot = [t for t in themes if t["kind"] in ("hot", "flow")][:2]
    warn = [t for t in themes if t["kind"] == "warn"][:2]
    if hot:
        p.append("【資金去向】" + ";".join(
            f"{t['theme']}({t['verdict']},5日跑贏{index_name} {t['rs5'] * 100:+.1f}%,成交 {t['turn_ratio']:.1f} 倍)" for t in hot) + "。")
    if warn:
        p.append("【提防】" + "、".join(t["theme"] for t in warn) + " 新聞多但資金未跟,追入風險高。")
    if short:
        p.append("【短炒候選】" + "、".join(f"{s['name']}({s['ticker']})" for s in short[:4]) +
                 ",參考止蝕位已列出,跌穿要走。")
    if long:
        p.append("【長揸候選】" + "、".join(f"{s['name']}({s['ticker']})" for s in long[:4]) + ",可分段收集。")
    p.append("以上只係數據分析,唔係投資建議。")
    return "\n".join(p)


def run(m: Market, use_news: bool = True, use_ai: bool = True, save: bool = True) -> dict:
    now = data.now_hkt()
    session = data.market_session(m, now)
    print(f"[{now:%Y-%m-%d %H:%M} HKT] {m.name} {session} - loading prices")
    prices, g = data.load(m)
    names, themes_of = m.names(), m.theme_of()
    cols = [c for c in prices.close.columns if c in names]
    f = indicators.features(prices)
    asof = prices.close.index[-1].strftime("%Y-%m-%d")

    print("  regime + flows")
    sb = flows.southbound_history() if m.key == "hk" else pd.Series(dtype=float)
    x = regime.inputs(prices, f, g, sb, cols, m)
    comp = regime.components(x, m)
    rscore = regime.score(comp, m)
    # live row: use the freshest global/southbound numbers, not the "as known before that day's open" version
    live_x = x.iloc[-1].copy()
    if m.key == "hk" and not g.empty:
        for col in ("^GSPC", "^IXIC"):
            if col in g:
                live_x[col] = g[col].dropna().pct_change().iloc[-1]
        if "^VIX" in g:
            live_x["vix"] = g["^VIX"].dropna().iloc[-1]
        if "CNY=X" in g:
            s = g["CNY=X"].dropna()
            if len(s) > 6:
                live_x["cnh5"] = s.iloc[-1] / s.iloc[-6] - 1
    if len(sb):
        live_x["sb5"] = sb.iloc[-5:].sum()
    live_comp = regime.components(pd.DataFrame([live_x]), m).iloc[0]
    live_score = float(regime.score(pd.DataFrame([live_comp]), m).iloc[0])
    reg = regime.explain(live_x, live_comp, live_score, m)

    print("  scores")
    short = scoring.short_score(f, cols)
    long = scoring.long_score(f, cols)

    print("  news")
    items = news.fetch(m) if use_news else []
    today = data.local_now(m).strftime("%Y-%m-%d")
    nstats = news.theme_stats(items, today, m, save=save) if use_news else {}
    tf = flows.theme_flows(f, prices, m)
    themes = theme_table(tf, nstats)
    heat = {t["theme"]: t["heat"] for t in themes}

    # suspended / not trading: no volume in the last 10 sessions -> never picked, flagged on the page
    suspended = {t for t in cols if prices.volume[t].iloc[-10:].sum() == 0}
    if suspended:
        print(f"  suspended / no trades: {', '.join(sorted(suspended))}")
    last = short.iloc[-1].drop(labels=list(suspended), errors="ignore").dropna()
    final = last + 0.1 * pd.Series({t: heat.get(themes_of[t], 50) - 50 for t in last.index})
    close = prices.close.iloc[-1]
    atr = f["atr_pct"].iloc[-1]

    L = levels.frames(prices, f)

    def pick(t, sc, kind):
        c = float(close[t])
        a = float(atr[t]) if not pd.isna(atr[t]) else 0.03
        row = {"ticker": t, "name": names[t], "theme": themes_of[t], "score": sc, "close": c,
               "ret1": f["ret1"][t].iloc[-1], "ret5": f["ret5"][t].iloc[-1], "ret20": f["ret20"][t].iloc[-1],
               "rsi": f["rsi14"][t].iloc[-1], "turn_ratio": f["turn_ratio"][t].iloc[-1],
               "spark": [round(float(v), 3) for v in prices.close[t].iloc[-60:].ffill().tolist()]}
        row["plan"] = levels.plan_for(L, t, m.key)
        if kind == "short":
            row.update(short_score=float(last[t]), theme_heat=heat.get(themes_of[t]), atr_pct=a,
                       reasons=scoring.short_reasons(f, t))
        else:
            row.update(mom_12_1=f["mom_12_1"][t].iloc[-1], vol60=f["vol60"][t].iloc[-1],
                       drawdown=f["drawdown"][t].iloc[-1], reasons=scoring.long_reasons(f, t))
        return row

    short_picks = [pick(t, float(final[t]), "short") for t in final.nlargest(10).index]
    lscore = long.iloc[-1].drop(labels=list(suspended), errors="ignore").dropna()
    long_picks = [pick(t, float(lscore[t]), "long") for t in lscore.nlargest(10).index]
    fund = _fundamentals([p["ticker"] for p in long_picks + short_picks])
    for p in short_picks + long_picks:
        p["fund"] = fund.get(p["ticker"])

    print("  backtest")
    bt = backtest.run(prices, short, long, rscore, m.index, m.cost, m.index_name)
    bt["plan"] = levels.test_short_plan(prices, short, L)

    # every stock, for the holdings check on the page
    lsc = long.iloc[-1]
    stocks = {}
    held_extra = [t for t in m.extras if t in prices.close]
    for t in cols + held_extra:
        v = lambda k: f[k][t].iloc[-1]
        stocks[t] = {"name": names.get(t) or m.extras[t], "theme": themes_of.get(t, "ETF及加密"), "close": float(close[t]),
                     "ret1": v("ret1"), "ret5": v("ret5"), "ret20": v("ret20"), "rsi": v("rsi14"),
                     "short_score": last.get(t), "long_score": lsc.get(t),
                     "above_ma20": v("ma20") and close[t] > v("ma20"), "above_ma50": v("ma50") and close[t] > v("ma50"),
                     "above_ma200": None if pd.isna(v("ma200")) else bool(close[t] > v("ma200")),
                     "ma20": levels.tick_round(v("ma20"), m.key), "ma50": levels.tick_round(v("ma50"), m.key),
                     "ma200": levels.tick_round(v("ma200"), m.key), "drawdown": v("drawdown"),
                     "plan": levels.plan_for(L, t, m.key), "suspended": t in suspended}

    cal = events.calendar(cols + held_extra, save=save)
    for t, st in stocks.items():
        e = cal.get(t, {})
        st["earnings"], st["ex_div"] = e.get("earnings"), e.get("ex_div")
        st["earn_days"] = events.days_until(e.get("earnings"), data.local_now(m).date())
    for p in short_picks + long_picks:
        p["earnings"], p["earn_days"] = stocks[p["ticker"]]["earnings"], stocks[p["ticker"]]["earn_days"]
    all_names = {**names, **m.extras}
    upcoming = events.upcoming(cal, all_names, 7, data.local_now(m).date())
    # log only on the session that produced today's bar: on a holiday the latest bar is an older day,
    # and re-logging it with newer information would flatter the live record
    if session == "收市後" and save and asof == today:
        tracking.record(asof, reg, [p["ticker"] for p in short_picks], [p["ticker"] for p in long_picks], m.suffix)
    live = tracking.evaluate(prices, cols, m.index, m.suffix)
    claude_live = tracking.evaluate_claude(prices, cols, m.index, m.key)

    ix = prices.close[m.index].iloc[-250:]
    market = {
        "key": m.key, "name": m.name, "index_name": m.index_name,
        "index": [[d.strftime("%Y-%m-%d"), round(float(v), 1)] for d, v in ix.items()],
        "index_ma50": [None if pd.isna(v) else round(float(v), 1)
                       for v in prices.close[m.index].rolling(50).mean().iloc[-250:]],
        "indices": {k: {"name": n, "close": prices.close[k].iloc[-1],
                        "ret1": f["ret1"][k].iloc[-1], "ret5": f["ret5"][k].iloc[-1]}
                    for k, n in m.benchmarks.items() if k in prices.close},
        "global": {k: {"name": n, "close": g[k].dropna().iloc[-1],
                       "ret1": g[k].dropna().pct_change().iloc[-1],
                       "date": g[k].dropna().index[-1].strftime("%m-%d")}
                   for k, n in m.context.items() if not g.empty and k in g and g[k].notna().any()},
    }
    sb_out = None if m.key != "hk" else {"history": [[d.strftime("%Y-%m-%d"), round(float(v), 1)] for d, v in sb.iloc[-30:].items()],
              "top": flows.southbound_top(), "sum5": _num(sb.iloc[-5:].sum(), 1) if len(sb) else None,
              "sum20": _num(sb.iloc[-20:].sum(), 1) if len(sb) else None}

    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "session": session, "asof": asof,
           "regime": reg, "market": market, "themes": themes, "southbound": sb_out,
           "short": short_picks, "long": long_picks, "stocks": stocks, "upcoming": upcoming, "claude_live": claude_live,
           "us_flow": None if m.key != "us" else {"etf": flows.etf_flows(prices, f, m), "insider": flows.insider_buys({**names, **m.extras})},
           "news": [{k: v for k, v in it.items() if k != "ts"} for it in items[:60]],
           "backtest": bt, "live": live,
           "weights": {"short": scoring.SHORT_WEIGHTS, "long": scoring.LONG_WEIGHTS, "regime": regime.WEIGHTS}}
    out = _clean(out)

    ai = None
    if use_ai:
        print("  agent")
        payload = {"market": m.name, "regime": reg, "themes": themes[:8],
                   "southbound": {"sum5": sb_out["sum5"], "top": sb_out["top"][:6]} if sb_out else None,
                   "short": [{k: p[k] for k in ("ticker", "name", "theme", "score", "close", "plan", "reasons")} for p in short_picks[:6]],
                   "long": [{k: p[k] for k in ("ticker", "name", "theme", "score", "reasons", "fund")} for p in long_picks[:6]],
                   "headlines": [it["title"] for it in items[:25]], "backtest": bt, "session": session}
        ai = agent.analyse(_clean(payload), m.name, m.suffix)
    out["analysis"] = {"text": ai["text"], "by": "Claude"} if ai else \
        {"text": fallback_text(reg, themes, short_picks, long_picks, m.index_name), "by": "系統規則"}

    if save:
        data.DOCS.mkdir(parents=True, exist_ok=True)
        (data.DOCS / m.out).write_text(json.dumps(out, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        print(f"  wrote {data.DOCS / m.out}")
    return out
