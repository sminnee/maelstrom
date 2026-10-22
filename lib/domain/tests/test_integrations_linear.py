"""Tests for the Linear client: issue queries, plan briefs, images, comments.

The ``mael linear`` commands are tested in ``cli/tests/test_linear.py``.
"""

from unittest.mock import patch

import pytest

from mael_domain.integrations import linear as linear_mod
from mael_domain.integrations.errors import IntegrationError, IntegrationHTTPError
from mael_domain.integrations.linear import (
    build_plan_task,
    create_comment,
    fetch_cycle_issues,
    graphql_paginated,
    localize_description_images,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00fakepngdata"


class TestFetchCycleIssues:
    """The rows behind ``mael linear list-tasks`` and the orchestrator's picker."""

    @patch("mael_domain.integrations.linear.get_current_cycle")
    @patch("mael_domain.integrations.linear.graphql_paginated")
    def test_returns_the_current_cycle_s_issues(self, mock_paginated, mock_cycle):
        mock_cycle.return_value = {"id": "c1", "name": "Sprint 4", "number": 4}
        mock_paginated.return_value = [
            {
                "identifier": "ME-1",
                "title": "Do the thing",
                "state": {"name": "Todo", "type": "unstarted"},
                "parent": None,
            }
        ]
        issues = fetch_cycle_issues("team-1")

        assert issues == [
            {
                "identifier": "ME-1",
                "title": "Do the thing",
                "state": {"name": "Todo", "type": "unstarted"},
                "parent": None,
            }
        ]
        # Scoped to the team and the cycle, and paginated so a cycle over 50
        # issues is not silently cut short.
        variables = mock_paginated.call_args.args[1]
        assert variables["teamId"] == "team-1"
        assert variables["cycleId"] == "c1"

    @patch("mael_domain.integrations.linear.get_current_cycle", return_value=None)
    @patch("mael_domain.integrations.linear.graphql_paginated")
    def test_falls_back_to_active_issues_with_no_cycle(self, mock_paginated, _cycle):
        mock_paginated.return_value = []
        assert fetch_cycle_issues("team-1") == []
        # No cycle to scope by, so the filter excludes the done states instead.
        query = mock_paginated.call_args.args[0]
        assert "cycle" not in query
        assert "nin" in query

    @patch("mael_domain.integrations.linear.get_current_cycle")
    @patch("mael_domain.integrations.linear.graphql_request")
    def test_declares_every_variable_it_passes(self, mock_request, mock_cycle):
        """`graphql_paginated` adds `first`/`after`, which the query must declare.

        Mocking the paginator away hides this: an undeclared variable is a
        server-side error, so the real helper is what this drives.
        """
        mock_cycle.return_value = {"id": "c1", "name": "S", "number": 1}
        mock_request.return_value = {
            "issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}
        }
        for cycle in ({"id": "c1", "name": "S", "number": 1}, None):
            mock_cycle.return_value = cycle
            fetch_cycle_issues("team-1")
            query, variables = mock_request.call_args.args
            for name in variables:
                assert f"${name}:" in query, f"{name} is passed but not declared"

    @patch("mael_domain.integrations.linear.graphql_request")
    def test_asks_for_the_cycle_of_the_team_it_was_given(self, mock_request):
        """The team is the argument's, never the ambient cwd's.

        The orchestrator is one long-lived process serving every project, so a
        cycle looked up from its own cwd would be another project's.
        """
        mock_request.return_value = {
            "team": {"activeCycle": {"id": "c1", "name": "S", "number": 1}},
            "issues": {"nodes": [], "pageInfo": {"hasNextPage": False}},
        }
        fetch_cycle_issues("team-1")
        asked = [call.args[1]["teamId"] for call in mock_request.call_args_list]
        assert asked == ["team-1", "team-1"]

    @patch("mael_domain.integrations.linear.get_current_cycle")
    @patch("mael_domain.integrations.linear.graphql_paginated")
    def test_filters_by_status_when_asked(self, mock_paginated, mock_cycle):
        mock_cycle.return_value = {"id": "c1", "name": "S", "number": 1}
        mock_paginated.return_value = []
        fetch_cycle_issues("team-1", status="progress")
        assert mock_paginated.call_args.args[1]["status"] == "progress"


# ``warn=pytest.fail`` asserts that nothing warns: ``pytest.fail`` raises a
# ``BaseException``, which no ``except IntegrationError`` swallows.


class TestBuildPlanTask:
    """The task fields ``mael linear plan`` creates, without creating them."""

    @patch("mael_domain.integrations.linear.get_issue")
    def test_returns_the_fields_cmd_plan_would_pass(self, mock_get):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "Do the thing",
            "description": "Some details.",
        }
        fields = build_plan_task("ME-99", "p", warn=pytest.fail)

        assert fields["title"] == "Plan ME-99"
        assert fields["command"] == "plan-task"
        assert fields["mode"] == "normal"
        assert fields["model"] == "opus"
        assert fields["parent"] == "linear.ME-99"
        assert fields["post_action"] == "linear.planned"
        assert fields["content"] == "# ME-99: Do the thing\n\nSome details."
        assert fields["branch"] == "feat/99-do-thing"

    @patch("mael_domain.integrations.linear.get_issue")
    def test_an_explicit_branch_skips_generation(self, mock_get):
        mock_get.return_value = {
            "identifier": "ME-99",
            "title": "T",
            "description": "",
        }
        # The caller already named a branch -- the orchestrator infers its own
        # rather than shelling out to generate one.
        fields = build_plan_task("ME-99", "p", branch="feat/99-given", warn=pytest.fail)
        assert fields["branch"] == "feat/99-given"


class TestLocalizeDescriptionImages:
    """Tests for ``localize_description_images`` — download + token rewrite."""

    def _patch_root(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "mael_domain.task_store.tasks_root", lambda: tmp_path / "tasks"
        )
        monkeypatch.setattr(linear_mod, "get_linear_api_key", lambda: "lin_key")

    def test_downloads_and_rewrites_ref(self, tmp_path, monkeypatch):
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/32eac60d-abcd"
        desc = f"Before\n\n![image.png]({url})\n\nAfter"

        with patch.object(
            linear_mod, "request_bytes", return_value=PNG_BYTES
        ) as mock_req:
            result = localize_description_images(
                "NORT-1", "proj", desc, warn=pytest.fail
            )

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
            result = localize_description_images(
                "NORT-1", "proj", desc, warn=pytest.fail
            )

        assert result == desc
        mock_req.assert_not_called()
        assert not (tmp_path / "tasks" / "proj" / "images").exists()

    def test_failed_download_keeps_original_url(self, tmp_path, monkeypatch):
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/deadbeef"
        desc = f"![x]({url})"

        warnings: list[str] = []
        with patch.object(
            linear_mod,
            "request_bytes",
            side_effect=IntegrationHTTPError(404, "gone"),
        ):
            result = localize_description_images(
                "NORT-1", "proj", desc, warn=warnings.append
            )

        # No raise; original ref untouched; nothing written.
        assert result == desc
        assert warnings == [
            f"warning: could not download image {url}: HTTP Error 404: gone; "
            "leaving the original URL in the brief"
        ]
        assert not (tmp_path / "tasks" / "proj" / "images" / "NORT-1").exists()

    def test_duplicate_url_downloaded_once(self, tmp_path, monkeypatch):
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/samefile"
        desc = f"![a]({url}) and again ![b]({url})"

        with patch.object(
            linear_mod, "request_bytes", return_value=PNG_BYTES
        ) as mock_req:
            result = localize_description_images(
                "NORT-1", "proj", desc, warn=pytest.fail
            )

        # One download, one file, both refs rewritten to the same token.
        assert mock_req.call_count == 1
        image_dir = tmp_path / "tasks" / "proj" / "images" / "NORT-1"
        assert len(list(image_dir.iterdir())) == 1
        assert result.count("{{MAEL_TASK_DIR}}/images/NORT-1/") == 2

    def test_an_unstorable_image_keeps_the_original_url(self, tmp_path, monkeypatch):
        """One bad image must not abort the plan, as the docstring promises.

        A Linear brief can hold an SVG, an oversized screenshot, or a URL that
        answers HTTP 200 with an HTML error body. The storage layer refuses all
        three, and the brief keeps the ref it came with.
        """
        self._patch_root(monkeypatch, tmp_path)
        url = "https://uploads.linear.app/7b3f/not-an-image"
        desc = f"Before\n\n![diagram.svg]({url})\n\nAfter"

        warnings: list[str] = []
        with patch.object(
            linear_mod, "request_bytes", return_value=b"<svg>not a raster image</svg>"
        ):
            result = localize_description_images(
                "NORT-1", "proj", desc, warn=warnings.append
            )

        assert result == desc
        assert len(warnings) == 1
        assert warnings[0].startswith(f"warning: could not store image {url}: ")
        assert not (tmp_path / "tasks" / "proj" / "images" / "NORT-1").exists()


class TestCreateComment:
    """Tests for create_comment function."""

    @patch("mael_domain.integrations.linear.graphql_request")
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

    @patch("mael_domain.integrations.linear.graphql_request")
    def test_create_comment_failure(self, mock_graphql):
        """Test comment creation failure raises IntegrationError."""
        mock_graphql.return_value = {
            "commentCreate": {
                "success": False,
                "comment": None,
            }
        }

        with pytest.raises(IntegrationError, match="Failed to create comment"):
            create_comment("issue-456", "Some comment")

    @patch("mael_domain.integrations.linear.graphql_request")
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

    @patch("mael_domain.integrations.linear.graphql_request")
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

    @patch("mael_domain.integrations.linear.graphql_request")
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

    @patch("mael_domain.integrations.linear.graphql_request")
    def test_page_cap_aborts(self, mock_graphql):
        # A server that never clears hasNextPage must not spin forever.
        mock_graphql.return_value = _page([{"id": "a"}], has_next=True, cursor="c")

        with pytest.raises(IntegrationError, match="exceeded 3 pages"):
            graphql_paginated("query {}", connection="issues", max_pages=3)

        assert mock_graphql.call_count == 3
