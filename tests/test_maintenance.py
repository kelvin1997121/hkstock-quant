"""The limits on automatic maintenance (hkstock/maintenance.py). Uses a small throwaway git repo. Offline."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime

import pytest

from hkstock import maintenance as mt

PAPER = 'MAX_RISK = 0.015\nNAME = "x"\n\n\ndef fill(p):\n    return p\n'
TESTS = 'PLAN = {"stop": 95}\n\n\ndef helper():\n    return 1\n\n\ndef test_fill():\n    assert True\n'


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    files = {"hkstock/paper.py": PAPER, "hkstock/market_days.py": "COVERED_UNTIL = 2028\n",
             "hkstock/universe.py": "THEMES = {}\n", "tests/test_paper.py": TESTS,
             "research/PREREG.md": "rule\n", "data/paper_state.json": "{}\n", "docs/data.json": "{}\n",
             "docs/index.html": "<p>page</p>\n", "requirements.txt": "pandas>=2.2,<4\nnumpy>=1.26,<3\n"}
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    return tmp_path


def write(root, path, text):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(text, encoding="utf-8")


def bad(root):
    return mt.guard("HEAD", None, root)


def test_a_bug_fix_with_a_new_test_is_allowed(repo):
    write(repo, "hkstock/paper.py", PAPER.replace("return p", "return max(p, 0)"))
    write(repo, "tests/test_paper.py", TESTS + "\n\ndef test_fill_floors_at_zero():\n    assert True\n")
    write(repo, "docs/index.html", "<p>page, clearer</p>\n")
    assert bad(repo) == []


def test_code_change_needs_a_new_test(repo):
    write(repo, "hkstock/paper.py", PAPER.replace("return p", "return max(p, 0)"))
    assert any("冇加新測試" in b for b in bad(repo))


def test_rules_and_weights_cannot_change(repo):
    write(repo, "hkstock/paper.py", PAPER.replace("0.015", "0.03"))
    write(repo, "tests/test_paper.py", TESTS + "\n\ndef test_new():\n    assert True\n")
    assert any("MAX_RISK" in b for b in bad(repo))
    write(repo, "hkstock/paper.py", PAPER.replace('MAX_RISK = 0.015\n', ""))
    assert any("刪除咗常數 MAX_RISK" in b for b in bad(repo))


def test_holiday_lists_may_be_extended(repo):
    write(repo, "hkstock/market_days.py", "COVERED_UNTIL = 2029\n")
    write(repo, "tests/test_paper.py", TESTS + "\n\ndef test_2029():\n    assert True\n")
    assert bad(repo) == []


def test_protected_places(repo):
    for path in ("research/PREREG.md", "hkstock/universe.py", "data/paper_state.json", "docs/data.json",
                 ".github/workflows/x.yml", "private/holdings.txt", "hkstock/maintenance.py"):
        write(repo, path, "changed\n")
    found = " ".join(bad(repo))
    for path in ("research/PREREG.md", "hkstock/universe.py", "data/paper_state.json", "docs/data.json",
                 ".github/workflows/x.yml", "private/holdings.txt", "hkstock/maintenance.py"):
        assert path in found


def test_own_log_is_allowed(repo):
    write(repo, mt.LOG, '{"entries": []}\n')
    write(repo, mt.VIEW, "{}\n")
    assert bad(repo) == []


def test_existing_tests_and_fixtures_stay_as_they_are(repo):
    write(repo, "tests/test_paper.py", TESTS.replace("assert True", "assert 1"))
    assert any("test_fill" in b for b in bad(repo))
    write(repo, "tests/test_paper.py", TESTS.replace("return 1", "return 2"))
    assert any("helper" in b for b in bad(repo))
    write(repo, "tests/test_paper.py", TESTS.replace('"stop": 95', '"stop": 90'))
    assert any("PLAN" in b for b in bad(repo))
    write(repo, "tests/test_paper.py", TESTS + "\n\n@pytest.mark.skip\ndef test_x():\n    assert True\n")
    assert any("skip" in b for b in bad(repo))
    (repo / "tests/test_paper.py").unlink()
    assert any("刪除測試檔案" in b for b in bad(repo))


def test_upper_version_caps_stay_and_size_is_limited(repo):
    write(repo, "requirements.txt", "pandas>=3.1.1,<4\nnumpy>=2.5\n")
    found = bad(repo)
    assert any("numpy" in b for b in found) and not any("pandas" in b for b in found)
    write(repo, "requirements.txt", "pandas>=2.2,<4\nnumpy>=1.26,<3\n")
    write(repo, "docs/index.html", "<p>x</p>\n" * (mt.MAX_LINES + 5))
    assert any("改動太大" in b for b in bad(repo))


def test_committed_range_is_checked_too(repo):
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    write(repo, "hkstock/paper.py", PAPER.replace("0.015", "0.03"))
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "maint: loosen")
    assert any("MAX_RISK" in b for b in mt.guard(base, "HEAD", repo))


def test_log_and_due(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    sat = datetime(2026, 11, 28, 12, 40, tzinfo=mt.HKT)
    assert mt.due(sat, tmp_path)
    mt.log("【檢查結果】一切正常,冇嘢要改。今次冇改動。", "ok", 0, 0, sat, tmp_path)
    assert not mt.due(datetime(2026, 11, 29, 12, 40, tzinfo=mt.HKT), tmp_path)        # Sunday backup: skip
    assert mt.due(datetime(2026, 12, 5, 12, 40, tzinfo=mt.HKT), tmp_path)             # next Saturday
    view = json.loads((tmp_path / mt.VIEW).read_text(encoding="utf-8"))
    assert view["entries"][0]["status"] == "ok"
    with pytest.raises(SystemExit):
        mt.log("太短", "ok", 0, 0, sat, tmp_path)
    with pytest.raises(SystemExit):
        mt.log("【檢查結果】一切正常,冇嘢要改。", "fine", 0, 0, sat, tmp_path)
