"""Tests for mael doctor functionality."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from maelstrom.doctor import CheckStatus, run_doctor
from maelstrom.worktree import update_local_main

from tests.git_helpers import create_commit, run_git, setup_git_repo


def _create_project_repo():
    """Create a maelstrom-style project repo with remote. Returns (tmpdir, project_path)."""
    tmpdir = TemporaryDirectory()
    tmp = Path(tmpdir.name)

    # Create source repo
    source_path = tmp / "source"
    source_path.mkdir()
    setup_git_repo(source_path)
    create_commit(source_path, "README.md", "# Test", "Initial commit")
    run_git(source_path, "branch", "-M", "main")

    # Clone as bare to create remote
    remote_path = tmp / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source_path), str(remote_path)],
        check=True, capture_output=True,
    )

    # Create project directory with bare clone structure
    project_path = tmp / "test-repo"
    project_path.mkdir()
    git_dir = project_path / ".git"
    subprocess.run(
        ["git", "clone", "--bare", str(remote_path), str(git_dir)],
        check=True, capture_output=True,
    )

    # Configure like add_project does
    run_git(project_path, "config", "core.bare", "false")
    run_git(project_path, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    run_git(project_path, "config", "user.email", "test@test.com")
    run_git(project_path, "config", "user.name", "Test")
    run_git(project_path, "fetch", "origin")

    # Detach HEAD (like add_project does)
    head_sha = run_git(project_path, "rev-parse", "HEAD").stdout.strip()
    run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

    # Create .mael marker
    (project_path / ".mael").touch()

    return tmpdir, project_path


class TestUpdateLocalMain:
    """Tests for update_local_main()."""

    def test_fast_forwards_when_behind(self):
        """Local main is fast-forwarded when origin/main is ahead."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Get current main SHA
            old_sha = run_git(project_path, "rev-parse", "main").stdout.strip()

            # Add a commit to the remote source, then fetch
            source_path = Path(tmpdir.name) / "source"
            create_commit(source_path, "new.txt", "new content", "New commit")
            # Push to bare remote
            remote_path = Path(tmpdir.name) / "remote.git"
            run_git(source_path, "push", str(remote_path), "main")

            # Fetch into project
            run_git(project_path, "fetch", "origin")

            # Verify local main is behind
            local_sha = run_git(project_path, "rev-parse", "main").stdout.strip()
            origin_sha = run_git(project_path, "rev-parse", "origin/main").stdout.strip()
            assert local_sha == old_sha
            assert origin_sha != old_sha

            # update_local_main should fast-forward
            result = update_local_main(project_path)
            assert result.status == "updated"

            # Verify local main now matches origin/main
            new_local_sha = run_git(project_path, "rev-parse", "main").stdout.strip()
            assert new_local_sha == origin_sha

    def test_warns_when_ahead(self):
        """Returns warning when local main is ahead of origin/main."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Create a worktree on main to make commits
            wt_path = project_path / "test-repo-alpha"
            run_git(project_path, "worktree", "add", str(wt_path), "main")

            # Add a local commit to main
            create_commit(wt_path, "local.txt", "local", "Local commit")

            # Detach the worktree so main isn't checked out
            run_git(wt_path, "checkout", "--detach", "HEAD")

            result = update_local_main(project_path)
            assert result.status == "warning"
            assert "ahead" in result.message

            # Clean up worktree
            run_git(project_path, "worktree", "remove", str(wt_path))

    def test_skips_when_already_in_sync(self):
        """Skips when local main equals origin/main."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            result = update_local_main(project_path)
            assert result.status == "skipped"

    def test_skips_when_main_checked_out(self):
        """Skips when main is checked out in a worktree."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Create a worktree on main
            wt_path = project_path / "test-repo-alpha"
            run_git(project_path, "worktree", "add", str(wt_path), "main")

            # Push a new commit to remote so local is behind
            source_path = Path(tmpdir.name) / "source"
            create_commit(source_path, "new.txt", "new", "New commit")
            remote_path = Path(tmpdir.name) / "remote.git"
            run_git(source_path, "push", str(remote_path), "main")
            run_git(project_path, "fetch", "origin")

            result = update_local_main(project_path)
            assert result.status == "skipped"
            assert "checked out" in result.message

            # Clean up
            run_git(project_path, "worktree", "remove", str(wt_path))


class TestDoctor:
    """Tests for run_doctor()."""

    def test_healthy_project(self):
        """All checks pass on a healthy project."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            result = run_doctor(project_path)
            assert result.issues_found == 0
            assert all(c.status == CheckStatus.OK for c in result.checks)

    def test_fixes_missing_core_bare(self):
        """Fixes core.bare when not set to false."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Break core.bare
            run_git(project_path, "config", "core.bare", "true")

            result = run_doctor(project_path)

            core_bare_check = [c for c in result.checks if "core.bare" in c.message][0]
            assert core_bare_check.status == CheckStatus.FIXED

    def test_stops_early_without_mael_marker(self):
        """Stops checking if .mael marker is missing."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            (project_path / ".mael").unlink()

            result = run_doctor(project_path)
            assert len(result.checks) == 1
            assert result.checks[0].status == CheckStatus.ERROR
            assert ".mael" in result.checks[0].message

    def test_warns_local_main_ahead(self):
        """Warns when local main is ahead of origin/main."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Create a worktree on main, commit, then detach
            wt_path = project_path / "test-repo-alpha"
            run_git(project_path, "worktree", "add", str(wt_path), "main")
            create_commit(wt_path, "local.txt", "local", "Local commit")
            run_git(wt_path, "checkout", "--detach", "HEAD")

            result = run_doctor(project_path)

            main_check = [c for c in result.checks if "ahead" in c.message]
            assert len(main_check) == 1
            assert main_check[0].status == CheckStatus.WARNING

            run_git(project_path, "worktree", "remove", str(wt_path))
