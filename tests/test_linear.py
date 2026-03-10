"""Tests for Linear integration functions."""

from unittest.mock import patch

import click
import pytest

from maelstrom.linear import create_comment


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
