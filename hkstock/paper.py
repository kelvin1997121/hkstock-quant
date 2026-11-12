"""Simulated account (模擬倉): HK$100,000 that Claude trades in Hong Kong and US stocks.

Who writes which file (two writers never touch the same file, so git rebases never drop an order):
  data/paper_orders.json  Claude only, through `order`, `cancel`, `exit` and `plan` below. Append-only;
                          every entry carries the UTC time it was placed.
  data/paper_state.json   GitHub Actions only (`update`): the account after the last finished session,
                          plus one equity point per Hong Kong date.
  docs/paper.json         GitHub Actions only: what the page shows (finished sessions + today so far).
  data/lot_sizes.json     GitHub Actions only: HK board lots from Tencent quotes.

Fill rules (an order only ever uses prices from sessions that opened after it was placed):
  - An order works from the first session of its market that opens after the order time, for `days`
    sessions (default 1), then expires.
  - Market order: that session's open, plus slippage. Limit buy: fills at the open if the open is at or
    below the limit, otherwise at the limit only if the low went below it. A low that merely touches the
    limit does not fill: other orders queued at that price first, so a real fill is not assured.
    Limit sell: the mirror image (open at or above the limit, or a high above it).
  - A buy carries a stop (required) and optional target for the whole position, checked from the fill
    day on: low <= stop -> sold at min(open, stop) less slippage; target works like a limit sell
    (open at or above it, or a high above it). Both in one day counts as the stop (daily bars cannot
    show which came first).
  - Within a session: control entries (cancel/exit), then sells, then buys, then stop/target checks.
  - No margin and no short selling: a buy is cut to what the cash pays for (whole board lots in HK),
    or rejected.
  - Fees approximate Futu's published schedule; US trades convert at the day's USD/HKD rate with no
    conversion fee. Prices are Yahoo daily bars, not dividend-adjusted.
  - Dividends: a position held before an ex-dividend session's open is credited the dividend in cash at
    that open (the price gap on the ex-date is real and can still trip a stop, as it would at a broker).
    US dividends lose the 30% withholding tax a Hong Kong resident pays; HK dividends are credited in
    full (H shares really lose 10%, which is not modelled). A dividend counts towards the trade's P&L.
  - Splits: on the split session shares are multiplied and cost, stop, target and 1R divided by the
    ratio; orders still waiting in that stock are cancelled because their prices are pre-split.

  python -m hkstock.paper update                       # GitHub Actions, every run
  python -m hkstock.paper brief                        # account summary for Claude
  python -m hkstock.paper order --ticker 0700.HK --side buy --shares 100 --stop 390 --target 440 --reason "..."
  python -m hkstock.paper order --ticker AAPL --side buy --shares 10 --limit 345 --stop 330 --days 2 --reason "..."
  python -m hkstock.paper order --ticker AAPL --side sell --shares 10 --reason "..."
  python -m hkstock.paper cancel --id 3 --reason "..."
  python -m hkstock.paper exit --ticker AAPL --stop 340 --target 380 --reason "..."
  python -m hkstock.paper plan --text-file /tmp/plan.md
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import market_days
from . import paper_learn as learn

ROOT = Path(__file__).resolve().parent.parent
DATA, DOCS = ROOT / "data", ROOT / "docs"
ORDERS, STATE, VIEW, LOTS = (DATA / "paper_orders.json", DATA / "paper_state.json",
                             DOCS / "paper.json", DATA / "lot_sizes.json")
UTC, HKT = timezone.utc, ZoneInfo("Asia/Hong_Kong")
TZ = {"hk": HKT, "us": ZoneInfo("America/New_York")}
CAPITAL = 100_000.0
SLIP = {"hk": 0.001, "us": 0.0005}
MAX_POSITION = 0.25      # of equity, checked when the order is placed
MAX_ONE_LOT = 0.45       # an HK board lot may go up to this, so big-lot blue chips stay tradeable
MAX_THEME = 0.40         # one theme (held + waiting + new), unless the new buy is the theme's only stock
CORR_LIMIT, MAX_GROUP = 0.6, 0.40   # stocks whose 60-day daily returns correlate this much count as one bet
MAX_RISK = 0.015         # shares x (entry - stop) x FX, as a share of equity
MIN_CASH = 0.10          # cash left after every waiting buy
MAX_POSITIONS = 8
LOCK_WORDS = ("鎖定", "鎖住", "保本", "鎖利")   # a stop described like this must be at or above the cost
MAX_REASON, MAX_PLAN = 200, 700
SETUPS = ("突破", "回調", "超賣反彈", "趨勢跟隨", "消息/業績", "其他")   # the kind of trade, for the learning stats
MAX_REVIEW, MAX_LESSON = 300, 200
MIN_ADOPT = 5           # a lesson needs this many supporting closed trades before it becomes a hard rule
FX_DEFAULT = 7.8
DIV_TAX = {"hk": 0.0, "us": 0.30}   # withholding on dividends for a Hong Kong resident
INDEX = {"hk": "^HSI", "us": "^GSPC"}
STOCK_FILES = {"hk": "data.json", "us": "us.json"}
RULES = ("起始資金 10 萬港元;港股美股共用現金(美元按當日匯率);唔可以借錢或者沽空;"
         f"每隻股票落單時最多佔資產 {MAX_POSITION:.0%}(港股每手太大嘅可以買一手,最多 {MAX_ONE_LOT:.0%});買入一定要有止蝕;"
         f"每單去到止蝕最多蝕資產 {MAX_RISK:.1%};同一板塊、或者 60 日走勢相關系數 {CORR_LIMIT} 以上嘅股票加埋最多 {MAX_GROUP:.0%};"
         f"最多 {MAX_POSITIONS} 隻股票;所有買單成交後要留最少 {MIN_CASH:.0%} 現金;"
         "落單後由下一個開市時段先生效,用真實開市價/最高/最低價成交;限價單同目標價要開市價已經去到,"
         "或者全日最低(沽出就係最高)穿過咗限價先算成交,啱啱掂到唔計;費用按富途收費估算;"
         "除淨日前持有嘅股票會收股息(美股扣 30% 預扣稅,港股全數,H 股實際要扣 10% 未計),股息計入嗰單交易嘅盈虧;"
         "拆股會自動調整股數、成本同止蝕。")


# ---------------------------------------------------------------- basics
def market_of(ticker: str) -> str:
    return "hk" if ticker.endswith(".HK") else "us"


def fee(market: str, side: str, shares: int, value: float) -> float:
    """Approximate Futu charges in the trade's own currency."""
    if market == "hk":
        f = (max(3.0, value * 0.0003) + 15.0 + math.ceil(value * 0.001)          # commission, platform, stamp duty
             + value * (0.0000565 + 0.000027 + 0.0000015)                        # HKEX fee, SFC and AFRC levies
             + min(max(2.0, value * 0.00002), 100.0))                            # settlement
    else:
        f = max(0.99, 0.0049 * shares) + max(1.0, 0.005 * shares)                # commission, platform
        if side == "sell":
            f += value * 0.0000278 + min(max(0.01, 0.000166 * shares), 8.30)     # SEC fee, FINRA TAF
    return round(f, 2)


def is_trading(m: str, d: date) -> bool:
    return d.weekday() < 5 and not market_days.is_holiday(m, d)


def local_dt(m: str, d: date, hh: int, mm: int) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ[m]).astimezone(UTC)


def sessions(start: datetime, end: datetime) -> list[tuple[datetime, str, date]]:
    """(open time UTC, market, local date) for every session opening after `start` and by `end`."""
    out = []
    d = start.astimezone(HKT).date() - timedelta(days=1)
    while d <= end.astimezone(HKT).date() + timedelta(days=1):
        for m in ("hk", "us"):
            if is_trading(m, d):
                o = local_dt(m, d, 9, 30)
                if start < o <= end:
                    out.append((o, m, d))
        d += timedelta(days=1)
    return sorted(out)


def finished_at(m: str, d: date) -> datetime:
    """When a session's daily bar can be treated as final."""
    return local_dt(m, d, 17, 0)


def parse_t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hkt_str(t: datetime | str) -> str:
    t = parse_t(t) if isinstance(t, str) else t
    return t.astimezone(HKT).strftime("%Y-%m-%d %H:%M")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) if compact
            else json.dumps(obj, ensure_ascii=False, indent=1))
    path.write_text(text, encoding="utf-8")


def new_state(start: datetime) -> dict:
    return {"start": iso(start), "capital": CAPITAL, "through": iso(start), "cash": CAPITAL,
            "positions": {}, "orders": {}, "fills": [], "trades": [], "fees": 0.0, "history": {}, "dividends": []}


# ---------------------------------------------------------------- prices
class Bars:
    """Daily OHLC by ticker. `frames` maps ticker -> DataFrame(Open, High, Low, Close) indexed by date."""

    def __init__(self, frames: dict):
        self.frames = frames

    def get(self, ticker: str, d: date):
        import pandas as pd
        f = self.frames.get(ticker)
        if f is None:
            return None
        ts = pd.Timestamp(d)
        if ts not in f.index:
            return None
        r = f.loc[ts]
        if any(not (v == v) or v <= 0 for v in (r["Open"], r["High"], r["Low"], r["Close"])):  # NaN check
            return None
        return {"o": float(r["Open"]), "h": float(r["High"]), "l": float(r["Low"]), "c": float(r["Close"])}

    def actions(self, ticker: str, d: date) -> tuple[float, float]:
        """(dividend per share, split ratio) with that date as ex-date; (0, 0) when none."""
        import pandas as pd
        f = self.frames.get(ticker)
        ts = pd.Timestamp(d)
        if f is None or ts not in f.index:
            return 0.0, 0.0
        r = f.loc[ts]
        num = lambda k: float(r[k]) if k in f.columns and r[k] == r[k] and r[k] > 0 else 0.0
        return num("Dividends"), num("Stock Splits")

    def last_close(self, ticker: str, upto: date | None = None):
        f = self.frames.get(ticker)
        if f is None:
            return None, None
        c = f["Close"].dropna()
        c = c[c > 0]
        if upto is not None:
            import pandas as pd
            c = c[c.index <= pd.Timestamp(upto)]
        if c.empty:
            return None, None
        return float(c.iloc[-1]), c.index[-1].date().isoformat()

    def fx(self, d: date) -> float:
        v, _ = self.last_close("HKD=X", d)
        return v if v and 7.0 < v < 8.5 else FX_DEFAULT


def download(tickers: list[str], start: date) -> Bars:
    import pandas as pd
    import yfinance as yf
    yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_"))
    df = yf.download(sorted(set(tickers)), start=start.isoformat(), auto_adjust=False, actions=True,
                     progress=False, threads=True, group_by="column")
    frames = {}
    if df is None or df.empty:
        return Bars(frames)
    if not isinstance(df.columns, pd.MultiIndex):
        df.columns = pd.MultiIndex.from_product([df.columns, tickers[:1]])
    for t in set(df.columns.get_level_values(1)):
        f = pd.DataFrame({k: df[(k, t)] for k in ("Open", "High", "Low", "Close", "Dividends", "Stock Splits")
                          if (k, t) in df.columns})
        f.index = pd.to_datetime(f.index).tz_localize(None).normalize()
        frames[t] = f[~f.index.duplicated(keep="last")]
    return Bars(frames)


# ---------------------------------------------------------------- the account engine
def _fill_buy(st, o, price, m, d, fx, why):
    lot = o.get("lot", 1)
    fxm = fx if m == "us" else 1.0
    shares = o["shares"]
    while shares > 0:
        v = shares * price
        f = fee(m, "buy", shares, v)
        if (v + f) * fxm <= st["cash"] + 1e-6:
            break
        shares -= lot
    if shares <= 0:
        return False, "現金不足"
    v = shares * price
    f = fee(m, "buy", shares, v)
    p = st["positions"].get(o["ticker"])
    if p:
        p["cost"] = (p["shares"] * p["cost"] + v + f) / (p["shares"] + shares)
        p["shares"] += shares
    else:
        p = st["positions"][o["ticker"]] = {"shares": shares, "cost": (v + f) / shares, "market": m,
                                            "opened": d.isoformat(), "reason": o.get("reason", ""),
                                            "order": o.get("id"), "setup": o.get("setup", "其他"),
                                            "ctx": o.get("ctx", {}), "entry_at_open": bool(o.get("at_open"))}
        if o.get("stop") is not None and o["stop"] < p["cost"]:
            p["risk"] = p["cost"] - o["stop"]          # first stop distance per share = 1R
    if o.get("stop") is not None:
        p["stop"] = o["stop"]
    if o.get("target") is not None:
        p["target"] = o["target"]
    st["cash"] -= (v + f) * fxm
    st["fees"] += f * fxm
    st["fills"].append({"date": d.isoformat(), "ticker": o["ticker"], "side": "buy", "shares": shares,
                        "price": round(price, 4), "fee_hkd": round(f * fxm, 2), "fx": round(fx, 4), "why": why})
    note = "" if shares == o["shares"] else f"現金不足,只買到 {shares} 股"
    return True, note


def _fill_sell(st, ticker, shares, price, m, d, fx, why):
    p = st["positions"][ticker]
    fxm = fx if m == "us" else 1.0
    v = shares * price
    f = fee(m, "sell", shares, v)
    div = p.get("div", 0.0) * shares / p["shares"]               # this part's share of dividends received
    p["div"] = p.get("div", 0.0) - div
    pnl_local = v - f - p["cost"] * shares + div
    st["cash"] += (v - f) * fxm
    st["fees"] += f * fxm
    st["fills"].append({"date": d.isoformat(), "ticker": ticker, "side": "sell", "shares": shares,
                        "price": round(price, 4), "fee_hkd": round(f * fxm, 2), "fx": round(fx, 4), "why": why})
    st["trades"].append({"ticker": ticker, "market": m, "opened": p["opened"], "closed": d.isoformat(),
                         "shares": shares, "entry": round(p["cost"], 4), "exit": round(price, 4),
                         "pnl_hkd": round(pnl_local * fxm, 2), "pnl_pct": round(pnl_local / (p["cost"] * shares), 4),
                         "why": why, "reason": p.get("reason", ""), "id": len(st["trades"]) + 1,
                         "div_hkd": round(div * fxm, 2), "entry_at_open": p.get("entry_at_open", False),
                         "setup": p.get("setup", "其他"), "ctx": p.get("ctx", {}), "order": p.get("order"),
                         "r": round(pnl_local / (p["risk"] * shares), 2) if p.get("risk") else None})
    p["shares"] -= shares
    if p["shares"] <= 0:
        del st["positions"][ticker]


def _corporate_actions(st, m, d, bars, fx, entries):
    """Dividends and splits with this session as ex-date, for positions held before the open."""
    fxm = fx if m == "us" else 1.0
    for t, p in list(st["positions"].items()):
        if p["market"] != m:
            continue
        div, split = bars.actions(t, d)
        if split and split != 1:
            p["shares"] = int(round(p["shares"] * split))
            for k in ("cost", "stop", "target", "risk"):
                if p.get(k) is not None:
                    p[k] = p[k] / split
            for i, o in st["orders"].items():
                e = next((x for x in entries if str(x["id"]) == i), None)
                if o["status"] == "pending" and e and e.get("ticker") == t:
                    o["status"], o["note"] = "cancelled", "拆股,舊價錢唔再啱,要重新落單"
            st.setdefault("dividends", []).append({"date": d.isoformat(), "ticker": t, "kind": "split", "ratio": split})
        if div:
            net = p["shares"] * div * (1 - DIV_TAX[m])
            p["div"] = p.get("div", 0.0) + net
            st["cash"] += net * fxm
            st.setdefault("dividends", []).append({"date": d.isoformat(), "ticker": t, "kind": "div", "per_share": div,
                                                   "shares": p["shares"], "net_hkd": round(net * fxm, 2)})


def _register(st, e, open_t):
    """An entry placed before this session's open (and after the previous processed open)."""
    kind = e["type"]
    if kind in ("buy", "sell"):
        st["orders"][str(e["id"])] = {"status": "pending", "seen": 0, "note": ""}
    elif kind == "cancel":
        o = st["orders"].get(str(e["order"]))
        if o and o["status"] == "pending":
            o["status"], o["note"] = "cancelled", e.get("reason", "")
    elif kind == "exit":
        p = st["positions"].get(e["ticker"])
        if p:
            if e.get("stop") is not None:
                p["stop"] = e["stop"]
            if e.get("target") is not None:
                p["target"] = e["target"]
            if e.get("clear_target"):
                p.pop("target", None)


def step(st: dict, session: tuple, entries: list[dict], bars: Bars, final: bool, closed: bool = False) -> None:
    """Apply one session to the account. `final` = the session has finished (missing bars count);
    `closed` = the exchange did not trade that day after all (only take in new entries)."""
    open_t, m, d = session
    prev = parse_t(st["through"])
    for e in entries:
        t = parse_t(e["t"])
        if prev < t < open_t:
            _register(st, e, open_t)
    st["through"] = iso(open_t)
    if closed:
        return
    fx = bars.fx(d)
    _corporate_actions(st, m, d, bars, fx, entries)
    by_id = {str(e["id"]): e for e in entries if e["type"] in ("buy", "sell")}
    active = [(by_id[i], o) for i, o in st["orders"].items()
              if o["status"] == "pending" and i in by_id and market_of(by_id[i]["ticker"]) == m]
    active.sort(key=lambda x: (x[0]["side"] != "sell", x[0]["id"]))       # sells first, then buys
    for e, o in active:
        b = bars.get(e["ticker"], d)
        if b is None:
            if final:
                o["seen"] += 1
                if o["seen"] >= e.get("days", 1):
                    o["status"], o["note"] = "expired", "冇成交數據(可能停牌)"
            continue
        lim = e.get("limit")
        if e["side"] == "buy":
            px = b["o"] * (1 + SLIP[m]) if lim is None else _limit_buy(b, lim)
            if px is not None:
                at_open = lim is None or px == b["o"]      # filled at the open: the whole day's range came after
                ok, note = _fill_buy(st, {**e, "lot": e.get("lot", 1), "at_open": at_open}, px, m, d, fx, f"單號 {e['id']}")
                o["status"], o["note"] = ("filled", note) if ok else ("rejected", note)
                continue
        else:
            p = st["positions"].get(e["ticker"])
            if not p:
                o["status"], o["note"] = "rejected", "冇持倉(可能已經止蝕或到目標)"
                continue
            px = b["o"] * (1 - SLIP[m]) if lim is None else _limit_sell(b, lim)
            if px is not None:
                n = min(e["shares"], p["shares"])
                _fill_sell(st, e["ticker"], n, px, m, d, fx, f"單號 {e['id']}")
                o["status"] = "filled"
                if n < e["shares"]:
                    o["note"] = f"只持有 {n} 股,全部沽出"
                continue
        o["seen"] += 1
        if o["seen"] >= e.get("days", 1):
            o["status"], o["note"] = "expired", "未到限價"
    for t, p in list(st["positions"].items()):
        if p["market"] != m:
            continue
        b = bars.get(t, d)
        if b is None:
            continue
        stop, target = p.get("stop"), p.get("target")
        if stop is not None and b["l"] <= stop:
            _fill_sell(st, t, p["shares"], min(b["o"], stop) * (1 - SLIP[m]), m, d, fx, "止蝕")
        elif target is not None and _limit_sell(b, target) is not None:
            _fill_sell(st, t, p["shares"], _limit_sell(b, target), m, d, fx, "到目標")


def _limit_buy(b: dict, lim: float):
    """Fill price for a limit buy on bar b, or None. Open at/below the limit fills at the open; otherwise
    the low has to go below the limit (a touch is not enough: earlier orders at that price come first)."""
    eps = lim * 1e-6
    if b["o"] <= lim + eps:
        return b["o"]
    return lim if b["l"] < lim - eps else None


def _limit_sell(b: dict, lim: float):
    """Mirror of _limit_buy for limit sells and targets."""
    eps = lim * 1e-6
    if b["o"] >= lim - eps:
        return b["o"]
    return lim if b["h"] > lim + eps else None


def advance(state: dict, entries: list[dict], bars: Bars, now: datetime) -> tuple[dict, dict]:
    """Returns (finished-session state to save, live state including sessions still in progress)."""
    entries = sorted((e for e in entries if parse_t(e["t"]) >= parse_t(state["start"])), key=lambda e: e["t"])
    final = copy.deepcopy(state)
    todo = sessions(parse_t(final["through"]), now)
    i = 0
    while i < len(todo):
        s = todo[i]
        open_t, m, d = s
        if finished_at(m, d) > now:
            break
        if bars.get(INDEX[m], d) is None and now - finished_at(m, d) < timedelta(days=2):
            break                      # Yahoo not caught up yet; keep it for the next run
        # no index bar two days later = the market did not trade that day (e.g. typhoon)
        step(final, s, entries, bars, final=True, closed=bars.get(INDEX[m], d) is None)
        i += 1
    live = copy.deepcopy(final)
    for s in todo[i:]:
        step(live, s, entries, bars, final=False)
    return final, live


# ---------------------------------------------------------------- page view
def names() -> dict:
    out = {}
    for f in STOCK_FILES.values():
        d = read_json(DOCS / f, {})
        out.update({t: s.get("name", t) for t, s in d.get("stocks", {}).items()})
    return out


def valuation(st: dict, bars: Bars, today: date) -> tuple[float, list[dict]]:
    nm = names()
    rows, total = [], st["cash"]
    for t, p in sorted(st["positions"].items()):
        last, when = bars.last_close(t)
        last = last if last is not None else p["cost"]
        fx = bars.fx(today) if p["market"] == "us" else 1.0
        val = p["shares"] * last * fx
        div = p.get("div", 0.0)
        total += val
        rows.append({"ticker": t, "name": nm.get(t, t), "market": p["market"], "shares": p["shares"],
                     "cost": round(p["cost"], 4), "last": round(last, 4), "last_date": when,
                     "value_hkd": round(val, 2), "pnl_hkd": round(((last - p["cost"]) * p["shares"] + div) * fx, 2),
                     "pnl_pct": round((last * p["shares"] + div) / (p["cost"] * p["shares"]) - 1, 4),
                     "div_hkd": round(div * fx, 2), "stop": p.get("stop"), "target": p.get("target"),
                     "opened": p["opened"], "reason": p.get("reason", "")})
    return total, rows


def next_ex_dividend(history: dict, known: str | None, today: date) -> dict | None:
    """Next ex-dividend date for a held stock: Yahoo's own date if it is still ahead, else one year after
    an ex-date in the past year (most companies keep the same calendar). history = {date: per share}."""
    if known and known >= today.isoformat():
        amt = next((v for d, v in sorted(history.items(), reverse=True)), None)
        return {"date": known, "est": False, "per_share": amt}
    guesses = sorted((d + timedelta(days=364), v) for d, v in history.items()
                     if today <= d + timedelta(days=364) <= today + timedelta(days=90))
    return {"date": guesses[0][0].isoformat(), "est": True, "per_share": guesses[0][1]} if guesses else None


def dividend_outlook(tickers: list[str], today: date) -> dict:
    import yfinance as yf
    cal = read_json(DATA / "events.json", {})
    out = {}
    for t in tickers:
        try:
            s = yf.Ticker(t).dividends
            hist = {ts.date(): float(v) for ts, v in s.items() if v > 0} if s is not None else {}
        except Exception as e:
            print(f"  dividends {t}: {e!r}")
            continue
        nxt = next_ex_dividend(hist, (cal.get(t) or {}).get("ex_div"), today)
        if nxt:
            out[t] = nxt
    return out


def benchmark(state: dict, bars: Bars) -> float | None:
    """Half Hang Seng, half S&P 500, each from the open of its first session after the start."""
    start = parse_t(state["start"])
    parts = []
    for m in ("hk", "us"):
        first = next((s for s in sessions(start, datetime.now(UTC) + timedelta(days=1))
                      if s[1] == m and bars.get(INDEX[m], s[2])), None)
        if first is None:
            parts.append(1.0)
            continue
        base = bars.get(INDEX[m], first[2])["o"]
        last, _ = bars.last_close(INDEX[m])
        parts.append(last / base if last else 1.0)
    return state["capital"] * sum(parts) / 2


def stats(live: dict, history: dict) -> dict:
    tr = live["trades"]
    wins = [t for t in tr if t["pnl_hkd"] > 0]
    losses = [t for t in tr if t["pnl_hkd"] <= 0]
    eq = [v["equity"] for _, v in sorted(history.items())]
    peak, mdd = -1e18, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    gain = sum(t["pnl_hkd"] for t in wins)
    loss = -sum(t["pnl_hkd"] for t in losses)
    return {"trades": len(tr), "win_rate": round(len(wins) / len(tr), 3) if tr else None,
            "avg_win": round(sum(t["pnl_pct"] for t in wins) / len(wins), 4) if wins else None,
            "avg_loss": round(sum(t["pnl_pct"] for t in losses) / len(losses), 4) if losses else None,
            "profit_factor": round(gain / loss, 2) if loss > 0 else None, "max_drawdown": round(mdd, 4),
            "realised": round(gain - loss, 2)}


def build_view(final: dict, live: dict, entries: list[dict], bars: Bars, now: datetime,
               outlook: dict | None = None) -> tuple[dict, dict]:
    today = now.astimezone(HKT).date()
    equity, rows = valuation(live, bars, today)
    for r in rows:
        x = (outlook or {}).get(r["ticker"])
        if x:
            r["ex_div"] = {**x, "drop": round(x["per_share"] / r["last"], 4) if x.get("per_share") else None}
    bench = benchmark(final, bars)
    history = dict(final.get("history", {}))
    history[today.isoformat()] = {"equity": round(equity, 2), "bench": round(bench, 2) if bench else None}
    final["history"] = history
    nm = names()
    orders = []
    cancel_asked = {e["order"] for e in entries if e["type"] == "cancel"}
    for e in reversed([e for e in entries if e["type"] in ("buy", "sell")][-40:]):
        o = live["orders"].get(str(e["id"]), {"status": "waiting", "note": "等下一個開市時段"})
        if o["status"] in ("pending", "waiting") and e["id"] in cancel_asked:
            o = {"status": "cancelling", "note": "已要求取消,下一個開市時段生效"}
        orders.append({"id": e["id"], "placed": hkt_str(e["t"]), "ticker": e["ticker"], "name": nm.get(e["ticker"], e["ticker"]),
                       "side": e["side"], "shares": e["shares"], "limit": e.get("limit"), "stop": e.get("stop"),
                       "target": e.get("target"), "days": e.get("days", 1), "status": o["status"],
                       "note": o.get("note", ""), "reason": e.get("reason", ""), "check": e.get("check")})
    controls = [{"placed": hkt_str(e["t"]), "type": e["type"], "ticker": e.get("ticker"), "order": e.get("order"),
                 "stop": e.get("stop"), "target": e.get("target"), "reason": e.get("reason", ""), "check": e.get("check")}
                for e in entries if e["type"] in ("cancel", "exit")][-20:][::-1]
    plans = [{"placed": hkt_str(e["t"]), "text": e["text"]} for e in entries if e["type"] == "plan"][-10:][::-1]
    rv = learn.reviews(entries)
    scored = [{**t, **learn.trade_extras(t, bars), "review": rv.get(t.get("id"), {}).get("text")} for t in live["trades"]]
    view = {"updated": hkt_str(now), "start": hkt_str(final["start"]), "capital": final["capital"],
            "fx": round(bars.fx(today), 4), "cash": round(live["cash"], 2), "equity": round(equity, 2),
            "ret": round(equity / final["capital"] - 1, 4), "bench": round(bench, 2) if bench else None,
            "bench_ret": round(bench / final["capital"] - 1, 4) if bench else None,
            "invested": round(1 - live["cash"] / equity, 4) if equity else 0, "fees": round(live["fees"], 2),
            "positions": rows, "orders": orders, "controls": controls,
            "fills": [{**f, "name": nm.get(f["ticker"], f["ticker"])} for f in live["fills"][-60:][::-1]],
            "trades": [{**t, "name": nm.get(t["ticker"], t["ticker"])} for t in scored[-60:][::-1]],
            "learn": {"overall": learn.overall(scored), "groups": learn.groups(scored),
                      "diagnostics": learn.diagnostics(scored), "min_n": learn.MIN_N, "min_adopt": MIN_ADOPT},
            "lessons": learn.fold_lessons(entries),
            "history": [[k, v["equity"], v["bench"]] for k, v in sorted(history.items())],
            "plans": plans, "stats": stats(live, history), "rules": RULES,
            "dividends": [{**x, "name": nm.get(x["ticker"], x["ticker"])} for x in live.get("dividends", [])][-30:][::-1]}
    return final, view


def refresh_lots(force: bool = False) -> dict:
    lots = read_json(LOTS, {})
    hk = sorted(read_json(DOCS / "data.json", {}).get("stocks", {}))
    fresh = lots.get("_updated", "") >= (date.today() - timedelta(days=7)).isoformat()
    if not force and fresh and all(t in lots for t in hk):
        return lots
    import requests
    codes = ["hk" + t[:-3].zfill(5) for t in hk]
    for i in range(0, len(codes), 60):
        try:
            r = requests.get("https://qt.gtimg.cn/q=" + ",".join(codes[i:i + 60]), timeout=15)
            for line in r.content.decode("gbk", "replace").split(";"):
                f = line.split("~")
                if len(f) > 60 and f[60].isdigit() and int(f[60]) > 0:
                    lots[f[2].lstrip("0").zfill(4) + ".HK"] = int(f[60])
        except Exception as e:  # keep the old table if Tencent is unreachable
            print(f"  lot sizes: {e!r}")
    lots["_updated"] = date.today().isoformat()
    write_json(LOTS, lots)
    return lots


def update(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    try:
        refresh_lots()
    except Exception as e:
        print(f"::warning::lot sizes not refreshed: {e!r}")
    state = read_json(STATE, None) or new_state(now)
    entries = read_json(ORDERS, {"entries": []})["entries"]
    tickers = {e["ticker"] for e in entries if e.get("ticker")} | set(state["positions"]) | set(INDEX.values()) | {"HKD=X"}
    bars = download(sorted(tickers), parse_t(state["start"]).date() - timedelta(days=10))
    final, live = advance(state, entries, bars, now)
    try:
        outlook = dividend_outlook(sorted(live["positions"]), now.astimezone(HKT).date())
    except Exception as e:
        print(f"::warning::dividend dates not fetched: {e!r}")
        outlook = {}
    final, view = build_view(final, live, entries, bars, now, outlook)
    write_json(STATE, final)
    write_json(VIEW, view, compact=True)
    return view


# ---------------------------------------------------------------- Claude's side
def _stocks() -> dict:
    out = {}
    for f in STOCK_FILES.values():
        out.update(read_json(DOCS / f, {}).get("stocks", {}))
    return out


def _open_orders(view: dict) -> list[dict]:
    """Buy/sell orders still waiting to fill, read from the order book itself so that orders placed
    earlier in the same run (not yet in docs/paper.json) count too."""
    status = {o["id"]: o["status"] for o in view.get("orders", [])}
    entries = read_json(ORDERS, {"entries": []})["entries"]
    cancelled = {e["order"] for e in entries if e["type"] == "cancel"}
    return [e for e in entries if e["type"] in ("buy", "sell") and e["id"] not in cancelled
            and status.get(e["id"], "waiting") in ("pending", "waiting")]


def _pending_estimates(view: dict) -> tuple[float, dict, dict]:
    """HKD tied up in open buy orders (total and by ticker), and shares tied up in open sell orders."""
    stocks = _stocks()
    buy, by_ticker, sell = 0.0, {}, {}
    for o in _open_orders(view):
        if o["side"] == "buy":
            fx = view.get("fx", FX_DEFAULT) if market_of(o["ticker"]) == "us" else 1.0
            v = o["shares"] * (o.get("limit") or stocks.get(o["ticker"], {}).get("close", 0)) * fx
            buy += v * 1.003
            by_ticker[o["ticker"]] = by_ticker.get(o["ticker"], 0) + v
        else:
            sell[o["ticker"]] = sell.get(o["ticker"], 0) + o["shares"]
    return buy, by_ticker, sell


def _append(entry: dict) -> dict:
    book = read_json(ORDERS, {"entries": []})
    entry = {"id": max([e["id"] for e in book["entries"]], default=0) + 1, "t": iso(datetime.now(UTC)), **entry}
    book["entries"].append(entry)
    write_json(ORDERS, book)
    return entry


def _reason(r: str) -> str:
    r = " ".join((r or "").split())
    if not 4 <= len(r) <= MAX_REASON:
        raise SystemExit(f"--reason 要 4 至 {MAX_REASON} 字")
    return r


def _context(ticker: str, ref: float, stop) -> dict:
    """What the data said when the order was placed, so closed trades can be grouped later."""
    m = market_of(ticker)
    d = read_json(DOCS / STOCK_FILES[m], {})
    st = d.get("stocks", {}).get(ticker, {})
    th = next((t for t in d.get("themes", []) if t.get("theme") == st.get("theme")), {})
    r = d.get("regime", {})
    rnd = lambda v, n=2: None if v is None else round(v, n)
    return {"market": m, "regime": r.get("label"), "regime_score": rnd(r.get("score"), 0),
            "theme": st.get("theme"), "theme_verdict": th.get("verdict"), "short_score": rnd(st.get("short_score"), 0),
            "long_score": rnd(st.get("long_score"), 0), "rsi": rnd(st.get("rsi"), 0), "ret5": rnd(st.get("ret5"), 4),
            "ret20": rnd(st.get("ret20"), 4), "above_ma50": st.get("above_ma50"), "above_ma200": st.get("above_ma200"),
            "earn_days": st.get("earn_days"), "stop_pct": rnd(stop / ref - 1, 4) if stop else None}


def _correlations(ticker: str, others: list[str]) -> dict:
    """60-session daily-return correlation of `ticker` with each of `others`; {} if prices are unavailable."""
    if not others:
        return {}
    try:
        import yfinance as yf
        yf.set_tz_cache_location(tempfile.mkdtemp(prefix="yf_"))
        px = yf.download(sorted({ticker, *others}), period="6mo", auto_adjust=True, progress=False)["Close"]
        r = px.ffill().pct_change().tail(60)
        return {o: float(r[ticker].corr(r[o])) for o in others if o in r and ticker in r}
    except Exception as e:
        print(f"(相關系數計唔到,今次略過呢項檢查:{e!r})")
        return {}


def place_order(ticker: str, side: str, shares: int, limit=None, stop=None, target=None, days: int = 1,
                reason: str = "", setup: str = "其他") -> dict:
    view = read_json(VIEW, None)
    if view is None:
        raise SystemExit("未有 docs/paper.json,等 GitHub Actions 跑一次 update")
    s = _stocks().get(ticker)
    if not s:
        raise SystemExit(f"{ticker} 唔喺股票池(用 brief 入面嘅代號,港股要 .HK)")
    if s.get("suspended"):
        raise SystemExit(f"{ticker} 近 10 日冇成交,唔落單")
    if side not in ("buy", "sell") or shares <= 0 or not 1 <= days <= 5:
        raise SystemExit("side 要 buy/sell,shares > 0,days 1-5")
    m = market_of(ticker)
    close = s["close"]
    lot = 1
    if m == "hk":
        lot = read_json(LOTS, {}).get(ticker)
        if not lot:
            raise SystemExit(f"未知 {ticker} 每手股數,今次唔好買佢")
        if shares % lot:
            raise SystemExit(f"{ticker} 每手 {lot} 股,股數要係 {lot} 嘅倍數")
    if limit is not None and not 0.85 * close <= limit <= 1.15 * close:
        raise SystemExit(f"限價 {limit} 離收市價 {close} 超過 15%")
    reason = _reason(reason)
    pos = {p["ticker"]: p for p in view["positions"]}
    pend_buy, pend_by_ticker, pend_sell = _pending_estimates(view)
    if side == "buy":
        ref = limit or close
        if stop is None or not 0.6 * ref <= stop < ref:
            raise SystemExit(f"買入一定要有止蝕,而且要低過 {ref}(唔可以低過 6 折)")
        if target is not None and target <= ref:
            raise SystemExit("目標價要高過買入價")
        fx = view["fx"] if m == "us" else 1.0
        cost = shares * ref * fx
        held = pos.get(ticker, {}).get("value_hkd", 0) + pend_by_ticker.get(ticker, 0)
        cap = MAX_POSITION * view["equity"]
        if m == "hk" and not held and shares == lot:            # a single board lot of a big-lot stock
            cap = max(cap, min(cost, MAX_ONE_LOT * view["equity"]))
        if held + cost > cap + 1e-6:
            raise SystemExit(f"超過單一股票上限:持有 {held:,.0f} + 新買 {cost:,.0f} 港元 > {cap:,.0f}"
                             f"(資產 {MAX_POSITION:.0%};港股一手最多 {MAX_ONE_LOT:.0%})")
        if cost * 1.003 > view["cash"] - pend_buy:
            raise SystemExit(f"現金唔夠:可用約 {view['cash'] - pend_buy:,.0f} 港元,呢張單約 {cost:,.0f}")
        eq = view["equity"]
        left = view["cash"] - pend_buy - cost * 1.003
        if left < MIN_CASH * eq:
            raise SystemExit(f"要留最少 {MIN_CASH:.0%} 現金:呢張單成交後只剩約 {left:,.0f} 港元")
        risk = shares * (ref - stop) * fx
        if risk > MAX_RISK * eq + 1e-6:
            most = int(MAX_RISK * eq / ((ref - stop) * fx) // lot * lot)
            raise SystemExit(f"風險太大:去到止蝕會蝕約 {risk:,.0f} 港元(資產 {risk / eq:.1%}),上限 {MAX_RISK:.1%};"
                             f"呢個止蝕最多買 {most} 股,或者收窄止蝕")
        exposure = {t: p["value_hkd"] for t, p in pos.items()}
        for t, v in pend_by_ticker.items():
            exposure[t] = exposure.get(t, 0) + v
        if ticker not in exposure and len(exposure) >= MAX_POSITIONS:
            raise SystemExit(f"已經有 {len(exposure)} 隻股票(連未成交買單),上限 {MAX_POSITIONS} 隻")
        stocks = _stocks()
        theme = s.get("theme")
        same = {t: v for t, v in exposure.items() if t != ticker and theme and stocks.get(t, {}).get("theme") == theme}
        if same and sum(same.values()) + exposure.get(ticker, 0) + cost > MAX_THEME * eq:
            raise SystemExit(f"「{theme}」板塊太集中:已有 {', '.join(same)} 共 {sum(same.values()):,.0f} 港元,"
                             f"加呢張單會超過資產 {MAX_THEME:.0%}")
        others = [t for t in exposure if t != ticker]
        corr = {t: c for t, c in _correlations(ticker, others).items() if c >= CORR_LIMIT}
        if corr and sum(exposure[t] for t in corr) + exposure.get(ticker, 0) + cost > MAX_GROUP * eq:
            raise SystemExit("同現有持倉走勢太相似:" + ", ".join(f"{t} 相關 {c:.2f}" for t, c in corr.items())
                             + f",加埋會超過資產 {MAX_GROUP:.0%}")
        check = {"risk_hkd": round(risk, 0), "risk_pct": round(risk / eq, 4), "size_pct": round(cost / eq, 4),
                 "corr": {t: round(c, 2) for t, c in corr.items()}}
    else:
        have = pos.get(ticker, {}).get("shares", 0) - pend_sell.get(ticker, 0)
        if shares > have:
            raise SystemExit(f"可以沽嘅只有 {have} 股")
        stop = target = None
    e = {"type": side, "ticker": ticker, "side": side, "shares": int(shares), "days": int(days), "reason": reason}
    if side == "buy":
        e["check"] = check
    if side == "buy":
        if setup not in SETUPS:
            raise SystemExit(f"--setup 要係 {SETUPS} 其中之一")
        e["setup"] = setup
        e["ctx"] = _context(ticker, limit or close, stop)
    if m == "hk":
        e["lot"] = lot
    for k, v in (("limit", limit), ("stop", stop), ("target", target)):
        if v is not None:
            e[k] = float(v)
    return _append(e)


def cancel(order_id: int, reason: str) -> dict:
    if not any(o["id"] == order_id for o in _open_orders(read_json(VIEW, {}))):
        raise SystemExit(f"單號 {order_id} 唔係等緊成交嘅單")
    return _append({"type": "cancel", "order": order_id, "reason": _reason(reason)})


def set_exit(ticker: str, stop=None, target=None, clear_target=False, reason: str = "") -> dict:
    view = read_json(VIEW, {})
    p = next((p for p in view.get("positions", []) if p["ticker"] == ticker), None)
    if not p:
        raise SystemExit(f"冇持有 {ticker}")
    if stop is not None and not 0.5 * p["last"] <= stop < p["last"]:
        raise SystemExit(f"止蝕要低過現價 {p['last']}")
    if target is not None and target <= p["last"]:
        raise SystemExit(f"目標要高過現價 {p['last']}")
    reason = _reason(reason)
    e = {"type": "exit", "ticker": ticker, "reason": reason}
    if stop is not None:
        vs_cost = stop / p["cost"] - 1
        fx = view.get("fx", FX_DEFAULT) if market_of(ticker) == "us" else 1.0
        if vs_cost < 0 and any(w in reason for w in LOCK_WORDS):
            raise SystemExit(f"理由講鎖定盈利/保本,但新止蝕 {stop} 低過成本 {p['cost']:.2f}(觸發會蝕約 "
                             f"{(p['cost'] - stop) * p['shares'] * fx:,.0f} 港元);改理由或者改止蝕")
        e["check"] = {"stop_vs_cost": round(vs_cost, 4), "pnl_at_stop_hkd": round((stop - p["cost"]) * p["shares"] * fx, 0)}
    if stop is not None:
        e["stop"] = float(stop)
    if target is not None:
        e["target"] = float(target)
    if clear_target:
        e["clear_target"] = True
    return _append(e)


def add_plan(text: str) -> dict:
    n = len("".join(text.split()))
    if not text.strip() or n > MAX_PLAN:
        raise SystemExit(f"策略筆記要有內容,唔多過 {MAX_PLAN} 字(而家 {n})")
    return _append({"type": "plan", "text": text.strip()})


def add_review(trade_id: int, text: str) -> dict:
    view = read_json(VIEW, {})
    if not any(t.get("id") == trade_id for t in view.get("trades", [])):
        raise SystemExit(f"冇第 {trade_id} 號已平倉交易")
    entries = read_json(ORDERS, {"entries": []})["entries"]
    if any(e["type"] == "review" and e["trade"] == trade_id for e in entries):
        raise SystemExit(f"第 {trade_id} 號交易已經檢討過")
    text = " ".join(text.split())
    if not 10 <= len(text) <= MAX_REVIEW:
        raise SystemExit(f"檢討要 10 至 {MAX_REVIEW} 字")
    return _append({"type": "review", "trade": trade_id, "text": text})


def add_lesson(text: str | None, evidence: list[int], status: str | None, lesson_id: str | None, note: str = "") -> dict:
    view = read_json(VIEW, {})
    known = {t.get("id") for t in view.get("trades", [])}
    bad = [i for i in evidence if i not in known]
    if bad:
        raise SystemExit(f"證據入面有唔存在嘅交易:{bad}")
    entries = read_json(ORDERS, {"entries": []})["entries"]
    lessons = {L["id"]: L for L in learn.fold_lessons(entries)}
    if status and status not in ("試行", "採用", "放棄"):
        raise SystemExit("status 要係 試行 / 採用 / 放棄")
    if lesson_id:
        L = lessons.get(lesson_id)
        if not L:
            raise SystemExit(f"冇 {lesson_id} 呢條教訓")
        if status == "採用" and len(set(L["evidence"]) | set(evidence)) < MIN_ADOPT:
            raise SystemExit(f"要有最少 {MIN_ADOPT} 單已平倉交易做證據先可以採用(而家 {len(set(L['evidence']) | set(evidence))} 單)")
        note = " ".join(note.split())
        if not 4 <= len(note) <= MAX_LESSON:
            raise SystemExit(f"--note 要 4 至 {MAX_LESSON} 字,講點解改")
        return _append({"type": "lesson", "lesson": lesson_id, "status": status, "evidence": evidence, "note": note})
    text = " ".join((text or "").split())
    if not 8 <= len(text) <= MAX_LESSON:
        raise SystemExit(f"教訓要 8 至 {MAX_LESSON} 字")
    if status == "採用":
        raise SystemExit("新教訓一定由 試行 開始")
    active = [L for L in lessons.values() if L["status"] != "放棄"]
    if len(active) >= 12:
        raise SystemExit("已經有 12 條生效教訓,先放棄或者合併舊嘅")
    return _append({"type": "lesson", "text": text, "status": "試行", "evidence": evidence, "note": note})


def brief() -> str:
    v = read_json(VIEW, None)
    if v is None:
        return "未有 docs/paper.json"
    st = _stocks()
    lots = read_json(LOTS, {})
    pc = lambda x: "–" if x is None else f"{x * 100:+.1f}%"
    lines = [f"# 模擬倉(更新 {v['updated']} HKT,開始 {v['start']})",
             f"資產 {v['equity']:,.0f} 港元({pc(v['ret'])});基準(一半恒指一半標普){pc(v['bench_ret'])};"
             f"現金 {v['cash']:,.0f};已投資 {v['invested'] * 100:.0f}%;累計費用 {v['fees']:,.0f};USD/HKD {v['fx']}",
             f"規則:{v['rules']}",
             f"統計:{json.dumps(v['stats'], ensure_ascii=False)}", "", "## 持倉"]
    for p in v["positions"] or []:
        s = st.get(p["ticker"], {})
        lines.append(f"- {p['name']} {p['ticker']} {p['shares']}股 成本{p['cost']:.2f} 現價{p['last']} ({p['last_date']}) "
                     f"盈虧{p['pnl_hkd']:+,.0f}港元 {pc(p['pnl_pct'])} 止蝕{p['stop']} 目標{p['target']} 開倉{p['opened']} "
                     f"| 系統短炒計劃{json.dumps(s.get('plan', {}).get('short'), ensure_ascii=False)} RSI{s.get('rsi', 0):.0f} "
                     f"業績{s.get('earnings') or '–'}{'(估)' if s.get('earnings_est') else ''}"
                     + (f" 已收股息{p['div_hkd']:,.0f}港元" if p.get("div_hkd") else "")
                     + (f" | 除淨{p['ex_div']['date']}{'(按去年估計)' if p['ex_div']['est'] else ''} 每股約{p['ex_div']['per_share']}"
                        f",除淨日股價約跌{(p['ex_div']['drop'] or 0) * 100:.1f}%,會收返股息但跌幅可能觸發止蝕" if p.get("ex_div") else "")
                     + f" | 買入理由:{p['reason']}")
    if not v["positions"]:
        lines.append("(冇持倉)")
    lines.append("\n## 未成交 / 最近嘅單")
    for o in v["orders"][:15]:
        lines.append(f"- #{o['id']} {o['placed']} {o['side']} {o['name']} {o['ticker']} {o['shares']}股 "
                     f"{'限價' + str(o['limit']) if o['limit'] else '市價'} 止蝕{o['stop']} 目標{o['target']} "
                     f"→ {o['status']} {o['note']}"
                     + (f" | 系統計:止蝕風險{o['check']['risk_hkd']:,.0f}港元({pc(o['check']['risk_pct'])}資產)"
                        f" 佔資產{pc(o['check']['size_pct'])}" if o.get("check") else ""))
    for c in v.get("controls", [])[:8]:
        k = c.get("check") or {}
        what = f"取消 #{c['order']}" if c["type"] == "cancel" else f"{c['ticker']} 改止蝕{c['stop']} 目標{c['target']}"
        lines.append(f"- {c['placed']} {what}"
                     + (f" | 系統計:止蝕對成本{pc(k['stop_vs_cost'])},觸發盈虧約{k['pnl_at_stop_hkd']:+,.0f}港元" if k else "")
                     + f" | 理由:{c['reason']}")
    if v.get("dividends"):
        lines.append("\n## 股息及拆股")
        for d in v["dividends"][:8]:
            lines.append(f"- {d['date']} {d['name']} " + (f"每股{d['per_share']} 淨收{d['net_hkd']:,.0f}港元"
                                                         if d["kind"] == "div" else f"拆股 1 變 {d['ratio']}"))
    lines.append("\n## 最近成交")
    for f in v["fills"][:10]:
        lines.append(f"- {f['date']} {f['side']} {f['name']} {f['shares']}股 @{f['price']} ({f['why']})")
    lines.append("\n## 已平倉")
    for t in v["trades"][:10]:
        lines.append(f"- {t['name']} {t['opened']}→{t['closed']} {pc(t['pnl_pct'])} {t['pnl_hkd']:+,.0f}港元 ({t['why']})")
    todo = [t for t in v["trades"] if t.get("id") and not t.get("review")]
    lines.append("\n## 未檢討嘅已平倉交易(每單都要寫 review)")
    for t in todo:
        c = t.get("ctx") or {}
        lines.append(f"- 交易#{t['id']} {t['name']} {t['ticker']} {t.get('setup')} {t['opened']}→{t['closed']} "
                     f"{pc(t['pnl_pct'])} R={t.get('r')} 平倉:{t['why']} 持有{t.get('held')}日 "
                     f"最高曾賺{pc(t.get('mfe'))} 最多曾蝕{pc(t.get('mae'))} 平倉後5日{pc(t.get('after5'))} | "
                     f"當時:型勢{c.get('regime')} 板塊{c.get('theme_verdict')} 短炒分{c.get('short_score')} "
                     f"RSI{c.get('rsi')} 200天線上{c.get('above_ma200')} 業績{c.get('earn_days')}日 止蝕距離{pc(c.get('stop_pct'))} "
                     f"| 買入理由:{t['reason']}")
    if not todo:
        lines.append("(冇)")
    lines.append("\n## 教訓(採用 = 必須遵守;試行 = 優先考慮;要引用交易號做證據)")
    for L in v.get("lessons", []):
        if L["status"] != "放棄":
            lines.append(f"- {L['id']} [{L['status']}] {L['text']} 證據:{L['evidence']}")
    if not any(L["status"] != "放棄" for L in v.get("lessons", [])):
        lines.append("(未有)")
    lv = v.get("learn") or {}
    if lv:
        o = lv["overall"]
        lines.append(f"\n## 學習統計(全部 {o['n']} 單:勝率 {o['win_rate']} 平均R {o['avg_r']} 盈虧比 {o['profit_factor']};"
                     f"少過 {lv['min_n']} 單嘅組別一律當樣本唔夠)")
        for g in lv["groups"]:
            lines.append(f"- {g['dim']}={g['key']}: {g['n']}單 勝率{g['win_rate']} 平均R{g['avg_r']} t={g['t']} → {g['verdict']}")
        lines += [f"- 檢查:{x}" for x in lv["diagnostics"]]
    now = datetime.now(UTC)
    nxt = {}
    for o, m, d in sessions(now, now + timedelta(days=10)):
        nxt.setdefault(m, hkt_str(o))
    lines.append(f"\n## 下一個開市(HKT):港股 {nxt.get('hk')};美股 {nxt.get('us')}。而家落嘅單喺嗰個時段先生效。")
    lines.append("## 港股每手股數:" + ", ".join(f"{t} {n}" for t, n in sorted(lots.items()) if t != "_updated"))
    return "\n".join(lines)


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update")
    sub.add_parser("brief")
    o = sub.add_parser("order")
    o.add_argument("--ticker", required=True)
    o.add_argument("--side", choices=("buy", "sell"), required=True)
    o.add_argument("--shares", type=int, required=True)
    o.add_argument("--limit", type=float)
    o.add_argument("--stop", type=float)
    o.add_argument("--target", type=float)
    o.add_argument("--days", type=int, default=1)
    o.add_argument("--reason", required=True)
    o.add_argument("--setup", choices=SETUPS, default="其他")
    c = sub.add_parser("cancel")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--reason", required=True)
    x = sub.add_parser("exit")
    x.add_argument("--ticker", required=True)
    x.add_argument("--stop", type=float)
    x.add_argument("--target", type=float)
    x.add_argument("--clear-target", action="store_true")
    x.add_argument("--reason", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--text-file", required=True)
    rv = sub.add_parser("review")
    rv.add_argument("--trade", type=int, required=True)
    rv.add_argument("--text", required=True)
    ls = sub.add_parser("lesson")
    ls.add_argument("--id")
    ls.add_argument("--text")
    ls.add_argument("--status")
    ls.add_argument("--evidence", default="")
    ls.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.cmd == "update":
        v = update()
        print(f"paper: equity {v['equity']:,.0f} HKD ({v['ret'] * 100:+.2f}%), {len(v['positions'])} positions, "
              f"{sum(o['status'] in ('pending', 'waiting') for o in v['orders'])} open orders")
    elif a.cmd == "brief":
        print(brief())
    elif a.cmd == "order":
        e = place_order(a.ticker.upper(), a.side, a.shares, a.limit, a.stop, a.target, a.days, a.reason, a.setup)
        print(f"order #{e['id']} placed {hkt_str(e['t'])} HKT: {e['side']} {e['shares']} {e['ticker']}")
    elif a.cmd == "cancel":
        e = cancel(a.id, a.reason)
        print(f"cancel of #{a.id} recorded (#{e['id']})")
    elif a.cmd == "exit":
        e = set_exit(a.ticker.upper(), a.stop, a.target, a.clear_target, a.reason)
        print(f"exit levels for {e['ticker']} recorded (#{e['id']})")
    elif a.cmd == "review":
        e = add_review(a.trade, a.text)
        print(f"review of trade {a.trade} saved (#{e['id']})")
    elif a.cmd == "lesson":
        ev = [int(x) for x in a.evidence.split(",") if x.strip()]
        e = add_lesson(a.text, ev, a.status, a.id, a.note)
        print(f"lesson entry saved (#{e['id']})")
    else:
        e = add_plan(Path(a.text_file).read_text(encoding="utf-8"))
        print(f"plan note #{e['id']} saved")


if __name__ == "__main__":
    main(sys.argv[1:])
