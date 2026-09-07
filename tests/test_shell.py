"""Tests for maelstrom.shell — the closed command algebra and its two views."""

import asyncio
import subprocess
import sys

import pytest

from maelstrom import shell
from maelstrom.shell import (
    Command,
    Pipeline,
    RawShell,
    describe,
    exec_cmd,
    run_cmd,
    run_cmd_async,
    to_argv,
)


class TestDescribe:
    """Table-driven coverage of ``describe`` (the human-readable view)."""

    @pytest.mark.parametrize(
        "expr,expected",
        [
            # Bare argv — base case, just shlex-joins.
            (["claude"], "claude"),
            (["git", "status"], "git status"),
            # argv with a space gets quoted.
            (["echo", "hi there"], "echo 'hi there'"),
            # Command with no env renders byte-identically to a bare argv.
            (
                Command(["claude", "--permission-mode", "plan", "hi there"]),
                "claude --permission-mode plan 'hi there'",
            ),
            # Command with env — the value is quoted, the prefix leads.
            (
                Command(["claude", "hi"], env={"MAEL_TASK_ID": "a b"}),
                "MAEL_TASK_ID='a b' claude hi",
            ),
            # Empty env yields no stray leading space (matches old env_prefixed).
            (Command(["claude"], env={}), "claude"),
        ],
    )
    def test_describe(self, expr, expected):
        assert describe(expr) == expected

    def test_task_pipeline_full(self):
        # The task launch pipeline: env on the RIGHT stage (the claude segment).
        expr = Pipeline(
            [
                Command(["mael", "task", "prompt", "t1", "--project", "proj"]),
                Command(
                    ["claude", "--permission-mode", "plan"],
                    env={"MAEL_TASK_ID": "t1"},
                ),
            ]
        )
        assert describe(expr) == (
            "mael task prompt t1 --project proj "
            "| MAEL_TASK_ID=t1 claude --permission-mode plan"
        )

    def test_env_on_right_segment_only(self):
        # Structural guard: env attaches to the claude Command, so MAEL_TASK_ID=
        # is absent from the left (prompt) stage. A front-of-pipeline prefix is
        # unrepresentable in the algebra.
        expr = Pipeline(
            [
                Command(["mael", "task", "prompt", "t1", "--project", "proj"]),
                Command(["claude"], env={"MAEL_TASK_ID": "t1"}),
            ]
        )
        left, right = describe(expr).split(" | ", 1)
        assert "MAEL_TASK_ID=" not in left
        assert right == "MAEL_TASK_ID=t1 claude"

    def test_empty_env_byte_identical_to_no_env(self):
        # Old env_prefixed stripped the prefix entirely when env was empty.
        assert describe(Command(["claude"], env={})) == describe(["claude"])


class TestToArgv:
    """``to_argv`` decides shell-vs-no-shell per node; this guards that split."""

    def test_bare_argv_runs_directly_no_shell(self):
        # A bare argv is returned as-is — no sh hop, no quoting round-trip, so no
        # injection surface for the ~30 git sites that pass list[str].
        assert to_argv(["git", "status"]) == ["git", "status"]

    def test_bare_argv_replace_is_noop(self):
        # exec/run are identical for a bare argv — it already replaces directly.
        assert to_argv(["claude"], replace_process=True) == ["claude"]

    def test_command_wraps_in_sh_c(self):
        # A Command carries shell syntax (the env prefix), so it goes through sh.
        assert to_argv(Command(["claude"], env={"X": "1"})) == [
            "sh",
            "-c",
            "X=1 claude",
        ]

    def test_command_replace_prefixes_exec(self):
        # replace_process prefixes ``exec`` so the wrapping sh replaces itself.
        assert to_argv(Command(["claude"]), replace_process=True) == [
            "sh",
            "-c",
            "exec claude",
        ]

    def test_pipeline_replace_prefixes_exec(self):
        expr = Pipeline(
            [
                Command(["mael", "task", "prompt", "t1", "--project", "p"]),
                Command(
                    ["claude", "--permission-mode", "plan"], env={"MAEL_TASK_ID": "t1"}
                ),
            ]
        )
        assert to_argv(expr, replace_process=True) == [
            "sh",
            "-c",
            "exec mael task prompt t1 --project p "
            "| MAEL_TASK_ID=t1 claude --permission-mode plan",
        ]


class TestRunCmdEnv:
    """Tests for the env merging behaviour of run_cmd (the execution chokepoint)."""

    def test_options_are_keyword_only(self):
        """Eight parameters read badly positionally, and drift silently."""
        with pytest.raises(TypeError):
            run_cmd(["true"], None, True)  # type: ignore[misc]

    def test_the_exec_path_is_no_longer_reachable_from_run_cmd(self):
        """exec_cmd owns it, so run_cmd's options can no longer conflict.

        A timeout no exec can enforce, and a captured result no exec returns,
        are now unrepresentable rather than silently ignored.
        """
        with pytest.raises(TypeError):
            run_cmd(["true"], quiet=True, replace_process=True)  # type: ignore[call-arg]

    def test_env_merges_over_os_environ(self, monkeypatch):
        """A provided env dict is merged over os.environ, not used wholesale."""
        monkeypatch.setenv("MAEL_PRESERVED", "from_parent")
        result = run_cmd(
            ["sh", "-c", "echo $MAEL_PRESERVED $MAEL_EXTRA"],
            quiet=True,
            env={"MAEL_EXTRA": "added"},
        )
        # Parent var survives the merge, and the override is applied.
        assert result.stdout.strip() == "from_parent added"

    def test_timeout_kills_a_command_that_overruns(self):
        """A long-running command needs a deadline the caller can set.

        The chokepoint owns it, so a streaming command can be bounded the same
        way a captured one is.
        """
        with pytest.raises(subprocess.TimeoutExpired):
            run_cmd(["sleep", "5"], quiet=True, timeout=0.1)

    def test_echo_precedes_a_streamed_command_output(self):
        """The `$ cmd` line must land before the output it describes.

        A streamed child writes to the shared stdout directly. Python block-buffers
        its own stdout when stdout is not a terminal, so an unflushed echo arrives
        after the child's output and appears to label the wrong command. This runs
        in a real subprocess through a pipe: pytest's capture replaces stdout and
        hides the interleaving.
        """
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from maelstrom.shell import run_cmd;"
                "run_cmd(['echo', 'child-output'], stream=True)",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

        assert out.index("$ echo child-output") < out.index("child-output\n")

    def test_env_none_uses_inherited_environment(self, monkeypatch):
        """With env=None the child inherits the parent environment unchanged."""
        monkeypatch.setenv("MAEL_PRESERVED", "inherited")
        result = run_cmd(
            ["sh", "-c", "echo $MAEL_PRESERVED"],
            quiet=True,
        )
        assert result.stdout.strip() == "inherited"


class TestRunCmdAsync:
    """``run_cmd_async`` is ``run_cmd``'s contract, off the calling thread.

    The model shells out from a server that holds one loop for every client,
    so a blocking wait stalls all of them. Keeping the contract identical is
    what lets a call site convert by adding ``await`` and nothing else. It is
    the only public async runner, so every async command in the codebase is
    one grep away.
    """

    def test_it_returns_a_completed_process_like_run_cmd(self):
        result = asyncio.run(
            run_cmd_async(["sh", "-c", "printf out; printf err >&2"], quiet=True)
        )
        assert result.stdout == "out"
        assert result.stderr == "err"
        assert result.returncode == 0

    def test_it_raises_on_a_non_zero_exit_like_run_cmd(self):
        """``check=True`` is the default on both, so a converted caller keeps
        the error handling it already had."""
        with pytest.raises(subprocess.CalledProcessError):
            asyncio.run(run_cmd_async(["false"], quiet=True))

    def test_check_false_returns_the_failure_instead_of_raising(self):
        """A server has no script to abort, so it reads the code instead."""
        result = asyncio.run(
            run_cmd_async(["sh", "-c", "exit 3"], quiet=True, check=False)
        )
        assert result.returncode == 3

    def test_it_captures_both_streams(self):
        result = asyncio.run(
            run_cmd_async(
                ["sh", "-c", "printf out; printf err >&2; exit 3"],
                quiet=True,
                check=False,
            )
        )
        assert (result.stdout, result.stderr, result.returncode) == ("out", "err", 3)

    def test_a_raw_string_goes_through_a_shell(self):
        """``RawShell`` is the one way a raw string reaches the chokepoint, so
        a grep for it finds every place user text is executed."""
        result = asyncio.run(run_cmd_async(RawShell("echo a | tr a b"), quiet=True))
        assert result.stdout.strip() == "b"

    def test_a_bare_argv_takes_no_shell(self):
        """The ShellExpr guarantee holds on the async path: no shell, so a
        metacharacter in an argument is data, not syntax."""
        result = asyncio.run(run_cmd_async(["echo", "a; whoami"], quiet=True))
        assert result.stdout.strip() == "a; whoami"
        assert result.args == ["echo", "a; whoami"]

    def test_it_runs_in_the_given_directory(self, tmp_path):
        (tmp_path / "marker.txt").write_text("x")
        result = asyncio.run(run_cmd_async(["ls"], cwd=tmp_path, quiet=True))
        assert "marker.txt" in result.stdout

    def test_a_timeout_kills_the_whole_process_group(self):
        """A pipeline's children must die with it, or they hold the worktree."""
        with pytest.raises(subprocess.TimeoutExpired):
            asyncio.run(
                run_cmd_async(RawShell("sleep 30 | cat"), quiet=True, timeout=0.3)
            )

    def test_it_does_not_block_the_loop(self):
        """The point of the async twin: other work runs while the child does."""
        ticks = []

        async def scenario():
            async def tick():
                for _ in range(5):
                    ticks.append(1)
                    await asyncio.sleep(0.01)

            ticker = asyncio.create_task(tick())
            await run_cmd_async(["sh", "-c", "sleep 0.2"], quiet=True)
            ticker.cancel()

        asyncio.run(scenario())
        assert len(ticks) > 1, "the loop was blocked for the whole command"


class TestAuditPrecedesEcho:
    """Every entry point audits before it echoes.

    The audit log and the console are read side by side, so an entry point
    that reversed the two would make one command appear in a different order
    from the next depending only on which runner the caller picked.
    """

    def _order(self, monkeypatch):
        order = []
        monkeypatch.setattr(shell, "_audit", lambda cmd, cwd: order.append("audit"))
        monkeypatch.setattr(shell, "_echo", lambda cmd: order.append("echo"))
        return order

    def test_run_cmd_audits_first(self, monkeypatch):
        order = self._order(monkeypatch)
        run_cmd(["true"])
        assert order == ["audit", "echo"]

    def test_run_cmd_async_audits_first(self, monkeypatch):
        order = self._order(monkeypatch)
        asyncio.run(run_cmd_async(["true"]))
        assert order == ["audit", "echo"]

    def test_exec_cmd_audits_first(self, monkeypatch):
        """``exec_cmd`` never returns, so the exec is stubbed to end the call."""
        order = self._order(monkeypatch)
        monkeypatch.setattr(
            shell.os, "execvp", lambda *a: (_ for _ in ()).throw(_ExecCalled())
        )
        with pytest.raises(_ExecCalled):
            exec_cmd(["true"])
        assert order == ["audit", "echo"]


class _ExecCalled(Exception):
    """Stands in for the exec that would replace this process."""
