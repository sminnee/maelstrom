"""Tests for the shared close sequence behind ``mael close`` and the server.

The seam is :func:`maelstrom.worktree_close.close_worktree_fully`: what it does,
in what order, and what it reports. The CLI tests in ``tests/test_cli.py`` cover
only what ``mael close`` prints on top of it.
"""

from pathlib import Path
from unittest.mock import MagicMock

from maelstrom.worktree import CloseResult
from maelstrom.worktree_close import CloseSteps, close_worktree_fully
from maelstrom.worktree_model import CopyBackResult

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
        close=lambda path, force: CloseResult(success=True, message="Closed"),
        close_workspace=lambda project, worktree: False,
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
            close=lambda path, force: (
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
            close=lambda path, force: (
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
            close=lambda path, force: CloseResult(
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
            close=lambda path, force: CloseResult(
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
                close=lambda path, force: (
                    seen.append(force) or CloseResult(success=True, message="Closed")
                )
            ),
        )
        assert seen == [True]

    async def test_without_force_the_close_is_asked_not_to_force(self):
        seen: list[bool] = []
        await run(
            close=lambda path, force: (
                seen.append(force) or CloseResult(success=True, message="Closed")
            )
        )
        assert seen == [False]


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
