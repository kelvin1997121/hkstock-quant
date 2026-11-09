"""Financial news from public RSS feeds, tagged by theme, with a per-theme mention baseline.

Headlines are only counted and shown; nothing in them is acted on.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser
import requests

from . import universe
from .data import DATA, HKT

UA = {"User-Agent": "Mozilla/5.0 (hkstock-quant)"}

FEEDS = {
    "RTHK財經": "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml",
    "經濟通": "https://www.etnet.com.hk/www/tc/news/rss.php?section=editor",
}
GOOGLE_QUERIES = ["港股", "恒指", "南向資金", "港股 板塊", "美股 收市", "中國 經濟 政策"]

POS = ["急升", "飆", "飙", "創新高", "创新高", "利好", "增持", "回購", "回购", "上調", "上调", "超預期", "超预期",
       "買入", "买入", "突破", "反彈", "反弹", "獲批", "获批", "大增", "勁升", "劲升", "升", "漲", "涨"]
NEG = ["急跌", "暴跌", "新低", "利淡", "減持", "减持", "下調", "下调", "遜預期", "逊预期", "警告", "虧損", "亏损",
       "制裁", "調查", "调查", "插水", "跌", "挫", "瀉", "泻", "沽"]

HISTORY = DATA / "news_history.json"


def _google(q: str) -> str:
    return f"https://news.google.com/rss/search?q={quote(q + ' when:1d')}&hl=zh-HK&gl=HK&ceid=HK:zh-Hant"


def _published(e) -> datetime | None:
    t = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


def fetch(hours: int = 30) -> list[dict]:
    srcs = dict(FEEDS)
    srcs.update({f"Google:{q}": _google(q) for q in GOOGLE_QUERIES})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen, items = set(), []
    for src, url in srcs.items():
        try:
            r = requests.get(url, headers=UA, timeout=20)
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"  news feed {src} failed: {e}")
            continue
        for e in feed.entries:
            title = re.sub(r"\s+-\s+[^-]{1,30}$", "", (e.get("title") or "").strip())  # drop " - 來源"
            key = re.sub(r"\W", "", title)[:40]
            pub = _published(e)
            if not title or key in seen or (pub and pub < cutoff):
                continue
            seen.add(key)
            publisher = (e.get("source") or {}).get("title") if src.startswith("Google") else src
            items.append({"title": title, "link": e.get("link"), "source": publisher or src,
                          "time": pub.astimezone(HKT).strftime("%m-%d %H:%M") if pub else "",
                          "ts": pub.timestamp() if pub else 0})
    for it in items:
        it["themes"] = [th for th, kws in universe.THEME_KEYWORDS.items() if any(k in it["title"] for k in kws)]
        it["tone"] = tone(it["title"])
    items.sort(key=lambda x: -x["ts"])
    return items


def tone(title: str) -> int:
    """+1 / -1 / 0 from simple keyword counts. Crude, so only used in aggregate."""
    p = sum(title.count(w) for w in POS)
    n = sum(title.count(w) for w in NEG)
    return (p > n) - (n > p)


def theme_stats(items: list[dict], today: str, save: bool = True) -> dict[str, dict]:
    hist = {}
    if HISTORY.exists():
        hist = json.loads(HISTORY.read_text(encoding="utf-8"))
    counts = {th: 0 for th in universe.THEMES}
    tones = {th: 0 for th in universe.THEMES}
    for it in items:
        for th in it["themes"]:
            counts[th] += 1
            tones[th] += it["tone"]
    prev = hist.get(today, {})
    hist[today] = {th: max(counts[th], prev.get(th, 0)) for th in counts}  # several runs a day: keep the max
    keep = sorted(hist)[-90:]
    hist = {d: hist[d] for d in keep}
    if save:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=0), encoding="utf-8")
    past = [d for d in keep if d < today][-20:]
    avg_all = (sum(counts.values()) / max(len(counts), 1)) or 1
    out = {}
    for th in counts:
        base = sum(hist[d].get(th, 0) for d in past) / len(past) if past else avg_all
        out[th] = {"count": counts[th], "baseline": round(base, 1),
                   "surge": round((counts[th] + 1) / (base + 1), 2), "tone": tones[th],
                   "baseline_days": len(past)}
    return out
