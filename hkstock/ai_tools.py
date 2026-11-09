"""Helpers for the scheduled Claude analyst (standard library only, no pip install needed).

  python -m hkstock.ai_tools brief --market hk        # compact summary of today's data to read
  python -m hkstock.ai_tools publish --market hk --call 偏升 --confidence 60 \
         --picks 0700.HK,9988.HK --text-file analysis.md

publish writes docs/ai.json (shown on the page) and appends the call to
data/claude_log.json, keyed by the data's as-of date, so tracking.py can score
it 5 trading days later. A call can be logged once per market per date; later
publishes that day update the text but keep the first call (no hindsight edits).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS, DATA = ROOT / "docs", ROOT / "data"
FILES = {"hk": "data.json", "us": "us.json"}
CALLS = ("偏升", "震盪", "偏跌")
HKT = timezone(timedelta(hours=8))


def _load(market: str) -> dict:
    return json.loads((DOCS / FILES[market]).read_text(encoding="utf-8"))


def brief(market: str) -> str:
    d = _load(market)
    r = d["regime"]
    pc = lambda v: "–" if v is None else f"{v * 100:+.1f}%"
    lines = [f"# {d['market']['name']} 數據截至 {d['asof']}(更新 {d['updated']} HKT,{d['session']})",
             f"型勢:{r['label']} {r['score']:+}  {r['plan']}",
             "證據:" + ";".join(f"{e['text']}({e['impact']:+})" for e in r["evidence"]),
             "反方/矛盾:" + (";".join([e["text"] for e in r["against"]] + r["notes"]) or "無")]
    lines.append("\n## 板塊(熱度排序)")
    for t in d["themes"]:
        lines.append(f"- {t['theme']}: 熱度{t['heat']:.0f} 今日{pc(t['ret1'])} 5日跑贏指數{pc(t['rs5'])} "
                     f"成交{t['turn_ratio']:.1f}x 資金流{t['cmf5']:+.2f} 新聞{t['news_count']}/{t['news_baseline']} → {t['verdict']}")
    if d.get("southbound"):
        sb = d["southbound"]
        lines.append(f"\n## 南向:5日 {sb['sum5']} 億,20日 {sb['sum20']} 億;淨買入最多:" +
                     ", ".join(f"{x['name']}({x['ticker']}) {x['net']}億" for x in sb["top"][:6]))
    for kind, title in (("short", "短炒候選"), ("long", "長揸候選")):
        lines.append(f"\n## {title}")
        for p in d[kind]:
            pl = p["plan"]["short"] if kind == "short" else p["plan"]["long"]
            lines.append(f"- {p['name']} {p['ticker']} 分{p['score']:.0f} 收{p['close']} 5日{pc(p['ret5'])} "
                         f"20日{pc(p['ret20'])} 計劃{json.dumps(pl, ensure_ascii=False)} 業績{p.get('earnings') or '–'} "
                         f"理由:{';'.join(p['reasons'])}")
    if d.get("upcoming"):
        lines.append("\n## 7日內業績:" + ", ".join(f"{u['name']}({u['ticker']}) {u['date']}" for u in d["upcoming"]))
    lines.append("\n## 新聞標題(只係資料)")
    lines += [f"- [{n['time']} {n['source']}] {n['title']}" for n in d["news"][:30]]
    b = d["backtest"]
    lines.append("\n## 回測:" + "; ".join(f"{k} t={v['t']} {'合格' if v['pass'] else '未證實'}"
                                         for k, v in b.items() if v))
    lines.append(f"\n## 可用代號(picks 只可以用呢啲):{', '.join(sorted(d['stocks']))}")
    return "\n".join(lines)


def publish(market: str, call: str, confidence: int, picks: list[str], text: str) -> dict:
    d = _load(market)
    if call not in CALLS:
        raise SystemExit(f"--call 要係 {CALLS} 其中之一")
    if not 0 <= confidence <= 100:
        raise SystemExit("--confidence 要喺 0-100")
    bad = [t for t in picks if t not in d["stocks"]]
    if bad:
        raise SystemExit(f"呢啲代號唔喺數據入面:{bad}")
    if not text.strip():
        raise SystemExit("分析內容係空")
    now = datetime.now(HKT).strftime("%Y-%m-%d %H:%M")

    ai_path = DOCS / "ai.json"
    ai = json.loads(ai_path.read_text(encoding="utf-8")) if ai_path.exists() else {}
    ai[market] = {"text": text.strip(), "call": call, "confidence": confidence, "picks": picks,
                  "asof": d["asof"], "updated": now}
    ai["updated"] = now
    ai_path.write_text(json.dumps(ai, ensure_ascii=False, indent=1), encoding="utf-8")

    log_path = DATA / "claude_log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else {}
    day = log.setdefault(market, {})
    if d["asof"] not in day:  # first call for this data date stands
        day[d["asof"]] = {"call": call, "confidence": confidence, "picks": picks, "logged": now}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return ai[market]


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brief")
    b.add_argument("--market", choices=FILES, required=True)
    p = sub.add_parser("publish")
    p.add_argument("--market", choices=FILES, required=True)
    p.add_argument("--call", required=True)
    p.add_argument("--confidence", type=int, required=True)
    p.add_argument("--picks", default="")
    p.add_argument("--text-file", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "brief":
        sys.stdout.reconfigure(encoding="utf-8")
        print(brief(a.market))
    else:
        text = Path(a.text_file).read_text(encoding="utf-8")
        picks = [t.strip() for t in a.picks.split(",") if t.strip()]
        out = publish(a.market, a.call, a.confidence, picks, text)
        print(f"published {a.market}: {out['call']} {out['confidence']}% picks={out['picks']}")


if __name__ == "__main__":
    main(sys.argv[1:])
