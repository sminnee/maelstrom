"""Tests for the ``mael linear`` commands.

The Linear client functions are tested in
``lib/domain/tests/test_integrations_linear.py``.
"""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mael_cli.integrations import linear_cli
from mael_cli.integrations.linear_cli import linear_group as linear
from mael_domain.integrations.errors import IntegrationError


class ThreadedCliRunner(CliRunner):
    """A ``CliRunner`` that invokes the command on its own thread.

    ``AsyncGroup`` opens one event loop per invocation with ``asyncio.run``,
    which refuses to nest. These tests are coroutines, so a plain ``invoke``
    would call it with a loop already running. A worker thread has no loop,
    which is the state a real ``mael`` process starts in.
    """

    def invoke(self, *args, **kwargs):  # type: ignore[override]
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: super(ThreadedCliRunner, self).invoke(*args, **kwargs)
            ).result()


class TestCmdPlan:
    """Tests for ``mael linear plan`` — a thin wrapper over ``mael task add``."""

    # Branch generation is forced down the deterministic fallback by the
    # conftest autouse fixture (the ``claude`` CLI is blocked in tests).

    @pytest.fixture(autouse=True)
    def _project(self, monkeypatch):
        """Resolve an unnamed project to ``p`` rather than from the cwd."""
        monkeypatch.setattr(
            linear_cli, "resolve_project", lambda project: project or "p"
        )

    @patch("mael_cli.task_cli.add_task")
    def test_plan_passes_the_planned_fields_to_task_add(self, mock_add, monkeypatch):
        """Unset flags fall back to ``build_plan_task``, which owns the values.

        ``test_integrations_linear.py::TestBuildPlanTask`` tests the values.
        """
        planned = {
            "title": "Plan ME-99",
            "command": "plan-task",
            "mode": "normal",
            "model": "opus",
            "parent": "linear.ME-99",
            "post_action": "linear.planned",
            "content": "# ME-99: T",
            "branch": "feat/99-t",
        }
        monkeypatch.setattr(
            linear_cli.linear, "build_plan_task", lambda *a, **k: planned
        )

        result = ThreadedCliRunner().invoke(linear, ["plan", "ME-99"])

        assert result.exit_code == 0, result.output
        kwargs = mock_add.call_args.kwargs
        assert {k: kwargs[k] for k in planned} == planned
        assert kwargs["run"] is True

    @patch("mael_cli.task_cli.add_task")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_plan_run_forwards_run_flag(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["plan", "ME-99", "--run"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["run"] is True

    @patch("mael_cli.task_cli.add_task")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_plan_no_run_forwards_run_flag(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["plan", "ME-99", "--no-run"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["run"] is False

    @patch("mael_cli.task_cli.add_task")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_plan_explicit_mode_overrides_default(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        result = ThreadedCliRunner().invoke(linear, ["plan", "ME-99", "--mode", "plan"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["mode"] == "plan"

    @patch("mael_cli.task_cli.add_task")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_plan_flags_override_the_planning_defaults(self, mock_get, mock_add):
        # Every hardcoded planning value is now a *default* the matching flag
        # overrides — the point of applying the shared decorator here.
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        result = ThreadedCliRunner().invoke(
            linear,
            [
                "plan",
                "ME-99",
                "--model",
                "sonnet",
                "--mode",
                "auto",
                "--command",
                "other",
                "--parent",
                "custom",
                "--post-action",
                "sentry.resolved",
            ],
        )
        assert result.exit_code == 0, result.output
        kwargs = mock_add.call_args.kwargs
        assert kwargs["model"] == "sonnet"
        assert kwargs["mode"] == "auto"
        assert kwargs["command"] == "other"
        assert kwargs["parent"] == "custom"
        assert kwargs["post_action"] == "sentry.resolved"

    @patch("mael_cli.task_cli.add_task")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_plan_empty_value_clears_rather_than_defaults(self, mock_get, mock_add):
        # distinguish_unset: an explicit '' must mean "empty", matching
        # `task add`, not silently fall back to the planning default.
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        result = ThreadedCliRunner().invoke(
            linear, ["plan", "ME-99", "--post-action", "", "--command", ""]
        )
        assert result.exit_code == 0, result.output
        kwargs = mock_add.call_args.kwargs
        assert kwargs["post_action"] == ""
        assert kwargs["command"] == ""

    @patch("mael_cli.task_cli.add_task")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_plan_forwards_project(self, mock_get, mock_add):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["plan", "ME-99", "--project", "myproj"])
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["project"] == "myproj"

    @patch("mael_domain.integrations.linear.get_issue")
    @patch("mael_cli.task_cli.add_task")
    def test_plan_forwards_the_execute_model(self, mock_add, mock_get):
        """``cmd_plan`` enumerates ``add_task``'s kwargs by hand, so the shared
        decorator proves the *flag* is offered while the value can still be
        dropped on the way through."""
        mock_get.return_value = {
            "identifier": "NORT-123",
            "title": "Do the thing",
            "description": "",
        }
        result = ThreadedCliRunner().invoke(
            linear, ["plan", "NORT-123", "--no-run", "--execute-model", "sonnet"]
        )
        assert result.exit_code == 0, result.output
        assert mock_add.call_args.kwargs["execute_model"] == "sonnet"


class TestCmdCreateTask:
    """Tests for cmd_create_task command."""

    @patch("mael_domain.integrations.linear.get_product_label")
    @patch("mael_domain.integrations.linear.get_labels")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.create_issue")
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

        runner = ThreadedCliRunner()
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

    @patch("mael_domain.integrations.linear.get_product_label")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.create_issue")
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

        runner = ThreadedCliRunner()
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

    @patch("mael_domain.integrations.linear.get_workflow_states")
    def test_create_task_no_backlog_state(self, mock_states):
        """Test error when Backlog state is not found."""
        mock_states.return_value = {"Todo": "state-2", "Done": "state-3"}

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["create-task", "Some task"])

        assert result.exit_code != 0
        assert "Backlog state not found" in result.output


class TestCmdSetStatus:
    """Tests for cmd_set_status command."""

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_set_status_planned(self, mock_get, mock_states, mock_update):
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "Todo"},
        }
        mock_states.return_value = {"Todo": "s-todo", "Planned": "s-planned"}

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "planned"])

        assert result.exit_code == 0, result.output
        assert "Todo -> Planned" in result.output
        mock_update.assert_called_once_with("issue-1", stateId="s-planned")

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_issue")
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

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "done"])

        assert result.exit_code == 0, result.output
        assert "In Review -> Unreleased" in result.output
        mock_update.assert_called_once_with("issue-1", stateId="s-unrel")

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_set_status_noop_when_already(self, mock_get, mock_states, mock_update):
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "Planned"},
        }
        mock_states.return_value = {"Todo": "s-todo", "Planned": "s-planned"}

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "planned"])

        assert result.exit_code == 0, result.output
        assert "already Planned" in result.output
        mock_update.assert_not_called()

    def test_set_status_invalid_choice_errors(self):
        # An unknown logical status is rejected by click before any API call.
        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "bogus"])

        assert result.exit_code != 0
        assert "Invalid value" in result.output

    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_set_status_missing_workflow_state_errors(self, mock_get, mock_states):
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-7",
            "state": {"name": "Todo"},
        }
        mock_states.return_value = {"Todo": "s-todo"}  # no Unreleased state

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["set-status", "PROJ-7", "done"])

        # The group turns the model's IntegrationError into a CLI error.
        assert result.exit_code == 1
        assert "Error: 'Unreleased' state not found in workflow." in result.output


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

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.get_issue")
    def test_edit_plan_string_mode_success(self, mock_get, mock_update):
        """Test successful edit with string mode."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": SAMPLE_DESCRIPTION_WITH_PLAN,
        }

        runner = ThreadedCliRunner()
        result = runner.invoke(
            linear,
            [
                "edit-plan",
                "PROJ-10",
                "-s",
                "## First Iteration: Build the API\n- Create endpoints\n- Add validation",
                "## Completed Iteration: Build the API\nBuilt endpoints with validation.",
            ],
        )

        assert result.exit_code == 0
        assert "Updated plan on PROJ-10" in result.output
        mock_update.assert_called_once()
        new_desc = mock_update.call_args[1]["description"]
        assert "## Completed Iteration: Build the API" in new_desc
        assert "Built endpoints with validation." in new_desc

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.get_issue")
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
        old_file.write_text(
            "## First Iteration: Build the API\n- Create endpoints\n- Add validation"
        )
        new_file.write_text("## Completed Iteration: Build the API\nDone.")

        runner = ThreadedCliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", str(old_file), str(new_file)],
        )

        assert result.exit_code == 0
        assert "Updated plan on PROJ-10" in result.output
        new_desc = mock_update.call_args[1]["description"]
        assert "## Completed Iteration: Build the API" in new_desc

    @patch("mael_domain.integrations.linear.get_issue")
    def test_edit_plan_old_string_not_found(self, mock_get):
        """Test error when search string is not found in plan."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": SAMPLE_DESCRIPTION_WITH_PLAN,
        }

        runner = ThreadedCliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s", "nonexistent text", "replacement"],
        )

        assert result.exit_code != 0
        assert "not found" in result.output

    @patch("mael_domain.integrations.linear.get_issue")
    def test_edit_plan_ambiguous_match(self, mock_get):
        """Test error when search string matches multiple times in plan."""
        desc_with_dups = "# Implementation Plan\n\n- item\n- item\n\n(end of plan)"
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": desc_with_dups,
        }

        runner = ThreadedCliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s", "--", "- item", "- new item"],
        )

        assert result.exit_code != 0
        assert "2 times" in result.output

    @patch("mael_domain.integrations.linear.get_issue")
    def test_edit_plan_no_plan(self, mock_get):
        """Test error when issue has no plan."""
        mock_get.return_value = {
            "id": "issue-1",
            "identifier": "PROJ-10",
            "title": "Test issue",
            "description": "Just a description, no plan.",
        }

        runner = ThreadedCliRunner()
        result = runner.invoke(
            linear,
            ["edit-plan", "PROJ-10", "-s", "old", "new"],
        )

        assert result.exit_code != 0
        assert "No implementation plan found" in result.output

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.get_issue")
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
        runner = ThreadedCliRunner()
        result = runner.invoke(
            linear,
            [
                "edit-plan",
                "PROJ-10",
                "-s",
                "## First Iteration: Build the API\n- Create endpoints\n- Add validation",
                "## Completed Iteration: Build the API\nDone.",
            ],
        )

        assert result.exit_code == 0
        new_desc = mock_update.call_args[1]["description"]
        # Footer text should be unchanged
        assert (
            "Some footer text with ## First Iteration: Build the API in it." in new_desc
        )
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


class TestCmdRelease:
    """Tests for cmd_release — the bulk 'Unreleased' -> 'Done' transition."""

    STATES = {"Unreleased": "s-unrel", "Done": "s-done"}

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.graphql_request")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_team_id")
    @patch("mael_domain.integrations.linear.get_product_label")
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

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code == 0, result.output
        assert mock_update.call_count == 101
        assert "Released 101 task(s)." in result.output
        assert "PROJ-100: Task 100 -> Done" in result.output
        # The second request carries page one's endCursor.
        assert mock_graphql.call_args_list[1][0][1]["after"] == "cur-1"
        mock_update.assert_any_call("i-100", stateId="s-done")

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.graphql_request")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_team_id")
    @patch("mael_domain.integrations.linear.get_product_label")
    def test_release_no_issues(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        mock_graphql.return_value = _page([])

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code == 0, result.output
        assert "No unreleased tasks found with label 'askastro'." in result.output
        mock_update.assert_not_called()

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.graphql_request")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_team_id")
    @patch("mael_domain.integrations.linear.get_product_label")
    def test_release_dry_run_mutates_nothing(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = self.STATES
        mock_graphql.return_value = _page(
            [{"id": "i-1", "identifier": "PROJ-1", "title": "Task 1"}]
        )

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Would release 1 task(s)" in result.output
        assert "PROJ-1: Task 1 -> Done" in result.output
        assert "no tasks changed" in result.output
        mock_update.assert_not_called()

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.graphql_request")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_team_id")
    @patch("mael_domain.integrations.linear.get_product_label")
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
            IntegrationError("Failed to update issue"),
            None,
        ]

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert mock_update.call_count == 3
        assert "PROJ-2: Task 2 -> FAILED" in result.output
        assert "PROJ-3: Task 3 -> Done" in result.output
        assert "Released 2 task(s)." in result.output
        assert "1 task(s) failed to release: PROJ-2" in result.output

    @patch("mael_domain.integrations.linear.update_issue")
    @patch("mael_domain.integrations.linear.graphql_request")
    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_team_id")
    @patch("mael_domain.integrations.linear.get_product_label")
    def test_release_continues_past_transport_errors(
        self, mock_label, mock_team, mock_states, mock_graphql, mock_update
    ):
        # A network/transport error is not an IntegrationError, but it must still
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

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert mock_update.call_count == 2
        assert "PROJ-1: Task 1 -> FAILED (connection reset)" in result.output
        assert "PROJ-2: Task 2 -> Done" in result.output
        assert "1 task(s) failed to release: PROJ-1" in result.output

    @patch("mael_domain.integrations.linear.get_workflow_states")
    @patch("mael_domain.integrations.linear.get_team_id")
    @patch("mael_domain.integrations.linear.get_product_label")
    def test_release_missing_workflow_state_errors(
        self, mock_label, mock_team, mock_states
    ):
        mock_label.return_value = "askastro"
        mock_team.return_value = "team-1"
        mock_states.return_value = {"Done": "s-done"}  # no Unreleased

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert "'Unreleased' state not found in workflow" in result.output

    @patch("mael_domain.integrations.linear.get_product_label")
    def test_release_missing_product_label_errors(self, mock_label):
        mock_label.return_value = None

        runner = ThreadedCliRunner()
        result = runner.invoke(linear, ["release"])

        assert result.exit_code != 0
        assert "linear.product_label not configured" in result.output
