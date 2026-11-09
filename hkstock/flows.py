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


def etf_flows(prices, f: dict, m) -> list[dict]:
    """US sector/asset ETFs: estimated money flow = daily dollar volume x where the close sits in the
    day's range (+1 at the high, -1 at the low), summed over 5 and 20 days, in 億 USD. An estimate of
    buying vs selling pressure, not actual fund creations/redemptions."""
    c, h, l, v = prices.close, prices.high, prices.low, prices.volume
    idx5 = c[m.index].iloc[-1] / c[m.index].iloc[-6] - 1
    rows = []
    for t, name in m.flow_etfs.items():
        if t not in c or c[t].notna().sum() < 30:
            continue
        rng = (h[t] - l[t]).replace(0, np.nan)
        mfm = (((c[t] - l[t]) - (h[t] - c[t])) / rng).fillna(0)
        dv = c[t] * v[t]
        mfv = mfm * dv
        base = dv.iloc[-23:-3].mean()
        rows.append({"ticker": t, "name": name,
                     "flow5": float(mfv.iloc[-5:].sum() / 1e8), "flow20": float(mfv.iloc[-20:].sum() / 1e8),
                     "ret1": float(c[t].iloc[-1] / c[t].iloc[-2] - 1), "ret5": float(c[t].iloc[-1] / c[t].iloc[-6] - 1),
                     "rs5": float(c[t].iloc[-1] / c[t].iloc[-6] - 1 - idx5),
                     "turn_ratio": float(dv.iloc[-3:].mean() / base) if base > 0 else None,
                     "cmf20": float(f["cmf20"][t].iloc[-1]) if not pd.isna(f["cmf20"][t].iloc[-1]) else None})
    return sorted(rows, key=lambda r: -r["flow5"])


INSIDER_URL = ("http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd={days}&fdr=&td=0&tdr=&fdlyl=&fdlyh="
               "&daysago=&xp=1&vl=25&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh="
               "&v2l=&v2h=&oc2l=&oc2h=&sortcol=0&cnt=300&page=1")


def insider_buys(watch: dict[str, str], days: int = 14, top: int = 12) -> list[dict]:
    """Open-market purchases by company insiders (SEC Form 4, via openinsider.com), grouped by stock.
    Large outside holders listed only as '10%' owners are left out, so this is officers and directors
    spending their own money. Names in `watch` (our universe) are always kept and flagged."""
    import html as htmlmod
    import re
    import json
    from datetime import date
    from .data import DATA
    cache = DATA / "insider_cache.json"
    # insider filings change slowly: ask the site at most once a day and reuse that list
    if cache.exists():
        old = json.loads(cache.read_text(encoding="utf-8"))
        if old.get("date") == date.today().isoformat() and old.get("rows"):
            return old["rows"]
    m = None
    for attempt in range(2):  # the site is sometimes slow
        try:
            r = requests.get(INSIDER_URL.format(days=days), headers=UA, timeout=45)
            m = re.search(r'<table[^>]*class="tinytable"[^>]*>(.*?)</table>', r.text, re.S)
            if m:
                break
        except Exception as e:
            print(f"  insider data attempt {attempt + 1} failed: {e}")
    if not m:  # fall back to the last good list (marked stale), so the table does not go blank
        if cache.exists():
            old = json.loads(cache.read_text(encoding="utf-8"))
            for row in old["rows"]:
                row["stale"] = old["date"]
            return old["rows"]
        return []
    agg = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.S)[1:]:
        raw = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(raw) < 13:
            continue
        cell = lambda i: htmlmod.unescape(re.sub(r"<[^>]+>", "", raw[i])).strip()
        tk = re.search(r'href="/([A-Za-z.\-]+)"', raw[3])
        title = cell(6)
        if not tk or not cell(7).startswith("P") or title.replace(" ", "") in ("10%", ""):
            continue
        try:
            value = float(re.sub(r"[^\d.]", "", cell(12)))
        except ValueError:
            continue
        t = tk.group(1).upper().replace(".", "-")
        a = agg.setdefault(t, {"ticker": t, "company": cell(4), "value": 0.0, "insiders": set(),
                               "titles": set(), "last": ""})
        a["value"] += value
        a["insiders"].add(cell(5))
        a["titles"].update(x.strip() for x in title.split(",") if x.strip())
        a["last"] = max(a["last"], cell(2))
    rows = []
    for a in agg.values():
        rows.append({"ticker": a["ticker"], "company": a["company"], "value_k": round(a["value"] / 1000),
                     "insiders": len(a["insiders"]), "titles": "、".join(sorted(a["titles"]))[:40],
                     "last": a["last"], "watch": watch.get(a["ticker"])})
    rows.sort(key=lambda r: -r["value_k"])
    keep = [r for r in rows if r["value_k"] >= 100][:top]
    keep += [r for r in rows if r["watch"] and r not in keep]
    cache.write_text(json.dumps({"date": date.today().isoformat(), "rows": keep}, ensure_ascii=False), encoding="utf-8")
    return keep
