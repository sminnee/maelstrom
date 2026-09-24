"""Tests for the teardown sequences behind ``mael close``, ``mael remove`` and
the server.

The seams are :func:`mael_domain.worktree_close.close_worktree_fully` and
:func:`mael_domain.worktree_close.remove_worktree_fully`: what each does, in what
order, and what it reports. The CLI tests in ``tests/test_cli.py`` cover only
what the commands print on top of them.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from mael_domain.worktree import CloseResult
from mael_domain.worktree_close import (
    CloseSteps,
    close_worktree_fully,
    remove_worktree_fully,
)
from mael_domain.worktree_model import CopyBackResult

WORKTREE_PATH = Path("/Users/dev/Projects/myproject/myproject-alpha")
PROJECT_PATH = Path("/Users/dev/Projects/myproject")


async def _no_agents(path):
    """The one awaited step, answering that no agent was there."""
    return []


def _recording_stop_agents(order):
    """A `stop_agents` that notes when it ran, so ordering can be asserted."""

    async def stop(path):
        order.append("daemon")
        return ["agent a1: stopped"]

    return stop


def steps(**over) -> CloseSteps:
    """Every collaborator stubbed to do nothing, so a test names only its own."""
    defaults = dict(
        env_status=lambda project, worktree: None,
        stop_env=lambda project, worktree: [],
        stop_agents=_no_agents,
        live_sessions=lambda path: [],
        stop_sessions=lambda sessions: [],
        copy_back=lambda project_path, path: CopyBackResult(),
        close=lambda path, force, discard: CloseResult(success=True, message="Closed"),
        close_workspace=lambda project, worktree: False,
        remove=lambda project_path, folder: None,
        dirty_files=lambda path: [],
    )
    return CloseSteps(**{**defaults, **over})


async def run(**over):
    return await close_worktree_fully(
        "myproject", "alpha", WORKTREE_PATH, PROJECT_PATH, steps=steps(**over)
    )


class TestTheSequence:
    async def test_a_clean_close_succeeds_and_reports_the_close_message(self):
        result = await run()
        assert result.close.success
        assert result.close.message == "Closed"

    async def test_a_running_environment_is_stopped_first(self):
        order: list[str] = []
        result = await run(
            env_status=lambda p, w: [MagicMock(alive=True)],
            stop_env=lambda p, w: order.append("stop_env") or ["web: stopped"],
            close=lambda path, force, discard: (
                order.append("close") or CloseResult(success=True, message="Closed")
            ),
        )
        assert order == ["stop_env", "close"]
        assert any("web: stopped" in line for line in result.messages)

    async def test_no_environment_means_no_stop(self):
        stop = MagicMock(return_value=[])
        await run(env_status=lambda p, w: None, stop_env=stop)
        stop.assert_not_called()

    async def test_a_dead_environment_means_no_stop(self):
        stop = MagicMock(return_value=[])
        await run(env_status=lambda p, w: [MagicMock(alive=False)], stop_env=stop)
        stop.assert_not_called()

    async def test_daemon_agents_stop_before_the_session_pids_are_signalled(self):
        order: list[str] = []
        result = await run(
            stop_agents=_recording_stop_agents(order),
            live_sessions=lambda path: [MagicMock()],
            stop_sessions=lambda sessions: order.append("pids") or ["sess: stopped"],
        )
        assert order == ["daemon", "pids"]
        assert any("agent a1: stopped" in line for line in result.messages)

    async def test_env_vars_are_copied_back_before_the_close(self):
        order: list[str] = []
        await run(
            copy_back=lambda pp, p: order.append("copy_back") or CopyBackResult(),
            close=lambda path, force, discard: (
                order.append("close") or CloseResult(success=True, message="Closed")
            ),
        )
        assert order == ["copy_back", "close"]

    async def test_the_cmux_workspace_is_closed_after_a_successful_close(self):
        result = await run(close_workspace=lambda p, w: True)
        assert any(
            "Closed cmux workspace 'myproject-alpha'" in line
            for line in result.messages
        )


class TestTheCopyBackSplit:
    async def test_the_rescue_is_reported_before_the_close_is_announced(self):
        """``messages_before_copy_back`` is where a caller renders the rescue."""
        result = await run()
        before = result.messages[: result.messages_before_copy_back]
        after = result.messages[result.messages_before_copy_back :]
        assert not any("Closing worktree" in line for line in before)
        assert after[0] == "Closing worktree 'alpha'..."


class TestARefusal:
    async def test_a_refused_close_leaves_the_cmux_workspace_open(self):
        close_workspace = MagicMock(return_value=True)
        result = await run(
            close=lambda path, force, discard: CloseResult(
                success=False,
                message="Worktree has 2 commit(s) not merged to origin/main",
                had_unpushed_commits=True,
            ),
            close_workspace=close_workspace,
        )
        assert not result.close.success
        assert result.close.had_unpushed_commits
        close_workspace.assert_not_called()

    async def test_the_refusal_message_is_the_model_s_own(self):
        result = await run(
            close=lambda path, force, discard: CloseResult(
                success=False, message="Sync failed: could not fetch"
            )
        )
        assert result.close.message == "Sync failed: could not fetch"


class TestForce:
    async def test_force_is_threaded_into_the_close(self):
        seen: list[bool] = []
        await close_worktree_fully(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            force=True,
            steps=steps(
                close=lambda path, force, discard: (
                    seen.append(force) or CloseResult(success=True, message="Closed")
                )
            ),
        )
        assert seen == [True]

    async def test_without_force_the_close_is_asked_not_to_force(self):
        seen: list[bool] = []
        await run(
            close=lambda path, force, discard: (
                seen.append(force) or CloseResult(success=True, message="Closed")
            )
        )
        assert seen == [False]


class TestDiscard:
    async def test_discard_is_threaded_into_the_close(self):
        seen: list[tuple[bool, bool]] = []
        await close_worktree_fully(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            discard=True,
            steps=steps(
                close=lambda path, force, discard: (
                    seen.append((force, discard))
                    or CloseResult(success=True, message="Closed")
                )
            ),
        )
        assert seen == [(False, True)]


class TestWarnings:
    async def test_a_copy_back_result_is_reported_but_never_fails_the_close(self):
        result = await run(
            copy_back=lambda pp, p: CopyBackResult(added={"API_KEY": "x"})
        )
        assert result.close.success
        assert result.copy_back.added == {"API_KEY": "x"}

    async def test_no_project_path_skips_the_copy_back(self):
        copy_back = MagicMock(return_value=CopyBackResult())
        await close_worktree_fully(
            "myproject", "alpha", WORKTREE_PATH, None, steps=steps(copy_back=copy_back)
        )
        copy_back.assert_not_called()


class TestRemove:
    """``remove_worktree_fully`` is the close sequence with a different git step.

    Written as a sequence rather than by hand, it gains the step ``cmd_remove``
    never had: the daemon is asked to stop its agents before any pid is
    signalled. Without it, every remove over a driven agent left a phantom
    ``exited`` row — see ``agent_stop``.
    """

    async def _remove(self, **over):
        return await remove_worktree_fully(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "myproject-alpha",
            steps=steps(**over),
        )

    async def test_the_daemon_is_asked_before_any_pid_is_signalled(self):
        """The defect: a removed worktree must leave no phantom crashed agent."""
        order: list[str] = []
        result = await self._remove(
            stop_agents=_recording_stop_agents(order),
            live_sessions=lambda path: [MagicMock()],
            stop_sessions=lambda sessions: order.append("pids") or ["sess: stopped"],
            remove=lambda project_path, folder: order.append("remove"),
        )
        assert order == ["daemon", "pids", "remove"]
        assert any("agent a1: stopped" in line for line in result.messages)

    async def test_a_running_environment_is_stopped_first(self):
        order: list[str] = []
        await self._remove(
            env_status=lambda p, w: [MagicMock(alive=True)],
            stop_env=lambda p, w: order.append("stop_env") or ["web: stopped"],
            remove=lambda project_path, folder: order.append("remove"),
        )
        assert order == ["stop_env", "remove"]

    async def test_the_worktree_is_removed_by_its_folder_name(self):
        seen: list[tuple[Path, str]] = []
        result = await self._remove(
            remove=lambda project_path, folder: seen.append((project_path, folder))
        )
        assert seen == [(PROJECT_PATH, "myproject-alpha")]
        assert result.close.success

    async def test_nothing_is_rescued_from_the_env(self):
        """The worktree is being deleted, not parked: there is nowhere to go back to."""
        copy_back = MagicMock(return_value=CopyBackResult())
        await self._remove(copy_back=copy_back)
        copy_back.assert_not_called()

    async def test_dirty_files_are_refused_rather_than_destroyed(self):
        """`git worktree remove --force` destroys uncommitted work.

        The CLI has always listed the files and asked first. The server has no
        prompt to fall back on, so the refusal is the model's — a caller that
        means it passes `force`.
        """
        remove = MagicMock()
        result = await self._remove(dirty_files=lambda path: ["src/a.py", "src/b.py"])
        assert not result.close.success
        assert "src/a.py" in result.close.message
        remove.assert_not_called()

    async def test_force_removes_a_dirty_worktree(self):
        seen: list[str] = []
        result = await remove_worktree_fully(
            "myproject",
            "alpha",
            WORKTREE_PATH,
            PROJECT_PATH,
            "myproject-alpha",
            force=True,
            steps=steps(
                dirty_files=lambda path: ["src/a.py"],
                remove=lambda project_path, folder: seen.append(folder),
            ),
        )
        assert result.close.success
        assert seen == ["myproject-alpha"]

    async def test_a_clean_worktree_needs_no_force(self):
        seen: list[str] = []
        result = await self._remove(
            dirty_files=lambda path: [],
            remove=lambda project_path, folder: seen.append(folder),
        )
        assert result.close.success
        assert seen == ["myproject-alpha"]

    async def test_a_failed_removal_is_reported_rather_than_raised(self):
        def boom(project_path, folder):
            raise OSError("git refused")

        result = await self._remove(remove=boom)
        assert not result.close.success
        assert "git refused" in result.close.message

    async def test_no_cmux_workspace_is_closed_when_the_removal_failed(self):
        def boom(project_path, folder):
            raise OSError("git refused")

        close_workspace = MagicMock(return_value=True)
        await self._remove(remove=boom, close_workspace=close_workspace)
        close_workspace.assert_not_called()

    async def test_the_cmux_workspace_is_closed_after_a_successful_removal(self):
        result = await self._remove(close_workspace=lambda p, w: True)
        assert any(
            "Closed cmux workspace 'myproject-alpha'" in line
            for line in result.messages
        )


class TestAgainstARealRepo:
    """The close sequence driven with the real ``close_worktree``.

    Every other test here stubs ``steps.close``, which is the collaborator the
    whole refactor put a scope around — so a fault in that pairing is invisible
    to them by construction. This one runs the real git algorithm inside the
    real sequence, against the real project layout, which is the only place the
    two meet.
    """

    async def test_a_close_finishes_rather_than_waiting_on_its_own_lock(
        self, project_with_worktree
    ):
        """The git step must not hold a scope the algorithm it wraps re-takes.

        ``close_worktree`` syncs, and ``rebase_worktree``'s fetch takes the repo
        scope itself. ``flock`` is per open file description, so a second
        acquire blocks against the first even in one thread: a sequence holding
        the repo scope over the whole git step would wait out ``LOCK_TIMEOUT``
        and then blame a peer process that does not exist.
        """
        project_path, worktree_path, _ = project_with_worktree

        result = await asyncio.wait_for(
            close_worktree_fully(
                "test-repo",
                "alpha",
                worktree_path,
                project_path,
                steps=steps(close=CloseSteps().close),
            ),
            timeout=30,
        )

        assert result.close.success, result.close.message
        assert "lock" not in result.close.message.lower()
