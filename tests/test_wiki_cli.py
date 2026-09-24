"""Tests for the ``mael wiki`` CLI, against an InMemoryStore.

The CLI is exercised via Click's ``CliRunner``. ``wiki_cli._store`` is patched to
return a shared :class:`InMemoryStore`, so no git happens.
"""

import pytest
from click.testing import CliRunner

from mael_domain import wiki as model
from mael_domain.task_store import InMemoryStore
from maelstrom import wiki_cli

PAGE = """---
description: How to publish a package to PyPI
---

# PyPI publication
"""


@pytest.fixture
def store(monkeypatch) -> InMemoryStore:
    """The wiki's own key→text store.

    Built here rather than taken from ``conftest``: the shared ``store``
    fixture is the task *table* now, and the wiki is the one subsystem still
    on :class:`~mael_domain.task_store.GitFileStore`.
    """
    wiki_store = InMemoryStore()
    monkeypatch.setattr(wiki_cli, "_store", lambda: wiki_store)
    return wiki_store


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- list ---


def test_list_reports_an_empty_wiki(runner, store):
    result = runner.invoke(wiki_cli.wiki, ["list"])
    assert result.exit_code == 0
    assert "No wiki pages yet." in result.output


def test_list_prints_paths_and_descriptions(runner, store):
    model.write_page(store, "dev-patterns/python/pypi", PAGE)
    result = runner.invoke(wiki_cli.wiki, ["list"])
    assert result.exit_code == 0
    assert "dev-patterns/python/pypi" in result.output
    assert "How to publish a package to PyPI" in result.output


# --- read ---


def test_read_prints_the_raw_page(runner, store):
    model.write_page(store, "linting", PAGE)
    result = runner.invoke(wiki_cli.wiki, ["read", "linting"])
    assert result.exit_code == 0
    assert result.output == PAGE


def test_read_fails_cleanly_when_the_page_is_missing(runner, store):
    result = runner.invoke(wiki_cli.wiki, ["read", "nope"])
    assert result.exit_code != 0
    assert "Wiki page not found: nope" in result.output


def test_read_fails_cleanly_on_an_unsafe_path(runner, store):
    result = runner.invoke(wiki_cli.wiki, ["read", "../escape"])
    assert result.exit_code != 0
    assert "Invalid wiki page" in result.output


# --- update ---


def test_update_writes_a_page_from_a_file(runner, store, tmp_path):
    src = tmp_path / "page.md"
    src.write_text(PAGE)
    result = runner.invoke(
        wiki_cli.wiki,
        ["update", "dev-patterns/python/pypi", "--content-file", str(src)],
    )
    assert result.exit_code == 0
    assert "Wrote wiki page dev-patterns/python/pypi." in result.output
    assert model.read_page(store, "dev-patterns/python/pypi") == PAGE


def test_update_reads_stdin_for_a_dash(runner, store):
    result = runner.invoke(
        wiki_cli.wiki, ["update", "linting", "--content-file", "-"], input=PAGE
    )
    assert result.exit_code == 0
    assert model.read_page(store, "linting") == PAGE


def test_update_replaces_the_whole_page(runner, store):
    model.write_page(store, "linting", "old\n")
    result = runner.invoke(
        wiki_cli.wiki, ["update", "linting", "--content-file", "-"], input="new\n"
    )
    assert result.exit_code == 0
    assert model.read_page(store, "linting") == "new\n"


def test_update_fails_cleanly_when_the_content_file_is_missing(runner, store, tmp_path):
    result = runner.invoke(
        wiki_cli.wiki,
        ["update", "linting", "--content-file", str(tmp_path / "nope.md")],
    )
    assert result.exit_code != 0
    assert "Content file not found" in result.output


def test_update_fails_cleanly_on_an_unsafe_path(runner, store):
    result = runner.invoke(
        wiki_cli.wiki, ["update", "../escape", "--content-file", "-"], input="x\n"
    )
    assert result.exit_code != 0
    assert "Invalid wiki page" in result.output


def test_update_requires_a_content_file(runner, store):
    result = runner.invoke(wiki_cli.wiki, ["update", "linting"])
    assert result.exit_code != 0
