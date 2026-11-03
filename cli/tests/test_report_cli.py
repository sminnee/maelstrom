"""Tests for ``mael report``: import a scan, list it, and read its findings.

Runs against the isolated notebook root: a real ``GitFileStore`` for the
reports and a real ``state.db`` for the task that references a finding.
"""

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain import task as model
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path
from mael_domain.task_table import SqliteTaskTable

FIXTURES = Path(__file__).parents[2] / "lib" / "domain" / "fixtures" / "reports"
REPORT = "2026-10-27-claude-security"


async def _seed_task() -> None:
    db = open_state_db(get_state_db_path())
    try:
        await db.migrate()
        await model.create(
            SqliteTaskTable(db), project="p", title="Allowlist columns", id="t1"
        )
    finally:
        db.close()


@pytest.fixture
def mael(migrated_notebook):
    asyncio.run(_seed_task())

    def run(*args: str):
        result = CliRunner().invoke(cli, list(args), catch_exceptions=False)
        assert result.exit_code == 0, result.output
        return result.output

    return run


def test_a_finding_lists_the_task_that_references_it(mael):
    out = mael("report", "import", str(FIXTURES / "claude-security"), "-p", "p")
    assert out.strip() == f"reports/{REPORT}"
    assert REPORT in mael("report", "list", "-p", "p")

    mael("task", "update", "t1", "--project", "p", "--ref", f"{REPORT}#F1")

    listing = mael("report", "show", REPORT, "-p", "p")
    assert "3 findings: 1 error, 1 warning, 1 note" in listing
    f1 = next(line for line in listing.splitlines() if "F1" in line)
    assert "t1" in f1
    f2 = next(line for line in listing.splitlines() if "F2" in line)
    assert "t1" not in f2

    detail = mael("report", "show", REPORT, "F1", "-p", "p")
    assert "Search column interpolated into the SELECT list" in detail
    assert "app/tools/search.py:149" in detail
    assert "t1  Allowlist columns" in detail


def test_a_reference_to_no_finding_is_refused(mael):
    mael("report", "import", str(FIXTURES / "claude-security"), "-p", "p")
    result = CliRunner().invoke(
        cli, ["task", "update", "t1", "--project", "p", "--ref", f"{REPORT}#F9"]
    )
    assert result.exit_code == 1
    assert "No finding F9" in result.output


def test_an_empty_ref_clears_the_references(mael):
    mael("report", "import", str(FIXTURES / "claude-security"), "-p", "p")
    mael("task", "update", "t1", "--project", "p", "--ref", f"{REPORT}#F1")
    mael("task", "update", "t1", "--project", "p", "--ref", "")
    detail = mael("report", "show", REPORT, "F1", "-p", "p")
    assert detail.endswith("## Referenced by\n\nNo task.\n")


def test_show_of_an_unknown_finding_is_refused(mael):
    mael("report", "import", str(FIXTURES / "claude-security"), "-p", "p")
    result = CliRunner().invoke(cli, ["report", "show", REPORT, "F9", "-p", "p"])
    assert result.exit_code == 1
    assert "No finding F9" in result.output
