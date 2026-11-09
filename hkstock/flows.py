"""Where the money is going: southbound (港股通) flow and per-theme flow from price/turnover."""
from __future__ import annotations

import numpy as np
import pandas as pd
import requests

from . import universe

UA = {"User-Agent": "Mozilla/5.0 (hkstock-quant)"}
EM = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def southbound_history(days: int = 1500) -> pd.Series:
    """Daily southbound net buying, in 億 (HK$/RMB 100m as East Money reports). Empty on failure."""
    rows, page = [], 1
    try:
        while len(rows) < days:
            r = requests.get(EM, headers=UA, timeout=20, params={
                "reportName": "RPT_MUTUAL_DEAL_HISTORY", "columns": "TRADE_DATE,NET_DEAL_AMT",
                "sortColumns": "TRADE_DATE", "sortTypes": "-1", "pageSize": "500",
                "pageNumber": str(page), "filter": '(MUTUAL_TYPE="006")'})
            res = (r.json().get("result") or {})
            data = res.get("data") or []
            rows += data
            if page >= (res.get("pages") or 1) or not data:
                break
            page += 1
    except Exception as e:
        print(f"  southbound history failed: {e}")
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(x["TRADE_DATE"][:10]): x["NET_DEAL_AMT"] for x in rows
                   if x.get("NET_DEAL_AMT") is not None}, dtype=float).sort_index()
    return s / 100  # 百萬 -> 億


def southbound_top() -> list[dict]:
    """Latest day's top southbound-traded stocks (Shanghai + Shenzhen connect), by net buy."""
    out = {}
    try:
        for t in ("002", "004"):
            r = requests.get(EM, headers=UA, timeout=20, params={
                "reportName": "RPT_MUTUAL_TOP10DEAL", "columns": "ALL", "sortColumns": "TRADE_DATE,RANK",
                "sortTypes": "-1,1", "pageSize": "10", "pageNumber": "1", "filter": f'(MUTUAL_TYPE="{t}")'})
            data = ((r.json().get("result") or {}).get("data") or [])
            if not data:
                continue
            day = data[0]["TRADE_DATE"][:10]
            for x in data:
                if x["TRADE_DATE"][:10] != day:
                    continue
                code = x["DERIVE_SECURITY_CODE"]  # 01888.HK
                yahoo = code[1:] if code.startswith("0") and len(code) == 8 else code
                o = out.setdefault(yahoo, {"ticker": yahoo, "name": x["SECURITY_NAME"], "date": day,
                                          "net": 0.0, "deal": 0.0, "chg": x.get("CHANGE_RATE")})
                o["net"] += (x.get("NET_BUY_AMT") or 0) / 1e8
                o["deal"] += (x.get("DEAL_AMT") or 0) / 1e8
    except Exception as e:
        print(f"  southbound top failed: {e}")
    rows = sorted(out.values(), key=lambda x: -x["net"])
    for x in rows:
        x["net"] = round(x["net"], 2)
        x["deal"] = round(x["deal"], 2)
        x["theme"] = universe.HK.theme_of().get(x["ticker"])
    return rows


def theme_flows(f: dict[str, pd.DataFrame], prices, m: universe.Market, i: int = -1) -> pd.DataFrame:
    """Per-theme snapshot on row i: turnover-weighted returns, turnover surge, money flow, breadth."""
    t = prices.turnover
    hsi = prices.close[m.index]
    hsi5 = hsi.iloc[i] / hsi.iloc[i - 5] - 1
    hsi20 = hsi.iloc[i] / hsi.iloc[i - 20] - 1
    rows = []
    for theme, group in m.themes.items():
        cols = [c for c in group if c in prices.close.columns]
        if not cols:
            continue
        w = f["turnover20"][cols].iloc[i].fillna(0)
        w = w / w.sum() if w.sum() > 0 else pd.Series(1 / len(cols), index=cols)
        wavg = lambda k: float((f[k][cols].iloc[i].fillna(0) * w).sum())
        end = len(t) + i + 1  # row i as a positive end index
        recent = t[cols].iloc[end - 3:end].sum().sum() / 3
        base = t[cols].iloc[end - 23:end - 3].mean().sum()
        rows.append({
            "theme": theme,
            "ret1": wavg("ret1"), "ret5": wavg("ret5"), "ret20": wavg("ret20"),
            "rs5": wavg("ret5") - hsi5, "rs20": wavg("ret20") - hsi20,
            "turn_ratio": float(recent / base) if base > 0 else np.nan,
            "cmf5": wavg("cmf5"),
            "up_share": float((f["ret1"][cols].iloc[i] > 0).mean()),
            "turnover_bn": float(t[cols].iloc[i].sum() / 1e8),  # 億
            "members": len(cols),
        })
    return pd.DataFrame(rows).set_index("theme")
