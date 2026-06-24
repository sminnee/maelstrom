"""Tests for maelstrom.shell — the closed command algebra and its two views."""

import pytest

from maelstrom.shell import Command, Pipeline, describe, run_cmd, to_argv


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
        expr = Pipeline([
            Command(["mael", "task", "prompt", "t1", "--project", "proj"]),
            Command(
                ["claude", "--permission-mode", "plan"],
                env={"MAEL_TASK_ID": "t1"},
            ),
        ])
        assert describe(expr) == (
            "mael task prompt t1 --project proj "
            "| MAEL_TASK_ID=t1 claude --permission-mode plan"
        )

    def test_env_on_right_segment_only(self):
        # Structural guard: env attaches to the claude Command, so MAEL_TASK_ID=
        # is absent from the left (prompt) stage. A front-of-pipeline prefix is
        # unrepresentable in the algebra.
        expr = Pipeline([
            Command(["mael", "task", "prompt", "t1", "--project", "proj"]),
            Command(["claude"], env={"MAEL_TASK_ID": "t1"}),
        ])
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
            "sh", "-c", "X=1 claude",
        ]

    def test_command_replace_prefixes_exec(self):
        # replace_process prefixes ``exec`` so the wrapping sh replaces itself.
        assert to_argv(Command(["claude"]), replace_process=True) == [
            "sh", "-c", "exec claude",
        ]

    def test_pipeline_replace_prefixes_exec(self):
        expr = Pipeline([
            Command(["mael", "task", "prompt", "t1", "--project", "p"]),
            Command(["claude", "--permission-mode", "plan"],
                    env={"MAEL_TASK_ID": "t1"}),
        ])
        assert to_argv(expr, replace_process=True) == [
            "sh", "-c",
            "exec mael task prompt t1 --project p "
            "| MAEL_TASK_ID=t1 claude --permission-mode plan",
        ]


class TestRunCmdEnv:
    """Tests for the env merging behaviour of run_cmd (the execution chokepoint)."""

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

    def test_env_none_uses_inherited_environment(self, monkeypatch):
        """With env=None the child inherits the parent environment unchanged."""
        monkeypatch.setenv("MAEL_PRESERVED", "inherited")
        result = run_cmd(
            ["sh", "-c", "echo $MAEL_PRESERVED"],
            quiet=True,
        )
        assert result.stdout.strip() == "inherited"
