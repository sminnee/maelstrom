"""The notebook root: where `state.db` and the task export live.

A dev build used to write to the production notebook. `uv run mael` runs the
worktree's code, and every root hung off an unconditional `~/.maelstrom`, so a
command run to *test* a feature *performed* it for real. The root is now named,
and there is no fallback.
"""

from pathlib import Path

import pytest

from mael_domain.notebook_root import (
    NOTEBOOK_ROOT_ENV,
    NotebookRootUnset,
    notebook_root,
)


class TestNotebookRootHasNoFallback:
    """No fallback: an unset root is an error, never a guessed directory.

    Mirrors `require_root`. A guessed root reaches the real notebook, which is
    how a planning agent testing `task promote` came to plan a real task.
    """

    def test_an_unset_root_raises(self, monkeypatch):
        monkeypatch.delenv(NOTEBOOK_ROOT_ENV, raising=False)
        with pytest.raises(NotebookRootUnset):
            notebook_root()

    def test_an_empty_root_raises(self, monkeypatch):
        """An empty `.env` value is as unset as an absent one, and reaches here
        as `""` rather than as a missing key."""
        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "")
        with pytest.raises(NotebookRootUnset):
            notebook_root()

    def test_a_tilde_expands(self, monkeypatch):
        """The value is written by hand in `.env`, so `~` reaches it. An
        unexpanded `~` makes a directory named `~` wherever the process ran."""
        monkeypatch.setenv("HOME", "/home/tester")
        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "~/.maelstrom/notebooks/bravo")
        assert notebook_root() == Path("/home/tester/.maelstrom/notebooks/bravo")

    def test_the_refusal_names_the_repair(self, monkeypatch):
        """The message is the only thing a user gets on a machine whose `mael`
        predates the shim, so it must carry the way out."""
        monkeypatch.delenv(NOTEBOOK_ROOT_ENV, raising=False)
        with pytest.raises(NotebookRootUnset) as excinfo:
            notebook_root()
        message = str(excinfo.value)
        assert NOTEBOOK_ROOT_ENV in message
        # The repair itself, not just the variable name: the message could lose
        # every instruction and keep the name, and still pass.
        assert "mael env reset" in message


class TestARefusedCommandWritesNothing:
    """The point of refusing: no write lands anywhere.

    Asserting one absent directory would pass even if the command wrote
    somewhere else entirely, so this compares the whole tree before and after.
    """

    def test_a_refused_promote_leaves_the_tree_untouched(self, monkeypatch, tmp_path):
        from maelstrom.cli import main

        monkeypatch.delenv(NOTEBOOK_ROOT_ENV, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        draft = tmp_path / "draft.md"
        draft.write_text("# A draft\n")
        before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

        assert main(["task", "promote", str(draft)]) == 1

        after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
        assert after == before


class TestTheNotebookPathsFollowTheRoot:
    """`state.db`, the desk and the task export all hang off the one root.

    `get_notebook_path` duplicates the export path deliberately (its docstring
    says why). Both move, or the two answer about different notebooks.
    """

    def test_the_state_db_follows_the_root(self, monkeypatch):
        from mael_domain.state_db.paths import get_state_db_path

        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "/tmp/nb")
        assert get_state_db_path() == Path("/tmp/nb/state.db")

    def test_the_desk_follows_the_root(self, monkeypatch):
        from mael_domain.state_db.paths import get_desk_json_path

        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "/tmp/nb")
        assert get_desk_json_path() == Path("/tmp/nb/desk.json")

    def test_the_notebook_export_follows_the_root(self, monkeypatch):
        from mael_domain.state_db.paths import get_notebook_path

        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "/tmp/nb")
        assert get_notebook_path() == Path("/tmp/nb/tasks")

    def test_the_store_root_follows_the_root(self, monkeypatch):
        from mael_domain.task_store import tasks_root

        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "/tmp/nb")
        assert tasks_root() == Path("/tmp/nb/tasks")

    def test_the_two_task_paths_agree(self, monkeypatch):
        """They resolve the same directory by different routes. Moving one and
        not the other desynchronises the export from its importer."""
        from mael_domain.state_db.paths import get_notebook_path
        from mael_domain.task_store import tasks_root

        monkeypatch.setenv(NOTEBOOK_ROOT_ENV, "/tmp/nb")
        assert get_notebook_path() == tasks_root()
