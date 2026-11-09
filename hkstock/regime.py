"""Today's market condition (當日型勢): weighs evidence for and against, then reaches a verdict.

Each piece of evidence gives a value in [-1, +1]; the verdict is the weighted
average x 100. The weights were set by hand (2026-11-09) and are not fitted.
Missing evidence (e.g. southbound data down) is left out and the rest re-weighted.

The "thinking" part: besides the verdict, it lists the strongest evidence
AGAINST the verdict and flags contradictions (index up on narrow breadth, etc.),
so the call is never one-sided.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WEIGHTS = {"trend": 20, "breadth": 15, "momentum": 10, "volatility": 10,
           "us": 15, "vix": 10, "cnh": 10, "southbound": 10}

LABELS = [(40, "強勢", "進攻:主攻強勢板塊突破股,可以順勢短炒"),
          (15, "偏好", "偏積極:揀資金流入嘅板塊,回調吸納,注意止蝕"),
          (-15, "震盪", "上落市:高沽低揸,減少追突破,注重注碼控制"),
          (-40, "偏淡", "偏防守:減少短炒,轉向高息及低波幅股"),
          (-999, "弱勢", "防守:現金為主,唔好撈底,等企穩訊號")]


def _clip(x):
    return np.clip(x, -1, 1)


def _global_aligned(g: pd.DataFrame, idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Global data as known before HK opens on each HK date (previous calendar day's close)."""
    if g.empty:
        return pd.DataFrame(index=idx)
    out = pd.DataFrame(index=g.index)
    for col in ("^GSPC", "^IXIC"):
        if col in g:
            out[col] = g[col].pct_change(fill_method=None)
    if "^VIX" in g:
        out["vix"] = g["^VIX"]
    if "CNY=X" in g:
        out["cnh5"] = g["CNY=X"].ffill().pct_change(5, fill_method=None)
    out.index = out.index + pd.Timedelta(days=1)
    return out.reindex(idx, method="ffill")


def inputs(prices, f: dict, g: pd.DataFrame, sb: pd.Series, stock_cols: list[str]) -> pd.DataFrame:
    c = prices.close
    hsi = c["^HSI"]
    x = pd.DataFrame(index=c.index)
    x["hsi"] = hsi
    x["ma20"] = hsi.rolling(20).mean()
    x["ma50"] = hsi.rolling(50).mean()
    x["ma200"] = hsi.rolling(200).mean()
    x["ret5"] = hsi.pct_change(5, fill_method=None)
    vol20 = hsi.pct_change(fill_method=None).rolling(20).std() * np.sqrt(252)
    x["vol20"] = vol20
    x["vol_pct"] = vol20.rolling(250, min_periods=120).rank(pct=True)
    above = (c[stock_cols] > f["ma20"][stock_cols]).where(f["ma20"][stock_cols].notna())
    x["breadth20"] = above.mean(axis=1)
    above50 = (c[stock_cols] > f["ma50"][stock_cols]).where(f["ma50"][stock_cols].notna())
    x["breadth50"] = above50.mean(axis=1)
    t = prices.turnover[stock_cols].sum(axis=1)
    x["mkt_turn_ratio"] = t / t.shift(1).rolling(20).mean()
    if "3033.HK" in c:
        x["tech_rs5"] = c["3033.HK"].pct_change(5, fill_method=None) - x["ret5"]
    ga = _global_aligned(g, c.index)
    for col in ga:
        x[col] = ga[col]
    if len(sb):
        # southbound for day t is published after t's close: use the 5 days to t-1 for day t
        sb5 = sb.rolling(5).sum().shift(1)
        x["sb5"] = sb5.reindex(c.index, method="ffill")
    return x


def components(x: pd.DataFrame) -> pd.DataFrame:
    s = pd.DataFrame(index=x.index)
    s["trend"] = (0.4 * np.sign(x["hsi"] - x["ma20"]) + 0.3 * np.sign(x["ma20"] - x["ma50"])
                  + 0.3 * np.sign(x["hsi"] - x["ma200"])).where(x["ma200"].notna())
    s["breadth"] = _clip((x["breadth20"] - 0.5) * 2.5)
    s["momentum"] = _clip(x["ret5"] / 0.03)
    s["volatility"] = (1 - 2 * x["vol_pct"])
    if "^GSPC" in x and "^IXIC" in x:
        s["us"] = _clip((x["^GSPC"] + x["^IXIC"]) / 2 / 0.015)
    if "vix" in x:
        s["vix"] = _clip((20 - x["vix"]) / 8)
    if "cnh5" in x:
        s["cnh"] = _clip(-x["cnh5"] / 0.01)
    if "sb5" in x:
        s["southbound"] = _clip(x["sb5"] / 200)
    return s


def score(comp: pd.DataFrame) -> pd.Series:
    w = pd.Series({k: v for k, v in WEIGHTS.items() if k in comp})
    present = comp[w.index].notna()
    num = (comp[w.index].fillna(0) * w).sum(axis=1)
    den = (present * w).sum(axis=1)
    out = num / den * 100
    return out.where(present["trend"] if "trend" in present else True)


def label(sc: float) -> tuple[str, str]:
    for th, name, plan in LABELS:
        if sc >= th:
            return name, plan
    return LABELS[-1][1], LABELS[-1][2]


def explain(x: pd.Series, comp: pd.Series, sc: float) -> dict:
    """Evidence in plain Cantonese, sorted by impact, plus counter-arguments and contradictions."""
    pct = lambda v: f"{v * 100:+.1f}%"
    text = {
        "trend": f"恒指 {x['hsi']:,.0f},{'高於' if x['hsi'] > x['ma20'] else '低於'}20天線({x['ma20']:,.0f})、"
                 f"{'高於' if x['hsi'] > x['ma50'] else '低於'}50天線、{'高於' if x['hsi'] > x['ma200'] else '低於'}200天線",
        "breadth": f"重點股中有 {x['breadth20'] * 100:.0f}% 企喺20天線上(50天線上:{x['breadth50'] * 100:.0f}%)",
        "momentum": f"恒指5日 {pct(x['ret5'])}",
        "volatility": f"恒指20日年化波幅 {x['vol20'] * 100:.0f}%,處於一年內第 {x['vol_pct'] * 100:.0f} 百分位",
        "us": f"隔晚美股:標普 {pct(x.get('^GSPC', np.nan))}、納指 {pct(x.get('^IXIC', np.nan))}",
        "vix": f"VIX {x.get('vix', np.nan):.1f}",
        "cnh": f"人民幣5日 {'轉強' if x.get('cnh5', 0) < 0 else '轉弱'}({pct(-x.get('cnh5', np.nan))})",
        "southbound": f"南向資金近5日淨{'買' if x.get('sb5', 0) >= 0 else '賣'} {abs(x.get('sb5', 0)):.0f} 億",
    }
    ev = []
    for k, w in WEIGHTS.items():
        v = comp.get(k)
        if v is None or pd.isna(v):
            continue
        ev.append({"key": k, "value": round(float(v), 2), "impact": round(float(v) * w, 1), "text": text[k]})
    ev.sort(key=lambda e: -abs(e["impact"]))
    side = 1 if sc >= 0 else -1
    against = [e for e in ev if np.sign(e["value"]) == -side and abs(e["value"]) >= 0.3]

    notes = []
    if x["ret5"] > 0.01 and x["breadth20"] < 0.45:
        notes.append("指數升但大部分股票未企穩20天線:屬窄幅上升,可能只係權重股托市,短炒要揀真係有資金嘅股")
    if x["ret5"] < -0.01 and x["breadth20"] > 0.55:
        notes.append("指數跌但多數股票仍企穩:跌市集中喺少數權重,個股未見全面轉弱")
    if x.get("mkt_turn_ratio", 1) > 1.3:
        notes.append(f"大市成交係平時 {x['mkt_turn_ratio']:.1f} 倍:{'放量上升,升勢較可信' if x['ret5'] > 0 else '放量下跌,有資金離場跡象'}")
    elif x.get("mkt_turn_ratio", 1) < 0.75:
        notes.append("成交萎縮:現時走勢欠缺資金確認,突破易失敗")
    if "tech_rs5" in x and not pd.isna(x["tech_rs5"]):
        if x["tech_rs5"] > 0.02:
            notes.append("恒生科技跑贏恒指:市場風險胃納提升,資金偏向高 beta 科技股")
        elif x["tech_rs5"] < -0.02:
            notes.append("恒生科技跑輸恒指:資金避險,偏向高息防守股")
    if x["vol_pct"] > 0.85:
        notes.append("波幅處於一年高位:注碼要細、止蝕位要闊,唔適合重倉")

    sc = round(float(sc))  # label from the same whole number the page shows
    name, plan = label(sc)
    return {"score": sc, "label": name, "plan": plan,
            "evidence": ev, "against": against, "notes": notes}
