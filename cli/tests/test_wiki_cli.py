"""Tests for the ``mael wiki`` CLI, over a real state database.

The CLI is exercised via Click's ``CliRunner``. The suite's autouse fixture
points the notebook root at a temp dir, so each test migrates its own database
through ``migrated_notebook``.
"""

import asyncio

import pytest
from click.testing import CliRunner

from mael_cli import wiki_cli
from mael_domain import wiki as model
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path

PAGE = """---
description: How to publish a package to PyPI
---

# PyPI publication
"""


class Wiki:
    """Seeds and reads the wiki behind the CLI, each call on its own connection."""

    def _run(self, call):
        async def go():
            db = open_state_db(get_state_db_path())
            try:
                return await call(model.SqliteWikiTable(db))
            finally:
                db.close()

        return asyncio.run(go())

    def write(self, page: str, text: str) -> None:
        self._run(lambda table: model.write_page(table, page, text))

    def read(self, page: str) -> str:
        return self._run(lambda table: model.read_page(table, page))


@pytest.fixture
def wiki(migrated_notebook) -> Wiki:
    return Wiki()


async def _create_empty_db() -> None:
    open_state_db(get_state_db_path()).close()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- list ---


def test_a_database_behind_this_build_is_refused(runner):
    """The wiki commands open state.db themselves, so they must not upgrade it."""
    get_state_db_path().parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_create_empty_db())
    result = runner.invoke(wiki_cli.wiki, ["list"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_list_reports_an_empty_wiki(runner, wiki):
    result = runner.invoke(wiki_cli.wiki, ["list"])
    assert result.exit_code == 0
    assert "No wiki pages yet." in result.output


def test_list_prints_paths_and_descriptions(runner, wiki):
    wiki.write("dev-patterns/python/pypi", PAGE)
    result = runner.invoke(wiki_cli.wiki, ["list"])
    assert result.exit_code == 0
    assert "dev-patterns/python/pypi" in result.output
    assert "How to publish a package to PyPI" in result.output


# --- read ---


def test_read_prints_the_raw_page(runner, wiki):
    wiki.write("linting", PAGE)
    result = runner.invoke(wiki_cli.wiki, ["read", "linting"])
    assert result.exit_code == 0
    assert result.output == PAGE


def test_read_fails_cleanly_when_the_page_is_missing(runner, wiki):
    result = runner.invoke(wiki_cli.wiki, ["read", "nope"])
    assert result.exit_code != 0
    assert "Wiki page not found: nope" in result.output


# --- update ---


def test_update_writes_a_page_from_a_file(runner, wiki, tmp_path):
    src = tmp_path / "page.md"
    src.write_text(PAGE)
    result = runner.invoke(
        wiki_cli.wiki,
        ["update", "dev-patterns/python/pypi", "--content-file", str(src)],
    )
    assert result.exit_code == 0
    assert "Wrote wiki page dev-patterns/python/pypi." in result.output
    assert wiki.read("dev-patterns/python/pypi") == PAGE


def test_update_reads_stdin_for_a_dash(runner, wiki):
    result = runner.invoke(
        wiki_cli.wiki, ["update", "linting", "--content-file", "-"], input=PAGE
    )
    assert result.exit_code == 0
    assert wiki.read("linting") == PAGE


def test_update_fails_cleanly_when_the_content_file_is_missing(runner, wiki, tmp_path):
    result = runner.invoke(
        wiki_cli.wiki,
        ["update", "linting", "--content-file", str(tmp_path / "nope.md")],
    )
    assert result.exit_code != 0
    assert "Content file not found" in result.output


def test_update_fails_cleanly_on_an_unsafe_path(runner, wiki):
    result = runner.invoke(
        wiki_cli.wiki, ["update", "../escape", "--content-file", "-"], input="x\n"
    )
    assert result.exit_code != 0
    assert "Invalid wiki page" in result.output
