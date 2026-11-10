"""Tests for mael_domain.worktree_model — the pure worktree domain logic."""

import pytest

from mael_domain.worktree_model import (
    WORKTREE_NAMES,
    WORKTREE_SHORTCODES,
    UnclosableWorktreeError,
    WorktreeError,
    WorktreeNamesExhaustedError,
    WorktreeSetupError,
    apply_preserved_values,
    describe_branch_deletion,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
    is_worktree_closable,
    parse_env_text,
    plan_squash_rebase,
    resolve_worktree_shortcode,
    sanitize_branch_name,
    worktree_num,
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
        assert (
            extract_worktree_name_from_folder("askastro", "askastro-alpha") == "alpha"
        )
        assert (
            extract_worktree_name_from_folder("askastro", "askastro-bravo") == "bravo"
        )
        assert (
            extract_worktree_name_from_folder("my-project", "my-project-charlie")
            == "charlie"
        )

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
        assert (
            extract_worktree_name_from_folder("ask-astro", "ask-astro-alpha") == "alpha"
        )
        assert (
            extract_worktree_name_from_folder("ask-astro", "ask-astro-bravo") == "bravo"
        )


class TestMainWorktree:
    """`_main` is the fixed environment: a real worktree that never closes."""

    def test_extract_names_the_main_folder(self):
        """The folder is called `_main`, so the extractor says `_main`."""
        assert extract_worktree_name_from_folder("askastro", "_main") == "_main"

    def test_extract_names_main_whatever_the_project(self):
        """`_main` carries no project prefix, so the project name is irrelevant."""
        assert extract_worktree_name_from_folder("other", "_main") == "_main"

    def test_main_is_not_closable(self):
        assert is_worktree_closable("_main") is False

    def test_nato_worktrees_are_closable(self):
        for name in WORKTREE_NAMES:
            assert is_worktree_closable(name) is True


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


class TestWorktreeNum:
    """Tests for worktree_num."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("alpha", 0),
            ("bravo", 1),
            ("papa", 15),
            ("quebec", 16),
            ("zulu", 25),
            ("_main", 26),
        ],
    )
    def test_number_is_the_name_index(self, name, expected):
        """Test that a NATO name's number is its index, and `_main` follows zulu."""
        assert worktree_num(name) == expected

    def test_numbers_are_unique_and_contiguous(self):
        """Test that the 27 worktrees, `_main` included, take exactly 0-26."""
        names = [*WORKTREE_NAMES, "_main"]
        assert sorted(worktree_num(name) for name in names) == list(range(27))

    def test_unknown_name_rejected(self):
        """Test that a name outside the NATO list raises."""
        with pytest.raises(ValueError):
            worktree_num("feature-branch")


class TestExtractProjectName:
    """Tests for extract_project_name function."""

    def test_ssh_url(self):
        """Test extracting name from SSH URL."""
        assert extract_project_name("git@github.com:sminnee/askastro.git") == "askastro"
        assert extract_project_name("git@github.com:user/repo.git") == "repo"

    def test_https_url(self):
        """Test extracting name from HTTPS URL."""
        assert (
            extract_project_name("https://github.com/sminnee/askastro.git")
            == "askastro"
        )
        assert extract_project_name("https://github.com/user/repo.git") == "repo"

    def test_without_git_suffix(self):
        """Test URL without .git suffix."""
        assert extract_project_name("https://github.com/user/repo") == "repo"
        assert extract_project_name("git@github.com:user/repo") == "repo"

    def test_trailing_slash(self):
        """Test URL with trailing slash."""
        assert extract_project_name("https://github.com/user/repo/") == "repo"
        assert extract_project_name("https://github.com/user/repo.git/") == "repo"


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


class TestWorktreeErrors:
    """The worktree domain errors share one base, so one `except` catches them."""

    def test_every_worktree_error_is_a_worktree_error(self):
        for error in (
            UnclosableWorktreeError,
            WorktreeNamesExhaustedError,
            WorktreeSetupError,
        ):
            assert issubclass(error, WorktreeError)

    def test_the_base_is_not_a_builtin_the_cli_already_catches(self):
        """A caller narrowing on ValueError must not sweep these up by accident."""
        assert not issubclass(WorktreeError, (ValueError, KeyError))

    def test_one_except_catches_any_of_them(self):
        with pytest.raises(WorktreeError):
            raise WorktreeNamesExhaustedError("full")


class TestApplyPreservedValues:
    """Re-asserting the values a worktree owns, as pure text work."""

    def test_no_preserved_keys_leaves_the_text_alone(self):
        text = "A=1\nB=2\n"
        assert apply_preserved_values(text, {}) == text

    def test_a_missing_key_is_appended(self):
        """The common path: the template dropped the blank sentinel line."""
        out = apply_preserved_values("A=1\n", {"SECRET": "s3cret"})
        assert out == "A=1\nSECRET=s3cret\n"

    def test_an_existing_key_is_rewritten_in_place(self):
        """A legacy `.env` still holding `KEY=` must not gain a duplicate."""
        out = apply_preserved_values("A=1\nSECRET=\nB=2\n", {"SECRET": "s3cret"})
        assert out == "A=1\nSECRET=s3cret\nB=2\n"

    def test_a_key_inside_a_comment_is_not_rewritten(self):
        out = apply_preserved_values("# SECRET=old\n", {"SECRET": "s3cret"})
        assert out == "# SECRET=old\nSECRET=s3cret\n"

    def test_several_keys_are_all_applied(self):
        out = apply_preserved_values("A=\n", {"A": "1", "B": "2"})
        assert out == "A=1\nB=2\n"

    def test_the_result_is_newline_terminated(self):
        assert apply_preserved_values("A=1", {"B": "2"}).endswith("\n")

    def test_a_key_appearing_twice_is_rewritten_at_both_lines(self):
        """Every assignment of the key must carry the preserved value.

        Leaving a later stale line would let a dotenv reader take it: the last
        assignment wins.
        """
        out = apply_preserved_values("S=old\nA=1\nS=older\n", {"S": "new"})
        assert out == "S=new\nA=1\nS=new\n"


class TestPlanSquashRebase:
    """The rebase argv, decided without touching git."""

    def test_squash_adds_autosquash_and_stubs_both_editors(self):
        """--autosquash goes interactive; the stubbed editors keep it unattended."""
        cmd = plan_squash_rebase(squash=True)
        assert cmd.argv == [
            "git",
            "rebase",
            "--autosquash",
            "--autostash",
            "origin/main",
        ]
        assert cmd.env == {"GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}

    def test_the_returned_env_is_the_caller_s_own_copy(self):
        """Mutating one command's env must not reach the next one."""
        first = plan_squash_rebase(squash=True)
        assert first.env is not None
        first.env["GIT_EDITOR"] = "vim"
        second = plan_squash_rebase(squash=True)
        assert second.env == {"GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}


class TestDescribeBranchDeletion:
    """The message a deleted empty branch reports."""

    def test_a_local_only_delete_says_so(self):
        assert describe_branch_deletion("feat/x", remote_deleted=False) == (
            "feat/x is empty (merged into origin/main); "
            "deleted branch (local) and closed worktree."
        )

    def test_a_remote_delete_is_named_too(self):
        assert describe_branch_deletion("feat/x", remote_deleted=True) == (
            "feat/x is empty (merged into origin/main); "
            "deleted branch (local + remote) and closed worktree."
        )
