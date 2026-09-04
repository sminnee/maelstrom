"""Tests for maelstrom.worktree_model — the pure worktree domain logic."""

from pathlib import Path

import pytest

from maelstrom.worktree_model import (
    WORKTREE_NAMES,
    WORKTREE_SHORTCODES,
    BaseRef,
    StackTip,
    claude_transcript_path,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
    has_claude_transcript,
    is_worktree_closable,
    order_by_stack,
    parse_env_text,
    plan_rebase,
    resolve_stack_tip,
    resolve_worktree_shortcode,
    sanitise_path_for_claude,
    sanitize_branch_name,
    validate_base,
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

    def test_main_takes_worktree_num_zero(self):
        """`_main` shares 0 with alpha — 26 NATO names already wrap onto 16."""
        assert worktree_num("_main") == 0


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
            ("quebec", 0),
            ("zulu", 9),
        ],
    )
    def test_index_wraps_at_sixteen(self, name, expected):
        """Test that the number is the name's index, wrapped at 16."""
        assert worktree_num(name) == expected

    def test_every_name_is_a_valid_redis_database(self):
        """Test that no NATO name gives a number Redis rejects."""
        assert all(0 <= worktree_num(name) <= 15 for name in WORKTREE_NAMES)

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


class TestSanitisePathForClaude:
    """Tests for sanitise_path_for_claude."""

    def test_basic_path(self):
        result = sanitise_path_for_claude(Path("/Users/sminnee/Projects/foo"))
        assert result == "-Users-sminnee-Projects-foo"

    def test_worktree_path(self):
        result = sanitise_path_for_claude(Path("/Users/sminnee/Projects/foo/foo-alpha"))
        assert result == "-Users-sminnee-Projects-foo-foo-alpha"

    def test_collapses_dot_like_claude(self):
        # Claude's own slug replaces '.' with '-' too, so a real temp path like
        # /private/tmp/claude.501/... must match. Pinning this locks the fix
        # against regressing back to a '/'-only replacement.
        result = sanitise_path_for_claude(Path("/private/tmp/claude.501/x"))
        assert result == "-private-tmp-claude-501-x"


class TestClaudeTranscript:
    """Tests for claude_transcript_path / has_claude_transcript."""

    def test_transcript_path_uses_sanitised_slug(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        path = claude_transcript_path(worktree, "sid-1", home=tmp_path)
        assert path == (
            tmp_path
            / ".claude"
            / "projects"
            / "-Users-sminnee-Projects-foo-foo-alpha"
            / "sid-1.jsonl"
        )

    def test_has_transcript_true_when_file_exists(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        transcript = claude_transcript_path(worktree, "sid-1", home=tmp_path)
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n")
        assert has_claude_transcript(worktree, "sid-1", home=tmp_path) is True

    def test_has_transcript_false_when_absent(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        assert has_claude_transcript(worktree, "never-run", home=tmp_path) is False

    def test_has_transcript_distinguishes_session_ids(self, tmp_path):
        worktree = Path("/Users/sminnee/Projects/foo/foo-alpha")
        ran = claude_transcript_path(worktree, "ran", home=tmp_path)
        ran.parent.mkdir(parents=True)
        ran.write_text("{}\n")
        assert has_claude_transcript(worktree, "ran", home=tmp_path) is True
        assert has_claude_transcript(worktree, "other", home=tmp_path) is False


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


class TestPlanRebase:
    """Tests for plan_rebase — the pure rebase-target decision."""

    def test_default_base_is_a_plain_rebase_onto_origin_main(self):
        """The default base must produce today's exact rebase: no --onto branch.

        ``upstream=None`` is what keeps the default path byte-identical to the
        pre-stacking argv. Anything else here is a behaviour change for every
        unstacked worktree in existence.
        """
        plan = plan_rebase(BaseRef(), base_exists=True)
        assert plan.onto == "origin/main"
        assert plan.upstream is None
        assert plan.collapsed is False
        assert plan.label == "origin/main"

    def test_default_base_ignores_base_exists(self):
        """``main`` is never "gone"; the default plan does not depend on the flag."""
        assert plan_rebase(BaseRef(), base_exists=False) == plan_rebase(
            BaseRef(), base_exists=True
        )

    def test_live_base_rebases_onto_the_base_from_its_recorded_tip(self):
        plan = plan_rebase(
            BaseRef(branch="feat/parent", tip="abc123"), base_exists=True
        )
        assert plan.onto == "origin/feat/parent"
        assert plan.upstream == "abc123"
        assert plan.collapsed is False
        assert plan.label == "origin/feat/parent"

    def test_missing_base_collapses_onto_main_keeping_the_tip(self):
        """A base whose remote branch is gone merged or was abandoned.

        The recorded tip is still the right ``<upstream>``: it is where this
        branch's own commits start, so replaying from it onto main drops the
        parent's commits whether or not their patch-ids survived the merge.
        """
        plan = plan_rebase(
            BaseRef(branch="feat/parent", tip="abc123"), base_exists=False
        )
        assert plan.onto == "origin/main"
        assert plan.upstream == "abc123"
        assert plan.collapsed is True
        assert plan.label == "origin/main"

    def test_the_plan_names_the_branch_it_actually_lands_on(self):
        """``effective_base`` must always agree with ``onto``.

        Callers report this as ``SyncResult.base`` and build ``origin/<base>``
        from it. A plan that lands on main while naming a branch whose ref is
        gone produces a ref that does not resolve, and the caller's git call
        fails rather than answering.
        """
        for base, exists in [
            (BaseRef(), True),
            (BaseRef(branch="feat/parent", tip="abc"), True),
            (BaseRef(branch="feat/parent", tip="abc"), False),
        ]:
            plan = plan_rebase(base, base_exists=exists)
            assert plan.onto == f"origin/{plan.effective_base}", plan

    def test_a_missing_base_reports_main_as_the_effective_base(self):
        plan = plan_rebase(BaseRef(branch="feat/gone", tip="abc"), base_exists=False)
        assert plan.effective_base == "main"

    def test_a_live_base_reports_itself_as_the_effective_base(self):
        plan = plan_rebase(BaseRef(branch="feat/parent"), base_exists=True)
        assert plan.effective_base == "feat/parent"

    def test_live_base_without_a_tip_falls_back_to_a_plain_rebase(self):
        """A base set but never yet rebased has no tip to replay from."""
        plan = plan_rebase(BaseRef(branch="feat/parent", tip=None), base_exists=True)
        assert plan.onto == "origin/feat/parent"
        assert plan.upstream is None

    def test_missing_base_without_a_tip_collapses_to_a_plain_rebase(self):
        plan = plan_rebase(BaseRef(branch="feat/parent", tip=None), base_exists=False)
        assert plan.onto == "origin/main"
        assert plan.upstream is None
        assert plan.collapsed is True


class TestBaseRef:
    """Tests for the BaseRef value object."""

    def test_default_is_main_with_no_tip(self):
        base = BaseRef()
        assert base.branch == "main"
        assert base.tip is None
        assert base.is_default is True

    def test_a_named_base_is_not_default(self):
        assert BaseRef(branch="feat/parent").is_default is False

    def test_main_with_a_tip_is_not_default(self):
        """A recorded tip means a real rebase happened; do not take the fast path."""
        assert BaseRef(branch="main", tip="abc123").is_default is False


class TestValidateBase:
    """Tests for validate_base — cycle and self-reference rejection."""

    def test_accepts_a_valid_chain(self):
        validate_base("feat/c", "feat/b", {"feat/b": "feat/a", "feat/a": "main"})

    def test_accepts_main_as_a_base(self):
        validate_base("feat/a", "main", {})

    def test_rejects_self_base(self):
        with pytest.raises(ValueError, match="itself"):
            validate_base("feat/a", "feat/a", {})

    def test_rejects_a_two_cycle(self):
        with pytest.raises(ValueError, match="[Cc]ycle"):
            validate_base("feat/a", "feat/b", {"feat/b": "feat/a"})

    def test_rejects_a_three_cycle(self):
        """A→B→C→A, caught when setting C→A closes the loop."""
        with pytest.raises(ValueError, match="[Cc]ycle"):
            validate_base("feat/c", "feat/a", {"feat/a": "feat/b", "feat/b": "feat/c"})

    def test_a_chain_ending_at_an_unregistered_branch_is_valid(self):
        """Not every ancestor need have a stored base; an unset one is main."""
        validate_base("feat/b", "feat/a", {})


class TestResolveStackTip:
    """Tests for resolve_stack_tip — self-healing and stale-tip warning."""

    def test_main_is_always_valid_and_never_stale(self):
        assert resolve_stack_tip("main", {}, stale_days=30) == StackTip("main")

    def test_a_live_recent_tip_is_kept(self):
        tip = resolve_stack_tip("feat/a", {"feat/a": 3}, stale_days=30)
        assert tip == StackTip("feat/a")

    def test_a_deleted_tip_self_heals_to_main(self):
        """A merged or abandoned base must never be what new work stacks on."""
        tip = resolve_stack_tip("feat/gone", {"feat/other": 1}, stale_days=30)
        assert tip.branch == "main"
        assert tip.healed is True
        assert tip.stale_days is None

    def test_a_stale_tip_warns_but_is_still_used(self):
        """Warn, never block: an unattended agent session must not stall."""
        tip = resolve_stack_tip("feat/old", {"feat/old": 180}, stale_days=30)
        assert tip.branch == "feat/old"
        assert tip.stale_days == 180
        assert tip.healed is False

    def test_the_staleness_boundary_is_exclusive(self):
        assert (
            resolve_stack_tip("feat/a", {"feat/a": 30}, stale_days=30).stale_days
            is None
        )
        assert (
            resolve_stack_tip("feat/a", {"feat/a": 31}, stale_days=30).stale_days == 31
        )

    def test_an_empty_tip_reads_as_main(self):
        assert resolve_stack_tip("", {}, stale_days=30) == StackTip("main")


class TestOrderByStack:
    """Tests for order_by_stack — parents sync before their children."""

    def test_an_unstacked_set_keeps_its_order(self):
        assert order_by_stack(["b", "a", "c"], {}) == ["b", "a", "c"]

    def test_a_parent_sorts_before_its_child(self):
        assert order_by_stack(["child", "parent"], {"child": "parent"}) == [
            "parent",
            "child",
        ]

    def test_a_deep_chain_sorts_bottom_up(self):
        bases = {"c": "b", "b": "a"}
        assert order_by_stack(["c", "a", "b"], bases) == ["a", "b", "c"]

    def test_a_base_outside_the_set_does_not_break_the_sort(self):
        """sync-all only sees branches with worktrees; a base may have none."""
        assert order_by_stack(["child"], {"child": "absent-parent"}) == ["child"]

    def test_a_cycle_still_returns_every_branch(self):
        """Convergence, not correctness: a second sync-all fixes the order."""
        result = order_by_stack(["a", "b"], {"a": "b", "b": "a"})
        assert sorted(result) == ["a", "b"]

    def test_siblings_on_one_base_keep_their_relative_order(self):
        bases = {"x": "base", "y": "base"}
        assert order_by_stack(["base", "x", "y"], bases) == ["base", "x", "y"]
