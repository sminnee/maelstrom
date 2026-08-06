"""Tests for the `mael mv-project` CLI adapter, with git and state mocked."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from maelstrom.cli import cli
from maelstrom.mv_project import build_move_plan
from maelstrom.mv_project_cli import _rewrite_path_string


class _FakeWorktree:
    """Stands in for a `WorktreeInfo` from `list_worktrees`."""

    def __init__(self, path: Path):
        self.path = path


def _make_project(tmp_path: Path, name: str = "old") -> Path:
    """Create a project directory that passes the precondition checks."""
    project = tmp_path / name
    (project / f"{name}-alpha").mkdir(parents=True)
    (project / "_main").mkdir(parents=True)
    (project / ".mael").touch()
    return project


class MvProjectHarness:
    """Runs `mv-project` with every external dependency mocked out."""

    def __init__(self, tmp_path: Path, *, live_sessions=None, env_state=None,
                 shared_state=None, tasks=None):
        self.tmp_path = tmp_path
        self.live_sessions = live_sessions or []
        self.env_state = env_state
        self.shared_state = shared_state
        self.tasks = tasks or []
        self.port_error: Exception | None = None
        self.mocks: dict = {}

    def run(self, args):
        project = self.tmp_path / "old"
        home = self.tmp_path / "home"
        home.mkdir(exist_ok=True)

        worktrees = [
            _FakeWorktree(project / "old-alpha"),
            _FakeWorktree(project / "_main"),
        ]

        with ExitStack() as stack:
            def mock(target, **kwargs):
                m = stack.enter_context(patch(target, **kwargs))
                self.mocks[target.rsplit(".", 1)[-1]] = m
                return m

            mock(
                "maelstrom.mv_project_cli.load_global_config",
                return_value=MagicMock(projects_dir=self.tmp_path),
            )
            stack.enter_context(patch("pathlib.Path.home", return_value=home))
            mock("maelstrom.mv_project_cli.list_worktrees", return_value=worktrees)
            mock("maelstrom.mv_project_cli.all_live_sessions",
                 return_value=self.live_sessions)
            mock("maelstrom.mv_project_cli.load_env_state", return_value=self.env_state)
            mock("maelstrom.mv_project_cli.load_shared_state",
                 return_value=self.shared_state)
            mock("maelstrom.mv_project_cli.make_env_store")
            mock("maelstrom.mv_project_cli.stop_env", return_value=["stopped env"])
            mock("maelstrom.mv_project_cli.stop_sessions",
                 return_value=["stopped session"])
            mock("maelstrom.mv_project_cli.stop_shared_services",
                 return_value=["stopped shared"])
            mock("maelstrom.mv_project_cli.run_git")
            mock(
                "maelstrom.mv_project_cli.rename_project_allocations",
                side_effect=self.port_error,
            )
            mock("maelstrom.mv_project_cli.GitFileStore")
            mock("maelstrom.mv_project_cli.open_index")
            mock("maelstrom.mv_project_cli.task_model.list_tasks",
                 return_value=self.tasks)
            mock("maelstrom.mv_project_cli.task_model.reindex", return_value=0)
            mock("maelstrom.mv_project_cli.setup_claude_memory_symlink")
            mock("maelstrom.mv_project_cli.update_claude_local_md")
            mock("maelstrom.mv_project_cli.find_all_projects", return_value=[])
            # The trap this command exists to avoid: doctor prunes port
            # allocations keyed by a path that no longer exists.
            mock("maelstrom.doctor.run_doctor")

            result = CliRunner().invoke(cli, ["mv-project"] + args)
        return result


class TestDryRun:
    """`--dry-run` computes the plan and changes nothing."""

    def test_moves_nothing_and_calls_no_git(self, tmp_path):
        project = _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        result = harness.run(["old", "new", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert project.is_dir()
        assert not (tmp_path / "new").exists()
        harness.mocks["run_git"].assert_not_called()
        harness.mocks["rename_project_allocations"].assert_not_called()

    def test_with_force_it_still_stops_nothing(self, tmp_path):
        """`--dry-run --force` must preview, not kill envs and live sessions."""
        _make_project(tmp_path)
        session = MagicMock(pid=4242, cwd=tmp_path / "old" / "old-alpha")
        harness = MvProjectHarness(
            tmp_path, env_state=MagicMock(), shared_state=MagicMock(),
            live_sessions=[session],
        )

        result = harness.run(["old", "new", "--dry-run", "--force"])

        assert result.exit_code == 0, result.output
        harness.mocks["stop_env"].assert_not_called()
        harness.mocks["stop_sessions"].assert_not_called()
        harness.mocks["stop_shared_services"].assert_not_called()
        assert (tmp_path / "old").is_dir()

    def test_shows_the_directory_moves(self, tmp_path):
        _make_project(tmp_path)
        result = MvProjectHarness(tmp_path).run(["old", "new", "--dry-run"])

        assert "rename project 'old' -> 'new'" in result.output
        assert "new-alpha" in result.output

    def test_reports_main_as_unchanged(self, tmp_path):
        _make_project(tmp_path)
        result = MvProjectHarness(tmp_path).run(["old", "new", "--dry-run"])

        assert "(name unchanged)" in result.output

    def test_mentions_the_unhandled_git_remote(self, tmp_path):
        _make_project(tmp_path)
        result = MvProjectHarness(tmp_path).run(["old", "new", "--dry-run"])

        assert "--git-url" in result.output


class TestPreconditions:
    """Everything that must abort before a single directory moves."""

    def test_refuses_when_the_target_exists(self, tmp_path):
        _make_project(tmp_path)
        (tmp_path / "new").mkdir()
        harness = MvProjectHarness(tmp_path)

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert "already exists" in result.output
        assert (tmp_path / "old").is_dir()

    def test_refuses_when_the_source_is_missing(self, tmp_path):
        result = MvProjectHarness(tmp_path).run(["old", "new"])

        assert result.exit_code != 0
        assert "not found" in result.output

    def test_refuses_without_a_mael_marker(self, tmp_path):
        project = tmp_path / "old"
        project.mkdir()
        harness = MvProjectHarness(tmp_path)

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert "not a maelstrom project" in result.output
        assert project.is_dir()
        assert not (tmp_path / "new").exists()

    def test_refuses_an_invalid_new_name(self, tmp_path):
        _make_project(tmp_path)
        result = MvProjectHarness(tmp_path).run(["old", "a.b"])

        assert result.exit_code != 0
        assert "cannot contain dots" in result.output

    def test_refuses_a_running_env_and_moves_nothing(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path, env_state=MagicMock())

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert "running environment" in result.output
        assert (tmp_path / "old").is_dir()
        assert not (tmp_path / "new").exists()
        harness.mocks["stop_env"].assert_not_called()

    def test_refuses_a_live_session_and_moves_nothing(self, tmp_path):
        project = _make_project(tmp_path)
        session = MagicMock(pid=4242, cwd=project / "old-alpha")
        harness = MvProjectHarness(tmp_path, live_sessions=[session])

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert "4242" in result.output
        assert (tmp_path / "old").is_dir()
        harness.mocks["stop_sessions"].assert_not_called()

    def test_a_session_outside_the_project_does_not_block(self, tmp_path):
        _make_project(tmp_path)
        session = MagicMock(pid=4242, cwd=tmp_path / "elsewhere")
        harness = MvProjectHarness(tmp_path, live_sessions=[session])

        result = harness.run(["old", "new", "--dry-run"])

        assert result.exit_code == 0, result.output

    def test_force_stops_a_running_env(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path, env_state=MagicMock())

        result = harness.run(["old", "new", "--force"])

        assert result.exit_code == 0, result.output
        harness.mocks["stop_env"].assert_called()
        assert (tmp_path / "new").is_dir()

    def test_force_stops_shared_services_with_no_worktree_env(self, tmp_path):
        """Shared services can outlive every per-worktree env and still hold the
        project open, so they must be stopped in their own right."""
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path, shared_state=MagicMock())

        result = harness.run(["old", "new", "--force"])

        assert result.exit_code == 0, result.output
        harness.mocks["stop_shared_services"].assert_called_once()

    def test_refuses_shared_services_without_force(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path, shared_state=MagicMock())

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert "shared services" in result.output
        assert (tmp_path / "old").is_dir()

    def test_force_stops_a_live_session(self, tmp_path):
        project = _make_project(tmp_path)
        session = MagicMock(pid=4242, cwd=project / "old-alpha")
        harness = MvProjectHarness(tmp_path, live_sessions=[session])

        result = harness.run(["old", "new", "--force"])

        assert result.exit_code == 0, result.output
        harness.mocks["stop_sessions"].assert_called_once()


class TestMigration:
    """The apply path."""

    def test_moves_the_project_and_renames_the_worktree(self, tmp_path):
        _make_project(tmp_path)

        result = MvProjectHarness(tmp_path).run(["old", "new"])

        assert result.exit_code == 0, result.output
        assert (tmp_path / "new").is_dir()
        assert (tmp_path / "new" / "new-alpha").is_dir()
        assert not (tmp_path / "old").exists()

    def test_keeps_the_main_worktree_name(self, tmp_path):
        _make_project(tmp_path)

        MvProjectHarness(tmp_path).run(["old", "new"])

        assert (tmp_path / "new" / "_main").is_dir()

    def test_migrates_port_allocations(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        harness.run(["old", "new"])

        harness.mocks["rename_project_allocations"].assert_called_once_with(
            tmp_path / "old", tmp_path / "new"
        )

    def test_repairs_git_worktrees_from_the_new_project_dir(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        harness.run(["old", "new"])

        calls = harness.mocks["run_git"].call_args_list
        repair = next(c for c in calls if c[0][0][:2] == ["worktree", "repair"])
        assert str(tmp_path / "new" / "new-alpha") in repair[0][0]
        assert str(tmp_path / "new" / "_main") in repair[0][0]
        assert repair.kwargs["cwd"] == tmp_path / "new"

    def test_prunes_after_repairing(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        harness.run(["old", "new"])

        commands = [c[0][0] for c in harness.mocks["run_git"].call_args_list]
        assert ["worktree", "prune"] in commands

    def test_never_runs_doctor_during_migration(self, tmp_path):
        """`doctor` prunes port allocations — running it mid-flight loses data."""
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        result = harness.run(["old", "new"])

        assert result.exit_code == 0, result.output
        harness.mocks["run_doctor"].assert_not_called()

    def test_suggests_doctor_afterwards(self, tmp_path):
        _make_project(tmp_path)

        result = MvProjectHarness(tmp_path).run(["old", "new"])

        assert "mael doctor new" in result.output

    def test_rebuilds_the_task_index(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        harness.run(["old", "new"])

        harness.mocks["reindex"].assert_called_once()

    def test_sets_the_git_remote_when_asked(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        harness.run(["old", "new", "--git-url", "git@github.com:me/new.git"])

        commands = [c[0][0] for c in harness.mocks["run_git"].call_args_list]
        assert ["remote", "set-url", "origin", "git@github.com:me/new.git"] in commands

    def test_leaves_the_remote_alone_by_default(self, tmp_path):
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)

        result = harness.run(["old", "new"])

        commands = [c[0][0] for c in harness.mocks["run_git"].call_args_list]
        assert not any(c[:1] == ["remote"] for c in commands)
        assert "remote.origin.url is unchanged" in result.output

    def test_a_post_move_failure_names_the_recovery_steps(self, tmp_path):
        """The directory has already moved, so the error must say where it is."""
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)
        harness.port_error = ValueError("collision")

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert str(tmp_path / "new") in result.output
        assert "mael doctor new" in result.output
        assert "mael task reindex" in result.output

    def test_a_non_click_failure_after_the_move_still_names_the_recovery(
        self, tmp_path
    ):
        """A bare traceback would leave the user not knowing what state it is in."""
        _make_project(tmp_path)
        harness = MvProjectHarness(tmp_path)
        harness.port_error = OSError("disk gone")

        result = harness.run(["old", "new"])

        assert result.exit_code != 0
        assert "mael doctor new" in result.output


class TestRewritePathString:
    """`_rewrite_path_string` — the embedded paths inside env state files."""

    def _plan(self, tmp_path):
        return build_move_plan(
            old_name="old",
            new_name="new",
            projects_dir=tmp_path,
            worktree_folders=["old-alpha", "_main"],
            task_ids=[],
            ran_task_ids=set(),
            home=tmp_path / "home",
        )

    def test_rewrites_a_worktree_path_including_the_folder_rename(self, tmp_path):
        plan = self._plan(tmp_path)

        result = _rewrite_path_string(str(tmp_path / "old" / "old-alpha"), plan)

        assert result == str(tmp_path / "new" / "new-alpha")

    def test_rewrites_a_path_inside_a_renamed_worktree(self, tmp_path):
        plan = self._plan(tmp_path)

        result = _rewrite_path_string(
            str(tmp_path / "old" / "old-alpha" / "src" / "app.py"), plan
        )

        assert result == str(tmp_path / "new" / "new-alpha" / "src" / "app.py")

    def test_leaves_the_main_worktree_folder_name_alone(self, tmp_path):
        plan = self._plan(tmp_path)

        result = _rewrite_path_string(str(tmp_path / "old" / "_main"), plan)

        assert result == str(tmp_path / "new" / "_main")

    def test_rewrites_the_project_root_itself(self, tmp_path):
        plan = self._plan(tmp_path)

        assert _rewrite_path_string(str(tmp_path / "old"), plan) == str(
            tmp_path / "new"
        )

    def test_rewrites_a_log_path(self, tmp_path):
        plan = self._plan(tmp_path)
        mael_dir = tmp_path / "mael"

        with patch(
            "maelstrom.mv_project_cli.get_maelstrom_dir", return_value=mael_dir
        ):
            result = _rewrite_path_string(
                str(mael_dir / "logs" / "old" / "alpha" / "web.log"), plan
            )

        assert result == str(mael_dir / "logs" / "new" / "alpha" / "web.log")

    def test_leaves_an_unrelated_path_alone(self, tmp_path):
        plan = self._plan(tmp_path)

        assert _rewrite_path_string("/somewhere/else", plan) == "/somewhere/else"

    def test_does_not_rewrite_a_sibling_project_with_a_shared_prefix(self, tmp_path):
        """`old2` starts with `old` but is a different project."""
        plan = self._plan(tmp_path)

        result = _rewrite_path_string(str(tmp_path / "old2" / "x"), plan)

        assert result == str(tmp_path / "old2" / "x")
