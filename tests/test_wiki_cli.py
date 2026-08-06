"""Tests for the ``mael wiki`` CLI, against an InMemoryStore.

The CLI is exercised via Click's ``CliRunner``. ``wiki_cli._store`` is patched to
return a shared :class:`InMemoryStore`, so no git happens.
"""

import pytest
from click.testing import CliRunner

from maelstrom import wiki as model
from maelstrom import wiki_cli
from maelstrom.task_store import InMemoryStore

PAGE = """---
description: How to publish a package to PyPI
---

# PyPI publication
"""


@pytest.fixture
def store(store, monkeypatch) -> InMemoryStore:
    monkeypatch.setattr(wiki_cli, "_store", lambda: store)
    return store


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
        wiki_cli.wiki, ["update", "dev-patterns/python/pypi", "--content-file", str(src)]
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
        wiki_cli.wiki, ["update", "linting", "--content-file", str(tmp_path / "nope.md")]
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


# --- interaction with the task index ---
#
# The wiki and the task notebook share one git repo, so a wiki write moves the
# store's HEAD. The task index judges its own freshness by comparing its stamp to
# that HEAD, so a wiki write must carry the stamp forward or every later task read
# silently falls back to a full filesystem scan.


class _FakeIndex:
    """Just the HEAD-stamp surface of the task index."""

    def __init__(self, head: str | None) -> None:
        self._head = head

    def head(self) -> str | None:
        return self._head

    def set_head(self, sha: str | None) -> None:
        self._head = sha


class _MovingHeadStore(InMemoryStore):
    """An in-memory store whose ``head()`` advances on every write, like git."""

    def __init__(self) -> None:
        super().__init__()
        self._commits = 0

    def head(self) -> str:
        return f"sha{self._commits}"

    def write(self, key: str, text: str, *, message: str | None = None) -> None:
        super().write(key, text, message=message)
        self._commits += 1


def test_update_carries_a_fresh_task_index_stamp_forward(runner, monkeypatch):
    """A fresh index stays fresh across a wiki write."""
    store = _MovingHeadStore()
    index = _FakeIndex(store.head())  # fresh: stamped at the current HEAD
    monkeypatch.setattr(wiki_cli, "_store", lambda: store)
    monkeypatch.setattr(wiki_cli, "open_index", lambda _s: index)

    result = runner.invoke(
        wiki_cli.wiki, ["update", "linting", "--content-file", "-"], input=PAGE
    )
    assert result.exit_code == 0
    assert index.head() == store.head(), "wiki write left a fresh index reading stale"


def test_update_leaves_a_stale_task_index_stale(runner, monkeypatch):
    """A stale index must not be promoted by a write that never rebuilt it."""
    store = _MovingHeadStore()
    index = _FakeIndex("some-older-sha")  # stale
    monkeypatch.setattr(wiki_cli, "_store", lambda: store)
    monkeypatch.setattr(wiki_cli, "open_index", lambda _s: index)

    result = runner.invoke(
        wiki_cli.wiki, ["update", "linting", "--content-file", "-"], input=PAGE
    )
    assert result.exit_code == 0
    assert index.head() == "some-older-sha"


def test_update_succeeds_when_the_index_cannot_be_opened(runner, monkeypatch):
    """The wiki must not fail because the task-index cache is unavailable."""
    store = _MovingHeadStore()

    def _boom(_s):
        raise OSError("no index")

    monkeypatch.setattr(wiki_cli, "_store", lambda: store)
    monkeypatch.setattr(wiki_cli, "open_index", _boom)

    result = runner.invoke(
        wiki_cli.wiki, ["update", "linting", "--content-file", "-"], input=PAGE
    )
    assert result.exit_code == 0
    assert model.read_page(store, "linting") == PAGE
