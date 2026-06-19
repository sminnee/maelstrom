"""Tests for Linear integration functions."""

from unittest.mock import patch

import click
import pytest

from click.testing import CliRunner

from maelstrom import task_cli
from maelstrom.linear import create_comment, linear
from maelstrom.task_store import InMemoryStore


class TestCmdPlan:
    """Tests for ``mael linear plan`` — a thin wrapper over ``mael task add``."""

    # Branch generation is forced down the deterministic fallback by the
    # conftest autouse fixture (the ``claude`` CLI is blocked in tests).

    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.task_cli.add_task")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.get_issue")
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


class TestCreateComment:
    """Tests for create_comment function."""

    @patch("maelstrom.linear.graphql_request")
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

    @patch("maelstrom.linear.graphql_request")
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

    @patch("maelstrom.linear.graphql_request")
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

    @patch("maelstrom.linear.get_product_label")
    @patch("maelstrom.linear.get_labels")
    @patch("maelstrom.linear.get_workflow_states")
    @patch("maelstrom.linear.create_issue")
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

    @patch("maelstrom.linear.get_product_label")
    @patch("maelstrom.linear.get_workflow_states")
    @patch("maelstrom.linear.create_issue")
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

    @patch("maelstrom.linear.get_workflow_states")
    def test_create_task_no_backlog_state(self, mock_states):
        """Test error when Backlog state is not found."""
        mock_states.return_value = {"Todo": "state-2", "Done": "state-3"}

        runner = CliRunner()
        result = runner.invoke(linear, ["create-task", "Some task"])

        assert result.exit_code != 0
        assert "Backlog state not found" in result.output


class TestCmdSetStatus:
    """Tests for cmd_set_status command."""

    @patch("maelstrom.linear.update_issue")
    @patch("maelstrom.linear.get_workflow_states")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.update_issue")
    @patch("maelstrom.linear.get_workflow_states")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.update_issue")
    @patch("maelstrom.linear.get_workflow_states")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.get_workflow_states")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.update_issue")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.update_issue")
    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.get_issue")
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

    @patch("maelstrom.linear.update_issue")
    @patch("maelstrom.linear.get_issue")
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
