"""Tests for maelstrom.worktree module."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from unittest.mock import patch

from maelstrom.worktree import (
    ENV_SECTION_END,
    ENV_SECTION_START,
    MAIN_BRANCH,
    WORKTREE_NAMES,
    WORKTREE_SHORTCODES,
    WorktreeInfo,
    close_worktree,
    create_worktree,
    extract_project_name,
    extract_worktree_name_from_folder,
    find_closed_worktree,
    get_commits_ahead,
    get_next_worktree_name,
    get_worktree_dirty_files,
    get_worktree_folder_name,
    has_root_worktree,
    is_worktree_closed,
    list_worktrees,
    open_worktree,
    start_claude_session,
    read_env_file,
    reclaim_or_allocate_ports,
    recycle_worktree,
    remove_worktree,
    remove_worktree_by_path,
    resolve_worktree_shortcode,
    run_install_cmd,
    sanitize_branch_name,
    update_claude_local_md,
    write_env_file,
)
from maelstrom.ports import (
    get_port_allocation,
    load_port_allocations,
    record_port_allocation,
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


class TestWorktreeShortcodes:
    """Tests for worktree shortcode mapping and resolution."""

    def test_shortcodes_has_26_entries(self):
        """Test that WORKTREE_SHORTCODES has all 26 letters."""
        assert len(WORKTREE_SHORTCODES) == 26

    def test_shortcodes_map_correctly(self):
        """Test specific shortcode mappings."""
        assert WORKTREE_SHORTCODES["a"] == "alpha"
        assert WORKTREE_SHORTCODES["b"] == "bravo"
        assert WORKTREE_SHORTCODES["z"] == "zulu"

    def test_all_first_letters_unique(self):
        """Test that all NATO names have unique first letters."""
        first_letters = [name[0] for name in WORKTREE_NAMES]
        assert len(first_letters) == len(set(first_letters))

    def test_resolve_single_letter(self):
        """Test resolving single-letter shortcodes."""
        assert resolve_worktree_shortcode("a") == "alpha"
        assert resolve_worktree_shortcode("b") == "bravo"
        assert resolve_worktree_shortcode("d") == "delta"
        assert resolve_worktree_shortcode("z") == "zulu"

    def test_resolve_full_name_passthrough(self):
        """Test that full NATO names pass through unchanged."""
        assert resolve_worktree_shortcode("alpha") == "alpha"
        assert resolve_worktree_shortcode("bravo") == "bravo"
        assert resolve_worktree_shortcode("zulu") == "zulu"

    def test_resolve_unknown_string_passthrough(self):
        """Test that unknown strings pass through unchanged."""
        assert resolve_worktree_shortcode("feature-branch") == "feature-branch"
        assert resolve_worktree_shortcode("main") == "main"
        assert resolve_worktree_shortcode("") == ""


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

    def test_writes_env_vars_with_section_markers(self):
        """Test writing environment variables with managed section markers."""
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
            assert ENV_SECTION_START in content
            assert ENV_SECTION_END in content
            assert "PORT_BASE=100" in content
            assert "FRONTEND_PORT=1000" in content
            assert "SERVER_PORT=1001" in content

    def test_sorts_variables_within_section(self):
        """Test that environment variables are sorted within the managed section."""
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
            assert lines[0] == ENV_SECTION_START
            assert lines[1] == "A_VAR=a"
            assert lines[2] == "M_VAR=m"
            assert lines[3] == "Z_VAR=z"
            assert lines[4] == ENV_SECTION_END

    def test_first_creation_with_template(self):
        """Test first-time creation with template text from project root .env."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"PORT_BASE": "100", "DB_PORT": "1002"}
            template = "DATABASE_URL=postgres://localhost:$DB_PORT/mydb\nAPI_KEY=secret\n"

            write_env_file(worktree_path, generated, template)

            env_file = worktree_path / ".env"
            content = env_file.read_text()
            # Managed section at top
            assert content.startswith(ENV_SECTION_START)
            assert "PORT_BASE=100" in content
            assert "DB_PORT=1002" in content
            # Template text below with resolved value + source comment
            assert "DATABASE_URL=postgres://localhost:1002/mydb  # source: [DATABASE_URL=postgres://localhost:$DB_PORT/mydb]" in content
            assert "API_KEY=secret" in content
            # Section ends before template
            section_end_pos = content.index(ENV_SECTION_END)
            template_pos = content.index("DATABASE_URL")
            assert section_end_pos < template_pos

    def test_updates_managed_section_preserves_user_content(self):
        """Test that updating replaces only the managed section."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"

            # Write initial .env with section + user content
            initial = (
                f"{ENV_SECTION_START}\n"
                "PORT_BASE=100\n"
                f"{ENV_SECTION_END}\n"
                "\n"
                "MY_CUSTOM_VAR=keep_me\n"
            )
            env_file.write_text(initial)

            # Update with new port allocation
            write_env_file(worktree_path, {"PORT_BASE": "200", "NEW_PORT": "2000"})

            content = env_file.read_text()
            # New values in managed section
            assert "PORT_BASE=200" in content
            assert "NEW_PORT=2000" in content
            # Old values gone
            assert "PORT_BASE=100" not in content
            # User content preserved
            assert "MY_CUSTOM_VAR=keep_me" in content

    def test_upgrade_path_no_existing_markers(self):
        """Test that existing .env without markers gets section prepended."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"

            # Pre-existing .env without markers
            env_file.write_text("LEGACY_VAR=old_value\nANOTHER=thing\n")

            write_env_file(worktree_path, {"PORT_BASE": "100"})

            content = env_file.read_text()
            # Managed section prepended
            assert content.startswith(ENV_SECTION_START)
            assert "PORT_BASE=100" in content
            # Legacy content preserved after section
            assert "LEGACY_VAR=old_value" in content
            assert "ANOTHER=thing" in content
            section_end_pos = content.index(ENV_SECTION_END)
            legacy_pos = content.index("LEGACY_VAR")
            assert section_end_pos < legacy_pos

    def test_template_text_with_var_references_resolved(self):
        """Test that $VAR references in template text are resolved with source comment."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"WORKTREE": "alpha", "WORKTREE_NUM": "0"}
            template = "DATABASE_NAME=myapp_$WORKTREE\n"

            write_env_file(worktree_path, generated, template)

            env_file = worktree_path / ".env"
            content = env_file.read_text()
            # $WORKTREE reference resolved with source comment
            assert "DATABASE_NAME=myapp_alpha  # source: [DATABASE_NAME=myapp_$WORKTREE]" in content
            assert "WORKTREE=alpha" in content
            assert "WORKTREE_NUM=0" in content

    def test_source_comment_added_on_substitution(self):
        """Test that ${VAR} references get resolved with a source comment."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"FRONTEND_PORT": "1000"}
            template = "APP_URL=http://localhost:${FRONTEND_PORT}\n"

            write_env_file(worktree_path, generated, template)

            content = (worktree_path / ".env").read_text()
            assert "APP_URL=http://localhost:1000  # source: [APP_URL=http://localhost:${FRONTEND_PORT}]" in content

    def test_rewrite_resolves_from_source_comment(self):
        """Test that updating managed section re-resolves user lines from source."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"

            # Write initial .env with resolved line + source comment
            initial = (
                f"{ENV_SECTION_START}\n"
                "FRONTEND_PORT=1000\n"
                f"{ENV_SECTION_END}\n"
                "\n"
                "APP_URL=http://localhost:1000  # source: [APP_URL=http://localhost:${FRONTEND_PORT}]\n"
            )
            env_file.write_text(initial)

            # Update with new port
            write_env_file(worktree_path, {"FRONTEND_PORT": "2000"})

            content = env_file.read_text()
            assert "FRONTEND_PORT=2000" in content
            assert "APP_URL=http://localhost:2000  # source: [APP_URL=http://localhost:${FRONTEND_PORT}]" in content

    def test_lines_without_var_refs_unchanged(self):
        """Test that lines without variable references get no source comment."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"PORT_BASE": "100"}
            template = "API_KEY=secret123\n"

            write_env_file(worktree_path, generated, template)

            content = (worktree_path / ".env").read_text()
            assert "API_KEY=secret123" in content
            assert "# source:" not in content

    def test_partial_var_resolution(self):
        """Test that only matching vars are resolved; unmatched pass through."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            generated = {"FRONTEND_PORT": "1000"}
            template = "CONN=http://localhost:${FRONTEND_PORT}/${UNKNOWN_VAR}\n"

            write_env_file(worktree_path, generated, template)

            content = (worktree_path / ".env").read_text()
            # FRONTEND_PORT resolved, UNKNOWN_VAR left as-is
            assert "CONN=http://localhost:1000/${UNKNOWN_VAR}  # source: [CONN=http://localhost:${FRONTEND_PORT}/${UNKNOWN_VAR}]" in content


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

    def test_read_env_file_strips_source_comment(self):
        """Test that read_env_file strips source comments and returns clean values."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            env_file = worktree_path / ".env"
            env_file.write_text(
                "APP_URL=http://localhost:1000  # source: [APP_URL=http://localhost:${FRONTEND_PORT}]\n"
                "API_KEY=secret\n"
                'QUOTED="value with # hash"  # source: [QUOTED="value with # hash"]\n'
            )

            result = read_env_file(worktree_path)
            assert result["APP_URL"] == "http://localhost:1000"
            assert result["API_KEY"] == "secret"
            assert result["QUOTED"] == "value with # hash"


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

    def test_run_install_cmd(self, git_repo):
        """Test that run_install_cmd runs the configured install command."""
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

        # Run install command (now a separate step from create_worktree)
        run_install_cmd(worktree_path)

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


class TestStartClaudeSession:
    """Tests for start_claude_session function."""

    def test_start_claude_session_calls_execvp(self):
        """Test that start_claude_session calls os.execvp with correct args."""
        with TemporaryDirectory() as tmpdir:
            worktree_path = Path(tmpdir)
            with patch("maelstrom.worktree.os.chdir") as mock_chdir, \
                 patch("maelstrom.worktree.os.execvp") as mock_execvp:
                start_claude_session(worktree_path)
                mock_chdir.assert_called_once_with(worktree_path)
                mock_execvp.assert_called_once_with("claude", ["claude"])


class TestIsWorktreeClosed:
    """Tests for is_worktree_closed function."""

    def test_returns_true_when_detached_at_origin_main(self):
        """Test returns True for detached worktree at origin/main."""
        # branch="" means detached HEAD
        wt = WorktreeInfo(path=Path("/fake"), branch="", commit="abc123")
        with patch("maelstrom.worktree.get_worktree_dirty_files", return_value=[]):
            with patch("maelstrom.worktree.get_commits_ahead", return_value=0):
                assert is_worktree_closed(wt) is True

    def test_returns_false_when_on_branch(self):
        """Test returns False for worktree on a branch (not detached)."""
        wt = WorktreeInfo(path=Path("/fake"), branch="feature/test", commit="abc123")
        # Should return False immediately because it's on a branch
        assert is_worktree_closed(wt) is False

    def test_returns_true_when_behind_origin_main(self):
        """Test returns True when HEAD is behind origin/main (closed but stale)."""
        # branch="" means detached HEAD
        wt = WorktreeInfo(path=Path("/fake"), branch="", commit="abc123")
        with patch("maelstrom.worktree.get_worktree_dirty_files", return_value=[]):
            with patch("maelstrom.worktree.get_commits_ahead", return_value=0):
                assert is_worktree_closed(wt) is True

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

            # Detach HEAD so main isn't "checked out" in project root (like add_project does)
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_path, check=True, capture_output=True, text=True
            ).stdout.strip()
            subprocess.run(
                ["git", "update-ref", "--no-deref", "HEAD", head_sha],
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

            # Detach HEAD so main isn't "checked out" in project root (like add_project does)
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_path, check=True, capture_output=True, text=True
            ).stdout.strip()
            subprocess.run(
                ["git", "update-ref", "--no-deref", "HEAD", head_sha],
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


class TestUpdateClaudeLocalMd:
    """Tests for update_claude_local_md function."""

    def test_creates_claude_local_md(self, tmp_path, monkeypatch):
        """Test that update_claude_local_md generates .claude/CLAUDE.local.md."""
        # Set up project structure: project_path/projectname-alpha
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        # Mock get_app_url to return None (no web port)
        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        result = update_claude_local_md(project_path, worktree_path, "alpha")

        assert result is True

        local_md = worktree_path / ".claude" / "CLAUDE.local.md"
        assert local_md.exists()

        content = local_md.read_text()
        assert "Always load the `/mael` skill" in content
        assert "## Environment" in content
        assert str(worktree_path) in content
        assert "app URL" not in content

    def test_includes_app_url_when_available(self, tmp_path, monkeypatch):
        """Test that app URL is included when the project has a web port."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr(
            "maelstrom.ports.get_app_url",
            lambda *a: ("http://localhost:3010", False),
        )

        result = update_claude_local_md(project_path, worktree_path, "alpha")

        assert result is True

        content = (worktree_path / ".claude" / "CLAUDE.local.md").read_text()
        assert "The app URL is http://localhost:3010" in content

    def test_overwrites_existing_file(self, tmp_path, monkeypatch):
        """Test that the file is regenerated on every call."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        # Write an old file
        claude_dir = worktree_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.local.md").write_text("old content")

        result = update_claude_local_md(project_path, worktree_path, "alpha")

        assert result is True
        content = (claude_dir / "CLAUDE.local.md").read_text()
        assert "old content" not in content
        assert "Always load the `/mael` skill" in content

    def test_adds_import_to_existing_claude_md(self, tmp_path, monkeypatch):
        """Test that @.claude/CLAUDE.local.md is prepended to CLAUDE.md."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        # Create a CLAUDE.md without the import
        (worktree_path / "CLAUDE.md").write_text("# My Project\n\nSome docs.\n")

        update_claude_local_md(project_path, worktree_path, "alpha")

        content = (worktree_path / "CLAUDE.md").read_text()
        assert content.startswith("@.claude/CLAUDE.local.md\n")
        assert "# My Project" in content

    def test_does_not_duplicate_import(self, tmp_path, monkeypatch):
        """Test that import line is not added if already present."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        original = "@.claude/CLAUDE.local.md\n\n# My Project\n"
        (worktree_path / "CLAUDE.md").write_text(original)

        update_claude_local_md(project_path, worktree_path, "alpha")

        content = (worktree_path / "CLAUDE.md").read_text()
        assert content.count("@.claude/CLAUDE.local.md") == 1

    def test_adds_gitignore_entry(self, tmp_path, monkeypatch):
        """Test that .claude/CLAUDE.local.md is added to .gitignore."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        # Create a .gitignore without the entry
        (worktree_path / ".gitignore").write_text(".env\nnode_modules/\n")

        update_claude_local_md(project_path, worktree_path, "alpha")

        content = (worktree_path / ".gitignore").read_text()
        assert ".claude/CLAUDE.local.md" in content.splitlines()

    def test_does_not_duplicate_gitignore_entry(self, tmp_path, monkeypatch):
        """Test that .gitignore entry is not duplicated."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        (worktree_path / ".gitignore").write_text(".env\n.claude/CLAUDE.local.md\n")

        update_claude_local_md(project_path, worktree_path, "alpha")

        content = (worktree_path / ".gitignore").read_text()
        assert content.count(".claude/CLAUDE.local.md") == 1

    def test_creates_gitignore_if_missing(self, tmp_path, monkeypatch):
        """Test that .gitignore is created if it doesn't exist."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("maelstrom.ports.get_app_url", lambda *a: None)

        update_claude_local_md(project_path, worktree_path, "alpha")

        gitignore = worktree_path / ".gitignore"
        assert gitignore.exists()
        assert ".claude/CLAUDE.local.md" in gitignore.read_text().splitlines()


class TestReclaimOrAllocatePorts:
    """Tests for reclaim_or_allocate_ports function."""

    def test_reclaims_old_ports_when_available(self, tmp_path, monkeypatch):
        """Test that old ports from .env are reclaimed if not allocated elsewhere."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_path = tmp_path / "Projects" / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)

        # Create a .maelstrom.yaml with port_names
        config_file = worktree_path / ".maelstrom.yaml"
        config_file.write_text("port_names:\n  - WEB\n  - API\n")

        # Create an existing .env with an old PORT_BASE
        env_file = worktree_path / ".env"
        env_file.write_text("PORT_BASE=350\nWEB_PORT=3500\nAPI_PORT=3501\nWORKTREE=alpha\n")

        # Reclaim ports - should succeed since 350 is not allocated
        reclaim_or_allocate_ports(project_path, worktree_path, "alpha")

        # Verify the old port_base was recorded
        assert get_port_allocation(project_path, "alpha") == 350

    def test_allocates_new_ports_when_old_taken(self, tmp_path, monkeypatch):
        """Test that new ports are allocated when old ones are taken by another worktree."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_path = tmp_path / "Projects" / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)

        # Create a .maelstrom.yaml with port_names
        config_file = worktree_path / ".maelstrom.yaml"
        config_file.write_text("port_names:\n  - WEB\n  - API\n")

        # Create an existing .env with PORT_BASE=350
        env_file = worktree_path / ".env"
        env_file.write_text("PORT_BASE=350\nWEB_PORT=3500\nAPI_PORT=3501\nWORKTREE=alpha\n")

        # Allocate 350 to bravo (making it unavailable for alpha)
        record_port_allocation(project_path, "bravo", 350)

        # Mock socket checking so we get a predictable result
        with patch("maelstrom.ports.check_ports_free", return_value=True):
            reclaim_or_allocate_ports(project_path, worktree_path, "alpha")

        # Alpha should have gotten a new port_base (not 350)
        allocation = get_port_allocation(project_path, "alpha")
        assert allocation is not None
        assert allocation != 350

        # The .env should have been regenerated with new ports
        new_env = read_env_file(worktree_path)
        assert new_env["PORT_BASE"] != "350"

    def test_allocates_when_no_env_file(self, tmp_path, monkeypatch):
        """Test that ports are allocated fresh when no .env exists."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_path = tmp_path / "Projects" / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)

        # Create a .maelstrom.yaml with port_names
        config_file = worktree_path / ".maelstrom.yaml"
        config_file.write_text("port_names:\n  - WEB\n  - API\n")

        # No .env file exists

        with patch("maelstrom.ports.check_ports_free", return_value=True):
            reclaim_or_allocate_ports(project_path, worktree_path, "alpha")

        # Should have allocated new ports
        allocation = get_port_allocation(project_path, "alpha")
        assert allocation is not None

        # .env should have been created
        new_env = read_env_file(worktree_path)
        assert "PORT_BASE" in new_env

    def test_noop_when_no_port_names_configured(self, tmp_path, monkeypatch):
        """Test that nothing happens when port_names is not configured."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_path = tmp_path / "Projects" / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)

        # Config without port_names
        config_file = worktree_path / ".maelstrom.yaml"
        config_file.write_text("start_cmd: npm start\n")

        reclaim_or_allocate_ports(project_path, worktree_path, "alpha")

        # No allocation should have been made
        assert get_port_allocation(project_path, "alpha") is None


class TestPortAllocationLifecycle:
    """Tests for port allocation integration with worktree lifecycle."""

    @pytest.fixture
    def git_repo_with_remote(self, tmp_path, monkeypatch):
        """Create a bare git repository with ports configured."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create a "remote" bare repository with initial content
        remote_path = tmp_path / "remote.git"
        source_path = tmp_path / "source"
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

        # Create .maelstrom.yaml with port_names
        (source_path / ".maelstrom.yaml").write_text(
            "port_names:\n  - WEB\n  - API\n"
        )
        (source_path / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "."], cwd=source_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=source_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "branch", "-M", "main"],
            cwd=source_path, check=True, capture_output=True
        )

        # Clone as bare to create the remote
        subprocess.run(
            ["git", "clone", "--bare", str(source_path), str(remote_path)],
            check=True, capture_output=True
        )

        # Create project directory with bare clone structure
        project_path = tmp_path / "Projects" / "test-repo"
        project_path.mkdir(parents=True)

        git_dir = project_path / ".git"
        subprocess.run(
            ["git", "clone", "--bare", str(remote_path), str(git_dir)],
            check=True, capture_output=True
        )

        subprocess.run(
            ["git", "config", "core.bare", "false"],
            cwd=project_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"],
            cwd=project_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=project_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=project_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=project_path, check=True, capture_output=True
        )

        # Detach HEAD so main isn't "checked out" in project root (like add_project does)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path, check=True, capture_output=True, text=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "--no-deref", "HEAD", head_sha],
            cwd=project_path, check=True, capture_output=True
        )

        return project_path

    def test_create_records_allocation(self, git_repo_with_remote):
        """Test that creating a worktree records a port allocation."""
        with patch("maelstrom.ports.check_ports_free", return_value=True):
            worktree_path = create_worktree(git_repo_with_remote, "feature/test")

        # Verify allocation was recorded
        allocation = get_port_allocation(git_repo_with_remote, "alpha")
        assert allocation is not None
        assert 300 <= allocation <= 999

        # Verify .env has matching PORT_BASE
        env = read_env_file(worktree_path)
        assert env["PORT_BASE"] == str(allocation)

        # Cleanup
        remove_worktree(git_repo_with_remote, "feature/test")

    def test_remove_frees_allocation(self, git_repo_with_remote):
        """Test that removing a worktree frees its port allocation."""
        with patch("maelstrom.ports.check_ports_free", return_value=True):
            create_worktree(git_repo_with_remote, "feature/test")

        # Verify allocation exists
        assert get_port_allocation(git_repo_with_remote, "alpha") is not None

        # Remove worktree
        remove_worktree(git_repo_with_remote, "feature/test")

        # Allocation should be freed
        assert get_port_allocation(git_repo_with_remote, "alpha") is None

    def test_close_frees_allocation(self, git_repo_with_remote):
        """Test that closing a worktree frees its port allocation."""
        with patch("maelstrom.ports.check_ports_free", return_value=True):
            worktree_path = create_worktree(git_repo_with_remote, "feature/test")

        # Verify allocation exists
        assert get_port_allocation(git_repo_with_remote, "alpha") is not None

        # Close worktree
        result = close_worktree(worktree_path)
        assert result.success is True

        # Allocation should be freed
        assert get_port_allocation(git_repo_with_remote, "alpha") is None

        # Cleanup
        remove_worktree_by_path(git_repo_with_remote, worktree_path.name)

    def test_create_avoids_allocated_ports(self, git_repo_with_remote):
        """Test that creating worktrees avoids already-allocated port bases."""
        with patch("maelstrom.ports.check_ports_free", return_value=True):
            path1 = create_worktree(git_repo_with_remote, "feature/one")
            path2 = create_worktree(git_repo_with_remote, "feature/two")

        alloc1 = get_port_allocation(git_repo_with_remote, "alpha")
        alloc2 = get_port_allocation(git_repo_with_remote, "bravo")

        # Port bases should be different
        assert alloc1 != alloc2
        assert alloc1 is not None
        assert alloc2 is not None

        # Cleanup
        remove_worktree(git_repo_with_remote, "feature/two")
        remove_worktree(git_repo_with_remote, "feature/one")


class TestRegenerateEnvFile:
    """Tests for regenerate_env_file function."""

    @patch("maelstrom.worktree.write_env_file")
    @patch("maelstrom.worktree.generate_port_env_vars", return_value={"PORT_BASE": "300", "APP_PORT": "3000"})
    @patch("maelstrom.worktree.allocate_port_base")
    @patch("maelstrom.worktree.get_port_allocation", return_value=300)
    @patch("maelstrom.worktree.load_config_or_default")
    def test_reuses_existing_port_base(
        self, mock_config, mock_get_alloc, mock_alloc, mock_gen, mock_write, tmp_path,
    ):
        """Uses get_port_allocation and does NOT call allocate_port_base."""
        from maelstrom.config import MaelstromConfig
        mock_config.return_value = MaelstromConfig(port_names=["APP"])

        project_path = tmp_path / "project"
        project_path.mkdir()
        worktree_path = tmp_path / "bravo"
        worktree_path.mkdir()

        from maelstrom.worktree import regenerate_env_file
        regenerate_env_file(project_path, worktree_path, "bravo")

        mock_get_alloc.assert_called_once_with(project_path, "bravo")
        mock_alloc.assert_not_called()
        mock_gen.assert_called_once_with(300, ["APP"])
        mock_write.assert_called_once()

    @patch("maelstrom.worktree.write_env_file")
    @patch("maelstrom.worktree.record_port_allocation")
    @patch("maelstrom.worktree.generate_port_env_vars", return_value={"PORT_BASE": "300", "APP_PORT": "3000"})
    @patch("maelstrom.worktree.allocate_port_base", return_value=300)
    @patch("maelstrom.worktree.get_port_allocation", return_value=None)
    @patch("maelstrom.worktree.load_config_or_default")
    def test_allocates_if_no_existing_ports(
        self, mock_config, mock_get_alloc, mock_alloc, mock_gen,
        mock_record, mock_write, tmp_path,
    ):
        """Falls back to allocate_port_base when no existing allocation."""
        from maelstrom.config import MaelstromConfig
        mock_config.return_value = MaelstromConfig(port_names=["APP"])

        project_path = tmp_path / "project"
        project_path.mkdir()
        worktree_path = tmp_path / "bravo"
        worktree_path.mkdir()

        from maelstrom.worktree import regenerate_env_file
        regenerate_env_file(project_path, worktree_path, "bravo")

        mock_get_alloc.assert_called_once_with(project_path, "bravo")
        mock_alloc.assert_called_once_with(project_path, 1)
        mock_record.assert_called_once_with(project_path, "bravo", 300)
        mock_write.assert_called_once()


class TestStaleWorktreeHandling:
    """Tests for handling worktrees whose directories no longer exist."""

    def test_get_worktree_dirty_files_nonexistent_path(self):
        """get_worktree_dirty_files returns [] for a non-existent path."""
        result = get_worktree_dirty_files(Path("/nonexistent/worktree/path"))
        assert result == []

    def test_get_commits_ahead_nonexistent_path(self):
        """get_commits_ahead returns 0 for a non-existent path."""
        result = get_commits_ahead(Path("/nonexistent/worktree/path"))
        assert result == 0

    def test_list_worktrees_filters_stale_entries(self, tmp_path, capsys):
        """list_worktrees filters out worktrees whose directories are missing."""
        existing_dir = tmp_path / "myproject-alpha"
        existing_dir.mkdir()
        missing_dir = tmp_path / "myproject-bravo"  # deliberately not created

        porcelain_output = (
            f"worktree {existing_dir}\n"
            f"HEAD abc123\n"
            f"branch refs/heads/main\n"
            f"\n"
            f"worktree {missing_dir}\n"
            f"HEAD def456\n"
            f"branch refs/heads/feature\n"
            f"\n"
        )

        with patch("maelstrom.worktree.run_git") as mock_run_git:
            mock_run_git.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=porcelain_output, stderr=""
            )
            result = list_worktrees(tmp_path)

        assert len(result) == 1
        assert result[0].path == existing_dir
        assert result[0].branch == "main"

        captured = capsys.readouterr()
        assert "directory is missing" in captured.err
        assert "git worktree prune" in captured.err
