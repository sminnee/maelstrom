"""Tests for maelstrom.session_cli module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from maelstrom import session_cli
from maelstrom import task as model
from maelstrom.cli import cli
from maelstrom.task_index import SqliteTaskIndex, TaskMeta


def _patch_live(sessions):
    """Patch the live-process sweep `session list` drives off."""
    from maelstrom import session_discovery

    swept = list(sessions)

    async def sweep():
        return list(swept)

    return patch.object(session_discovery, "all_live_sessions", sweep)


def _patch_pid_lookup(pid, cwd="/w/alpha"):
    """Patch the single-process lookup the sweep-blind pid fallback uses.

    ``pid=None`` answers "no session for that pid" — a dead pid, or a live one
    that is not a ``claude``. Patched rather than run for real so no test shells
    out to `ps`/`lsof` and depends on what is running on the machine.
    """
    from maelstrom import session_discovery

    found = None if pid is None else _live(pid, cwd)

    async def lookup(_pid):
        return found

    return patch.object(session_discovery, "session_for_pid", lookup)


def _live(pid, cwd):
    from maelstrom.session_discovery import LiveSession

    return LiveSession(pid=pid, cwd=Path(cwd))


class TestSessionList:
    @pytest.fixture(autouse=True)
    def _fresh_index(self, monkeypatch):
        # Default every session-list test to an empty in-memory index so none
        # touches the real on-disk notebook. Tests that assert an index hit
        # override this with their own populated index.
        monkeypatch.setattr(
            session_cli, "_task_index", lambda: SqliteTaskIndex(":memory:")
        )

    def test_empty_when_no_live_processes(self, tmp_path):
        with _patch_live([]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0
        assert "No running Claude Code sessions." in result.output

    def test_live_process_listed(self, tmp_path):
        # The process itself supplies PID and CWD, so a claude mael did not
        # launch still lists.
        with _patch_live([_live(4242, "/w/alpha")]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output
        assert "/w/alpha" in result.output

    def test_task_column_from_session_id_index_lookup(self, tmp_path, monkeypatch):
        # A live session whose --session-id resolves via the task index shows TASK.
        sid = model.session_id_for("askastro", "daily.maintenance.2026-07-03.2")
        sess = _live(4242, "/w/delta")
        sess.session_id = sid
        index = SqliteTaskIndex(":memory:")
        index.upsert(
            TaskMeta(
                project="askastro",
                id="daily.maintenance.2026-07-03.2",
                status="in-progress",
                session_id=sid,
            )
        )
        monkeypatch.setattr(session_cli, "_task_index", lambda: index)
        with _patch_live([sess]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "TASK" in result.output
        assert "daily.maintenance.2026-07-03.2" in result.output

    def test_task_column_blank_for_non_mael_session(self, tmp_path):
        # A bare claude carries no --session-id, so nothing resolves a task.
        with _patch_live([_live(4242, "/w/alpha")]):
            runner = CliRunner()
            result = runner.invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_id_column_shows_the_session_id_prefix(self, tmp_path):
        # The ID column is what makes a listed session addressable: it prints the
        # first 8 characters, which is what `session info <prefix>` takes.
        sess = _live(4242, "/w/alpha")
        sess.session_id = "97894d02-f335-5ea3-9d9f-050330a4902b"
        with _patch_live([sess]):
            result = CliRunner().invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "ID" in result.output
        assert "97894d02" in result.output

    def test_id_column_blank_for_a_bare_claude(self, tmp_path):
        # A session started outside mael carries no --session-id. The ID cell
        # must be empty rather than "None" — the pid is how you name it.
        with _patch_live([_live(4242, "/w/alpha")]):
            result = CliRunner().invoke(cli, ["session", "list"])
        assert result.exit_code == 0, result.output
        assert "None" not in result.output
        row = next(line for line in result.output.splitlines() if "4242" in line)
        header = result.output.splitlines()[0]
        start = header.index("ID")
        assert row[start : start + len("ID")].strip() == ""


class TestSessionInfo:
    """``mael session info [ID]`` — the fields for one session.

    The handle defaults from the environment, so the four cases are the same
    ones ``TestGetStatus`` pins for ``mael task get-status``: explicit argument,
    environment fallback, neither, and an unknown id.
    """

    _SID = "97894d02-f335-5ea3-9d9f-050330a4902b"

    @pytest.fixture(autouse=True)
    def _fresh_index(self, monkeypatch):
        monkeypatch.setattr(
            session_cli, "_task_index", lambda: SqliteTaskIndex(":memory:")
        )
        # A session command must never read the ambient session env of the
        # process running the tests.
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_PID", raising=False)

    def _sess(self, pid=4242, cwd="/w/alpha", session_id=None):
        s = _live(pid, cwd)
        s.session_id = session_id
        return s

    def test_explicit_id_shows_the_session(self, tmp_path):
        with (
            _patch_live([self._sess(session_id=self._SID)]),
        ):
            result = CliRunner().invoke(cli, ["session", "info", self._SID])
        assert result.exit_code == 0, result.output
        assert self._SID in result.output
        assert "4242" in result.output
        assert "/w/alpha" in result.output

    def test_resolves_an_id_prefix(self, tmp_path):
        with (
            _patch_live([self._sess(session_id=self._SID)]),
        ):
            result = CliRunner().invoke(cli, ["session", "info", "97894d02"])
        assert result.exit_code == 0, result.output
        assert self._SID in result.output

    def test_resolves_a_pid(self, tmp_path):
        with _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info", "4242"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_defaults_to_the_live_session_id_from_the_environment(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", self._SID)
        with (
            _patch_live([self._sess(session_id=self._SID)]),
        ):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code == 0, result.output
        assert self._SID in result.output

    def test_falls_back_to_claude_pid_when_the_live_id_does_not_match(
        self, tmp_path, monkeypatch
    ):
        # After a /clear the live id is not the one in argv, so the pid is the
        # only handle that still resolves. Both are tried, in that order.
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cleared-and-unknown-id")
        monkeypatch.setenv("CLAUDE_PID", "4242")
        with (
            _patch_live([self._sess(session_id=self._SID)]),
        ):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_an_explicit_id_never_falls_back_to_the_environment(
        self, tmp_path, monkeypatch
    ):
        # A named session that does not exist is an error, even when the
        # environment could have resolved some other session. Falling back would
        # silently show the wrong session.
        monkeypatch.setenv("CLAUDE_PID", "4242")
        with _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info", "zzzzzzzz"])
        assert result.exit_code != 0
        assert "zzzzzzzz" in result.output

    def test_claude_pid_resolves_when_the_sweep_cannot_see_this_session(
        self, tmp_path, monkeypatch
    ):
        # The real case this defends: `pgrep` does not list the claude running
        # `mael`, so the sweep is blind to the current session. CLAUDE_PID is
        # authoritative for "which process am I", so `session info` still works —
        # with the process-only fields, since there is no swept session to enrich.
        monkeypatch.setenv("CLAUDE_PID", "4242")
        with _patch_live([]), _patch_pid_lookup(4242):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code == 0, result.output
        assert "4242" in result.output

    def test_errors_when_no_id_and_no_environment(self, tmp_path):
        # Both variables that would have resolved it get named, so a user
        # debugging a hook environment does not chase only one of them.
        with _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info"])
        assert result.exit_code != 0
        assert "CLAUDE_CODE_SESSION_ID" in result.output
        assert "CLAUDE_PID" in result.output

    def test_errors_on_an_unknown_id(self, tmp_path):
        with _patch_live([self._sess()]):
            result = CliRunner().invoke(cli, ["session", "info", "zzzzzzzz"])
        assert result.exit_code != 0
        assert "zzzzzzzz" in result.output

    def test_errors_on_an_ambiguous_prefix(self, tmp_path):
        sessions = [
            self._sess(
                pid=1, cwd="/w/a", session_id="abcd1111-0000-0000-0000-000000000000"
            ),
            self._sess(
                pid=2, cwd="/w/b", session_id="abcd2222-0000-0000-0000-000000000000"
            ),
        ]
        with _patch_live(sessions):
            result = CliRunner().invoke(cli, ["session", "info", "abcd"])
        assert result.exit_code != 0
        assert "ambiguous" in result.output

    def test_json_output_via_the_global_flag(self, tmp_path):
        with (
            _patch_live([self._sess(session_id=self._SID)]),
        ):
            result = CliRunner().invoke(cli, ["--json", "session", "info", self._SID])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == self._SID
        assert data["pid"] == 4242
        assert data["cwd"] == "/w/alpha"

    def test_shows_the_task_when_the_index_resolves_it(self, tmp_path, monkeypatch):
        sid = model.session_id_for("askastro", "2026-07-03.7")
        index = SqliteTaskIndex(":memory:")
        index.upsert(
            TaskMeta(
                project="askastro",
                id="2026-07-03.7",
                status="in-progress",
                session_id=sid,
            )
        )
        monkeypatch.setattr(session_cli, "_task_index", lambda: index)
        with _patch_live([self._sess(session_id=sid)]):
            result = CliRunner().invoke(cli, ["session", "info", sid])
        assert result.exit_code == 0, result.output
        assert "2026-07-03.7" in result.output


class TestSessionEnd:
    """``mael session end [ID]`` — stop one session without closing its worktree."""

    _SID = "97894d02-f335-5ea3-9d9f-050330a4902b"

    @pytest.fixture(autouse=True)
    def _no_ambient_env(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CLAUDE_PID", raising=False)

    def test_stops_the_named_session(self, tmp_path, monkeypatch):
        sess = _live(4242, "/w/alpha")
        sess.session_id = self._SID
        stopped = []

        def fake_stop(sessions, **kwargs):
            stopped.extend(sessions)
            return [f"claude session (pid {s.pid}): stopped" for s in sessions]

        monkeypatch.setattr(session_cli, "stop_sessions", fake_stop)
        with _patch_live([sess]):
            result = CliRunner().invoke(cli, ["session", "end", "97894d02"])

        assert result.exit_code == 0, result.output
        assert [s.pid for s in stopped] == [4242]
        assert "stopped" in result.output

    def test_ending_the_mael_process_itself_says_so_and_stops_nothing(
        self, tmp_path, monkeypatch
    ):
        # A handle that names the `mael` process must not signal it, and must say
        # why nothing happened — silence reads as a crash.
        import os

        sess = _live(os.getpid(), "/w/alpha")
        stopped = []
        monkeypatch.setattr(
            session_cli, "stop_sessions", lambda s, **kw: stopped.extend(s) or []
        )
        monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))
        with _patch_live([sess]):
            result = CliRunner().invoke(cli, ["session", "end"])

        assert result.exit_code == 0, result.output
        assert stopped == []  # never even handed to the stopper
        assert "this session" in result.output

    def test_errors_on_an_unknown_id(self, tmp_path):
        with _patch_live([_live(4242, "/w/alpha")]):
            result = CliRunner().invoke(cli, ["session", "end", "zzzzzzzz"])
        assert result.exit_code != 0
        assert "zzzzzzzz" in result.output

    def test_stops_a_live_pid_the_sweep_cannot_see(self, tmp_path, monkeypatch):
        # `pgrep` does not report every live claude — the session running `mael`
        # is itself missing from the sweep on some setups. A pid read straight
        # from the process resolves even with an empty sweep.
        stopped = []
        monkeypatch.setattr(
            session_cli,
            "stop_sessions",
            lambda s, **kw: stopped.extend(s) or ["stopped"],
        )
        with _patch_live([]), _patch_pid_lookup(4242):
            result = CliRunner().invoke(cli, ["session", "end", "4242"])

        assert result.exit_code == 0, result.output
        assert [s.pid for s in stopped] == [4242]

    def test_a_pid_that_is_no_session_is_an_error(self, tmp_path, monkeypatch):
        # A dead pid, or a live one that is not a claude. `session end` signals
        # what it resolves, so neither may resolve — a typo must not reach an
        # unrelated process.
        stopped = []
        monkeypatch.setattr(
            session_cli,
            "stop_sessions",
            lambda s, **kw: stopped.extend(s) or ["stopped"],
        )
        with _patch_live([]), _patch_pid_lookup(None):
            result = CliRunner().invoke(cli, ["session", "end", "4242"])
        assert result.exit_code != 0
        assert "4242" in result.output
        assert stopped == []

    def test_errors_when_no_id_and_no_environment(self, tmp_path):
        with _patch_live([_live(4242, "/w/alpha")]):
            result = CliRunner().invoke(cli, ["session", "end"])
        assert result.exit_code != 0
        assert "CLAUDE_CODE_SESSION_ID" in result.output
