"""Fixtures for the mael CLI's suite."""

import tempfile
from pathlib import Path

import pytest
from domain_fixtures import (  # noqa: F401  (pytest fixtures, found by name)
    _block_real_claude_branch_gen,
    _block_real_cmux,
    _isolate_notebook_root,
    _mark_test_commands_production,
    project_with_worktree,
    state_db,
    store,
)


@pytest.fixture
def collapsible_project():
    """A bare-clone project with a worktree on ``feature/work``.

    The source → bare-remote → working-clone pattern, so ``origin`` is a real
    remote and a rebase inside the command under test can fetch. Shared by the
    squash and uncommit suites, which drive the same two commands.

    Named apart from ``domain_fixtures``' ``project_with_worktree``, which
    yields a third element and patches ``get_maelstrom_dir``. Two fixtures of
    one name, one shadowing the other by import, is how that suite's ``F811``
    suppressions arose.

    Yields ``(project_path, worktree_path)``.
    """
    import subprocess

    from git_helpers import create_commit, run_git, setup_git_repo

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        source_path = tmp / "source"
        source_path.mkdir()
        setup_git_repo(source_path)
        create_commit(source_path, "README.md", "# Test\n", "Initial commit")
        run_git(source_path, "branch", "-M", "main")

        remote_path = tmp / "remote.git"
        subprocess.run(
            ["git", "clone", "--bare", str(source_path), str(remote_path)],
            check=True,
            capture_output=True,
        )

        project_path = tmp / "test-repo"
        project_path.mkdir()
        git_dir = project_path / ".git"
        subprocess.run(
            ["git", "clone", "--bare", str(remote_path), str(git_dir)],
            check=True,
            capture_output=True,
        )
        run_git(project_path, "config", "core.bare", "true")
        run_git(
            project_path,
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/remotes/origin/*",
        )
        run_git(project_path, "config", "user.email", "test@test.com")
        run_git(project_path, "config", "user.name", "Test")
        run_git(project_path, "fetch", "origin")

        head_sha = run_git(project_path, "rev-parse", "HEAD").stdout.strip()
        run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

        worktree_path = project_path / "test-repo-alpha"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                "feature/work",
                str(worktree_path),
                "origin/main",
            ],
            cwd=project_path,
            check=True,
            capture_output=True,
        )
        run_git(worktree_path, "config", "user.email", "test@test.com")
        run_git(worktree_path, "config", "user.name", "Test")

        yield project_path, worktree_path


@pytest.fixture(autouse=True)
def _reset_task_db():
    """Forget the task CLI's cached database between tests.

    ``task_cli`` opens the state database once and keeps it in a module global,
    which is right in production — one process is one invocation. In the suite
    one process runs every test, so without this a test's temporary database
    would serve the next one, and the leak is invisible: the reads succeed and
    answer about the wrong notebook.
    """
    from mael_cli import task_cli

    task_cli._DB = None
    task_cli._CHECKED = False
    yield
    if task_cli._DB is not None:
        task_cli._DB.close()
    task_cli._DB = None
    task_cli._CHECKED = False
