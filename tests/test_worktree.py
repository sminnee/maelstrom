"""Tests for maelstrom.worktree module."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from unittest.mock import patch

from maelstrom.worktree import (
    CLAUDE_HEADER_END,
    CLAUDE_HEADER_STARTS,
    MAIN_BRANCH,
    WORKTREE_NAMES,
    WorktreeInfo,
    close_worktree,
    create_worktree,
    extract_project_name,
    extract_worktree_name_from_folder,
    find_closed_worktree,
    get_next_worktree_name,
    get_worktree_folder_name,
    has_root_worktree,
    is_worktree_closed,
    list_worktrees,
    open_claude_code,
    open_worktree,
    read_env_file,
    recycle_worktree,
    remove_worktree,
    remove_worktree_by_path,
    sanitize_branch_name,
    substitute_env_vars,
    update_claude_md,
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


class TestWorktreeFolderNaming:
    """Tests for worktree folder naming helper functions."""

    def test_get_worktree_folder_name(self):
        """Test generating folder names from project and worktree."""
        assert get_worktree_folder_name("askastro", "alpha") == "askastro-alpha"
        assert get_worktree_folder_name("askastro", "bravo") == "askastro-bravo"
        assert get_worktree_folder_name("my-project", "charlie") == "my-project-charlie"

    def test_extract_worktree_name_from_folder(self):
        """Test extracting worktree name from folder name."""
        assert extract_worktree_name_from_folder("askastro", "askastro-alpha") == "alpha"
        assert extract_worktree_name_from_folder("askastro", "askastro-bravo") == "bravo"
        assert extract_worktree_name_from_folder("my-project", "my-project-charlie") == "charlie"

    def test_extract_worktree_name_from_folder_invalid(self):
        """Test that invalid folder names return None."""
        # Wrong project prefix
        assert extract_worktree_name_from_folder("askastro", "other-alpha") is None
        # Not a valid worktree name
        assert extract_worktree_name_from_folder("askastro", "askastro-invalid") is None
        # No prefix
        assert extract_worktree_name_from_folder("askastro", "alpha") is None

    def test_extract_worktree_name_project_with_dashes(self):
        """Test extracting worktree name when project has dashes."""
        # Project name has dashes, folder should still work correctly
        assert extract_worktree_name_from_folder("ask-astro", "ask-astro-alpha") == "alpha"
        assert extract_worktree_name_from_folder("ask-astro", "ask-astro-bravo") == "bravo"


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
        # Create worktree - should use first fixed name "alpha" with project prefix
        worktree_path = create_worktree(git_repo, "feature/test")
        assert worktree_path.exists()
        assert worktree_path.name == "test-repo-alpha"

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
        assert path1.name == "test-repo-alpha"

        # Create second worktree
        path2 = create_worktree(git_repo, "feature/two")
        assert path2.name == "test-repo-bravo"

        # Create third worktree
        path3 = create_worktree(git_repo, "feature/three")
        assert path3.name == "test-repo-charlie"

        # Cleanup
        remove_worktree(git_repo, "feature/three")
        remove_worktree(git_repo, "feature/two")
        remove_worktree(git_repo, "feature/one")

    def test_get_next_worktree_name_reuses_freed_name(self, git_repo):
        """Test that freed names are reused."""
        # Create two worktrees
        create_worktree(git_repo, "feature/one")
        path2 = create_worktree(git_repo, "feature/two")
        assert path2.name == "test-repo-bravo"

        # Remove the first one (alpha)
        remove_worktree(git_repo, "feature/one")

        # Create another - should reuse "alpha"
        path3 = create_worktree(git_repo, "feature/three")
        assert path3.name == "test-repo-alpha"

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
        """Test opening a worktree with a valid command (no chat)."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code", open_chat=False)
                mock_run.assert_called_once_with(["code", str(worktree_path)], check=True)

    def test_open_worktree_command_not_found(self):
        """Test that FileNotFoundError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.side_effect = FileNotFoundError()
                with pytest.raises(RuntimeError, match="Command not found"):
                    open_worktree(worktree_path, "nonexistent-command", open_chat=False)

    def test_open_worktree_command_fails(self):
        """Test that CalledProcessError is wrapped in RuntimeError."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.CalledProcessError(1, "code")
                with pytest.raises(RuntimeError, match="Failed to open worktree"):
                    open_worktree(worktree_path, "code", open_chat=False)


class TestIsWorktreeClosed:
    """Tests for is_worktree_closed function."""

    def test_returns_true_when_detached_at_origin_main(self):
        """Test returns True for detached worktree synced to origin/main."""
        # branch="" means detached HEAD
        wt = WorktreeInfo(path=Path("/fake"), branch="", commit="abc123")
        with patch("maelstrom.worktree.get_worktree_dirty_files", return_value=[]):
            with patch("maelstrom.worktree.get_commits_ahead", return_value=0):
                # Mock run_cmd to return matching SHAs for HEAD and origin/main
                def mock_run_cmd(cmd, **kwargs):
                    class Result:
                        returncode = 0
                        stdout = "abc123\n"
                    return Result()
                with patch("maelstrom.worktree.run_cmd", mock_run_cmd):
                    assert is_worktree_closed(wt) is True

    def test_returns_false_when_on_branch(self):
        """Test returns False for worktree on a branch (not detached)."""
        wt = WorktreeInfo(path=Path("/fake"), branch="feature/test", commit="abc123")
        # Should return False immediately because it's on a branch
        assert is_worktree_closed(wt) is False

    def test_returns_false_when_head_differs_from_origin_main(self):
        """Test returns False when HEAD doesn't match origin/main."""
        # branch="" means detached HEAD
        wt = WorktreeInfo(path=Path("/fake"), branch="", commit="abc123")
        with patch("maelstrom.worktree.get_worktree_dirty_files", return_value=[]):
            with patch("maelstrom.worktree.get_commits_ahead", return_value=0):
                # Mock run_cmd to return different SHAs
                call_count = [0]
                def mock_run_cmd(cmd, **kwargs):
                    call_count[0] += 1
                    class Result:
                        returncode = 0
                        stdout = "abc123\n" if call_count[0] == 1 else "def456\n"
                    return Result()
                with patch("maelstrom.worktree.run_cmd", mock_run_cmd):
                    assert is_worktree_closed(wt) is False

    def test_returns_false_for_dirty_worktree(self):
        """Test returns False for worktree with uncommitted changes."""
        wt = WorktreeInfo(path=Path("/fake"), branch="feature/test", commit="abc123")
        with patch("maelstrom.worktree.get_worktree_dirty_files", return_value=["file.txt"]):
            assert is_worktree_closed(wt) is False

    def test_returns_false_for_worktree_with_unpushed_commits(self):
        """Test returns False for worktree with commits ahead."""
        wt = WorktreeInfo(path=Path("/fake"), branch="feature/test", commit="abc123")
        with patch("maelstrom.worktree.get_worktree_dirty_files", return_value=[]):
            with patch("maelstrom.worktree.get_commits_ahead", return_value=2):
                assert is_worktree_closed(wt) is False


class TestFindClosedWorktree:
    """Tests for find_closed_worktree function."""

    def test_returns_none_when_no_worktrees(self):
        """Test returns None when no worktrees exist."""
        with patch("maelstrom.worktree.list_worktrees", return_value=[]):
            result = find_closed_worktree(Path("/fake/project"))
            assert result is None

    def test_returns_none_when_no_closed_worktrees(self):
        """Test returns None when no worktrees are closed."""
        wt = WorktreeInfo(path=Path("/fake/project/alpha"), branch="feature/test", commit="abc")
        with patch("maelstrom.worktree.list_worktrees", return_value=[wt]):
            with patch("maelstrom.worktree.is_worktree_closed", return_value=False):
                result = find_closed_worktree(Path("/fake/project"))
                assert result is None

    def test_returns_closed_worktree(self):
        """Test returns a closed worktree when one exists."""
        wt = WorktreeInfo(path=Path("/fake/project/alpha"), branch=MAIN_BRANCH, commit="abc")
        with patch("maelstrom.worktree.list_worktrees", return_value=[wt]):
            with patch("maelstrom.worktree.is_worktree_closed", return_value=True):
                result = find_closed_worktree(Path("/fake/project"))
                assert result == wt

    def test_skips_project_root(self):
        """Test that the project root is skipped."""
        project_path = Path("/fake/project")
        wt_root = WorktreeInfo(path=project_path, branch=MAIN_BRANCH, commit="abc")
        wt_alpha = WorktreeInfo(path=project_path / "alpha", branch=MAIN_BRANCH, commit="abc")
        with patch("maelstrom.worktree.list_worktrees", return_value=[wt_root, wt_alpha]):
            with patch("maelstrom.worktree.is_worktree_closed", return_value=True):
                result = find_closed_worktree(project_path)
                assert result == wt_alpha


class TestCloseWorktreeIntegration:
    """Integration tests for close_worktree function."""

    @pytest.fixture
    def git_repo_with_remote(self):
        """Create a bare git repository with a remote for testing close operations.

        This mimics maelstrom's actual structure:
        - Project root has a .git subdirectory (bare clone)
        - Worktrees are created as subdirectories
        - The project root itself isn't a worktree (no files checked out)
        """
        with TemporaryDirectory() as tmpdir:
            # Create a "remote" bare repository with initial content
            remote_path = Path(tmpdir) / "remote.git"
            source_path = Path(tmpdir) / "source"
            source_path.mkdir()

            # Initialize source repo with a commit
            subprocess.run(["git", "init"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=source_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=source_path, check=True, capture_output=True
            )
            (source_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=source_path, check=True, capture_output=True
            )
            # Ensure branch is named 'main' regardless of git's default
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=source_path, check=True, capture_output=True
            )

            # Clone as bare to create the remote
            subprocess.run(
                ["git", "clone", "--bare", str(source_path), str(remote_path)],
                check=True, capture_output=True
            )

            # Create project directory with bare clone structure (like maelstrom does)
            project_path = Path(tmpdir) / "test-repo"
            project_path.mkdir()

            # Clone as bare into .git subdirectory
            git_dir = project_path / ".git"
            subprocess.run(
                ["git", "clone", "--bare", str(remote_path), str(git_dir)],
                check=True, capture_output=True
            )

            # Configure the bare repo to work with worktrees
            subprocess.run(
                ["git", "config", "core.bare", "false"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
                cwd=project_path, check=True, capture_output=True
            )
            # Configure user settings for commits in worktrees
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )

            # Fetch to get remote tracking refs
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=project_path, check=True, capture_output=True
            )

            yield project_path

    def test_close_fails_with_dirty_files(self, git_repo_with_remote):
        """Test that close fails when worktree has uncommitted changes."""
        # Create a worktree
        worktree_path = create_worktree(git_repo_with_remote, "feature/test")

        # Create a dirty file
        (worktree_path / "dirty.txt").write_text("uncommitted")

        result = close_worktree(worktree_path)

        assert result.success is False
        assert result.had_dirty_files is True

        # Cleanup
        (worktree_path / "dirty.txt").unlink()
        remove_worktree(git_repo_with_remote, "feature/test")

    def test_close_fails_with_unpushed_commits(self, git_repo_with_remote):
        """Test that close fails when worktree has commits not merged to main."""
        # Create a worktree
        worktree_path = create_worktree(git_repo_with_remote, "feature/test")

        # Make a commit that's ahead of origin/main
        (worktree_path / "new_file.txt").write_text("new content")
        subprocess.run(["git", "add", "."], cwd=worktree_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Unpushed commit"],
            cwd=worktree_path, check=True, capture_output=True
        )

        result = close_worktree(worktree_path)

        assert result.success is False
        assert result.had_unpushed_commits is True

        # Cleanup
        remove_worktree(git_repo_with_remote, "feature/test")

    def test_close_succeeds_when_clean(self, git_repo_with_remote):
        """Test that close succeeds when worktree is clean and synced."""
        # Create a worktree on a feature branch
        worktree_path = create_worktree(git_repo_with_remote, "feature/clean")
        worktree_folder = worktree_path.name  # Full folder name like "test-repo-alpha"

        # The worktree is already clean (no changes, no commits ahead)
        # close_worktree should sync, verify, and detach at origin/main
        result = close_worktree(worktree_path)

        assert result.success is True
        assert "closed" in result.message.lower()

        # Verify HEAD matches origin/main
        head_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_path, check=True, capture_output=True, text=True
        )
        main_result = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=worktree_path, check=True, capture_output=True, text=True
        )
        assert head_result.stdout.strip() == main_result.stdout.strip()

        # Verify HEAD is detached (not on a branch)
        branch_result = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=worktree_path, capture_output=True, text=True
        )
        assert branch_result.returncode != 0, "HEAD should be detached after close"

        # Cleanup - use full folder name since branch is detached
        remove_worktree_by_path(git_repo_with_remote, worktree_folder)


class TestRecycleWorktreeIntegration:
    """Integration tests for recycle_worktree function."""

    @pytest.fixture
    def git_repo_with_remote(self):
        """Create a bare git repository with a remote for testing recycle operations.

        This mimics maelstrom's actual structure:
        - Project root has a .git subdirectory (bare clone)
        - Worktrees are created as subdirectories
        - The project root itself isn't a worktree (no files checked out)
        """
        with TemporaryDirectory() as tmpdir:
            # Create a "remote" bare repository with initial content
            remote_path = Path(tmpdir) / "remote.git"
            source_path = Path(tmpdir) / "source"
            source_path.mkdir()

            # Initialize source repo with a commit
            subprocess.run(["git", "init"], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=source_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=source_path, check=True, capture_output=True
            )
            (source_path / "README.md").write_text("# Test")
            subprocess.run(["git", "add", "."], cwd=source_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=source_path, check=True, capture_output=True
            )
            # Ensure branch is named 'main' regardless of git's default
            subprocess.run(
                ["git", "branch", "-M", "main"],
                cwd=source_path, check=True, capture_output=True
            )

            # Clone as bare to create the remote
            subprocess.run(
                ["git", "clone", "--bare", str(source_path), str(remote_path)],
                check=True, capture_output=True
            )

            # Create project directory with bare clone structure (like maelstrom does)
            project_path = Path(tmpdir) / "test-repo"
            project_path.mkdir()

            # Clone as bare into .git subdirectory
            git_dir = project_path / ".git"
            subprocess.run(
                ["git", "clone", "--bare", str(remote_path), str(git_dir)],
                check=True, capture_output=True
            )

            # Configure the bare repo to work with worktrees
            subprocess.run(
                ["git", "config", "core.bare", "false"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
                cwd=project_path, check=True, capture_output=True
            )
            # Configure user settings for commits in worktrees
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=project_path, check=True, capture_output=True
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=project_path, check=True, capture_output=True
            )

            # Fetch to get remote tracking refs
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=project_path, check=True, capture_output=True
            )

            yield project_path

    def test_recycle_creates_new_branch(self, git_repo_with_remote):
        """Test recycling a worktree for a new branch."""
        # Create a worktree on a feature branch first
        worktree_path = create_worktree(git_repo_with_remote, "feature/original")

        # Close it (switches to main)
        close_result = close_worktree(worktree_path)
        assert close_result.success is True

        # Now recycle it for a different new branch
        result_path = recycle_worktree(worktree_path, "feature/recycled")

        assert result_path == worktree_path

        # Verify the branch was switched
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=worktree_path, check=True, capture_output=True, text=True
        )
        assert result.stdout.strip() == "feature/recycled"

        # Cleanup
        remove_worktree(git_repo_with_remote, "feature/recycled")

    def test_recycle_switches_to_existing_local_branch(self, git_repo_with_remote):
        """Test recycling a worktree for an existing local branch."""
        # Create two worktrees - one will create the branch, one will be closed
        worktree_alpha = create_worktree(git_repo_with_remote, "feature/existing")
        worktree_beta = create_worktree(git_repo_with_remote, "feature/beta")

        # Close the beta worktree
        close_result = close_worktree(worktree_beta)
        assert close_result.success is True

        # The branch feature/existing exists locally (checked out in alpha)
        # but not on remote. Recycling beta to feature/existing should work.
        # First, close alpha so the branch isn't checked out elsewhere
        close_result = close_worktree(worktree_alpha)
        assert close_result.success is True

        # Now recycle beta to the existing local branch
        result_path = recycle_worktree(worktree_beta, "feature/existing")

        assert result_path == worktree_beta

        # Verify the branch was switched
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=worktree_beta, check=True, capture_output=True, text=True
        )
        assert result.stdout.strip() == "feature/existing"

        # Cleanup
        remove_worktree(git_repo_with_remote, "feature/existing")
        remove_worktree_by_path(git_repo_with_remote, worktree_alpha.name)


class TestUpdateClaudeMd:
    """Tests for update_claude_md function."""

    def test_creates_claude_md_with_valid_snippet(self, tmp_path):
        """Test that update_claude_md works with the real template.

        This test will fail if shared/claude-header.md doesn't have valid markers.
        The validation in update_claude_md checks that the snippet starts with
        one of CLAUDE_HEADER_STARTS and ends with CLAUDE_HEADER_END.
        """
        # Call update_claude_md - it uses the real shared/claude-header.md
        result = update_claude_md(tmp_path)

        # Should return True (file was created)
        assert result is True

        # Verify the file was created
        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.exists()

        # Verify content has the expected markers
        content = claude_md.read_text()
        has_valid_start = any(marker in content for marker in CLAUDE_HEADER_STARTS)
        assert has_valid_start, "CLAUDE.md should contain a valid start marker"
        assert CLAUDE_HEADER_END in content, "CLAUDE.md should contain the end marker"

    def test_replaces_old_style_marker(self, tmp_path):
        """Test that old-style '# Maelstrom-based workflow' marker is detected and replaced."""
        # Create a CLAUDE.md with old-style marker
        claude_md = tmp_path / "CLAUDE.md"
        old_content = """# Project README

Some existing content.

# Maelstrom-based workflow

Old workflow instructions here.

(maelstrom instructions end)

More content after.
"""
        claude_md.write_text(old_content)

        # Call update_claude_md - should detect and replace the old section
        result = update_claude_md(tmp_path)

        # Should return True (file was modified)
        assert result is True

        # Verify the new content has the current style marker
        content = claude_md.read_text()
        assert "# Maelstrom Workflow" in content
        assert "More content after." in content  # Content after marker preserved


class TestOpenWorktreeWithChat:
    """Tests for open_worktree function with Claude Code chat integration."""

    def test_open_worktree_with_chat(self):
        """Test opening a worktree with Claude Code chat enabled."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code", open_chat=True)
                # Should call: open editor, then claude code editor.open, then focus
                assert mock_run.call_count == 3
                mock_run.assert_any_call(["code", str(worktree_path)], check=True)
                mock_run.assert_any_call(
                    ["code", "--command", "claude-vscode.editor.open"], check=True
                )
                mock_run.assert_any_call(
                    ["code", "--command", "claude-vscode.focus"], check=True
                )

    def test_open_worktree_chat_default_enabled(self):
        """Test that open_chat defaults to True."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "code")  # open_chat defaults to True
                # Should call: open editor, then claude code commands
                assert mock_run.call_count == 3

    def test_open_worktree_chat_skipped_for_non_vscode(self):
        """Test that Claude Code is skipped for non-VS Code editors."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                mock_run.return_value = None
                open_worktree(worktree_path, "cursor", open_chat=True)
                # Should only call open editor, not claude code commands
                assert mock_run.call_count == 1
                mock_run.assert_called_once_with(
                    ["cursor", str(worktree_path)], check=True
                )

    def test_open_worktree_chat_failure_warns_but_continues(self, capsys):
        """Test that Claude Code failure doesn't fail the whole operation."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.subprocess.run") as mock_run:
                # First call succeeds (open editor), subsequent calls fail (chat)
                mock_run.side_effect = [
                    None,  # editor opens successfully
                    subprocess.CalledProcessError(1, "code --command"),  # chat fails
                ]
                # Should not raise, just warn
                open_worktree(worktree_path, "code", open_chat=True)
                captured = capsys.readouterr()
                assert "Warning: Could not open Claude Code" in captured.err


class TestOpenClaudeCode:
    """Tests for open_claude_code function."""

    def test_open_claude_code_vscode(self):
        """Test opening Claude Code with VS Code."""
        with patch("maelstrom.worktree.subprocess.run") as mock_run:
            mock_run.return_value = None
            open_claude_code("code")
            assert mock_run.call_count == 2
            mock_run.assert_any_call(
                ["code", "--command", "claude-vscode.editor.open"], check=True
            )
            mock_run.assert_any_call(
                ["code", "--command", "claude-vscode.focus"], check=True
            )

    def test_open_claude_code_vscode_insiders(self):
        """Test opening Claude Code with VS Code Insiders."""
        with patch("maelstrom.worktree.subprocess.run") as mock_run:
            mock_run.return_value = None
            open_claude_code("code-insiders")
            assert mock_run.call_count == 2
            mock_run.assert_any_call(
                ["code-insiders", "--command", "claude-vscode.editor.open"], check=True
            )

    def test_open_claude_code_skipped_for_cursor(self):
        """Test that Claude Code is skipped for Cursor editor."""
        with patch("maelstrom.worktree.subprocess.run") as mock_run:
            open_claude_code("cursor")
            mock_run.assert_not_called()

    def test_open_claude_code_skipped_for_other_editors(self):
        """Test that Claude Code is skipped for other editors."""
        with patch("maelstrom.worktree.subprocess.run") as mock_run:
            open_claude_code("vim")
            mock_run.assert_not_called()

    def test_open_claude_code_command_not_found(self):
        """Test that FileNotFoundError is wrapped in RuntimeError."""
        with patch("maelstrom.worktree.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            with pytest.raises(RuntimeError, match="Command not found"):
                open_claude_code("code")

    def test_open_claude_code_command_fails(self):
        """Test that CalledProcessError is wrapped in RuntimeError."""
        with patch("maelstrom.worktree.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "code")
            with pytest.raises(RuntimeError, match="Failed to open Claude Code"):
                open_claude_code("code")
