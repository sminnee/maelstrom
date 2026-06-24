"""Tests for maelstrom.worktree_model — the pure worktree domain logic."""

from pathlib import Path

from maelstrom.worktree_model import (
    WORKTREE_NAMES,
    WORKTREE_SHORTCODES,
    _sanitise_path_for_claude,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
    parse_env_text,
    resolve_worktree_shortcode,
    sanitize_branch_name,
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


class TestSanitisePathForClaude:
    """Tests for _sanitise_path_for_claude."""

    def test_basic_path(self):
        result = _sanitise_path_for_claude(Path("/Users/sminnee/Projects/foo"))
        assert result == "-Users-sminnee-Projects-foo"

    def test_worktree_path(self):
        result = _sanitise_path_for_claude(Path("/Users/sminnee/Projects/foo/foo-alpha"))
        assert result == "-Users-sminnee-Projects-foo-foo-alpha"


class TestParseEnvText:
    """Tests for parse_env_text."""

    def test_parses_and_strips_source_comment(self):
        text = (
            "APP_URL=http://localhost:1200  # source: [APP_URL=http://localhost:${WEB_PORT}]\n"
            "FOO=bar\n"
        )
        assert parse_env_text(text) == {
            "APP_URL": "http://localhost:1200",
            "FOO": "bar",
        }

    def test_empty_text(self):
        assert parse_env_text("") == {}
