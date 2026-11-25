"""Weekly automatic maintenance (自動維護): the health check the maintenance routine reads first, the guard
that limits what it may change, and the log shown on the page.

The cloud routine "hkstock 每週自動維護" runs on Saturdays. It fixes bugs, keeps the Python packages
current and makes small robustness improvements, then pushes to main. It never changes how the system
trades, scores stocks or is judged: those rules are registered in research/ and only the owner changes
them, so for those it writes a proposal instead. `guard` enforces the limits in code, and
.github/workflows/maintenance.yml runs it again on every pushed `maint:` commit, together with the tests,
and reverts the push when either fails. This file itself is protected from the routine.

  python -m hkstock.maintenance due                   # RUN or SKIP: was this week's maintenance done?
  python -m hkstock.maintenance check                 # health report to read before changing anything
  python -m hkstock.maintenance guard --base <sha> [--head <sha>]   # changes allowed? (exit 1 if not)
  python -m hkstock.maintenance log --file r.md --status ok|changed|attention --changes N --proposals N
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HKT = timezone(timedelta(hours=8))
REPO = "kelvin1997121/hkstock-quant"
LOG, VIEW = "data/maintenance_log.json", "docs/maintenance.json"

# ---------------------------------------------------------------- what the routine may change
PROTECTED_DIRS = ("research/", "private/", ".github/", "data/", ".claude/")
PROTECTED_FILES = {"hkstock/universe.py", "hkstock/maintenance.py", "tests/test_maintenance.py"}
ALLOWED_DATA = {LOG, VIEW}                      # the routine's own log, inside otherwise protected places
CONST_EXEMPT = {"hkstock/market_days.py", "hkstock/events.py", "hkstock/news.py", "hkstock/data.py"}
MAX_LINES = 400                                 # changed lines outside tests/ and the log
SKIP_MARKS = ("pytest.mark.skip", "pytest.skip(", "pytest.mark.xfail", "pytest.xfail(", "unittest.skip")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def _read(root: Path, path: str, rev: str | None) -> str | None:
    """A file's text at a revision, or in the working tree when rev is None; None when it does not exist."""
    if rev is None:
        p = root / path
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    try:
        return _git(root, "show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return None


def _changes(root: Path, base: str, head: str | None) -> dict[str, tuple[int, int]]:
    """{path: (lines added, lines removed)} between base and head (or the working tree, untracked included)."""
    out: dict[str, tuple[int, int]] = {}
    args = ["diff", "--numstat", base] + ([head] if head else [])
    for line in _git(root, *args).splitlines():
        a, d, path = line.split("\t", 2)
        if " => " in path:                                   # a rename: count it under the new name
            path = re.sub(r"\{(.*) => (.*)\}", r"\2", path).split(" => ")[-1]
        out[path] = (int(a) if a.isdigit() else 0, int(d) if d.isdigit() else 0)
    if head is None:
        for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines():
            text = _read(root, path, None) or ""
            out[path] = (text.count("\n") + 1, 0)
    for path in _git(root, *(["diff", "--name-only", "--diff-filter=D", base] + ([head] if head else []))).splitlines():
        out.setdefault(path, (0, 0))
    return out


def _constants(src: str) -> dict[str, str]:
    """Module-level ALL_CAPS assignments: {name: dump of the assigned value}."""
    out: dict[str, str] = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        names = []
        for t in targets:
            if isinstance(t, ast.Name):
                names.append(t.id)
            elif isinstance(t, ast.Tuple):
                names += [e.id for e in t.elts if isinstance(e, ast.Name)]
        for n in names:
            if n == n.upper() and any(c.isalpha() for c in n):
                out[n] = ast.dump(value)
    return out


def _defs(src: str) -> dict[str, str]:
    """Top-level functions and classes: {name: dump} (line numbers are not part of the dump)."""
    return {n.name: ast.dump(n) for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _requirement_caps(text: str) -> dict[str, bool]:
    out = {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        m = re.match(r"([A-Za-z0-9_.\-]+)", line)
        if m:
            out[m.group(1).lower()] = "<" in line
    return out


def guard(base: str, head: str | None = None, root: Path = ROOT) -> list[str]:
    """Everything wrong with the changes from base to head; an empty list means they are allowed."""
    bad: list[str] = []
    changes = _changes(root, base, head)
    code_changed = tests_added = False
    size = 0
    for path, (added, removed) in sorted(changes.items()):
        if path in ALLOWED_DATA:
            continue
        if path in PROTECTED_FILES or path.startswith(PROTECTED_DIRS):
            bad.append(f"{path}:受保護,自動維護唔可以改")
            continue
        if path.startswith("docs/") and path.endswith(".json"):
            bad.append(f"{path}:系統自動產生嘅數據,自動維護唔可以改")
            continue
        old, new = _read(root, path, base), _read(root, path, head)
        if path.startswith("tests/"):
            if old is not None and new is None:
                bad.append(f"{path}:唔可以刪除測試檔案")
            continue
        size += added + removed
        if path.startswith("hkstock/") and path.endswith(".py"):
            code_changed = True
            if old is not None and new is not None and path not in CONST_EXEMPT:
                try:
                    before, after = _constants(old), _constants(new)
                except SyntaxError as e:
                    bad.append(f"{path}:語法錯誤 {e}")
                    continue
                for name, value in before.items():
                    if name not in after:
                        bad.append(f"{path}:刪除咗常數 {name}(規則、權重同上限唔可以由自動維護改)")
                    elif after[name] != value:
                        bad.append(f"{path}:改咗常數 {name}(規則、權重同上限唔可以由自動維護改)")
        if path == "requirements.txt" and old is not None and new is not None:
            before, after = _requirement_caps(old), _requirement_caps(new)
            for pkg, capped in before.items():
                if capped and not after.get(pkg, False):
                    bad.append(f"requirements.txt:{pkg} 嘅版本上限唔可以拎走")
    # tests: existing tests and fixtures stay exactly as they were; new ones may be added
    n_before = n_after = 0
    for path in sorted(set(_list_tests(root, base)) | set(_list_tests(root, head))):
        old, new = _read(root, path, base), _read(root, path, head)
        try:
            d_old = _defs(old) if old else {}
            d_new = _defs(new) if new else {}
            c_old = _constants(old) if old else {}
            c_new = _constants(new) if new else {}
        except SyntaxError as e:
            bad.append(f"{path}:語法錯誤 {e}")
            continue
        n_before += sum(k.startswith("test_") for k in d_old)
        n_after += sum(k.startswith("test_") for k in d_new)
        for name, dump in d_old.items():
            if d_new.get(name) != dump:
                bad.append(f"{path}:改咗或者刪咗現有嘅 {name}(只可以加新測試)")
        for name, dump in c_old.items():
            if c_new.get(name) != dump:
                bad.append(f"{path}:改咗測試常數 {name}")
        if any(k.startswith("test_") and k not in d_old for k in d_new):
            tests_added = True
        added_lines = set((new or "").splitlines()) - set((old or "").splitlines())
        for mark in SKIP_MARKS:
            if any(mark in line for line in added_lines):
                bad.append(f"{path}:唔可以加 {mark}(唔准略過測試)")
    if n_after < n_before:
        bad.append(f"測試數目減少咗:{n_before} → {n_after}")
    if code_changed and not tests_added:
        bad.append("改咗 hkstock/ 嘅程式但冇加新測試:每個改動都要有測試證明")
    if size > MAX_LINES:
        bad.append(f"改動太大:{size} 行(上限 {MAX_LINES});大改動要寫成建議俾主人決定")
    return bad


def _list_tests(root: Path, rev: str | None) -> list[str]:
    if rev is None:
        return [str(p.relative_to(root)).replace("\\", "/") for p in (root / "tests").glob("*.py")]
    try:
        names = _git(root, "ls-tree", "--name-only", f"{rev}:tests").split()
    except subprocess.CalledProcessError:
        return []
    return [f"tests/{n}" for n in names if n.endswith(".py")]


# ---------------------------------------------------------------- health check
def _age_hours(stamp: str | None, now: datetime) -> float | None:
    if not stamp:
        return None
    t = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
    return round((now - t).total_seconds() / 3600, 1)


def _json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _github(path: str):
    """Public GitHub API without a token (60 requests an hour is plenty); None when unreachable."""
    try:
        import requests
        r = requests.get(f"https://api.github.com/repos/{REPO}{path}", timeout=15,
                         headers={"Accept": "application/vnd.github+json"})
        return r.json() if r.ok else None
    except Exception:
        return None


def check(now: datetime | None = None, root: Path = ROOT, run_tests: bool = True, online: bool = True) -> dict:
    """Facts for the maintenance routine. `problems` are things to fix; `notes` are for the report."""
    from . import ai_tools, market_days
    from .universe import MARKETS
    now = (now or datetime.now(HKT)).astimezone(HKT)
    problems, notes, facts = [], [], {}

    # data freshness (the update runs on weekdays only, so allow a weekend)
    for name, limit in (("data.json", 72), ("us.json", 72), ("paper.json", 72), ("plans.json", 96), ("ai.json", 72)):
        d = _json(root / "docs" / name, {})
        age = _age_hours(d.get("updated") or (d.get("hk") or {}).get("updated"), now)
        facts[f"age_{name}"] = age
        if age is None or age > limit:
            problems.append(f"docs/{name} 已經 {age} 個鐘冇更新(上限 {limit})")
    # stocks Yahoo no longer returns, earnings coverage
    for key, m in MARKETS.items():
        d = _json(root / "docs" / m.out, {})
        stocks = d.get("stocks", {})
        expected = set(m.names()) | set(m.extras)
        missing = sorted(expected - set(stocks))
        susp = sorted(t for t, s in stocks.items() if s.get("suspended"))
        no_earn = sorted(t for t, s in stocks.items() if not s.get("earnings") and t not in m.extras)
        facts[f"{key}_stocks"] = {"expected": len(expected), "present": len(stocks), "missing": missing,
                                  "suspended": susp, "no_earnings": no_earn}
        if missing:
            notes.append(f"{m.name} 有 {len(missing)} 隻冇數據(Yahoo 冇俾):{', '.join(missing[:12])}"
                         "。改股票名單屬建議,唔好自己改 universe.py")
        if len(no_earn) > len(stocks) * 0.2:
            problems.append(f"{m.name} 有 {len(no_earn)} 隻冇業績日期,超過兩成")
    # the Claude routine
    st = ai_tools.routine_status(now)
    facts["routine"] = st
    if st["missed"]:
        problems.append(f"Claude 分析 {st['slot']} 冇做到(上次 {st['last']})")
    # simulated account and the trade-plan record
    p = _json(root / "docs" / "paper.json", {})
    unpriced = [x["ticker"] for x in p.get("positions", []) if not x.get("last_date")]
    if unpriced:
        problems.append(f"模擬倉持倉冇價錢:{', '.join(unpriced)}")
    facts["paper_verdict"] = p.get("verdict")
    pl = _json(root / "docs" / "plans.json", {})
    facts["plans"] = {k: {x: v.get(x) for x in ("days_logged", "plans", "done", "pending")}
                      for k, v in (pl.get("markets") or {}).items()}
    log = _json(root / "data" / "plan_log.json", {})
    for key in ("hk", "us"):
        days = sorted((log.get(key) or {}))
        if now.weekday() < 5 and now.date() - date(2026, 11, 16) > timedelta(days=4) and \
                (not days or (now.date() - date.fromisoformat(days[-1])).days > 4):
            problems.append(f"買賣計劃實測 {key} 超過 4 日冇新紀錄(最後 {days[-1] if days else '冇'})")
    # holidays
    left = (market_days.COVERED_UNTIL - now.date()).days
    facts["holidays_covered_until"] = market_days.COVERED_UNTIL.isoformat()
    if left < 300:
        notes.append(f"假期表只列到 {market_days.COVERED_UNTIL}(剩 {left} 日);政府公布新一年假期之後可以加入 market_days.py")
    # big files
    for f in sorted((root / "data").glob("*.json")):
        mb = f.stat().st_size / 1e6
        if mb > 5:
            notes.append(f"{f.name} 有 {mb:.1f} MB,會令 repo 越嚟越大")
    # Dependabot branches
    try:
        heads = _git(root, "ls-remote", "origin", "refs/heads/dependabot/*").split()
        facts["dependabot"] = [h.replace("refs/heads/", "") for h in heads if h.startswith("refs/heads/")]
    except subprocess.CalledProcessError:
        facts["dependabot"] = None
    if facts["dependabot"]:
        notes.append(f"Dependabot 有 {len(facts['dependabot'])} 個套件更新等緊處理:{', '.join(facts['dependabot'])}")
    # GitHub Actions and issues (public API)
    if online:
        week = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        runs = _github(f"/actions/runs?per_page=100&created=%3E%3D{week}")
        if runs is None:
            notes.append("讀唔到 GitHub Actions 紀錄(網絡限制),今次唔檢查")
        else:
            failed = [r for r in runs.get("workflow_runs", [])
                      if r.get("conclusion") in ("failure", "timed_out", "startup_failure")]
            facts["failed_runs"] = [{"name": r["name"], "at": r["created_at"], "url": r["html_url"]} for r in failed]
            by_name: dict[str, int] = {}
            for r in failed:
                by_name[r["name"]] = by_name.get(r["name"], 0) + 1
            for name, n in by_name.items():
                (problems if n >= 3 else notes).append(f"GitHub Actions「{name}」過去 7 日失敗 {n} 次")
        issues = _github("/issues?state=open&per_page=30")
        if issues is not None:
            facts["open_issues"] = [{"n": i["number"], "title": i["title"]} for i in issues if "pull_request" not in i]
            for i in facts["open_issues"]:
                notes.append(f"未關閉 issue #{i['n']}:{i['title']}")
    # tests
    if run_tests:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = [l for l in r.stdout.splitlines() if l.strip()][-1:] or ["(冇輸出)"]
        warn = sorted({m.group(1) for m in re.finditer(r"(\w+Warning): ", r.stdout)})
        facts["tests"] = {"ok": r.returncode == 0, "summary": tail[0], "warning_types": warn}
        if r.returncode != 0:
            problems.append(f"測試唔合格:{tail[0]}")
        if warn:
            notes.append(f"測試有警告:{', '.join(warn)}(可能係套件將來會改,值得睇吓)")
    return {"checked": now.strftime("%Y-%m-%d %H:%M"), "problems": problems, "notes": notes, "facts": facts}


def render(report: dict) -> str:
    lines = [f"# 系統健康檢查 {report['checked']} HKT", "", "## 要處理"]
    lines += [f"- {x}" for x in report["problems"]] or ["- 冇"]
    lines += ["", "## 留意"] + ([f"- {x}" for x in report["notes"]] or ["- 冇"])
    lines += ["", "## 詳細數據", "```json", json.dumps(report["facts"], ensure_ascii=False, indent=1), "```"]
    return "\n".join(lines)


# ---------------------------------------------------------------- log
def due(now: datetime | None = None, root: Path = ROOT) -> bool:
    """True when no maintenance was logged in the last 4 days (the Sunday run backs up Saturday's)."""
    now = (now or datetime.now(HKT)).astimezone(HKT)
    entries = (_json(root / LOG, {}) or {}).get("entries", [])
    if not entries:
        return True
    last = datetime.strptime(entries[-1]["at"], "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
    return now - last > timedelta(days=4)


def log(text: str, status: str, changes: int, proposals: int, now: datetime | None = None, root: Path = ROOT) -> dict:
    if status not in ("ok", "changed", "attention"):
        raise SystemExit("--status 要係 ok / changed / attention")
    text = text.strip()
    if not 20 <= len(text) <= 3000:
        raise SystemExit(f"報告要 20 至 3000 字(而家 {len(text)})")
    now = (now or datetime.now(HKT)).astimezone(HKT)
    book = _json(root / LOG, {"entries": []}) or {"entries": []}
    entry = {"at": now.strftime("%Y-%m-%d %H:%M"), "status": status, "changes": int(changes),
             "proposals": int(proposals), "text": text}
    book["entries"] = (book.get("entries", []) + [entry])[-52:]
    (root / LOG).parent.mkdir(parents=True, exist_ok=True)
    (root / LOG).write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    view = {"updated": entry["at"], "entries": book["entries"][-12:][::-1]}
    (root / VIEW).write_text(json.dumps(view, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return entry


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("due")
    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    c.add_argument("--no-tests", action="store_true")
    g = sub.add_parser("guard")
    g.add_argument("--base", required=True)
    g.add_argument("--head")
    lg = sub.add_parser("log")
    lg.add_argument("--file", required=True)
    lg.add_argument("--status", required=True)
    lg.add_argument("--changes", type=int, default=0)
    lg.add_argument("--proposals", type=int, default=0)
    a = ap.parse_args(argv)
    if a.cmd == "due":
        print("RUN: 今個星期未做維護" if due() else "SKIP: 過去 4 日已經做咗維護")
    elif a.cmd == "check":
        r = check(run_tests=not a.no_tests)
        print(json.dumps(r, ensure_ascii=False, indent=1) if a.json else render(r))
    elif a.cmd == "guard":
        bad = guard(a.base, a.head)
        if bad:
            print("NOT ALLOWED:\n" + "\n".join(f"- {b}" for b in bad))
            sys.exit(1)
        print("OK")
    else:
        e = log(Path(a.file).read_text(encoding="utf-8"), a.status, a.changes, a.proposals)
        print(f"maintenance log saved {e['at']} ({e['status']})")


if __name__ == "__main__":
    main(sys.argv[1:])
