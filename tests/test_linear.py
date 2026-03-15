"""Tests for Linear integration functions."""

from unittest.mock import patch

import click
import pytest

from click.testing import CliRunner

from maelstrom.linear import create_comment, linear


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
