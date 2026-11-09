"""Telegram alerts for your own holdings. Holdings never touch the public repo or page.

Secrets (GitHub → Settings → Secrets and variables → Actions):
  TELEGRAM_BOT_TOKEN  from @BotFather
  TELEGRAM_CHAT_ID    from `python -m hkstock.alerts chatid`
  HOLDINGS            one holding per line, same format as the page:  AAPL 10 190
                      optional margin line:  MARGIN AAPL,MSFT loan_usd=5000 maint=0.4 fx=7.8
                      (loan in USD; maint = maintenance margin / market value, from the broker app)

Sent:
  * a morning digest once per HK trading day (first run after 08:00 HKT)
  * alerts when something changes: crosses the 50 / 200-day line, a move of 5%+ in a day,
    RSI above 75, loss from cost passing 10/20/30/40/50%, earnings within 3 days,
    market regime label change, estimated margin cushion below HK$25,000 or down 25%.
Alert state lives in .alert_state/ (kept in the Actions cache, never committed).

  python -m hkstock.alerts run      # used by the workflow
  python -m hkstock.alerts chatid   # after you message your bot, prints your chat id
  python -m hkstock.alerts test     # sends a test message
"""
from __future__ import annotations

import getpass
import json
import os
import sys
from datetime import datetime

import requests

from .data import DOCS, HKT, ROOT

STATE = ROOT / ".alert_state" / "state.json"
LIQ_FLOOR = 25000


def _api(token: str, method: str, **params) -> dict:
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=params, timeout=20)
    return r.json()


def send(text: str) -> bool:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("  alerts: Telegram not configured, message not sent")
        return False
    ok = True
    for i in range(0, len(text), 3900):  # Telegram limit is 4096 chars
        res = _api(token, "sendMessage", chat_id=chat, text=text[i:i + 3900], disable_web_page_preview="true")
        ok = ok and res.get("ok", False)
        if not res.get("ok"):
            print(f"  alerts: Telegram error {res.get('description')}")
    return ok


def norm(t: str) -> str:
    t = t.strip().upper()
    if t.endswith(".HK"):
        t = t[:-3]
    if t.isdigit():
        n = str(int(t))
        return (n.zfill(4) if len(n) < 4 else n) + ".HK"
    return {"BTC": "BTC-USD", "ETH": "ETH-USD", "BRK.B": "BRK-B"}.get(t, t.replace(".", "-"))


def parse_holdings(text: str) -> tuple[list[dict], dict | None]:
    rows, margin = [], None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace(",", " ").split() if not line.upper().startswith("MARGIN") else line.split()
        if parts[0].upper() == "MARGIN":
            kv = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
            margin = {"tickers": [norm(t) for t in parts[1].split(",")], "loan_usd": float(kv.get("loan_usd", 0)),
                      "maint": float(kv.get("maint", 0.4)), "fx": float(kv.get("fx", 7.8))}
            continue
        try:
            rows.append({"ticker": norm(parts[0]), "qty": float(parts[1]), "cost": float(parts[2])})
        except (IndexError, ValueError):
            print(f"  alerts: skipped a holdings line that is not 'code qty cost'")
    return rows, margin


def _load():
    data = {}
    for k, f in (("hk", "data.json"), ("us", "us.json")):
        p = DOCS / f
        if p.exists():
            data[k] = json.loads(p.read_text(encoding="utf-8"))
    return data


def margin_estimate(margin: dict, stocks: dict) -> dict | None:
    if not margin:
        return None
    val = sum(stocks[t]["close"] * q for t, q in margin["qty"].items() if t in stocks)
    if not val:
        return None
    fx = margin["fx"]
    equity = (val - margin["loan_usd"]) * fx
    liq = equity - margin["maint"] * val * fx
    lev = val * fx / equity if equity > 0 else None
    drop = liq / ((1 - margin["maint"]) * val * fx) if val else None  # all fall together by this much -> call
    return {"value_hkd": val * fx, "equity_hkd": equity, "liquidity_hkd": liq, "leverage": lev, "call_drop": drop}


def run() -> None:
    text = os.environ.get("HOLDINGS", "")
    if not text.strip():
        print("  alerts: no HOLDINGS secret, nothing to do")
        return
    holds, margin = parse_holdings(text)
    data = _load()
    stocks = {**data.get("hk", {}).get("stocks", {}), **data.get("us", {}).get("stocks", {})}
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    first_run = not state
    now = datetime.now(HKT)
    alerts = []
    fx_of = lambda t: 1.0 if t.endswith(".HK") else 7.8

    for h in holds:
        t, s = h["ticker"], stocks.get(h["ticker"])
        if not s:
            continue
        nm, st = f"{s['name']}({t})", state.setdefault(t, {})
        pl = s["close"] / h["cost"] - 1
        for key, label_down, label_up in (("above_ma50", "跌穿50天線,中期轉弱", "重上50天線"),
                                           ("above_ma200", "跌穿200天線,長線趨勢轉弱", "重上200天線,長線趨勢轉好")):
            now_v = s.get(key)
            if now_v is not None and key in st and st[key] != now_v:
                alerts.append(f"{'🔻' if not now_v else '🔺'} {nm} {label_up if now_v else label_down}(現價 {s['close']:.2f})")
            if now_v is not None:
                st[key] = now_v
        if s.get("ret1") is not None and abs(s["ret1"]) >= 0.05 and st.get("big_move") != s.get("close"):
            alerts.append(f"⚡ {nm} 今日 {s['ret1'] * 100:+.1f}%(現價 {s['close']:.2f})")
            st["big_move"] = s.get("close")
        hot = (s.get("rsi") or 0) > 75
        if hot and not st.get("hot"):
            alerts.append(f"🔥 {nm} RSI {s['rsi']:.0f},短線過熱")
        st["hot"] = hot
        level = max([x for x in (10, 20, 30, 40, 50) if pl <= -x / 100], default=0)
        if level > st.get("loss_level", 0):
            alerts.append(f"⚠️ {nm} 由成本計已蝕 {-pl * 100:.0f}%(成本 {h['cost']}, 現價 {s['close']:.2f})")
        st["loss_level"] = level
        ed = s.get("earn_days")
        if ed is not None and 0 <= ed <= 3 and st.get("earn_warned") != s.get("earnings"):
            alerts.append(f"📅 {nm} {'今日' if ed == 0 else f'{ed} 日後'}({s['earnings']})公佈業績,前後波幅可能好大")
            st["earn_warned"] = s.get("earnings")

    for k, d in data.items():
        key = f"regime_{k}"
        lab = d["regime"]["label"]
        if key in state and state[key] != lab:
            alerts.append(f"🧭 {d['market']['name']}大市型勢由「{state[key]}」轉為「{lab}」({d['regime']['score']:+})")
        state[key] = lab

    mg = None
    if margin:
        margin["qty"] = {h["ticker"]: h["qty"] for h in holds if h["ticker"] in margin["tickers"]}
        mg = margin_estimate(margin, stocks)
        if mg:
            prev = state.get("liq_alerted")
            low = mg["liquidity_hkd"] < LIQ_FLOOR
            fell = prev is not None and mg["liquidity_hkd"] < prev * 0.75
            if (low and not state.get("liq_low")) or fell:
                alerts.append(f"🚨 孖展戶口估算剩餘流動性 HK${mg['liquidity_hkd']:,.0f}(槓桿 {mg['leverage']:.2f} 倍),"
                              f"請打開富途核對;一齊再跌約 {mg['call_drop'] * 100:.0f}% 會追孖展")
                state["liq_alerted"] = mg["liquidity_hkd"]
            state["liq_low"] = low
            if prev is None:
                state["liq_alerted"] = mg["liquidity_hkd"]

    today = now.strftime("%Y-%m-%d")
    digest = None
    if now.weekday() < 5 and now.hour >= 8 and state.get("digest_day") != today:
        digest = build_digest(holds, stocks, data, mg, fx_of)
        state["digest_day"] = today

    if first_run:
        alerts = []  # first run only records the starting state, so nothing old is reported as new
    msgs = []
    if alerts:
        msgs.append("📣 持倉提醒\n" + "\n".join(alerts))
    if digest:
        msgs.append(digest)
    sent = all(send(m) for m in msgs) if msgs else True
    print(f"  alerts: {len(alerts)} alert(s){', digest' if digest else ''}{'' if sent else ' (send failed)'}")
    if sent:  # keep state only when Telegram accepted it, so a failed send is retried next run
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def build_digest(holds, stocks, data, mg, fx_of) -> str:
    lines, tot, cost = [], 0.0, 0.0
    for h in holds:
        s = stocks.get(h["ticker"])
        if not s:
            lines.append(f"· {h['ticker']}:未有數據")
            continue
        fx = fx_of(h["ticker"])
        tot += s["close"] * h["qty"] * fx
        cost += h["cost"] * h["qty"] * fx
        pl = s["close"] / h["cost"] - 1
        trend = "✅" if s.get("above_ma200") and s.get("above_ma50") else ("⚠️" if s.get("above_ma200") else "🔻")
        extra = f" 📅{s['earnings']}" if s.get("earn_days") is not None and 0 <= s["earn_days"] <= 7 else ""
        lines.append(f"{trend} {s['name']} {s['close']:.2f}({(s.get('ret1') or 0) * 100:+.1f}%) 盈虧 {pl * 100:+.0f}% "
                     f"| 長線止蝕參考 {s['plan']['long']['stop'] or '–'}{extra}")
    head = [f"🌅 每日持倉摘要 {datetime.now(HKT):%m-%d}",
            f"總值 HK${tot:,.0f} 盈虧 HK${tot - cost:,.0f}({(tot / cost - 1) * 100:+.1f}%)" if cost else ""]
    for k, d in data.items():
        head.append(f"{d['market']['name']}型勢:{d['regime']['label']}({d['regime']['score']:+})")
    if mg:
        head.append(f"孖展估算:流動性 HK${mg['liquidity_hkd']:,.0f},槓桿 {mg['leverage']:.2f} 倍,"
                    f"一齊跌約 {mg['call_drop'] * 100:.0f}% 先追孖展(以富途顯示為準)")
    head.append("✅ 企穩50/200天線 ⚠️ 跌穿50天線 🔻 200天線之下")
    return "\n".join(x for x in head if x) + "\n\n" + "\n".join(lines) + \
        "\n\n只係數據整理,唔係投資建議。https://kelvin1997121.github.io/hkstock-quant/"


def chatid() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or getpass.getpass("貼上 bot token(唔會顯示):")
    res = _api(token.strip(), "getUpdates")
    chats = {(u.get("message") or {}).get("chat", {}).get("id") for u in res.get("result", [])} - {None}
    if not res.get("ok"):
        print("token 唔啱:", res.get("description"))
    elif not chats:
        print("搵唔到對話:請先喺 Telegram 打開你個 bot 撳 Start,再隨便send一句,然後再跑一次。")
    else:
        print("你嘅 chat id:", ", ".join(str(c) for c in chats))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    sys.stdout.reconfigure(encoding="utf-8")
    if cmd == "chatid":
        chatid()
    elif cmd == "test":
        print("sent" if send("✅ hkstock 提醒已接通。") else "failed")
    else:
        run()
