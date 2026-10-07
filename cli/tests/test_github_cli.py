"""Tests for mael_cli.github_cli module."""

import asyncio
import socket
import subprocess
import threading
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from domain_fixtures import IN_A_PANE

from mael_cli import github_cli, task_cli
from mael_cli.cli import cli
from mael_cli.github_cli import _format_size, _render_pr_comments
from mael_cli.orchestrator_notify import orchestrator_url, tell_orchestrator
from mael_domain import task as model
from mael_domain.github_model import (
    GitHubCliMissing,
    GitHubCommandFailed,
    NoPullRequest,
    PRComment,
    PRInfo,
    PushedPr,
    SyncFailed,
)
from mael_domain.worktree_model import PrePushFailed


@pytest.fixture
def tasks(store, monkeypatch):
    """The task table the gh commands register a PR on, in memory.

    The project resolves to ``p`` and the repo to ``https://github.com/o/r``,
    as the cwd's would in a real worktree.
    """

    async def _table():
        return store

    async def _repo_url(_path):
        return "https://github.com/o/r"

    monkeypatch.setattr(task_cli, "_table", _table)
    monkeypatch.setattr(task_cli, "resolve_project", lambda project: project or "p")
    monkeypatch.setattr(github_cli, "project_repo_url", _repo_url)
    monkeypatch.delenv("MAEL_TASK_ID", raising=False)
    return store


async def _registered(store, id: str) -> tuple[int, str]:
    task = await model.load(store, "p", id)
    return task.pr_number, task.pr_url


class TestLinkPr:
    """``mael gh link-pr`` registers an existing PR against a task."""

    @pytest.fixture
    def task_id(self, tasks):
        return asyncio.run(model.create(tasks, project="p", title="t")).id

    @pytest.mark.parametrize("ref", ["118", "#118", "https://github.com/o/r/pull/118"])
    def test_a_ref_registers_on_the_running_task(
        self, tasks, task_id, ref, monkeypatch
    ):
        monkeypatch.setenv("MAEL_TASK_ID", task_id)
        result = CliRunner().invoke(cli, ["gh", "link-pr", ref])
        assert result.exit_code == 0, result.output
        assert asyncio.run(_registered(tasks, task_id)) == (
            118,
            "https://github.com/o/r/pull/118",
        )

    def test_task_overrides_the_running_task(self, tasks, task_id, monkeypatch):
        monkeypatch.setenv("MAEL_TASK_ID", "elsewhere")
        result = CliRunner().invoke(cli, ["gh", "link-pr", "5", "--task", task_id])
        assert result.exit_code == 0, result.output
        assert asyncio.run(_registered(tasks, task_id)) == (
            5,
            "https://github.com/o/r/pull/5",
        )

    def test_an_unknown_task_is_a_clear_error(self, tasks):
        result = CliRunner().invoke(cli, ["gh", "link-pr", "5", "--task", "nope"])
        assert result.exit_code != 0
        assert "Task not found: nope" in result.output

    def test_no_task_id_is_a_clear_error(self, tasks):
        result = CliRunner().invoke(cli, ["gh", "link-pr", "5"])
        assert result.exit_code != 0
        assert "MAEL_TASK_ID" in result.output

    def test_nonsense_is_refused(self, tasks, task_id, monkeypatch):
        monkeypatch.setenv("MAEL_TASK_ID", task_id)
        result = CliRunner().invoke(cli, ["gh", "link-pr", "abc"])
        assert result.exit_code != 0
        assert "Not a PR number or URL" in result.output


class TestCreatePrRegisters:
    """``mael gh create-pr`` registers the PR it pushed to on the running task."""

    def _push(self, pushed):
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch("mael_cli.github_cli.create_pr", return_value=pushed),
            patch("mael_cli.github_cli._open_pr_in_cmux") as opened,
        ):
            mock_ctx.return_value.worktree_path = None
            mock_ctx.return_value.project = None
            result = CliRunner().invoke(cli, ["gh", "create-pr"])
        self.opened = opened.called
        return result

    def test_the_running_task_registers_the_pr(self, tasks, monkeypatch):
        task = asyncio.run(model.create(tasks, project="p", title="t"))
        monkeypatch.setenv("MAEL_TASK_ID", task.id)
        result = self._push(PushedPr("https://github.com/o/r/pull/9", 9, False))
        assert result.exit_code == 0, result.output
        assert asyncio.run(_registered(tasks, task.id)) == (
            9,
            "https://github.com/o/r/pull/9",
        )

    def test_no_running_task_registers_nothing(self, tasks):
        task = asyncio.run(model.create(tasks, project="p", title="t"))
        result = self._push(PushedPr("https://github.com/o/r/pull/9", 9, True))
        assert result.exit_code == 0, result.output
        assert asyncio.run(_registered(tasks, task.id)) == (0, "")

    def test_an_unknown_running_task_warns_and_the_push_still_succeeds(
        self, tasks, monkeypatch
    ):
        """The PR is on GitHub: a failure here must not read as a failed push."""
        monkeypatch.setenv("MAEL_TASK_ID", "nope")
        result = self._push(PushedPr("https://github.com/o/r/pull/9", 9, True))
        assert result.exit_code == 0, result.output
        assert "Task not found: nope" in result.output
        assert self.opened


class TestFormatSize:
    """Tests for the _format_size helper."""

    def test_bytes(self):
        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1023) == "1023 B"

    def test_kilobytes(self):
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(1536) == "1.5 KB"

    def test_megabytes(self):
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(int(2.5 * 1024 * 1024)) == "2.5 MB"


class TestRenderPRComments:
    """Tests for the _render_pr_comments helper."""

    def _pr(self, comments, last_push_at=None):
        return PRInfo(
            number=1,
            title="Test PR",
            url="https://github.com/x/y/pull/1",
            state="OPEN",
            merged=False,
            head_ref="feat/x",
            comments=comments,
            last_push_at=last_push_at,
        )

    def test_no_comments_renders_nothing(self, capsys):
        _render_pr_comments(self._pr([]), all_comments=False)
        assert capsys.readouterr().out == ""

    def test_new_comment_shown(self, capsys):
        comments = [
            PRComment(
                author="alice",
                body="looks good",
                created_at="2026-06-24T00:00:00Z",
                kind="issue",
            ),
        ]
        _render_pr_comments(
            self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=False
        )
        out = capsys.readouterr().out
        assert "Top-level (1 new):" in out
        assert "@alice" in out
        assert "looks good" in out

    def test_old_comment_hidden_without_all(self, capsys):
        comments = [
            PRComment(
                author="bob",
                body="old note",
                created_at="2026-06-20T00:00:00Z",
                kind="issue",
            ),
        ]
        _render_pr_comments(
            self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=False
        )
        out = capsys.readouterr().out
        assert "old note" not in out
        assert "1 older comment hidden" in out

    def test_old_comment_shown_with_all(self, capsys):
        comments = [
            PRComment(
                author="bob",
                body="old note",
                created_at="2026-06-20T00:00:00Z",
                kind="issue",
            ),
        ]
        _render_pr_comments(
            self._pr(comments, last_push_at="2026-06-23T00:00:00Z"), all_comments=True
        )
        out = capsys.readouterr().out
        assert "old note" in out


class TestGhCliRegistration:
    """The gh group is reachable through the top-level cli."""

    def test_help_lists_all_commands(self):
        result = CliRunner().invoke(cli, ["gh", "--help"])
        assert result.exit_code == 0
        for cmd in (
            "create-pr",
            "wait-for-pr",
            "read-pr",
            "download-artifact",
            "check-log",
            "show-code",
            "has-pr",
        ):
            assert cmd in result.output

    def _run_create_pr(self, args):
        """Invoke `gh create-pr` with create_pr mocked; return its kwargs."""
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch(
                "mael_cli.github_cli.create_pr",
                return_value=PushedPr("https://example/pr", 0, True),
            ) as mock_create,
            patch("mael_cli.github_cli._open_pr_in_cmux"),
        ):
            mock_ctx.return_value.worktree_path = None
            result = CliRunner().invoke(cli, ["gh", "create-pr", *args])
        assert result.exit_code == 0, result.output
        return mock_create.call_args.kwargs

    def test_create_pr_recycles_the_github_browser_tab_only_in_a_pane(
        self, fake_cmux, caller
    ):
        fake_cmux.with_workspace(
            "myproject-alpha",
            [["Claude"], ["Terminal"], [("browser", "https://github.com/o")]],
        )
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch(
                "mael_cli.github_cli.create_pr",
                return_value=PushedPr("https://github.com/o/r/pull/9", 9, True),
            ),
        ):
            mock_ctx.return_value.worktree_path = None
            result = CliRunner().invoke(cli, ["gh", "create-pr"])
        assert result.exit_code == 0, result.output
        pr = "https://github.com/o/r/pull/9"
        expected = pr if caller == IN_A_PANE else "https://github.com/o"
        assert fake_cmux.tabs("myproject-alpha")[2] == [expected]

    def test_create_pr_passes_autorepair_through(self):
        assert self._run_create_pr(["--autorepair"])["autorepair"] is True

    def test_create_pr_leaves_autorepair_off_by_default(self):
        """A PR push must not start an agent unasked."""
        assert self._run_create_pr([])["autorepair"] is False

    def test_create_pr_runs_the_pre_push_check_by_default(self):
        assert self._run_create_pr([])["pre_push"] is True

    def test_skip_pre_push_reaches_create_pr(self):
        assert self._run_create_pr(["--skip-pre-push"])["pre_push"] is False

    def test_create_pr_uses_the_running_task_id(self, monkeypatch):
        monkeypatch.setenv("MAEL_TASK_ID", "maintenance.2026-09-17")

        assert self._run_create_pr([])["task_id"] == "maintenance.2026-09-17"

    def test_create_pr_rejects_legacy_id_and_progress_inputs(self):
        for args in (["ME-41"], ["--progress"]):
            result = CliRunner().invoke(cli, ["gh", "create-pr", *args])
            assert result.exit_code != 0

    def test_create_pr_tells_the_orchestrator(self):
        """The PR is not in any world until something looks it up, and the next
        worktree poll is up to a minute away. The canvas would sit without a
        chip through exactly the moment the user is watching for one."""
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch(
                "mael_cli.github_cli.create_pr",
                return_value=PushedPr("https://example/pr", 0, True),
            ),
            patch("mael_cli.github_cli._open_pr_in_cmux"),
            patch("mael_cli.github_cli.tell_orchestrator") as told,
        ):
            mock_ctx.return_value.worktree_path = None
            result = CliRunner().invoke(cli, ["gh", "create-pr"])
        assert result.exit_code == 0, result.output
        told.assert_called_once()

    def test_a_pr_still_succeeds_when_no_orchestrator_is_listening(self, tmp_path):
        """Running `mael gh create-pr` with no UI open is the ordinary case.
        The PR is already on GitHub, so nothing about telling a server that is
        not there may fail the command or print an error.

        The real `tell_orchestrator` runs here rather than a patched one: its
        not-raising is the whole contract, and a fake that cannot raise would
        assert nothing. `tmp_path` has no `.env`, so it finds no port.
        """
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch(
                "mael_cli.github_cli.create_pr",
                return_value=PushedPr("https://example/pr", 0, True),
            ),
            patch("mael_cli.github_cli._open_pr_in_cmux"),
        ):
            mock_ctx.return_value.worktree_path = tmp_path
            result = CliRunner().invoke(cli, ["gh", "create-pr"])
        assert result.exit_code == 0, result.output
        assert "PR created" in result.output

    def test_telling_an_orchestrator_that_refuses_the_connection_is_silent(
        self, tmp_path
    ):
        """A port in the `.env` with nothing listening on it: the ordinary case
        once an orchestrator has been stopped. The command has already pushed."""
        (tmp_path / ".env").write_text("ORCHESTRATOR_PORT=1\n")
        tell_orchestrator(tmp_path, "/api/worktrees/refresh")

    def test_a_worktree_with_no_port_tells_nobody(self, tmp_path):
        """A worktree made before the service existed names no port. It must
        read as nothing to tell, not as a failure."""
        assert orchestrator_url(tmp_path, "/x") is None
        tell_orchestrator(tmp_path, "/x")

    @pytest.mark.binds_socket
    def test_an_https_orchestrator_is_told_over_tls(self, tmp_path, tls_server_context):
        """Under ``dev_https:`` the notify reaches a TLS-only server whose
        certificate does not name ``127.0.0.1``."""
        listener = socket.create_server(("127.0.0.1", 0))
        listener.settimeout(5)
        heard: list[list[str]] = []

        def answer_one() -> None:
            conn, _ = listener.accept()
            with tls_server_context.wrap_socket(conn, server_side=True) as tls:
                heard.append(tls.recv(4096).decode().split(" ")[:2])
                tls.sendall(b"HTTP/1.1 204 No Content\r\n\r\n")

        thread = threading.Thread(target=answer_one, daemon=True)
        thread.start()
        port = listener.getsockname()[1]
        (tmp_path / ".env").write_text(f"DEV_SCHEME=https\nORCHESTRATOR_PORT={port}\n")
        try:
            tell_orchestrator(tmp_path, "/api/worktrees/refresh")
        finally:
            thread.join(timeout=5)
            listener.close()
        assert heard == [["POST", "/api/worktrees/refresh"]]

    def test_show_code_smoke(self):
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch("mael_cli.github_cli.get_worktree_code") as mock_code,
        ):
            mock_ctx.return_value.worktree_path = None
            mock_code.return_value = ("abc123 commit", "")
            result = CliRunner().invoke(cli, ["gh", "show-code", "--committed"])
        assert result.exit_code == 0
        assert "=== Commits ===" in result.output
        assert "abc123 commit" in result.output


class TestCreatePrErrorHandling:
    """`gh create-pr` turns domain errors into clean messages, not tracebacks."""

    @staticmethod
    def _invoke(error):
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch("mael_cli.github_cli.create_pr", side_effect=error),
            patch("mael_cli.github_cli._open_pr_in_cmux"),
        ):
            mock_ctx.return_value.worktree_path = None
            return CliRunner().invoke(cli, ["gh", "create-pr"])

    def test_a_github_error_reads_as_a_clean_message(self):
        result = self._invoke(GitHubCommandFailed("push branch", "rejected"))
        assert result.exit_code == 1
        assert "Failed to push branch: rejected" in result.output

    def test_a_failed_pre_push_check_reads_as_a_clean_message(self):
        result = self._invoke(PrePushFailed("bin/gates", 2))
        assert result.exit_code == 1
        assert "Pre-push check `bin/gates` failed (exit 2)" in result.output

    def test_a_sync_failure_reads_as_a_clean_message(self):
        result = self._invoke(SyncFailed("Sync failed: conflicts"))
        assert result.exit_code == 1
        assert "Sync failed: conflicts" in result.output

    def test_a_programming_error_is_not_dressed_up_as_a_user_message(self):
        """A bug in maelstrom must surface as a bug, not as advice to the user."""
        result = self._invoke(AttributeError("'NoneType' has no attribute 'strip'"))
        assert isinstance(result.exception, AttributeError)

    def test_a_git_failure_inside_create_pr_still_reads_as_a_message(self):
        """`create_pr` calls git through `run_git`, which raises
        CalledProcessError — not a GitHubError. Narrowing the catch must not
        turn that into a traceback at the user."""
        err = subprocess.CalledProcessError(1, ["git"], stderr="detached HEAD")
        result = self._invoke(err)
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)


class TestGhHasPr:
    """``mael gh has-pr`` answers with its exit code, for a skill to branch on.

    0 = has one, 1 = has none, 2 = the check could not run. The 2 is the point:
    an unauthenticated ``gh`` must never read as "no PR", or a skill opens a
    duplicate.
    """

    def _run(self, args=None, info=None, error=None):
        with (
            patch("mael_cli.github_cli.resolve_context") as mock_ctx,
            patch(
                "mael_cli.github_cli.get_pr_info",
                side_effect=error,
                **({} if error is not None else {"return_value": info}),
            ),
        ):
            mock_ctx.return_value.worktree_path = None
            return CliRunner().invoke(cli, ["gh", "has-pr", *(args or [])])

    def _info(self, state="OPEN", merged=False):
        return PRInfo(
            number=7,
            title="A change",
            url="https://example/pr/7",
            state=state,
            merged=merged,
            head_ref="feature/work",
        )

    def test_an_open_pr_exits_0(self):
        result = self._run(info=self._info())

        assert result.exit_code == 0
        assert "7" in result.output

    def test_no_pr_exits_1(self):
        result = self._run(error=NoPullRequest())

        assert result.exit_code == 1

    def test_a_merged_pr_exits_0_by_default(self):
        """Without ``--open`` the question is only whether a PR exists."""
        result = self._run(info=self._info(state="MERGED", merged=True))

        assert result.exit_code == 0

    def test_open_treats_a_merged_pr_as_missing(self):
        result = self._run(
            args=["--open"], info=self._info(state="MERGED", merged=True)
        )

        assert result.exit_code == 1

    def test_open_treats_a_closed_pr_as_missing(self):
        result = self._run(args=["--open"], info=self._info(state="CLOSED"))

        assert result.exit_code == 1

    def test_open_accepts_an_open_pr(self):
        result = self._run(args=["--open"], info=self._info())

        assert result.exit_code == 0

    def test_a_broken_gh_exits_2_not_1(self):
        """The distinction a skill depends on: undetermined is not "no"."""
        result = self._run(
            error=GitHubCommandFailed("get PR info", "gh: not logged in")
        )

        assert result.exit_code == 2

    def test_a_missing_gh_exits_2_not_1(self):
        result = self._run(error=GitHubCliMissing("gh"))

        assert result.exit_code == 2
