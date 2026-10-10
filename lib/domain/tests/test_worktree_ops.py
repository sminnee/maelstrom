"""Tests for the sync and environment sequences.

The seam is :mod:`mael_domain.worktree_ops`: which model call each mode and
action makes, in what order, and what a refusal reads like. These are the two
operations the orchestrator offers that are not a teardown, and they are
sequences for the same reason the teardown is — so the scope comes with them.
"""

from pathlib import Path
from unittest.mock import MagicMock

from mael_domain.worktree import SyncResult
from mael_domain.worktree_ops import EnvSteps, SyncSteps, run_env, run_sync

WORKTREE_PATH = Path("/Users/dev/Projects/myproject/myproject-alpha")
PROJECT_PATH = Path("/Users/dev/Projects/myproject")


def sync_steps(**over) -> SyncSteps:
    defaults = dict(
        sync=lambda path, squash, abort, token: SyncResult(
            success=True, branch="feat/x", message="Rebased"
        ),
        autorepair=lambda path, token: SyncResult(
            success=True, branch="feat/x", message="Rebased"
        ),
    )
    return SyncSteps(**{**defaults, **over})


def env_steps(**over) -> EnvSteps:
    defaults = dict(
        start=lambda project, worktree, path, services: [],
        stop=lambda project, worktree, services: [],
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
                autorepair=lambda path, token: (
                    seen.append("autorepair")
                    or SyncResult(success=True, branch="feat/x", message="Rebased")
                ),
                sync=lambda path, squash, abort, token: (
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
                sync=lambda path, squash, abort, token: (
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
                sync=lambda path, squash, abort, token: (
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
                sync=lambda path, squash, abort, token: SyncResult(
                    success=False, branch="feat/x", message="Rebase conflicted"
                )
            ),
        )
        assert not result.ok
        assert result.blocked == "Rebase conflicted"

    async def test_the_push_is_reported(self):
        """The UI shows a push's outcome, as the CLI does."""
        result = await run_sync(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "plain",
            steps=sync_steps(
                sync=lambda path, squash, abort, token: SyncResult(
                    success=True,
                    branch="feat/x",
                    message="Rebased",
                    pushed=True,
                    push_message="Pushed feat/x to origin",
                )
            ),
        )
        assert result.ok
        assert result.messages == [
            "Rebased",
            "Pushed feat/x to origin",
        ]


class TestSyncPush:
    """The push is the orchestrator's: its token, and a refusal it shows."""

    async def test_a_refused_push_is_blocked_with_the_push_message(self):
        """The rebase landed, but the UI must not report the branch as synced."""
        result = await run_sync(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "plain",
            steps=sync_steps(
                sync=lambda path, squash, abort, token: SyncResult(
                    success=True,
                    branch="feat/x",
                    message="Rebased",
                    push_failed=True,
                    push_message="Push failed: denied",
                )
            ),
        )
        assert not result.ok
        assert result.blocked == "Rebased. Push failed: denied"

    async def test_the_token_reaches_every_mode(self):
        seen: list[str | None] = []

        def record(token):
            seen.append(token)
            return SyncResult(success=True, branch="feat/x", message="Rebased")

        for mode in ("plain", "squash", "autorepair"):
            await run_sync(
                "myproject",
                "alpha",
                WORKTREE_PATH,
                PROJECT_PATH,
                mode,
                token="ghp_orch",
                steps=sync_steps(
                    sync=lambda path, squash, abort, token: record(token),
                    autorepair=lambda path, token: record(token),
                ),
            )
        assert seen == ["ghp_orch"] * 3


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
                start=lambda p, w, path, services: order.append("start") or [],
                stop=lambda p, w, services: order.append("stop") or [],
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
                start=lambda p, w, path, services: order.append("start") or [],
                stop=lambda p, w, services: order.append("stop") or [],
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
                start=lambda p, w, path, services: order.append("start") or [],
                stop=lambda p, w, services: order.append("stop") or [],
            ),
        )
        assert order == ["stop", "start"]

    async def test_a_named_start_or_stop_passes_the_service_on(self):
        """The card starts one optional service, not the whole environment."""
        asked: list[tuple[str, list[str] | None]] = []
        steps = env_steps(
            start=lambda p, w, path, services: asked.append(("start", services)) or [],
            stop=lambda p, w, services: asked.append(("stop", services)) or [],
        )
        for action in ("start", "stop"):
            await run_env(
                "myproject",
                "alpha",
                WORKTREE_PATH,
                PROJECT_PATH,
                action,
                service="ladle",
                steps=steps,
            )
        assert asked == [("start", ["ladle"]), ("stop", ["ladle"])]

    async def test_a_start_reports_what_it_brought_up(self):
        result = await run_env(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "start",
            steps=env_steps(
                start=lambda p, w, path, services: ["web: started on 3320"]
            ),
        )
        assert any("web: started" in line for line in result.messages)

    async def test_a_failed_start_is_blocked_rather_than_raised(self):
        def boom(project, worktree, path, services):
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

        def boom(project, worktree, services):
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
