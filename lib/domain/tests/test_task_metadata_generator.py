"""Tests for the task metadata generator (``mael_domain.task_metadata_generator``)."""

from collections.abc import Callable
from unittest.mock import ANY

from mael_domain import task as model
from mael_domain import task_metadata_generator as tmg

# Held before the autouse fixture swaps it for a SlugGenerator.
_REAL_DEFAULT_GENERATOR = tmg.default_generator


class _Fake:
    """A generator whose reply comes from ``reply``, called with the prompt.

    ``systems`` records the system prompt of each call.
    """

    def __init__(self, reply: Callable[[str], str]) -> None:
        self._reply = reply
        self.systems: list[str] = []

    def complete(self, prompt: str, system: str) -> str:
        self.systems.append(system)
        return self._reply(prompt)


# --- slugify (deterministic fallback) ---


class TestSlugify:
    def test_lowercases_and_kebab_cases(self):
        assert tmg.slugify("Flaky Port Test") == "flaky-port-test"

    def test_strips_stopwords(self):
        # "the", "a", "in" are dropped; meaningful words survive in order.
        assert tmg.slugify("Fix the bug in a parser") == "fix-bug-parser"

    def test_drops_punctuation(self):
        assert tmg.slugify("Add scheduled / repeating tasks!") == (
            "add-scheduled-repeating-tasks"
        )

    def test_caps_at_max_words(self):
        assert tmg.slugify("one two three four five six") == ("one-two-three-four")

    def test_respects_explicit_max_words(self):
        assert tmg.slugify("one two three four", max_words=2) == "one-two"

    def test_all_stopwords_falls_back_to_raw_words(self):
        # Nothing meaningful survives stripping → keep the raw words.
        assert tmg.slugify("the and of") == "the-and-of"

    def test_empty_text(self):
        assert tmg.slugify("") == ""


# --- generate_branch_name ---


class TestGenerateBranchName:
    def test_valid_model_line_is_parsed(self):
        result = tmg.generate_branch_name(
            "Fix flaky port allocation test",
            generator=_Fake(lambda _prompt: "fix/flaky-port-test"),
        )
        assert result == "fix/flaky-port-test"

    def test_prefix_leads_the_desc(self):
        result = tmg.generate_branch_name(
            "Fix flaky port test",
            generator=_Fake(lambda _prompt: "fix/flaky-port-test"),
            prefix="123",
        )
        assert result == "fix/123-flaky-port-test"

    def test_model_output_with_trailing_prose_is_used_first_line(self):
        # The model returns the answer on line 1, then chatter — we take line 1.
        result = tmg.generate_branch_name(
            "Add templates",
            generator=_Fake(
                lambda _prompt: "feat/scheduled-templates\nHope that helps!"
            ),
        )
        assert result == "feat/scheduled-templates"

    def test_junk_output_falls_back_to_slug(self):
        result = tmg.generate_branch_name(
            "Fix flaky port test",
            generator=_Fake(lambda _prompt: "here is a branch name for you"),
        )
        assert result == "feat/fix-flaky-port-test"

    def test_wrong_type_falls_back_to_slug(self):
        # "wip" is not an allowed type → validation fails → fallback.
        result = tmg.generate_branch_name(
            "Fix flaky port test",
            generator=_Fake(lambda _prompt: "wip/flaky-port-test"),
        )
        assert result == "feat/fix-flaky-port-test"

    def test_empty_output_falls_back_to_slug(self):
        result = tmg.generate_branch_name(
            "Fix flaky port test",
            generator=_Fake(lambda _prompt: ""),
        )
        assert result == "feat/fix-flaky-port-test"

    def test_raising_runner_falls_back_to_slug(self):
        def _boom(_prompt: str) -> str:
            raise FileNotFoundError("claude")

        result = tmg.generate_branch_name("Fix flaky port test", generator=_Fake(_boom))
        assert result == "feat/fix-flaky-port-test"

    def test_fallback_preserves_prefix(self):
        def _boom(_prompt: str) -> str:
            raise TimeoutError()

        result = tmg.generate_branch_name(
            "Fix flaky port test", generator=_Fake(_boom), prefix="123"
        )
        assert result == "feat/123-fix-flaky-port-test"

    def test_custom_default_type_used_on_fallback(self):
        result = tmg.generate_branch_name(
            "Fix flaky port test",
            generator=_Fake(lambda _prompt: "garbage"),
            default_type="fix",
        )
        assert result == "fix/fix-flaky-port-test"

    def test_empty_title_skips_model_and_uses_fallback(self):
        calls: list[str] = []

        def _runner(prompt: str) -> str:
            calls.append(prompt)
            return "feat/should-not-be-used"

        result = tmg.generate_branch_name("   ", generator=_Fake(_runner), prefix="123")
        # The model is never consulted for an empty title.
        assert calls == []
        # Title produced no meaningful words → desc is "task", not a bare prefix.
        assert result == "feat/123-task"

    def test_empty_title_no_prefix_uses_task_slug(self):
        result = tmg.generate_branch_name("", generator=_Fake(lambda _prompt: "feat/x"))
        assert result == "feat/task"

    def test_unrelated_slug_is_rejected_and_falls_back(self):
        # Regression for NORT-907: the model returned a well-formed but unrelated
        # slug ("not applicable" refusal) for "Mermaid charts". Shares no token
        # with the title → rejected → deterministic fallback.
        result = tmg.generate_branch_name(
            "Mermaid charts",
            generator=_Fake(lambda _prompt: "fix/branch-name-not-applicable"),
            prefix="907",
        )
        assert result == "feat/907-mermaid-charts"

    def test_unknown_sentinel_falls_back_to_slug(self):
        result = tmg.generate_branch_name(
            "Mermaid charts",
            generator=_Fake(lambda _prompt: "unknown"),
            prefix="907",
        )
        assert result == "feat/907-mermaid-charts"

    def test_retry_after_unknown_uses_second_attempt(self):
        # First draw is the refusal sentinel; the retried draw slugs fine. The
        # result comes from the model, proving the retry (not just fallback).
        calls: list[str] = []

        def _runner(_prompt: str) -> str:
            calls.append(_prompt)
            return "unknown" if len(calls) == 1 else "feat/mermaid-charts"

        result = tmg.generate_branch_name(
            "Mermaid charts", generator=_Fake(_runner), prefix="907"
        )
        assert len(calls) == 2
        assert result == "feat/907-mermaid-charts"

    def test_token_overlap_accepts_related_slug(self):
        result = tmg.generate_branch_name(
            "Fix flaky port allocation test",
            generator=_Fake(lambda _prompt: "fix/flaky-port-test"),
        )
        assert result == "fix/flaky-port-test"

    def test_token_overlap_rejects_unrelated_slug(self):
        result = tmg.generate_branch_name(
            "Fix flaky port allocation test",
            generator=_Fake(lambda _prompt: "fix/totally-unrelated-words"),
        )
        assert result == "feat/fix-flaky-port-allocation"


# --- default_branch generation wiring ---


class TestDefaultBranchGeneration:
    def _generator(self, line: str):
        return lambda: _Fake(lambda _prompt: line)

    def test_orphan_generate_produces_descriptive_branch(self, monkeypatch):
        monkeypatch.setattr(
            tmg, "default_generator", self._generator("fix/flaky-port-test")
        )
        assert (
            model.default_branch("x", title="Fix flaky port test", generate=True)
            == "fix/flaky-port-test"
        )

    def test_orphan_without_generate_is_task_id(self):
        assert model.default_branch("x", title="Fix flaky port test") == "task/x"

    def test_linear_parent_generate_prepends_number(self, monkeypatch):
        monkeypatch.setattr(
            tmg, "default_generator", self._generator("fix/flaky-port-test")
        )
        assert (
            model.default_branch(
                "x", "linear.NORT-123", title="Fix flaky port test", generate=True
            )
            == "fix/123-flaky-port-test"
        )

    def test_linear_parent_without_title_is_feat_number(self):
        # New deterministic fallback drops the NORT- team prefix.
        assert model.default_branch("x", "linear.NORT-123") == "feat/123"

    def test_linear_parent_generate_without_title_is_feat_number(self):
        assert model.default_branch("x", "linear.NORT-123", generate=True) == "feat/123"

    def test_non_linear_parent_unchanged(self):
        assert (
            model.default_branch("x", "2026-06-09.3", title="whatever", generate=True)
            == "task/2026-06-09.3"
        )

    def test_non_linear_dotted_parent_unchanged(self):
        assert model.default_branch("x", "linear.foo") == "task/linear.foo"


# --- infer_task_names ---


class TestInferTaskNames:
    def test_three_line_output_is_parsed(self):
        result = tmg.infer_task_names(
            "The port allocator hands out the same port twice when two "
            "worktrees open at once.",
            generator=_Fake(
                lambda _p: (
                    "Fix duplicate port allocation\nfix/duplicate-port-allocation\n"
                )
            ),
        )
        assert result.title == "Fix duplicate port allocation"
        assert result.branch == "fix/duplicate-port-allocation"
        assert result.command == ""

    def test_command_line_is_kept_when_known(self):
        result = tmg.infer_task_names(
            "Work out how to split the transcript store into its own module.",
            generator=_Fake(
                lambda _p: (
                    "Split the transcript store\nrefactor/split-transcript-store\nplan-task"
                )
            ),
        )
        assert result.command == "plan-task"

    def test_unknown_command_falls_back_to_empty(self):
        result = tmg.infer_task_names(
            "Fix the duplicate port allocation bug",
            generator=_Fake(
                lambda _p: (
                    "Fix duplicate port allocation\nfix/duplicate-port-allocation\ndeploy"
                )
            ),
        )
        assert result.command == ""

    def test_json_output_is_parsed(self):
        result = tmg.infer_task_names(
            "The port allocator hands out the same port twice.",
            generator=_Fake(
                lambda _p: (
                    '{"title": "Fix duplicate port allocation", '
                    '"branch": "fix/duplicate-port-allocation", "command": ""}'
                )
            ),
        )
        assert result.title == "Fix duplicate port allocation"
        assert result.branch == "fix/duplicate-port-allocation"

    def test_bad_branch_in_json_keeps_the_good_title(self):
        # Per-field validation: a malformed branch does not throw away the
        # title. JSON is the form this holds in — it identifies itself, so a
        # bad branch inside it is a bad field, not a sign the reply is prose.
        result = tmg.infer_task_names(
            "The port allocator hands out the same port twice.",
            generator=_Fake(
                lambda _p: (
                    '{"title": "Fix duplicate port allocation", '
                    '"branch": "not a branch name", "command": ""}'
                )
            ),
        )
        assert result.title == "Fix duplicate port allocation"
        assert result.branch == "feat/fix-duplicate-port-allocation"

    def test_a_json_field_that_is_not_a_string_is_no_field(self):
        # Without this, a number or an object lands its Python repr in a field.
        result = tmg.infer_task_names(
            "Fix the duplicate port allocation",
            generator=_Fake(
                lambda _p: '{"title": {"a": 1}, "branch": null, "command": 5}'
            ),
        )
        assert result.title == "Fix the duplicate port allocation"
        assert result.branch == "feat/fix-duplicate-port-allocation"
        assert result.command == ""

    def test_unrelated_branch_is_rejected(self):
        result = tmg.infer_task_names(
            "The port allocator hands out the same port twice.",
            generator=_Fake(
                lambda _p: (
                    "Fix duplicate port allocation\nfix/totally-unrelated-words\n"
                )
            ),
        )
        assert result.branch == "feat/fix-duplicate-port-allocation"

    def test_junk_output_falls_back_to_the_drafts_first_line(self):
        result = tmg.infer_task_names(
            "Fix the duplicate port allocation\n\nMore detail here.",
            generator=_Fake(lambda _p: "I'm sorry, I can't help with that."),
        )
        assert result.title == "Fix the duplicate port allocation"
        assert result.branch == "feat/fix-duplicate-port-allocation"
        assert result.command == ""

    def test_multi_line_refusal_is_not_a_title(self):
        # A refusal runs to more than one line as often as one, so line count
        # cannot be what tells a reply from prose.
        result = tmg.infer_task_names(
            "Fix the duplicate port allocation",
            generator=_Fake(
                lambda _p: (
                    "I cannot help with that request.\nPlease rephrase your question."
                )
            ),
        )
        assert result.title == "Fix the duplicate port allocation"
        assert result.branch == "feat/fix-duplicate-port-allocation"

    def test_raising_runner_falls_back(self):
        def _boom(_p: str) -> str:
            raise FileNotFoundError("claude")

        result = tmg.infer_task_names("Fix the port bug", generator=_Fake(_boom))
        assert result.title == "Fix the port bug"
        assert result.branch == "feat/fix-port-bug"

    def test_retry_after_junk_uses_second_attempt(self):
        calls: list[str] = []

        def _runner(prompt: str) -> str:
            calls.append(prompt)
            if len(calls) == 1:
                return "I cannot help with that.\nPlease rephrase your question."
            return "Fix duplicate port allocation\nfix/duplicate-port-allocation\n"

        result = tmg.infer_task_names(
            "The port allocator duplicates a port", generator=_Fake(_runner)
        )
        assert len(calls) == 2
        assert result.branch == "fix/duplicate-port-allocation"

    def test_empty_draft_skips_the_model(self):
        calls: list[str] = []

        def _runner(prompt: str) -> str:
            calls.append(prompt)
            return "Title\nfeat/x\n"

        result = tmg.infer_task_names("   ", generator=_Fake(_runner))
        assert calls == []
        assert result.title == ""
        assert result.branch == "feat/task"
        assert result.command == ""

    def test_long_first_line_is_trimmed_for_the_fallback_title(self):
        draft = "word " * 40
        result = tmg.infer_task_names(draft, generator=_Fake(lambda _p: "junk"))
        assert len(result.title) <= tmg.MAX_TITLE


class TestLeadWithNumber:
    """Numbering a composed branch, for a caller that has no parts to compose."""

    def test_leads_the_description_with_the_number(self):
        assert tmg.lead_with_number("feat/order-export", "99") == "feat/99-order-export"

    def test_a_branch_already_numbered_is_left_alone(self):
        assert (
            tmg.lead_with_number("feat/99-order-export", "99") == "feat/99-order-export"
        )

    def test_a_branch_with_no_type_prefix_is_left_alone(self):
        assert tmg.lead_with_number("order-export", "99") == "order-export"


class TestDefaultGenerator:
    """Which generator names the work, and what the OpenAI one sends."""

    def test_no_key_names_the_work_with_the_slug(self, monkeypatch, tmp_path):
        monkeypatch.setattr(tmg, "default_generator", _REAL_DEFAULT_GENERATOR)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "mael_domain.integrations._auth.load_global_config",
            lambda: type("C", (), {"openai_api_key": None})(),
        )
        assert isinstance(tmg.default_generator(), tmg.SlugGenerator)
        result = tmg.infer_task_names("Fix the port bug")
        assert result.title == "Fix the port bug"
        assert result.branch == "feat/fix-port-bug"

    def test_a_key_calls_luna_over_http(self, monkeypatch):
        monkeypatch.setattr(tmg, "default_generator", _REAL_DEFAULT_GENERATOR)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        calls: list[dict] = []

        def _request_json(url, **kwargs):
            calls.append({"url": url, **kwargs})
            return {"choices": [{"message": {"content": "  fix/port-bug\n"}}]}

        monkeypatch.setattr(
            "mael_domain.integrations.openai.request_json", _request_json
        )
        result = tmg.generate_branch_name("Fix the port bug")

        assert result == "fix/port-bug"
        assert calls == [
            {
                "url": "https://api.openai.com/v1/chat/completions",
                "method": "POST",
                "headers": {"Authorization": "Bearer sk-test"},
                "json_body": {
                    "model": "gpt-6-luna",
                    "messages": [
                        {"role": "system", "content": tmg._SYSTEM_PROMPT},
                        {"role": "user", "content": ANY},
                    ],
                },
                "timeout": 15,
            }
        ]

    def test_each_path_sends_its_own_system_prompt(self):
        branch = _Fake(lambda _p: "fix/port-bug")
        tmg.generate_branch_name("Fix the port bug", generator=branch)
        names = _Fake(lambda _p: "Fix the port bug\nfix/port-bug\n")
        tmg.infer_task_names("Fix the port bug", generator=names)

        assert branch.systems == [tmg._SYSTEM_PROMPT]
        assert names.systems == [tmg._INFER_SYSTEM_PROMPT]
