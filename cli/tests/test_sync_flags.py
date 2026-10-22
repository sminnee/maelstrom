"""Tests for `mael sync` and the stacking commands, through ``CliRunner``.

Each test drives ``cmd_sync`` and its neighbours with the domain functions
mocked: flag handling, output, and exit codes. The real-git behaviour of the
flags is tested in ``lib/domain/tests/test_worktree_sync.py``.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain.base_store import InMemoryBaseStore
from mael_domain.worktree import SyncResult, TidyBranchResult, WorktreeInfo
from mael_domain.worktree_model import BaseRef, StackTip

# ---------------------------------------------------------------------------
# CLI: cmd_sync via CliRunner (sync_worktree mocked)
# ---------------------------------------------------------------------------


class TestSyncCli:
    """`mael sync` flag handling via CliRunner."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, sync_result):
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.sync_worktree", return_value=sync_result) as mock_sync,
        ):
            result = runner.invoke(cli, ["sync", "myproject.alpha", *args])
        return result, mock_sync

    @pytest.mark.parametrize("pushed", [True, False])
    def test_a_sync_that_pushed_tells_the_orchestrator(self, pushed):
        """The push moved the PR's head; the next read is what tells its agent."""
        synced = SyncResult(
            success=True, branch="feature/work", message="Synced.", pushed=pushed
        )
        with patch("mael_cli.orchestrator_client.tell_orchestrator") as told:
            result, _ = self._run([], synced)
        assert result.exit_code == 0, result.output
        calls = [c.args for c in told.call_args_list]
        expected = (
            "/api/worktrees/refresh",
            {"project": "myproject", "branch": "feature/work"},
        )
        assert calls == ([expected] if pushed else [])

    def test_abort_on_conflict_short_message(self):
        sync_result = SyncResult(
            success=False,
            branch="feature/work",
            message="Rebase of feature/work onto origin/main hit conflicts; "
            "aborted and restored worktree to its previous state.",
            had_conflicts=True,
            aborted=True,
        )
        result, mock_sync = self._run(["--abort"], sync_result)

        assert result.exit_code == 1
        assert "aborted and restored" in result.output
        assert "git rebase --continue" not in result.output
        _, kwargs = mock_sync.call_args
        assert kwargs["abort_on_conflict"] is True

    def test_conflict_without_abort_shows_help(self):
        sync_result = SyncResult(
            success=False,
            branch="feature/work",
            message="CONFLICT",
            had_conflicts=True,
            aborted=False,
            merge_base="abc1234",
            upstream_head="def5678",
        )
        result, _ = self._run([], sync_result)

        assert result.exit_code == 1
        # The multi-line resolution help mentions rebase --continue.
        assert "git rebase --continue" in result.output

    def test_close_empty_branch_no_push_line(self):
        sync_result = SyncResult(
            success=True,
            branch="feature/work",
            message="feature/work is empty (merged into origin/main); "
            "deleted branch (local + remote) and closed worktree.",
            closed=True,
            deleted_remote=True,
        )
        result, mock_sync = self._run(["--close"], sync_result)

        assert result.exit_code == 0
        assert "closed worktree" in result.output
        assert "Pushed" not in result.output
        _, kwargs = mock_sync.call_args
        assert kwargs["close_if_empty"] is True

    def test_close_non_empty_branch_normal_push(self):
        sync_result = SyncResult(
            success=True,
            branch="feature/work",
            message="Successfully rebased feature/work onto origin/main",
            closed=False,
            pushed=True,
            push_message="Pushed feature/work to origin",
        )
        result, _ = self._run(["--close"], sync_result)

        assert result.exit_code == 0
        assert "Pushed feature/work to origin" in result.output

    def test_a_refused_push_fails_the_command(self):
        """The rebase landed, but a caller reading the exit code must not read
        the branch as published. The CLI passes no token: an agent keeps the
        token it inherits."""
        sync_result = SyncResult(
            success=True,
            branch="feature/work",
            message="Successfully rebased feature/work onto origin/main",
            push_failed=True,
            push_message="Push failed: Permission denied",
        )
        result, mock_sync = self._run([], sync_result)

        assert result.exit_code == 1
        assert "Push failed: Permission denied" in result.stderr
        assert mock_sync.call_args.kwargs.get("token") is None

    def test_abort_and_close_on_conflict_aborts_cleanly(self):
        """--abort --close: a conflict aborts; close/delete is never attempted."""
        sync_result = SyncResult(
            success=False,
            branch="feature/work",
            message="Rebase of feature/work onto origin/main hit conflicts; "
            "aborted and restored worktree to its previous state.",
            had_conflicts=True,
            aborted=True,
        )
        result, mock_sync = self._run(["--abort", "--close"], sync_result)

        assert result.exit_code == 1
        assert "aborted and restored" in result.output
        # closed/deleted never reported; both flags forwarded.
        assert "closed worktree" not in result.output
        _, kwargs = mock_sync.call_args
        assert kwargs["abort_on_conflict"] is True
        assert kwargs["close_if_empty"] is True


class TestSyncNoPush:
    """`mael sync --no-push` — rebase locally, leave the remote alone."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, args):
        rebased = SyncResult(
            success=True,
            branch="feature/work",
            message="Rebased feature/work onto origin/main",
        )
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.sync_worktree", return_value=rebased) as sync,
            patch(
                "mael_cli.cli.sync_worktree_with_autorepair", return_value=rebased
            ) as sync_repair,
            patch("mael_cli.cli.rebase_worktree", return_value=rebased) as squash,
            patch(
                "mael_cli.cli.rebase_worktree_with_autorepair", return_value=rebased
            ) as squash_repair,
        ):
            result = runner.invoke(cli, ["sync", "myproject.alpha", *args])
        return result, (sync, sync_repair, squash, squash_repair)

    def test_no_push_rebases_without_pushing(self):
        result, (sync, sync_repair, squash, squash_repair) = self._run(["--no-push"])

        assert result.exit_code == 0, result.output
        squash.assert_called_once()
        sync.assert_not_called()
        sync_repair.assert_not_called()
        squash_repair.assert_not_called()

    def test_no_push_does_not_autosquash_by_default(self):
        _, (_, _, squash, _) = self._run(["--no-push"])

        _, kwargs = squash.call_args
        assert kwargs["squash"] is False

    def test_squash_no_push_autosquashes(self):
        _, (_, _, squash, _) = self._run(["--squash", "--no-push"])

        _, kwargs = squash.call_args
        assert kwargs["squash"] is True

    def test_no_push_with_autorepair_uses_the_repairing_rebase(self):
        result, (sync, sync_repair, squash, squash_repair) = self._run(
            ["--no-push", "--autorepair"]
        )

        assert result.exit_code == 0, result.output
        squash_repair.assert_called_once()
        squash.assert_not_called()
        sync.assert_not_called()
        sync_repair.assert_not_called()

    def test_pushing_stays_the_default(self):
        result, (sync, _, squash, _) = self._run([])

        assert result.exit_code == 0, result.output
        sync.assert_called_once()
        squash.assert_not_called()

    def test_the_pre_push_check_runs_by_default(self):
        _, (sync, _, _, _) = self._run([])

        assert sync.call_args.kwargs["pre_push"] is True

    def test_skip_pre_push_reaches_the_sync(self):
        _, (sync, _, _, _) = self._run(["--skip-pre-push"])

        assert sync.call_args.kwargs["pre_push"] is False

    def test_skip_pre_push_reaches_the_repairing_sync(self):
        _, (_, sync_repair, _, _) = self._run(["--autorepair", "--skip-pre-push"])

        assert sync_repair.call_args.kwargs["pre_push"] is False

    def test_no_push_with_close_is_a_usage_error(self):
        """--close deletes the remote branch, which is a push."""
        result, (_, _, squash, _) = self._run(["--no-push", "--close"])

        assert result.exit_code != 0
        assert "--no-push cannot be combined with --close" in result.output
        squash.assert_not_called()


class TestSyncBaseCli:
    """`mael sync --base` — set, change, and clear a branch's base."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, *, bases=None, branch="feature/work"):
        """Invoke `mael sync` with an in-memory base store."""
        store = InMemoryBaseStore()
        for child, parent in (bases or {}).items():
            store.write(child, BaseRef(branch=parent))
        sync_result = SyncResult(
            success=True,
            branch=branch,
            message=f"Successfully rebased {branch} onto origin/main",
        )
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.get_current_branch", return_value=branch),
            patch("mael_cli.cli.check_base_exists"),
            patch("mael_cli.cli.sync_worktree", return_value=sync_result),
        ):
            result = runner.invoke(cli, ["sync", "myproject.alpha", *args])
        return result, store

    def test_base_sets_the_base_before_syncing(self):
        result, store = self._run(["--base", "feat/parent"])

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef(branch="feat/parent")

    def test_changing_the_base_clears_the_stale_tip(self):
        """The old tip points into the old base's history; keeping it would misreplay."""
        store = InMemoryBaseStore()
        store.write("feature/work", BaseRef(branch="feat/one", tip="abc123"))
        sync_result = SyncResult(success=True, branch="feature/work", message="ok")
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.get_current_branch", return_value="feature/work"),
            patch("mael_cli.cli.check_base_exists"),
            patch("mael_cli.cli.sync_worktree", return_value=sync_result),
        ):
            result = runner.invoke(cli, ["sync", "--base", "feat/two"])

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef(branch="feat/two", tip=None)

    def test_base_main_clears_the_base(self):
        result, store = self._run(
            ["--base", "main"], bases={"feature/work": "feat/parent"}
        )

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef()
        assert store.all() == {}

    def test_self_base_exits_one_with_the_validation_message(self):
        result, store = self._run(["--base", "feature/work"])

        assert result.exit_code == 1
        assert "cannot be based on itself" in result.output
        assert store.all() == {}

    def test_a_cyclic_base_exits_one(self):
        result, store = self._run(
            ["--base", "feat/a"], bases={"feat/a": "feature/work"}
        )

        assert result.exit_code == 1
        assert "Cycle" in result.output
        # The rejected base is not written, so the store is left as it was.
        assert store.all() == {"feat/a": "feature/work"}

    def test_a_base_that_does_not_exist_exits_one(self):
        """A typo must be refused, not accepted and silently collapsed later."""
        store = InMemoryBaseStore()
        sync_result = SyncResult(success=True, branch="feature/work", message="ok")
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.get_current_branch", return_value="feature/work"),
            patch(
                "mael_cli.cli.check_base_exists",
                side_effect=ValueError("No such branch to stack on: feat/typo."),
            ),
            patch("mael_cli.cli.sync_worktree", return_value=sync_result),
        ):
            result = runner.invoke(cli, ["sync", "--base", "feat/typo"])

        assert result.exit_code == 1
        assert "No such branch to stack on" in result.output
        assert store.all() == {}

    def test_no_base_flag_leaves_the_store_untouched(self):
        """A plain `mael sync` must not write anything."""
        result, store = self._run([], bases={"feature/work": "feat/parent"})

        assert result.exit_code == 0
        assert store.read("feature/work") == BaseRef(branch="feat/parent")


class TestBaseCli:
    """`mael base` — show the current branch's base."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.worktree = "alpha"
        mock_ctx.project = "myproject"
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, bases, branch="feature/work"):
        store = InMemoryBaseStore()
        for child, parent in bases.items():
            store.write(child, BaseRef(branch=parent))
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.get_current_branch", return_value=branch),
        ):
            return runner.invoke(cli, ["base"]), store

    def test_shows_the_stacked_base(self):
        result, _ = self._run({"feature/work": "feat/parent"})

        assert result.exit_code == 0
        assert "feat/parent" in result.output

    def test_an_unstacked_branch_shows_main(self):
        result, _ = self._run({})

        assert result.exit_code == 0
        assert "main" in result.output


class TestStackTipCli:
    """`mael stack-tip` — show or move where new work stacks."""

    def _ctx(self, project_path=None):
        mock_ctx = MagicMock()
        mock_ctx.project = "myproject"
        mock_ctx.project_path = project_path or MagicMock()
        mock_ctx.project_path.exists.return_value = True
        return mock_ctx

    def _run(self, args, *, tip="main", bases=None):
        store = InMemoryBaseStore()
        store.write_stack_tip(tip)
        for child, parent in (bases or {}).items():
            store.write(child, BaseRef(branch=parent))
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.check_base_exists"),
            patch("mael_cli.cli.current_stack_tip", return_value=StackTip(tip)),
        ):
            result = runner.invoke(cli, ["stack-tip", *args])
        return result, store

    def test_with_no_argument_it_shows_the_tip(self):
        result, store = self._run([], tip="feat/parent")

        assert result.exit_code == 0
        assert "feat/parent" in result.output
        assert store.read_stack_tip() == "feat/parent"

    def test_it_moves_the_tip_to_a_named_branch(self):
        result, store = self._run(["feat/other"], tip="feat/parent")

        assert result.exit_code == 0
        assert store.read_stack_tip() == "feat/other"

    def test_a_tip_that_does_not_exist_exits_one(self):
        """A tip naming no branch would be healed back to main at the next add."""
        store = InMemoryBaseStore()
        store.write_stack_tip("main")
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch(
                "mael_cli.cli.check_base_exists",
                side_effect=ValueError("No such branch to stack on: feat/typo."),
            ),
        ):
            result = runner.invoke(cli, ["stack-tip", "feat/typo"])

        assert result.exit_code == 1
        assert store.read_stack_tip() == "main"

    def test_main_resets_the_tip_to_the_bottom(self):
        """The one-command escape from a deepening stack."""
        result, store = self._run(["main"], tip="feat/parent")

        assert result.exit_code == 0
        assert store.read_stack_tip() == "main"


class TestPromoteAndEjectCli:
    """`mael promote` and `mael eject` — the urgent-PR escape hatches."""

    def _ctx(self):
        mock_ctx = MagicMock()
        mock_ctx.project = "myproject"
        mock_ctx.worktree = "alpha"
        mock_ctx.project_path = MagicMock()
        mock_ctx.project_path.exists.return_value = True
        mock_ctx.worktree_path = MagicMock()
        mock_ctx.worktree_path.exists.return_value = True
        return mock_ctx

    def _run(self, command, args, bases, branch="feat/urgent"):
        store = InMemoryBaseStore()
        for child, parent in bases.items():
            store.write(child, BaseRef(branch=parent))
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.get_current_branch", return_value=branch),
        ):
            result = runner.invoke(cli, [command, *args])
        return result, store

    def test_promote_moves_the_branch_to_the_bottom(self):
        """Merge order is enforced bottom-up, so an urgent PR must be able to jump."""
        result, store = self._run(
            "promote", [], {"feat/urgent": "feat/parent", "feat/parent": "main"}
        )

        assert result.exit_code == 0
        assert store.read("feat/urgent") == BaseRef()

    def test_promote_repoints_the_branches_that_were_based_on_it(self):
        """Otherwise a child would be stacked on a branch that left from under it."""
        result, store = self._run(
            "promote",
            [],
            {"feat/urgent": "feat/parent", "feat/child": "feat/urgent"},
        )

        assert result.exit_code == 0
        assert store.read("feat/child").branch == "feat/parent"

    def test_promote_clears_a_repointed_childs_stale_tip(self):
        store = InMemoryBaseStore()
        store.write("feat/urgent", BaseRef(branch="feat/parent"))
        store.write("feat/child", BaseRef(branch="feat/urgent", tip="abc123"))
        runner = CliRunner()
        with (
            patch("mael_cli.cli.resolve_context", return_value=self._ctx()),
            patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
            patch("mael_cli.cli.get_current_branch", return_value="feat/urgent"),
        ):
            result = runner.invoke(cli, ["promote"])

        assert result.exit_code == 0
        assert store.read("feat/child").tip is None

    def test_promote_on_an_unstacked_branch_is_a_no_op(self):
        result, store = self._run("promote", [], {})

        assert result.exit_code == 0
        assert store.all() == {}

    def test_eject_unstacks_the_branch_and_leaves_the_rest_alone(self):
        """Eject is promote without the re-point: pull one branch out, touch nothing else."""
        result, store = self._run(
            "eject", [], {"feat/urgent": "feat/parent", "feat/child": "feat/urgent"}
        )

        assert result.exit_code == 0
        assert store.read("feat/urgent") == BaseRef()
        assert store.read("feat/child").branch == "feat/urgent"


# ---------------------------------------------------------------------------
# sync-all and tidy-branches tell the orchestrator about each branch they pushed
# ---------------------------------------------------------------------------


def _project_ctx(tmp_path: Path) -> MagicMock:
    ctx = MagicMock()
    ctx.project = "myproject"
    ctx.project_path = tmp_path
    return ctx


def _told(told: MagicMock) -> list[tuple]:
    return [c.args for c in told.call_args_list]


def test_sync_all_tells_the_orchestrator_of_each_branch_it_pushed(tmp_path):
    worktrees = [
        WorktreeInfo(path=tmp_path / "myproject-alpha", branch="feat/a", commit="a"),
        WorktreeInfo(path=tmp_path / "myproject-bravo", branch="feat/b", commit="b"),
    ]
    results = [
        SyncResult(success=True, branch="feat/a", message="Pushed.", pushed=True),
        SyncResult(success=True, branch="feat/b", message="Up to date."),
    ]
    store = MagicMock()
    store.all.return_value = {}
    with (
        patch("mael_cli.cli.resolve_context", return_value=_project_ctx(tmp_path)),
        patch("mael_cli.cli.list_worktrees", return_value=worktrees),
        patch("mael_cli.cli.GitConfigBaseStore", return_value=store),
        patch("mael_cli.cli.ensure_fetched", return_value=None),
        patch("mael_cli.cli.sync_worktree", side_effect=results),
        patch("mael_cli.orchestrator_client.tell_orchestrator") as told,
    ):
        result = CliRunner().invoke(cli, ["sync-all", "myproject"])
    assert result.exit_code == 0, result.output
    assert _told(told) == [
        ("/api/worktrees/refresh", {"project": "myproject", "branch": "feat/a"})
    ]


def test_tidy_branches_tells_the_orchestrator_of_each_branch_it_pushed(tmp_path):
    results = [
        TidyBranchResult(branch="feat/a", action="pushed", success=True, message=""),
        TidyBranchResult(branch="feat/b", action="rebased", success=True, message=""),
    ]
    with (
        patch("mael_cli.cli.resolve_context", return_value=_project_ctx(tmp_path)),
        patch("mael_cli.cli.tidy_branches", return_value=results),
        patch("mael_cli.orchestrator_client.tell_orchestrator") as told,
    ):
        result = CliRunner().invoke(cli, ["tidy-branches", "myproject"])
    assert result.exit_code == 0, result.output
    assert _told(told) == [
        ("/api/worktrees/refresh", {"project": "myproject", "branch": "feat/a"})
    ]
