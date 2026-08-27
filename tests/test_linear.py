"""Tests for Linear integration functions."""

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from maelstrom import task_cli
from maelstrom.integrations import linear as linear_mod
from maelstrom.integrations.linear import (
    create_comment,
    graphql_paginated,
    linear,
    localize_description_images,
)
from maelstrom.task_store import InMemoryStore


class TestCmdPlan:
    """Tests for ``mael linear plan`` — a thin wrapper over ``mael task add``."""

    # Branch generation is forced down the deterministic fallback by the
    # conftest autouse fixture (the ``claude`` CLI is blocked in tests).

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_assembles_brief_and_invokes_task_add(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "Do the thing",
            "description": "Some details.",
        }
        runner = CliRunner()
        result = runner.invoke(linear, ["plan", "ME-99"])
        assert result.exit_code == 0, result.output

        mock_get.assert_called_once_with("ME-99")
        mock_add.assert_called_once()
        kwargs = mock_add.call_args.kwargs
        assert kwargs["title"] == "Plan ME-99"
        assert kwargs["command"] == "plan-task"
        assert kwargs["parent"] == "linear.ME-99"
        assert kwargs["run"] is True
        assert kwargs["post_action"] == "linear.planned"
        assert kwargs["content"] == "# ME-99: Do the thing\n\nSome details."
        # The branch is generated from the *issue* title/number (here the
        # fallback, since the model call is forced to fail): number-led desc.
        assert kwargs["branch"] == "feat/99-do-thing"

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_run_forwards_run_flag(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        runner = CliRunner()
        result = runner.invoke(linear, ["plan", "ME-99", "--run"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["run"] is True

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_no_run_forwards_run_flag(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        runner = CliRunner()
        result = runner.invoke(linear, ["plan", "ME-99", "--no-run"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["run"] is False

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_defaults_to_opus(self, mock_get, mock_add):
        # Planning is pinned to Opus: the plan is the leverage point, so the
        # created plan-task session runs there regardless of the user's default.
        mock_get.return_value = {
            "identifier": "ME-99", "title": "T", "description": "",
        }
        result = CliRunner().invoke(linear, ["plan", "ME-99"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["model"] == "opus"

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_defaults_to_normal_mode(self, mock_get, mock_add):
        # The planning session writes draft task files in normal permission
        # mode; the skill prompt, not plan mode, forbids code edits.
        mock_get.return_value = {
            "identifier": "ME-99", "title": "T", "description": "",
        }
        result = CliRunner().invoke(linear, ["plan", "ME-99"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["mode"] == "normal"

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_explicit_mode_overrides_default(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99", "title": "T", "description": "",
        }
        result = CliRunner().invoke(linear, ["plan", "ME-99", "--mode", "plan"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["mode"] == "plan"

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_flags_override_the_planning_defaults(self, mock_get, mock_add):
        # Every hardcoded planning value is now a *default* the matching flag
        # overrides — the point of applying the shared decorator here.
        mock_get.return_value = {
            "identifier": "ME-99", "title": "T", "description": "",
        }
        result = CliRunner().invoke(linear, [
            "plan", "ME-99",
            "--model", "sonnet", "--mode", "auto", "--command", "other",
            "--parent", "custom", "--post-action", "sentry.resolved",
        ])
        assert result.exit_code == 0, result.output
        kwargs = mock_add.call_args.kwargs
        assert kwargs["model"] == "sonnet"
        assert kwargs["mode"] == "auto"
        assert kwargs["command"] == "other"
        assert kwargs["parent"] == "custom"
        assert kwargs["post_action"] == "sentry.resolved"

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_empty_value_clears_rather_than_defaults(self, mock_get, mock_add):
        # distinguish_unset: an explicit '' must mean "empty", matching
        # `task add`, not silently fall back to the planning default.
        mock_get.return_value = {
            "identifier": "ME-99", "title": "T", "description": "",
        }
        result = CliRunner().invoke(
            linear, ["plan", "ME-99", "--post-action", "", "--command", ""]
        )
        assert result.exit_code == 0, result.output
        kwargs = mock_add.call_args.kwargs
        assert kwargs["post_action"] == ""
        assert kwargs["command"] == ""

    @patch("maelstrom.task_cli._resolve_project", lambda project: project or "p")
    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_explicit_branch_skips_generation(self, mock_get, mock_add, monkeypatch):
        # Branch generation shells out to `claude -p`; an explicit --branch must
        # skip it entirely rather than generate-then-discard.
        mock_get.return_value = {
            "identifier": "ME-99", "title": "T", "description": "",
        }
        calls = []
        monkeypatch.setattr(
            "maelstrom.branch_name.generate_branch_name",
            lambda *a, **k: calls.append(a) or "generated",
        )
        result = CliRunner().invoke(
            linear, ["plan", "ME-99", "--branch", "mine/explicit"]
        )
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["branch"] == "mine/explicit"
        assert calls == []

    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_forwards_project(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        runner = CliRunner()
        result = runner.invoke(linear, ["plan", "ME-99", "--project", "myproj"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["project"] == "myproj"

    @patch("maelstrom.integrations.linear.get_issue")
    def test_plan_creates_task_on_generated_branch(self, mock_get, monkeypatch):
        """End-to-end: ``plan`` computes a descriptive branch from the issue
        title + bare number and persists it on the created task. With the model
        call forced to fail (autouse fixture) this is the deterministic fallback:
        ``feat/<number>-<slug>``."""
        mock_get.return_value = {
            "identifier": "NORT-123",
            "title": "Do the thing",
            "description": "",
        }
        store = InMemoryStore()
        monkeypatch.setattr(task_cli, "_store", lambda: store)
        # An InMemoryStore has no on-disk root for the SQLite index; point the CLI
        # index seam at the model's default in-memory index (set by conftest).
        monkeypatch.setattr(
            task_cli, "open_index", lambda _store: task_cli.model._DEFAULT_INDEX
        )
        monkeypatch.setattr(
            task_cli, "_resolve_project", lambda project: project or "p"
        )
        runner = CliRunner()
        result = runner.invoke(linear, ["plan", "NORT-123", "--no-run"])
        assert result.exit_code == 0, result.output

        created = task_cli.model.list_tasks(store, project="p")
        assert len(created) == 1
        assert created[0].parent == "linear.NORT-123"
        assert created[0].branch == "feat/123-do-thing"


PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00fakepngdata"


class TestLocalizeDescriptionImages:
    """Tests for ``localize_description_images`` — download + token rewrite."""

    def _patch_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "maelstrom.task_store.tasks_root", lambda: tmp_path / "tasks"
        )
        monkeypatch.setattr(
            linear_mod, "get_linear_api_key", lambda: "lin_key"
        )

    def test_downloads_and_rewrites_ref(self, tmp_path, monkeypatch):
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/32eac60d-abcd"
        desc = f"Before\n\n![image.png]({url})\n\nAfter"

        with patch.object(
            linear_mod, "request_bytes", return_value=PNG_BYTES
        ) as mock_req:
            result = localize_description_images("NORT-1", "proj", desc)

        mock_req.assert_called_once()
        assert mock_req.call_args.kwargs["headers"] == {"Authorization": "lin_key"}

        image_dir = tmp_path / "tasks" / "proj" / "images" / "NORT-1"
        written = list(image_dir.iterdir())
        assert len(written) == 1
        assert written[0].suffix == ".png"  # sniffed from magic bytes
        assert written[0].read_bytes() == PNG_BYTES

        # Ref rewritten to the portable token, alt text preserved.
        expected = f"{{{{MAEL_TASK_DIR}}}}/images/NORT-1/{written[0].name}"
        assert f"![image.png]({expected})" in result
        assert url not in result

    def test_no_images_returns_unchanged_and_writes_nothing(
        self, tmp_path, monkeypatch
    ):
        self._patch_root(monkeypatch, tmp_path)
        desc = "Just text, no images here."

        with patch.object(linear_mod, "request_bytes") as mock_req:
            result = localize_description_images("NORT-1", "proj", desc)

        assert result == desc
        mock_req.assert_not_called()
        assert not (tmp_path / "tasks" / "proj" / "images").exists()

    def test_failed_download_keeps_original_url(self, tmp_path, monkeypatch):
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/deadbeef"
        desc = f"![x]({url})"

        with patch.object(
            linear_mod,
            "request_bytes",
            side_effect=click.ClickException("HTTP Error 404: gone"),
        ):
            result = localize_description_images("NORT-1", "proj", desc)

        # No raise; original ref untouched; nothing written.
        assert result == desc
        assert not (tmp_path / "tasks" / "proj" / "images" / "NORT-1").exists()

    def test_duplicate_url_downloaded_once(self, tmp_path, monkeypatch):
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/samefile"
        desc = f"![a]({url}) and again ![b]({url})"

        with patch.object(
            linear_mod, "request_bytes", return_value=PNG_BYTES
        ) as mock_req:
            result = localize_description_images("NORT-1", "proj", desc)

        # One download, one file, both refs rewritten to the same token.
        assert mock_req.call_count == 1
        image_dir = tmp_path / "tasks" / "proj" / "images" / "NORT-1"
        assert len(list(image_dir.iterdir())) == 1
        assert result.count("{{MAEL_TASK_DIR}}/images/NORT-1/") == 2


class TestCreateComment:
    """Tests for create_comment function."""

    @patch("maelstrom.integrations.linear.graphql_request")
    def test_create_comment_success(self, mock_graphql):
        """Test successful comment creation."""
        mock_graphql.return_value = {
            "commentCreate": {
                "success": True,
                "comment": {"id": "comment-123"},
            }
        }

        result = create_comment("issue-456", "This is a progress report")

        assert result == {"id": "comment-123"}
        mock_graphql.assert_called_once()
        call_args = mock_graphql.call_args
        assert call_args[0][1] == {
            "input": {
                "issueId": "issue-456",
                "body": "This is a progress report",
            }
        }

    @patch("maelstrom.integrations.linear.graphql_request")
    def test_create_comment_failure(self, mock_graphql):
        """Test comment creation failure raises ClickException."""
        mock_graphql.return_value = {
            "commentCreate": {
                "success": False,
                "comment": None,
            }
        }

        with pytest.raises(click.ClickException, match="Failed to create comment"):
            create_comment("issue-456", "Some comment")

    @patch("maelstrom.integrations.linear.graphql_request")
    def test_create_comment_sends_correct_mutation(self, mock_graphql):
        """Test that the correct GraphQL mutation is sent."""
        mock_graphql.return_value = {
            "commentCreate": {
                "success": True,
                "comment": {"id": "c-1"},
            }
        }

        create_comment("issue-abc", "# Progress\n\nDone some work.")

        mutation = mock_graphql.call_args[0][0]
        assert "commentCreate" in mutation
        assert "CommentCreateInput" in mutation


class TestCmdCreateTask:
    """Tests for cmd_create_task command."""

    @patch("maelstrom.integrations.linear.get_product_label")
    @patch("maelstrom.integrations.linear.get_labels")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.create_issue")
    def test_create_task_with_product_label(
        self, mock_create, mock_states, mock_labels, mock_product_label
    ):
        """Test successful task creation with product label."""
        mock_states.return_value = {"Backlog": "state-1", "Todo": "state-2"}
        mock_product_label.return_value = "MyProduct"
        mock_labels.return_value = {"MyProduct": "label-1", "Bug": "label-2"}
        mock_create.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-42",
            "title": "New task",
        }

        runner = CliRunner()
        result = runner.invoke(linear, ["create-task", "New task"])

        assert result.exit_code == 0
        assert "PROJ-42" in result.output
        assert "New task" in result.output
        assert "Backlog" in result.output
        assert "MyProduct" in result.output
        mock_create.assert_called_once_with(
            title="New task",
            description="",
            state_id="state-1",
            label_ids=["label-1"],
        )

    @patch("maelstrom.integrations.linear.get_product_label")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.create_issue")
    def test_create_task_no_product_label(
        self, mock_create, mock_states, mock_product_label
    ):
        """Test task creation when no product label is configured."""
        mock_states.return_value = {"Backlog": "state-1"}
        mock_product_label.return_value = None
        mock_create.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-43",
            "title": "Another task",
        }

        runner = CliRunner()
        result = runner.invoke(linear, ["create-task", "Another task"])

        assert result.exit_code == 0
        assert "PROJ-43" in result.output
        assert "Label" not in result.output
        mock_create.assert_called_once_with(
            title="Another task",
            description="",
            state_id="state-1",
            label_ids=None,
        )

    @patch("maelstrom.integrations.linear.get_workflow_states")
    def test_create_task_no_backlog_state(self, mock_states):
        """Test error when Backlog state is not found."""
        mock_states.return_value = {"Todo": "state-2", "Done": "state-3"}

        runner = CliRunner()
        result = runner.invoke(linear, ["create-task", "Some task"])

        assert result.exit_code != 0
        assert "Backlog state not found" in result.output


class TestCmdSetStatus:
    """Tests for cmd_set_status command."""

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_set_status_planned(self, mock_get, mock_states, mock_update):
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "Todo"},
        }
        mock_states.return_value = {"Todo": "s-todo", "Planned": "s-planned"}

        runner = CliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "planned"])

        assert result.exit_code == 0, result.output
        assert "Todo -> Planned" in result.output
        mock_update.assert_called_once_with("issue-1", stateId="s-planned")

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_set_status_done_maps_to_unreleased(
        self, mock_get, mock_states, mock_update
    ):
        # `done` maps to the Unreleased state, with no subtask special-casing.
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "In Review"},
            "parent": {"id": "parent-1"},  # ignored — no special subtask handling
        }
        mock_states.return_value = {
            "In Review": "s-rev",
            "Unreleased": "s-unrel",
            "Done": "s-done",
        }

        runner = CliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "done"])

        assert result.exit_code == 0, result.output
        assert "In Review -> Unreleased" in result.output
        mock_update.assert_called_once_with("issue-1", stateId="s-unrel")

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_set_status_noop_when_already(self, mock_get, mock_states, mock_update):
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "Planned"},
        }
        mock_states.return_value = {"Todo": "s-todo", "Planned": "s-planned"}

        runner = CliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "planned"])

        assert result.exit_code == 0, result.output
        assert "already Planned" in result.output
        mock_update.assert_not_called()

    def test_set_status_invalid_choice_errors(self):
        # An unknown logical status is rejected by click before any API call.
        runner = CliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "bogus"])

        assert result.exit_code != 0
        assert "Invalid value" in result.output

    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_set_status_missing_workflow_state_errors(self, mock_get, mock_states):
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "Todo"},
        }
        mock_states.return_value = {"Todo": "s-todo"}  # no Unreleased state

        runner = CliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "done"])

        assert result.exit_code != 0
        assert "not found in workflow" in result.output


SAMPLE_DESCRIPTION_WITH_PLAN = (
    "Some preamble text.\n\n"
    "---\n\n"
    "# Implementation Plan\n\n"
    "**Session type: multi**\n\n"
    "## First Iteration: Build the API\n"
    "- Create endpoints\n"
    "- Add validation\n\n"
    "## Remaining Work\n"
    "- Build the UI\n"
    "- Write docs\n\n"
    "(end of plan)\n\n"
    "---\n\n"
    "Some footer text with ## First Iteration: Build the API in it."
)


class TestCmdEditPlan:
    """Tests for cmd_edit_plan command."""

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_edit_plan_string_mode_success(self, mock_get, mock_update):
        """Test successful edit with string mode."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": SAMPLE_DESCRIPTION_WITH_PLAN,
        }

        runner = CliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s",
             "## First Iteration: Build the API\n- Create endpoints\n- Add validation",
             "## Completed Iteration: Build the API\nBuilt endpoints with validation."],
        )

        assert result.exit_code == 0
        assert "Updated plan on PROJ-10" in result.output
        mock_update.assert_called_once()
        new_desc = mock_update.call_args[1]["description"]
        assert "## Completed Iteration: Build the API" in new_desc
        assert "Built endpoints with validation." in new_desc

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_edit_plan_file_mode_success(self, mock_get, mock_update, tmp_path):
        """Test successful edit with file-based mode."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": SAMPLE_DESCRIPTION_WITH_PLAN,
        }

        old_file = tmp_path / "old.md"
        new_file = tmp_path / "new.md"
        old_file.write_text("## First Iteration: Build the API\n- Create endpoints\n- Add validation")
        new_file.write_text("## Completed Iteration: Build the API\nDone.")

        runner = CliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", str(old_file), str(new_file)],
        )

        assert result.exit_code == 0
        assert "Updated plan on PROJ-10" in result.output
        new_desc = mock_update.call_args[1]["description"]
        assert "## Completed Iteration: Build the API" in new_desc

    @patch("maelstrom.integrations.linear.get_issue")
    def test_edit_plan_old_string_not_found(self, mock_get):
        """Test error when search string is not found in plan."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": SAMPLE_DESCRIPTION_WITH_PLAN,
        }

        runner = CliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s", "nonexistent text", "replacement"],
        )

        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("maelstrom.integrations.linear.get_issue")
    def test_edit_plan_ambiguous_match(self, mock_get):
        """Test error when search string matches multiple times in plan."""
        desc_with_dups = (
            "# Implementation Plan\n\n"
            "- item\n- item\n\n"
            "(end of plan)"
        )
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": desc_with_dups,
        }

        runner = CliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s", "--", "- item", "- new item"],
        )

        assert result.exit_code != 0
        assert "2 times" in result.output

    @patch("maelstrom.integrations.linear.get_issue")
    def test_edit_plan_no_plan(self, mock_get):
        """Test error when issue has no plan."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": "Just a description, no plan.",
        }

        runner = CliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s", "old", "new"],
        )

        assert result.exit_code != 0
        assert "No implementation plan found" in result.output

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.get_issue")
    def test_edit_plan_scoped_to_plan_section(self, mock_get, mock_update):
        """Test that edit only affects plan section, not text outside it."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": SAMPLE_DESCRIPTION_WITH_PLAN,
        }

        # "## First Iteration: Build the API" also appears in footer text,
        # but string mode should only match within plan section
        runner = CliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s",
             "## First Iteration: Build the API\n- Create endpoints\n- Add validation",
             "## Completed Iteration: Build the API\nDone."],
        )

        assert result.exit_code == 0
        new_desc = mock_update.call_args[1]["description"]
        # Footer text should be unchanged
        assert "Some footer text with ## First Iteration: Build the API in it." in new_desc
        # Plan section should be updated
        assert "## Completed Iteration: Build the API\nDone." in new_desc


def _page(nodes, *, has_next=False, cursor=None):
    """Build a single `issues` connection page as the API would return it."""
    return {
        "issues": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        }
    }


class TestGraphqlPaginated:
    """Tests for the graphql_paginated helper."""

    @patch("maelstrom.integrations.linear.graphql_request")
    def test_single_page(self, mock_graphql):
        mock_graphql.return_value = _page([{"id": "a"}, {"id": "b"}])

        nodes = graphql_paginated("query {}", {"teamId": "t"}, connection="issues")

        assert nodes == [{"id": "a"}, {"id": "b"}]
        assert mock_graphql.call_count == 1
        # The caller's variables are preserved and first/after are added.
        sent = mock_graphql.call_args[0][1]
        assert sent["teamId"] == "t"
        assert sent["first"] == 100
        assert sent["after"] is None

    @patch("maelstrom.integrations.linear.graphql_request")
    def test_multi_page_follows_cursor(self, mock_graphql):
        mock_graphql.side_effect = [
            _page([{"id": "a"}], has_next=True, cursor="cur-1"),
            _page([{"id": "b"}], has_next=True, cursor="cur-2"),
            _page([{"id": "c"}]),
        ]

        nodes = graphql_paginated("query {}", connection="issues", page_size=1)

        assert nodes == [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert mock_graphql.call_count == 3
        afters = [call[0][1]["after"] for call in mock_graphql.call_args_list]
        assert afters == [None, "cur-1", "cur-2"]
        assert mock_graphql.call_args_list[0][0][1]["first"] == 1

    @patch("maelstrom.integrations.linear.graphql_request")
    def test_page_cap_aborts(self, mock_graphql):
        # A server that never clears hasNextPage must not spin forever.
        mock_graphql.return_value = _page([{"id": "a"}], has_next=True, cursor="c")

        with pytest.raises(click.ClickException, match="exceeded 3 pages"):
            graphql_paginated("query {}", connection="issues", max_pages=3)

        assert mock_graphql.call_count == 3


class TestCmdRelease:
    """Tests for cmd_release — the bulk 'Unreleased' -> 'Done' transition."""

    STATES = {"Unreleased": "s-unrel", "Done": "s-done"}

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.graphql_request")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_team_id")
    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_paginates_all_pages(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        # The regression test: >50 unreleased tickets used to be silently capped
        # at Linear's default page size, releasing only the first page.
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        page_one = [
            {"id": f"i-{n}", "identifier": f"PROJ-{n}", "title": f"Task {n}"}
            for n in range(100)
        ]
        page_two = [{"id": "i-100", "identifier": "PROJ-100", "title": "Task 100"}]
        mock_graphql.side_effect = [
            _page(page_one, has_next=True, cursor="cur-1"),
            _page(page_two),
        ]

        runner = CliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code == 0, result.output
        assert mock_update.call_count == 101
        assert "Released 101 task(s)." in result.output
        assert "PROJ-100: Task 100 -> Done" in result.output
        # The second request carries page one's endCursor.
        assert mock_graphql.call_args_list[1][0][1]["after"] == "cur-1"
        mock_update.assert_any_call("i-100", stateId="s-done")

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.graphql_request")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_team_id")
    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_no_issues(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        mock_graphql.return_value = _page([])

        runner = CliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code == 0, result.output
        assert "No unreleased tasks found with label 'askastro'." in result.output
        mock_update.assert_not_called()

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.graphql_request")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_team_id")
    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_dry_run_mutates_nothing(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        mock_graphql.return_value = _page(
            [{"id": "i-1", "identifier": "PROJ-1", "title": "Task 1"}]
        )

        runner = CliRunner()
        result = runner.invoke(linear, ["release", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would release 1 task(s)" in result.output
        assert "PROJ-1: Task 1 -> Done" in result.output
        assert "no tasks changed" in result.output
        mock_update.assert_not_called()

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.graphql_request")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_team_id")
    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_continues_past_failures(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        # One bad ticket must not strand the rest half-released.
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        mock_graphql.return_value = _page(
            [
                {"id": "i-1", "identifier": "PROJ-1", "title": "Task 1"},
                {"id": "i-2", "identifier": "PROJ-2", "title": "Task 2"},
                {"id": "i-3", "identifier": "PROJ-3", "title": "Task 3"},
            ]
        )
        mock_update.side_effect = [
            None,
            click.ClickException("Failed to update issue"),
            None,
        ]

        runner = CliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert mock_update.call_count == 3
        assert "PROJ-2: Task 2 -> FAILED" in result.output
        assert "PROJ-3: Task 3 -> Done" in result.output
        assert "Released 2 task(s)." in result.output
        assert "1 task(s) failed to release: PROJ-2" in result.output

    @patch("maelstrom.integrations.linear.update_issue")
    @patch("maelstrom.integrations.linear.graphql_request")
    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_team_id")
    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_continues_past_transport_errors(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        # A network/transport error is not a ClickException, but it must still
        # not abort the run and strand the remaining tickets.
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        mock_graphql.return_value = _page(
            [
                {"id": "i-1", "identifier": "PROJ-1", "title": "Task 1"},
                {"id": "i-2", "identifier": "PROJ-2", "title": "Task 2"},
            ]
        )
        mock_update.side_effect = [OSError("connection reset"), None]

        runner = CliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert mock_update.call_count == 2
        assert "PROJ-1: Task 1 -> FAILED (connection reset)" in result.output
        assert "PROJ-2: Task 2 -> Done" in result.output
        assert "1 task(s) failed to release: PROJ-1" in result.output

    @patch("maelstrom.integrations.linear.get_workflow_states")
    @patch("maelstrom.integrations.linear.get_team_id")
    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_missing_workflow_state_errors(
        self, mock_label, mock_team, mock_states
    ):
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = {"Done": "s-done"}  # no Unreleased

        runner = CliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert "'Unreleased' state not found in workflow" in result.output

    @patch("maelstrom.integrations.linear.get_product_label")
    def test_release_missing_product_label_errors(self, mock_label):
        mock_label.return_value = None

        runner = CliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert "linear.product_label not configured" in result.output
