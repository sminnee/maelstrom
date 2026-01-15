"""Tests for maelstrom.worktree module."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from unittest.mock import patch

from maelstrom.worktree import (
    WORKTREE_NAMES,
    WorktreeInfo,
    create_worktree,
    extract_project_name,
    get_next_worktree_name,
    has_root_worktree,
    list_worktrees,
    open_worktree,
    read_env_file,
    remove_worktree,
    sanitize_branch_name,
    substitute_env_vars,
    write_env_file,
)


class TestSanitizeBranchName:
    """Tests for sanitize_branch_name function."""

    def test_replaces_slashes(self):
        """Test that slashes are replaced with dashes."""
        assert sanitize_branch_name("feature/avatar-upload") == "feature-avatar-upload"
        assert sanitize_branch_name("fix/login/bug") == "fix-login-bug"

    def test_no_slashes(self):
        """Test branch name without slashes."""
        assert sanitize_branch_name("main") == "main"
        assert sanitize_branch_name("develop") == "develop"

    def test_multiple_slashes(self):
        """Test branch name with multiple slashes."""
        assert sanitize_branch_name("a/b/c/d") == "a-b-c-d"


class TestExtractProjectName:
    """Tests for extract_project_name function."""

    def test_ssh_url(self):
        """Test extracting name from SSH URL."""
        assert extract_project_name("git@github.com:sminnee/askastro.git") == "askastro"
        assert extract_project_name("git@github.com:user/repo.git") == "repo"

    def test_https_url(self):
        """Test extracting name from HTTPS URL."""
        assert extract_project_name("https://github.com/sminnee/askastro.git") == "askastro"
        assert extract_project_name("https://github.com/user/repo.git") == "repo"

    def test_without_git_suffix(self):
        """Test URL without .git suffix."""
        assert extract_project_name("https://github.com/user/repo") == "repo"
        assert extract_project_name("git@github.com:user/repo") == "repo"

    def test_trailing_slash(self):
        """Test URL with trailing slash."""
        assert extract_project_name("https://github.com/user/repo/") == "repo"
        assert extract_project_name("https://github.com/user/repo.git/") == "repo"


class TestWriteEnvFile:
    """Tests for write_env_file function."""

    def test_writes_env_vars(self):
        """Test writing environment variables to .env file."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_vars = {
                "PORT_BASE": "100",
                "FRONTEND_PORT": "1000",
                "SERVER_PORT": "1001",
            }

            write_env_file(worktree_path, env_vars)

            env_file = worktree_path / ".env"
            assert env_file.exists()

            content = env_file.read_text()
            assert "PORT_BASE=100" in content
            assert "FRONTEND_PORT=1000" in content
            assert "SERVER_PORT=1001" in content

    def test_sorts_variables(self):
        """Test that environment variables are sorted."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_vars = {
                "Z_VAR": "z",
                "A_VAR": "a",
                "M_VAR": "m",
            }

            write_env_file(worktree_path, env_vars)

            env_file = worktree_path / ".env"
            lines = env_file.read_text().strip().split("\n")
            assert lines[0] == "A_VAR=a"
            assert lines[1] == "M_VAR=m"
            assert lines[2] == "Z_VAR=z"

    def test_merges_existing_vars(self):
        """Test that existing variables are preserved and merged."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"PORT_BASE": "100", "DB_PORT": "1002"}
            existing = {"DATABASE_URL": "postgres://localhost:5432/mydb", "API_KEY": "secret"}

            write_env_file(worktree_path, generated, existing)

            env_file = worktree_path / ".env"
            content = env_file.read_text()
            assert "PORT_BASE=100" in content
            assert "DB_PORT=1002" in content
            assert "DATABASE_URL=postgres://localhost:5432/mydb" in content
            assert "API_KEY=secret" in content

    def test_generated_vars_override_existing(self):
        """Test that generated variables override existing on conflict."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"PORT_BASE": "100"}
            existing = {"PORT_BASE": "200"}  # Should be overwritten

            write_env_file(worktree_path, generated, existing)

            env_file = worktree_path / ".env"
            content = env_file.read_text()
            assert "PORT_BASE=100" in content
            assert "PORT_BASE=200" not in content

    def test_substitutes_variables_in_existing(self):
        """Test that $VAR references are substituted in existing vars."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"DB_PORT": "1002", "PORT_BASE": "100"}
            existing = {"DATABASE_URL": "postgres://localhost:$DB_PORT/mydb"}

            write_env_file(worktree_path, generated, existing)

            env_file = worktree_path / ".env"
            content = env_file.read_text()
            assert "DATABASE_URL=postgres://localhost:1002/mydb" in content

    def test_substitutes_worktree_variable(self):
        """Test that $WORKTREE is substituted in existing vars."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"WORKTREE": "alpha"}
            existing = {"DATABASE_NAME": "myapp_$WORKTREE"}

            write_env_file(worktree_path, generated, existing)

            env_file = worktree_path / ".env"
            content = env_file.read_text()
            assert "DATABASE_NAME=myapp_alpha" in content
            assert "WORKTREE=alpha" in content


class TestReadEnvFile:
    """Tests for read_env_file function."""

    def test_returns_empty_dict_if_not_exists(self):
        """Test that non-existent file returns empty dict."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            result = read_env_file(worktree_path)
            assert result == {}

    def test_parses_key_value_pairs(self):
        """Test parsing KEY=value format."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"
            env_file.write_text("FOO=bar\nBAZ=qux\n")

            result = read_env_file(worktree_path)
            assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments(self):
        """Test that comments are skipped."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"
            env_file.write_text("# This is a comment\nFOO=bar\n# Another comment\n")

            result = read_env_file(worktree_path)
            assert result == {"FOO": "bar"}

    def test_skips_empty_lines(self):
        """Test that empty lines are skipped."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"
            env_file.write_text("FOO=bar\n\n\nBAZ=qux\n")

            result = read_env_file(worktree_path)
            assert result == {"FOO": "bar", "BAZ": "qux"}

    def test_handles_values_with_equals(self):
        """Test values containing equals signs."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"
            env_file.write_text("DATABASE_URL=postgres://user:pass=123@host/db\n")

            result = read_env_file(worktree_path)
            assert result == {"DATABASE_URL": "postgres://user:pass=123@host/db"}


class TestSubstituteEnvVars:
    """Tests for substitute_env_vars function."""

    def test_simple_var(self):
        """Test substituting $VAR format."""
        env_vars = {"DB_PORT": "1002"}
        result = substitute_env_vars("postgres://localhost:$DB_PORT/mydb", env_vars)
        assert result == "postgres://localhost:1002/mydb"

    def test_braced_var(self):
        """Test substituting ${VAR} format."""
        env_vars = {"DB_PORT": "1002"}
        result = substitute_env_vars("postgres://localhost:${DB_PORT}/mydb", env_vars)
        assert result == "postgres://localhost:1002/mydb"

    def test_multiple_vars(self):
        """Test substituting multiple variables."""
        env_vars = {"HOST": "localhost", "PORT": "1002"}
        result = substitute_env_vars("http://$HOST:$PORT/api", env_vars)
        assert result == "http://localhost:1002/api"

    def test_missing_var_unchanged(self):
        """Test that unknown variables are left unchanged."""
        env_vars = {"DB_PORT": "1002"}
        result = substitute_env_vars("$UNKNOWN_VAR and $DB_PORT", env_vars)
        assert result == "$UNKNOWN_VAR and 1002"

    def test_no_vars(self):
        """Test string without variables."""
        env_vars = {"DB_PORT": "1002"}
        result = substitute_env_vars("just a plain string", env_vars)
        assert result == "just a plain string"

    def test_mixed_format(self):
        """Test mixing $VAR and ${VAR} formats."""
        env_vars = {"A": "1", "B": "2"}
        result = substitute_env_vars("$A and ${B}", env_vars)
        assert result == "1 and 2"


class TestWorktreeIntegration:
    """Integration tests for worktree operations."""

    @pytest.fixture
    def git_repo(self):
        """Create a temporary git repository for testing."""
        with TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test-repo"
            repo_path.mkdir()

            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            # Create initial commit
            (repo_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=repo_path,
                check=True,
                capture_output=True,
            )

            yield repo_path

    def test_has_root_worktree(self, git_repo):
        """Test detecting root worktree."""
        assert has_root_worktree(git_repo) is True

    def test_has_root_worktree_no_git(self):
        """Test with non-git directory."""
        with TemporaryDirectory() as tmpdir:
            assert has_root_worktree(Path(tmpdir)) is False

    def test_list_worktrees(self, git_repo):
        """Test listing worktrees."""
        worktrees = list_worktrees(git_repo)
        # Should have at least the main worktree
        assert len(worktrees) >= 1
        # Compare resolved paths to handle symlinks (e.g., /var -> /private/var on macOS)
        git_repo_resolved = git_repo.resolve()
        assert any(wt.path.resolve() == git_repo_resolved for wt in worktrees)

    def test_create_and_remove_worktree(self, git_repo):
        """Test creating and removing a worktree."""
        # Create worktree - should use first fixed name "alpha"
        worktree_path = create_worktree(git_repo, "feature/test")
        assert worktree_path.exists()
        assert worktree_path.name == "alpha"

        # Verify it's in the list with correct branch
        worktrees = list_worktrees(git_repo)
        assert any(wt.path == worktree_path and wt.branch == "feature/test" for wt in worktrees)

        # Remove worktree by branch name
        remove_worktree(git_repo, "feature/test")
        assert not worktree_path.exists()

    def test_create_multiple_worktrees_sequential_names(self, git_repo):
        """Test that multiple worktrees get sequential fixed names."""
        # Create first worktree
        path1 = create_worktree(git_repo, "feature/one")
        assert path1.name == "alpha"

        # Create second worktree
        path2 = create_worktree(git_repo, "feature/two")
        assert path2.name == "bravo"

        # Create third worktree
        path3 = create_worktree(git_repo, "feature/three")
        assert path3.name == "charlie"

        # Cleanup
        remove_worktree(git_repo, "feature/three")
        remove_worktree(git_repo, "feature/two")
        remove_worktree(git_repo, "feature/one")

    def test_get_next_worktree_name_reuses_freed_name(self, git_repo):
        """Test that freed names are reused."""
        # Create two worktrees
        create_worktree(git_repo, "feature/one")
        path2 = create_worktree(git_repo, "feature/two")
        assert path2.name == "bravo"

        # Remove the first one (alpha)
        remove_worktree(git_repo, "feature/one")

        # Create another - should reuse "alpha"
        path3 = create_worktree(git_repo, "feature/three")
        assert path3.name == "alpha"

        # Cleanup
        remove_worktree(git_repo, "feature/three")
        remove_worktree(git_repo, "feature/two")

    def test_remove_nonexistent_worktree(self, git_repo):
        """Test removing a worktree that doesn't exist."""
        with pytest.raises(RuntimeError, match="No worktree found for branch"):
            remove_worktree(git_repo, "nonexistent-branch")

    def test_create_worktree_runs_install_cmd(self, git_repo):
        """Test that install_cmd is run after worktree creation."""
        # Create .maelstrom.yaml with install_cmd
        config_file = git_repo / ".maelstrom.yaml"
        config_file.write_text("install_cmd: touch .installed\n")

        # Commit the config file so it's in the worktree
        subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add config"],
            cwd=git_repo,
            check=True,
            capture_output=True,
        )

        # Create worktree
        worktree_path = create_worktree(git_repo, "feature/install-test")

        # Verify install_cmd was run
        installed_marker = worktree_path / ".installed"
        assert installed_marker.exists(), "install_cmd should have created .installed file"

        # Cleanup - remove untracked file first so git worktree remove works without --force
        installed_marker.unlink()
        remove_worktree(git_repo, "feature/install-test")


class TestOpenWorktree:
    """Tests for open_worktree function."""

    def test_open_worktree_success(self):
        """Test opening a worktree with a valid command."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code")
                mock_run.assert_called_once_with(["code", str(worktree_path)], check=True)

    def test_open_worktree_command_not_found(self):
        """Test that FileNotFoundError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                with pytest.raises(RuntimeError, match="Command not found"):
                    open_worktree(worktree_path, "nonexistent-command")

    def test_open_worktree_command_fails(self):
        """Test that CalledProcessError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.CalledProcessError(1, "code")
                with pytest.raises(RuntimeError, match="Failed to open worktree"):
                    open_worktree(worktree_path, "code")
