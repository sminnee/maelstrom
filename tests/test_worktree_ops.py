"""Tests for the sync and environment sequences.

The seam is :mod:`maelstrom.worktree_ops`: which model call each mode and
action makes, in what order, and what a refusal reads like. These are the two
operations the orchestrator offers that are not a teardown, and they are
sequences for the same reason the teardown is — so the scope comes with them.
"""

from pathlib import Path
from unittest.mock import MagicMock

from maelstrom.worktree import SyncResult
from maelstrom.worktree_ops import EnvSteps, SyncSteps, run_env, run_sync

WORKTREE_PATH = Path("/Users/dev/Projects/myproject/myproject-alpha")
PROJECT_PATH = Path("/Users/dev/Projects/myproject")


def sync_steps(**over) -> SyncSteps:
    defaults = dict(
        sync=lambda path, squash, abort: SyncResult(
            success=True, branch="feat/x", message="Rebased"
        ),
        autorepair=lambda path: SyncResult(
            success=True, branch="feat/x", message="Rebased"
        ),
    )
    return SyncSteps(**{**defaults, **over})


def env_steps(**over) -> EnvSteps:
    defaults = dict(
        start=lambda project, worktree, path: [], stop=lambda project, worktree: []
    )
    return EnvSteps(**{**defaults, **over})


class TestSyncModes:
    """One operation with three settings, the three ``mael sync`` has."""

    async def test_autorepair_runs_the_repairing_sync(self):
        seen: list[str] = []
        result = await run_sync(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "autorepair",
            steps=sync_steps(
                autorepair=lambda path: (
                    seen.append("autorepair")
                    or SyncResult(success=True, branch="feat/x", message="Rebased")
                ),
                sync=lambda path, squash, abort: (
                    seen.append("plain")
                    or SyncResult(success=True, branch="feat/x", message="Rebased")
                ),
            ),
        )
        assert seen == ["autorepair"]
        assert result.ok

    async def test_plain_aborts_on_conflict_and_does_not_squash(self):
        seen: list[tuple[bool, bool]] = []
        await run_sync(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "plain",
            steps=sync_steps(
                sync=lambda path, squash, abort: (
                    seen.append((squash, abort))
                    or SyncResult(success=True, branch="feat/x", message="Rebased")
                )
            ),
        )
        assert seen == [(False, True)]

    async def test_squash_autosquashes_and_still_aborts_on_conflict(self):
        """`abort_on_conflict` is implied on both non-repairing modes.

        Only autorepair wants the conflicted tree left standing, because its
        repair session is what reads it.
        """
        seen: list[tuple[bool, bool]] = []
        await run_sync(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "squash",
            steps=sync_steps(
                sync=lambda path, squash, abort: (
                    seen.append((squash, abort))
                    or SyncResult(success=True, branch="feat/x", message="Rebased")
                )
            ),
        )
        assert seen == [(True, True)]

    async def test_a_failed_sync_is_blocked_with_the_model_s_message(self):
        result = await run_sync(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "plain",
            steps=sync_steps(
                sync=lambda path, squash, abort: SyncResult(
                    success=False, branch="feat/x", message="Rebase conflicted"
                )
            ),
        )
        assert not result.ok
        assert result.blocked == "Rebase conflicted"


class TestEnvActions:
    async def test_start_starts_and_never_stops(self):
        order: list[str] = []
        result = await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "start",
            steps=env_steps(
                start=lambda p, w, path: order.append("start") or [],
                stop=lambda p, w: order.append("stop") or [],
            ),
        )
        assert order == ["start"]
        assert result.ok

    async def test_stop_stops_and_never_starts(self):
        order: list[str] = []
        await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "stop",
            steps=env_steps(
                start=lambda p, w, path: order.append("start") or [],
                stop=lambda p, w: order.append("stop") or [],
            ),
        )
        assert order == ["stop"]

    async def test_restart_is_the_two_in_order(self):
        """Restart is stop then start, not a third code path."""
        order: list[str] = []
        await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "restart",
            steps=env_steps(
                start=lambda p, w, path: order.append("start") or [],
                stop=lambda p, w: order.append("stop") or [],
            ),
        )
        assert order == ["stop", "start"]

    async def test_a_start_reports_what_it_brought_up(self):
        result = await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "start",
            steps=env_steps(start=lambda p, w, path: ["web: started on 3320"]),
        )
        assert any("web: started" in line for line in result.messages)

    async def test_a_failed_start_is_blocked_rather_than_raised(self):
        def boom(project, worktree, path):
            raise OSError("port in use")

        result = await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "start",
            steps=env_steps(start=boom),
        )
        assert not result.ok
        assert "port in use" in result.blocked

    async def test_a_restart_whose_stop_fails_does_not_start(self):
        """A half-restart is worse than a refused one: it hides the fault."""
        start = MagicMock(return_value=[])

        def boom(project, worktree):
            raise OSError("will not die")

        result = await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "restart",
            steps=env_steps(stop=boom, start=start),
        )
        assert not result.ok
        start.assert_not_called()
