"""Tests for mael doctor functionality."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from maelstrom.doctor import CheckStatus, run_doctor
from maelstrom.worktree import update_local_main

from tests.git_helpers import create_commit, run_git, setup_git_repo


def _create_project_repo(default_branch="main"):
    """Create a maelstrom-style project repo with remote. Returns (tmpdir, project_path)."""
    tmpdir = TemporaryDirectory()
    tmp = Path(tmpdir.name)

    # Create source repo
    source_path = tmp / "source"
    source_path.mkdir()
    setup_git_repo(source_path)
    create_commit(source_path, "README.md", "# Test", "Initial commit")
    run_git(source_path, "branch", "-M", default_branch)

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

    # Configure like add_project does (core.bare stays true from bare clone)
    run_git(project_path, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    run_git(project_path, "config", "notes.rewriteRef", "refs/notes/*")
    run_git(project_path, "config", "user.email", "test@test.com")
    run_git(project_path, "config", "user.name", "Test")
    run_git(project_path, "fetch", "origin")

    # Detach HEAD (like add_project does)
    head_sha = run_git(project_path, "rev-parse", "HEAD").stdout.strip()
    run_git(project_path, "update-ref", "--no-deref", "HEAD", head_sha)

    # The default branch lives in _main, like add_project does
    run_git(project_path, "worktree", "add", str(project_path / "_main"), default_branch)
    run_git(
        project_path, "branch", "--set-upstream-to",
        f"origin/{default_branch}", default_branch,
    )

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
            # main is checked out in _main; commit there to get ahead of origin
            wt_path = project_path / "_main"
            create_commit(wt_path, "local.txt", "local", "Local commit")

            # Detach the worktree so main isn't checked out
            run_git(wt_path, "checkout", "--detach", "HEAD")

            result = update_local_main(project_path)
            assert result.status == "warning"
            assert "ahead" in result.message

    def test_skips_when_already_in_sync(self):
        """Skips when local main equals origin/main."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            result = update_local_main(project_path)
            assert result.status == "skipped"

    def test_fast_forwards_when_main_checked_out(self):
        """Fast-forwards main via merge when checked out in a worktree."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # main is checked out in _main
            wt_path = project_path / "_main"

            # Push a new commit to remote so local is behind
            source_path = Path(tmpdir.name) / "source"
            create_commit(source_path, "new.txt", "new", "New commit")
            remote_path = Path(tmpdir.name) / "remote.git"
            run_git(source_path, "push", str(remote_path), "main")
            run_git(project_path, "fetch", "origin")

            # Get origin/main sha before update
            origin_sha = run_git(
                project_path, "rev-parse", "refs/remotes/origin/main"
            ).stdout.strip()

            result = update_local_main(project_path)
            assert result.status == "updated"
            assert "Fast-forwarded" in result.message

            # Verify the ref was actually updated
            local_sha = run_git(
                project_path, "rev-parse", "refs/heads/main"
            ).stdout.strip()
            assert local_sha == origin_sha

            # Clean up
            run_git(project_path, "worktree", "remove", str(wt_path))


class TestDoctor:
    """Tests for run_doctor()."""

    @pytest.fixture(autouse=True)
    def _isolate_home(self, tmp_path, monkeypatch):
        """Point ~ at a scratch dir so the secret-perms check never reads or
        chmods the developer's real ~/.maelstrom during the suite."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

    def test_healthy_project(self):
        """All checks pass on a healthy project."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            result = run_doctor(project_path)
            assert result.issues_found == 0
            assert all(c.status == CheckStatus.OK for c in result.checks)

    def test_every_result_carries_its_check_name(self):
        """Callers select a result by name, so no result may go unnamed."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            result = run_doctor(project_path)

            names = [c.name for c in result.checks]
            assert all(names)
            assert len(set(names)) == len(names)
            assert "main_upstream" in names

    def test_fixes_wrong_core_bare(self):
        """Fixes core.bare when set to false instead of true."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Break core.bare
            run_git(project_path, "config", "core.bare", "false")

            result = run_doctor(project_path)

            core_bare_check = [c for c in result.checks if "core.bare" in c.message][0]
            assert core_bare_check.status == CheckStatus.FIXED

    def test_warns_when_main_is_in_a_nato_worktree(self):
        """A project predating _main holds main in a workspace that cannot be used."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # Undo the _main layout and put main in alpha, as older projects did.
            run_git(project_path, "worktree", "remove", str(project_path / "_main"))
            alpha = project_path / "test-repo-alpha"
            run_git(project_path, "worktree", "add", str(alpha), "main")

            result = run_doctor(project_path)

            check = [c for c in result.checks if "test-repo-alpha" in c.message][0]
            assert check.status == CheckStatus.WARNING
            assert "worktree add" in check.message

    def test_warns_when_there_is_no_main_worktree(self):
        """No _main at all is also worth flagging."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path, "worktree", "remove", str(project_path / "_main"))

            result = run_doctor(project_path)

            check = [c for c in result.checks if "No _main" in c.message][0]
            assert check.status == CheckStatus.WARNING

    def test_warns_when_main_worktree_is_detached(self):
        """A detached _main is not a main checkout, whatever the folder is called."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path / "_main", "checkout", "--detach", "HEAD")

            result = run_doctor(project_path)

            check = [c for c in result.checks if "does not hold main" in c.message][0]
            assert check.status == CheckStatus.WARNING

    def test_main_in_a_nato_worktree_is_told_to_free_it_first(self):
        """git refuses `worktree add main` while another worktree holds it."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path, "worktree", "remove", str(project_path / "_main"))
            alpha = project_path / "test-repo-alpha"
            run_git(project_path, "worktree", "add", str(alpha), "main")

            result = run_doctor(project_path)

            check = [c for c in result.checks if "test-repo-alpha" in c.message][0]
            assert "checkout --detach" in check.message

    def test_stops_early_without_mael_marker(self):
        """Stops checking if .mael marker is missing."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            (project_path / ".mael").unlink()

            result = run_doctor(project_path)
            assert len(result.checks) == 1
            assert result.checks[0].status == CheckStatus.ERROR
            assert ".mael" in result.checks[0].message

    def test_sets_notes_rewrite_ref_when_unset(self):
        """Sets notes.rewriteRef so review notes survive a rebase."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path, "config", "--unset-all", "notes.rewriteRef")

            result = run_doctor(project_path)

            notes_check = [c for c in result.checks if "notes.rewriteRef" in c.message][0]
            assert notes_check.status == CheckStatus.FIXED
            value = run_git(project_path, "config", "--get", "notes.rewriteRef").stdout.strip()
            assert value == "refs/notes/*"

    @staticmethod
    def _upstream_check(result):
        """The main-upstream check, selected by name rather than by message."""
        checks = [c for c in result.checks if c.name == "main_upstream"]
        assert len(checks) == 1, f"expected 1 main_upstream check, got {checks}"
        return checks[0]

    @staticmethod
    def _upstream_config(project_path):
        return (
            run_git(project_path, "config", "--get", "branch.main.remote").stdout.strip(),
            run_git(project_path, "config", "--get", "branch.main.merge").stdout.strip(),
        )

    def test_sets_the_main_upstream_when_unset(self):
        """A bare clone writes no branch.main.*, so main tracks nothing."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path, "config", "--unset", "branch.main.remote")
            run_git(project_path, "config", "--unset", "branch.main.merge")

            result = run_doctor(project_path)

            check = self._upstream_check(result)
            assert check.status == CheckStatus.FIXED
            assert check.message == "main had no upstream → set to origin/main"
            assert self._upstream_config(project_path) == ("origin", "refs/heads/main")

    def test_names_the_upstream_it_repointed(self):
        """A main tracking elsewhere is repointed, and the report says so."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path, "config", "branch.main.remote", "upstream")
            run_git(project_path, "config", "branch.main.merge", "refs/heads/trunk")

            result = run_doctor(project_path)

            check = self._upstream_check(result)
            assert check.status == CheckStatus.FIXED
            assert check.message == "main tracked upstream/trunk → set to origin/main"
            assert self._upstream_config(project_path) == ("origin", "refs/heads/main")

    def test_reports_an_error_when_origin_main_is_missing(self):
        """--set-upstream-to cannot run without the ref. _check_origin_main
        already names that cause, so this check does not repeat it."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            run_git(project_path, "config", "--unset", "branch.main.remote")
            run_git(project_path, "config", "--unset", "branch.main.merge")
            run_git(project_path, "update-ref", "-d", "refs/remotes/origin/main")

            result = run_doctor(project_path)

            check = self._upstream_check(result)
            assert check.status == CheckStatus.ERROR

    def test_sets_the_upstream_on_a_non_main_default_branch(self):
        """8 of ~50 local projects default to develop, master or 6, not main."""
        tmpdir, project_path = _create_project_repo(default_branch="develop")
        with tmpdir:
            run_git(project_path, "config", "--unset", "branch.develop.remote")
            run_git(project_path, "config", "--unset", "branch.develop.merge")

            result = run_doctor(project_path)

            check = self._upstream_check(result)
            assert check.status == CheckStatus.FIXED
            assert check.message == "develop had no upstream → set to origin/develop"
            remote = run_git(project_path, "config", "--get", "branch.develop.remote")
            merge = run_git(project_path, "config", "--get", "branch.develop.merge")
            assert remote.stdout.strip() == "origin"
            assert merge.stdout.strip() == "refs/heads/develop"

    def test_a_non_main_default_branch_project_is_healthy(self):
        """A develop-default project must not report spurious issues."""
        tmpdir, project_path = _create_project_repo(default_branch="develop")
        with tmpdir:
            result = run_doctor(project_path)

            assert result.issues_found == 0, [
                (c.name, c.message) for c in result.checks
                if c.status != CheckStatus.OK
            ]

    def test_leaves_a_configured_main_upstream_alone(self):
        """Already tracking origin/main: report OK, rewrite nothing."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            result = run_doctor(project_path)

            check = self._upstream_check(result)
            assert check.status == CheckStatus.OK
            assert check.message == "main upstream is origin/main"
            assert self._upstream_config(project_path) == ("origin", "refs/heads/main")

    def test_warns_local_main_ahead(self):
        """Warns when local main is ahead of origin/main."""
        tmpdir, project_path = _create_project_repo()
        with tmpdir:
            # main is checked out in _main; commit there, then detach
            wt_path = project_path / "_main"
            create_commit(wt_path, "local.txt", "local", "Local commit")
            run_git(wt_path, "checkout", "--detach", "HEAD")

            result = run_doctor(project_path)

            main_check = [c for c in result.checks if "ahead" in c.message]
            assert len(main_check) == 1
            assert main_check[0].status == CheckStatus.WARNING


class TestCheckSecretFilePerms:
    """Tests for the _check_secret_file_perms doctor check."""

    @staticmethod
    def _mode(path) -> int:
        import os
        import stat

        return stat.S_IMODE(os.stat(path).st_mode)

    def _setup(self, tmp_path, monkeypatch):
        """Wire up a fake home + a single worktree under project_path."""
        from types import SimpleNamespace

        import maelstrom.doctor as doctor

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        mael_dir = tmp_path / ".maelstrom"
        mael_dir.mkdir()
        config = mael_dir / "config.yaml"
        config.write_text("linear:\n  api_key: secret\n")
        allocations = mael_dir / "port_allocations.json"
        allocations.write_text("{}\n")

        project_path = tmp_path / "proj"
        project_path.mkdir()
        wt_path = project_path / "proj-bravo"
        wt_path.mkdir()
        env_file = wt_path / ".env"
        env_file.write_text("PORT_BASE=300\n")

        # Stub enumeration: project root + one worktree.
        worktrees = [
            SimpleNamespace(path=project_path),
            SimpleNamespace(path=wt_path),
        ]
        monkeypatch.setattr(doctor, "list_worktrees", lambda _p: worktrees)
        return project_path, mael_dir, config, allocations, env_file

    def test_ok_when_all_tight(self, tmp_path, monkeypatch):
        import os

        from maelstrom.doctor import _check_secret_file_perms

        project_path, mael_dir, config, allocations, env_file = self._setup(
            tmp_path, monkeypatch
        )
        os.chmod(mael_dir, 0o700)
        os.chmod(config, 0o600)
        os.chmod(allocations, 0o600)
        os.chmod(env_file, 0o600)

        result = _check_secret_file_perms(project_path)
        assert result.status == CheckStatus.OK

    def test_fixes_loose_files_and_names_them(self, tmp_path, monkeypatch):
        import os

        from maelstrom.doctor import _check_secret_file_perms

        project_path, mael_dir, config, allocations, env_file = self._setup(
            tmp_path, monkeypatch
        )
        os.chmod(mael_dir, 0o700)
        os.chmod(allocations, 0o600)
        os.chmod(config, 0o644)
        os.chmod(env_file, 0o644)

        result = _check_secret_file_perms(project_path)

        assert result.status == CheckStatus.FIXED
        assert "config.yaml" in result.message
        assert "bravo/.env" in result.message
        # Files actually tightened.
        assert self._mode(config) == 0o600
        assert self._mode(env_file) == 0o600

    def test_rerun_after_fix_reports_ok(self, tmp_path, monkeypatch):
        import os

        from maelstrom.doctor import _check_secret_file_perms

        project_path, mael_dir, config, allocations, env_file = self._setup(
            tmp_path, monkeypatch
        )
        os.chmod(mael_dir, 0o700)
        os.chmod(allocations, 0o600)
        os.chmod(config, 0o600)
        os.chmod(env_file, 0o644)

        assert _check_secret_file_perms(project_path).status == CheckStatus.FIXED
        assert _check_secret_file_perms(project_path).status == CheckStatus.OK
