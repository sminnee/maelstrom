"""Tests for the ``mael task`` CLI, against an InMemoryStore.

The CLI is exercised via Click's ``CliRunner``. ``task_cli._store`` is patched
to return a shared :class:`InMemoryStore` and ``_resolve_project`` to a fixed
project, so no git or cwd resolution happens.
"""

import pytest
from click.testing import CliRunner

from maelstrom import task as model
from maelstrom import task_cli
from maelstrom.task_store import InMemoryStore


@pytest.fixture
def store(monkeypatch) -> InMemoryStore:
    s = InMemoryStore()
    monkeypatch.setattr(task_cli, "_store", lambda: s)
    monkeypatch.setattr(task_cli, "_resolve_project", lambda project: project or "p")
    return s


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# --- add: branch defaulting / override ---


class TestAddBranch:
    def test_branch_defaults_to_id(self, runner, store):
        result = runner.invoke(task_cli.task, ["add", "Smoke"])
        assert result.exit_code == 0, result.output
        new_id = result.output.strip()
        assert model.load(store, "p", new_id).branch == new_id

    def test_branch_override(self, runner, store):
        result = runner.invoke(
            task_cli.task, ["add", "On branch", "--branch", "fix/login"]
        )
        assert result.exit_code == 0, result.output
        new_id = result.output.strip()
        assert model.load(store, "p", new_id).branch == "fix/login"

    def test_command_and_mode_recorded(self, runner, store):
        result = runner.invoke(
            task_cli.task,
            ["add", "Plan it", "--command", "plan-task", "--mode", "plan"],
        )
        assert result.exit_code == 0, result.output
        t = model.load(store, "p", result.output.strip())
        assert t.command == "plan-task"
        assert t.mode == "plan"


# --- next: selection ---


class TestNext:
    def test_no_tasks_errors(self, runner, store):
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code != 0
        assert "No actionable task" in result.output

    def test_prints_first_actionable(self, runner, store):
        a = model.create(store, project="p", title="a")
        model.create(store, project="p", title="b")
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == a.id

    def test_skips_blocked(self, runner, store):
        a = model.create(store, project="p", title="a")
        b = model.create(store, project="p", title="b", follows=[a.id])
        model.move(store, "p", a.id, "done")
        result = runner.invoke(task_cli.task, ["next"])
        assert result.exit_code == 0, result.output
        # a is done (terminal); b is now the next actionable.
        assert result.output.strip() == b.id

    def test_filters_by_parent(self, runner, store):
        parent = model.create(store, project="p", title="parent")
        child = model.create(store, project="p", title="child", parent=parent.id)
        result = runner.invoke(task_cli.task, ["next", "--parent", parent.id])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == child.id
