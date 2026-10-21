"""Tests for ``inventory.py``, the section planner of the ``/review-codebase`` skill.

The seam is the script's command line, run on a small git repository: the skill
runs it in a project that has no maelstrom installed.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from git_helpers import run_git, setup_git_repo

from mael_domain.shared_dir import get_shared_dir

SCRIPT = get_shared_dir() / "skills" / "review-codebase" / "inventory.py"


def _write(root: Path, rel: str, lines: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"x{i} = {i}\n" for i in range(lines)))


def _manifest(root: Path, unit: str) -> None:
    (root / unit).mkdir(parents=True, exist_ok=True)
    (root / unit / "pyproject.toml").write_text("")


def _commit(root: Path) -> None:
    run_git(root, "add", "-A")
    run_git(root, "commit", "-qm", "c")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    setup_git_repo(tmp_path)
    return tmp_path


def _run(repo: Path, *args: str, counter: str = "lines") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--counter", counter, *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _inventory(repo: Path, *args: str, counter: str = "lines") -> dict:
    result = _run(repo, *args, counter=counter)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _sections(inventory: dict) -> dict[str, tuple[str, list[str], int]]:
    """Each section as name -> (unit, paths, lines)."""
    return {
        s["name"]: (s["unit"], s["paths"], s["lines"]) for s in inventory["sections"]
    }


class TestSections:
    def test_each_package_gets_its_own_sections_and_a_big_one_splits_by_directory(
        self, repo
    ):
        # api/ is a package of 90 lines: over the cap of 40, so it splits.
        _manifest(repo, "api")
        _write(repo, "api/src/store/a.py", 30)
        _write(repo, "api/src/model/b.py", 30)
        _write(repo, "api/src/cli/c.py", 30)
        # api/plugins/x is a package inside a package: its files are its own.
        _manifest(repo, "api/plugins/x")
        _write(repo, "api/plugins/x/p.py", 20)
        # web/ and bin/ are both small, but they are in two units: never merged.
        (repo / "web").mkdir()
        (repo / "web" / "package.json").write_text("{}")
        _write(repo, "web/src/app.ts", 5)
        _write(repo, "bin/tool.py", 5)
        _commit(repo)

        inventory = _inventory(repo, "--max-lines", "40", "--min-lines", "10")

        assert inventory["units"] == [
            {"path": ".", "lines": 5},
            {"path": "api", "lines": 90},
            {"path": "api/plugins/x", "lines": 20},
            {"path": "web", "lines": 5},
        ]
        assert _sections(inventory) == {
            "api/src/store": ("api", ["api/src/store"], 30),
            "api/src/model": ("api", ["api/src/model"], 30),
            "api/src/cli": ("api", ["api/src/cli"], 30),
            "api/plugins/x": ("api/plugins/x", ["api/plugins/x"], 20),
            "web/src": ("web", ["web/src"], 5),
            "bin": (".", ["bin"], 5),
        }
        assert inventory["total_lines"] == 120
        assert inventory["reviewers"] == 6
        assert inventory["batches"] == 2

    def test_small_siblings_pack_up_to_the_cap_and_are_named_from_their_parent(
        self, repo
    ):
        _write(repo, "pkg/big/a.py", 30)
        _write(repo, "pkg/t1/b.py", 4)
        _write(repo, "pkg/t2/c.py", 4)
        _write(repo, "pkg/t3/deep/d.py", 4)
        _write(repo, "pkg/t4/e.py", 4)
        _write(repo, "pkg/loose.py", 4)
        _commit(repo)

        inventory = _inventory(repo, "--max-lines", "12", "--min-lines", "10")

        assert _sections(inventory) == {
            "pkg/big": (".", ["pkg/big"], 30),
            "pkg/{t1,t2,t3/deep}": (".", ["pkg/t1", "pkg/t2", "pkg/t3/deep"], 12),
            "pkg/{t4,loose.py}": (".", ["pkg/t4", "pkg/loose.py"], 8),
        }

    def test_sections_rank_by_churn_times_size_and_flag_test_only_sections(self, repo):
        _write(repo, "pkg/hot/a.py", 20)
        _write(repo, "pkg/cold/b.py", 20)
        _write(repo, "pkg/tests/test_a.py", 25)
        _commit(repo)
        for i in range(1, 4):
            _write(repo, "pkg/hot/a.py", 20 + i)
            _commit(repo)

        inventory = _inventory(repo, "--max-lines", "40", "--min-lines", "10")

        # hot: 4 commits x 23 lines = 92; tests: 1 x 25; cold: 1 x 20.
        assert [
            (s["name"], s["churn"], s["tests_only"]) for s in inventory["sections"]
        ] == [
            ("pkg/hot", 4, False),
            ("pkg/tests", 1, True),
            ("pkg/cold", 1, False),
        ]

    def test_a_target_directory_scopes_the_inventory(self, repo):
        _manifest(repo, "other")
        _write(repo, "other/x.py", 50)
        _write(repo, "sub/a/one.py", 10)
        _manifest(repo, "sub/pkg")
        _write(repo, "sub/pkg/two.py", 10)
        _commit(repo)

        inventory = _inventory(repo, "sub")

        assert inventory["target"] == "sub"
        assert inventory["total_lines"] == 20
        assert [u["path"] for u in inventory["units"]] == ["sub", "sub/pkg"]


class TestExclusions:
    def test_lockfiles_docs_vendored_and_excluded_paths_are_left_out_and_counted(
        self, repo
    ):
        _write(repo, "src/app.py", 10)
        _write(repo, "uv.lock", 500)
        _write(repo, "README.md", 50)
        _write(repo, "src/vendor/lib.js", 900)
        _write(repo, "src/generated/api.py", 900)
        _write(repo, "src/gen2/deep/api.py", 900)
        _commit(repo)

        inventory = _inventory(
            repo, "--exclude", "src/generated/*", "--exclude", "src/gen2/"
        )

        assert inventory["total_lines"] == 10
        assert inventory["excluded_files"] == {
            "lockfile": 1,
            "docs": 1,
            "vendored or built": 1,
            "--exclude": 2,
        }

    def test_binary_and_empty_files_are_counted_as_left_out(self, repo):
        _write(repo, "src/app.py", 10)
        (repo / "src" / "logo.png").write_bytes(b"\x89PNG\0\0")
        (repo / "src" / "__init__.py").write_text("")
        _commit(repo)

        inventory = _inventory(repo)

        assert inventory["total_lines"] == 10
        assert inventory["excluded_files"] == {"not code": 1, "empty": 1}

    def test_a_package_with_no_counted_code_is_not_a_unit(self, repo):
        _write(repo, "src/app.py", 10)
        (repo / "vendor" / "lib").mkdir(parents=True)
        (repo / "vendor" / "lib" / "composer.json").write_text("{}")
        _write(repo, "vendor/lib/x.php", 100)
        _commit(repo)

        inventory = _inventory(repo)

        assert inventory["units"] == [{"path": ".", "lines": 10}]


class TestLedgerCheck:
    LEDGER_HEADER = (
        "# Codebase review ledger\n\n"
        "Target: `.` — counter `lines` — units `.`, `pkg/c`\n\n"
    )

    def test_names_every_counted_file_that_no_ledger_line_covers(self, repo):
        _write(repo, "pkg/a/one.py", 10)
        _write(repo, "pkg/a/two.py", 10)
        _write(repo, "pkg/ab/five.py", 10)
        _write(repo, "pkg/b/three.py", 10)
        _manifest(repo, "pkg/c")
        _write(repo, "pkg/c/four.py", 10)
        _write(repo, "pkg/README.md", 10)
        _commit(repo)
        ledger = repo / "ledger.md"
        # `.` is the root unit's own section: it covers no other unit's files.
        ledger.write_text(
            self.LEDGER_HEADER
            + "- [ ] a — `pkg/a/one.py`, `pkg/a` — 20 lines — churn 2\n"
            + "- [x] root — `.` — 0 lines\n"
        )

        result = _run(repo, "--check", str(ledger))

        assert result.returncode == 1
        assert json.loads(result.stdout) == {
            "total_lines": 50,
            "uncovered_lines": 10,
            "uncovered": ["pkg/c/four.py"],
        }

    def test_a_path_does_not_cover_a_sibling_that_shares_its_prefix(self, repo):
        _write(repo, "pkg/a/one.py", 10)
        _write(repo, "pkg/ab/two.py", 10)
        _commit(repo)
        ledger = repo / "ledger.md"
        ledger.write_text(self.LEDGER_HEADER + "- [ ] a — `pkg/a` — 10 lines\n")

        result = _run(repo, "--check", str(ledger))

        assert json.loads(result.stdout)["uncovered"] == ["pkg/ab/two.py"]

    def test_passes_when_the_ledger_covers_everything(self, repo):
        _write(repo, "pkg/a/one.py", 10)
        _manifest(repo, "pkg/c")
        _write(repo, "pkg/c/four.py", 10)
        _commit(repo)
        ledger = repo / "ledger.md"
        ledger.write_text(
            self.LEDGER_HEADER
            + "- [ ] a — `pkg` — 10 lines\n- [ ] c — `pkg/c` — 10 lines\n"
        )

        result = _run(repo, "--check", str(ledger))

        assert result.returncode == 0
        assert json.loads(result.stdout)["uncovered"] == []


def test_a_git_failure_reports_its_cause(tmp_path):
    result = _run(tmp_path)

    assert result.returncode != 0
    assert "not a git repository" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.skipif(shutil.which("cloc") is None, reason="cloc is not installed")
def test_cloc_counts_code_lines_and_drops_files_it_cannot_read_as_code(repo):
    (repo / "app.py").write_text("# one\n# two\n\nx = 1\ny = 2\n")
    (repo / "notes.weird").write_text("not\ncode\n")
    _commit(repo)

    inventory = _inventory(repo, counter="cloc")

    assert inventory["total_lines"] == 2
    assert inventory["excluded_files"] == {"not code": 1}
