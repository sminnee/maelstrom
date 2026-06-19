"""Tests for descriptive branch-name generation (``maelstrom.branch_name``)."""

from maelstrom import branch_name
from maelstrom import task as model


# --- slugify (deterministic fallback) ---


class TestSlugify:
    def test_lowercases_and_kebab_cases(self):
        assert branch_name.slugify("Flaky Port Test") == "flaky-port-test"

    def test_strips_stopwords(self):
        # "the", "a", "in" are dropped; meaningful words survive in order.
        assert branch_name.slugify("Fix the bug in a parser") == "fix-bug-parser"

    def test_drops_punctuation(self):
        assert branch_name.slugify("Add scheduled / repeating tasks!") == (
            "add-scheduled-repeating-tasks"
        )

    def test_caps_at_max_words(self):
        assert branch_name.slugify("one two three four five six") == (
            "one-two-three-four"
        )

    def test_respects_explicit_max_words(self):
        assert branch_name.slugify("one two three four", max_words=2) == "one-two"

    def test_all_stopwords_falls_back_to_raw_words(self):
        # Nothing meaningful survives stripping → keep the raw words.
        assert branch_name.slugify("the and of") == "the-and-of"

    def test_empty_text(self):
        assert branch_name.slugify("") == ""


# --- generate_branch_name ---


class TestGenerateBranchName:
    def test_valid_model_line_is_parsed(self):
        result = branch_name.generate_branch_name(
            "Fix flaky port allocation test",
            runner=lambda _prompt: "fix/flaky-port-test",
        )
        assert result == "fix/flaky-port-test"

    def test_prefix_leads_the_desc(self):
        result = branch_name.generate_branch_name(
            "Fix flaky port test",
            runner=lambda _prompt: "fix/flaky-port-test",
            prefix="123",
        )
        assert result == "fix/123-flaky-port-test"

    def test_model_output_with_trailing_prose_is_used_first_line(self):
        # The model returns the answer on line 1, then chatter — we take line 1.
        result = branch_name.generate_branch_name(
            "Add templates",
            runner=lambda _prompt: "feat/scheduled-templates\nHope that helps!",
        )
        assert result == "feat/scheduled-templates"

    def test_junk_output_falls_back_to_slug(self):
        result = branch_name.generate_branch_name(
            "Fix flaky port test",
            runner=lambda _prompt: "here is a branch name for you",
        )
        assert result == "feat/fix-flaky-port-test"

    def test_wrong_type_falls_back_to_slug(self):
        # "wip" is not an allowed type → validation fails → fallback.
        result = branch_name.generate_branch_name(
            "Fix flaky port test",
            runner=lambda _prompt: "wip/flaky-port-test",
        )
        assert result == "feat/fix-flaky-port-test"

    def test_empty_output_falls_back_to_slug(self):
        result = branch_name.generate_branch_name(
            "Fix flaky port test",
            runner=lambda _prompt: "",
        )
        assert result == "feat/fix-flaky-port-test"

    def test_raising_runner_falls_back_to_slug(self):
        def _boom(_prompt: str) -> str:
            raise FileNotFoundError("claude")

        result = branch_name.generate_branch_name(
            "Fix flaky port test", runner=_boom
        )
        assert result == "feat/fix-flaky-port-test"

    def test_fallback_preserves_prefix(self):
        def _boom(_prompt: str) -> str:
            raise TimeoutError()

        result = branch_name.generate_branch_name(
            "Fix flaky port test", runner=_boom, prefix="123"
        )
        assert result == "feat/123-fix-flaky-port-test"

    def test_custom_default_type_used_on_fallback(self):
        result = branch_name.generate_branch_name(
            "Fix flaky port test",
            runner=lambda _prompt: "garbage",
            default_type="fix",
        )
        assert result == "fix/fix-flaky-port-test"

    def test_empty_title_skips_model_and_uses_fallback(self):
        calls: list[str] = []

        def _runner(prompt: str) -> str:
            calls.append(prompt)
            return "feat/should-not-be-used"

        result = branch_name.generate_branch_name("   ", runner=_runner, prefix="123")
        # The model is never consulted for an empty title.
        assert calls == []
        # Title produced no meaningful words → desc is "task", not a bare prefix.
        assert result == "feat/123-task"

    def test_empty_title_no_prefix_uses_task_slug(self):
        result = branch_name.generate_branch_name(
            "", runner=lambda _prompt: "feat/x"
        )
        assert result == "feat/task"


# --- default_branch generation wiring ---


class TestDefaultBranchGeneration:
    def _runner(self, line: str):
        return lambda _prompt: line

    def test_orphan_generate_produces_descriptive_branch(self, monkeypatch):
        monkeypatch.setattr(
            branch_name, "_run_claude", self._runner("fix/flaky-port-test")
        )
        assert model.default_branch(
            "x", title="Fix flaky port test", generate=True
        ) == "fix/flaky-port-test"

    def test_orphan_without_generate_is_task_id(self):
        assert model.default_branch("x", title="Fix flaky port test") == "task/x"

    def test_linear_parent_generate_prepends_number(self, monkeypatch):
        monkeypatch.setattr(
            branch_name, "_run_claude", self._runner("fix/flaky-port-test")
        )
        assert model.default_branch(
            "x", "linear.NORT-123", title="Fix flaky port test", generate=True
        ) == "fix/123-flaky-port-test"

    def test_linear_parent_without_title_is_feat_number(self):
        # New deterministic fallback drops the NORT- team prefix.
        assert model.default_branch("x", "linear.NORT-123") == "feat/123"

    def test_linear_parent_generate_without_title_is_feat_number(self):
        assert model.default_branch(
            "x", "linear.NORT-123", generate=True
        ) == "feat/123"

    def test_non_linear_parent_unchanged(self):
        assert model.default_branch(
            "x", "2026-06-09.3", title="whatever", generate=True
        ) == "task/2026-06-09.3"

    def test_non_linear_dotted_parent_unchanged(self):
        assert model.default_branch("x", "linear.foo") == "task/linear.foo"
