"""Tests for the pure GitHub model — no subprocess, no mocking."""

import ast
import copy
import json
import pathlib

import pytest

from maelstrom import github_model, worktree_model
from maelstrom.github_model import (
    Artifact,
    CheckRun,
    GitHubCliMissing,
    GitHubCommandFailed,
    GitHubError,
    NoChecksFound,
    NoPullRequest,
    OpenPrsPage,
    PRComment,
    PRInfo,
    PullRequestNotMergeable,
    SyncFailed,
    is_missing_pr_error,
    parse_artifacts,
    parse_check_runs,
    parse_open_prs_page,
    parse_pr_comments,
    parse_pr_info,
    run_id_from_link,
    stack_chain,
)


class TestStackChain:
    """`stack_chain` — the bottom-to-top branch list `gh stack link` wants."""

    def test_an_unstacked_branch_is_a_chain_of_one(self):
        assert stack_chain("feat/solo", {}) == ["feat/solo"]

    def test_a_two_branch_stack_reads_bottom_to_top(self):
        assert stack_chain("feat/b", {"feat/b": "feat/a"}) == ["feat/a", "feat/b"]

    def test_a_deep_stack_reads_bottom_to_top(self):
        bases = {"feat/c": "feat/b", "feat/b": "feat/a"}
        assert stack_chain("feat/c", bases) == ["feat/a", "feat/b", "feat/c"]

    def test_the_walk_stops_at_main(self):
        assert stack_chain("feat/b", {"feat/b": "feat/a", "feat/a": "main"}) == [
            "feat/a",
            "feat/b",
        ]

    def test_unrelated_bases_do_not_join_the_chain(self):
        bases = {"feat/b": "feat/a", "feat/x": "feat/y"}
        assert stack_chain("feat/b", bases) == ["feat/a", "feat/b"]

    def test_a_cycle_terminates_rather_than_hanging(self):
        """Cycles are rejected at set time; this is the belt-and-braces stop."""
        assert stack_chain("feat/a", {"feat/a": "feat/b", "feat/b": "feat/a"}) == [
            "feat/b",
            "feat/a",
        ]


class TestParsePrInfo:
    """`parse_pr_info` — the `gh pr view --json …` payload becomes a PRInfo."""

    def test_reads_every_field(self):
        info = parse_pr_info(
            json.dumps(
                {
                    "number": 7,
                    "title": "A PR",
                    "url": "https://example/pr/7",
                    "state": "OPEN",
                    "mergedAt": None,
                    "headRefName": "feat/x",
                }
            )
        )
        assert info == PRInfo(
            number=7,
            title="A PR",
            url="https://example/pr/7",
            state="OPEN",
            merged=False,
            head_ref="feat/x",
        )

    def test_a_merged_at_timestamp_means_merged(self):
        info = parse_pr_info(
            json.dumps(
                {
                    "number": 7,
                    "title": "A PR",
                    "url": "u",
                    "state": "MERGED",
                    "mergedAt": "2026-01-01T00:00:00Z",
                    "headRefName": "feat/x",
                }
            )
        )
        assert info.merged is True

    def test_an_absent_merged_at_key_means_unmerged(self):
        info = parse_pr_info(
            json.dumps(
                {
                    "number": 7,
                    "title": "A PR",
                    "url": "u",
                    "state": "OPEN",
                    "headRefName": "feat/x",
                }
            )
        )
        assert info.merged is False

    def test_unparseable_output_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_pr_info("not json")


class TestParsePrComments:
    """`parse_pr_comments` — the review GraphQL payload becomes flat comments."""

    @staticmethod
    def _payload(pr):
        return json.dumps({"data": {"repository": {"pullRequest": pr}}})

    def test_an_unresolved_thread_yields_one_comment_per_reply(self):
        comments, _ = parse_pr_comments(
            self._payload(
                {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "T1",
                                "isResolved": False,
                                "path": "src/a.py",
                                "line": 12,
                                "comments": {
                                    "nodes": [
                                        {
                                            "body": "first",
                                            "author": {"login": "ann"},
                                            "createdAt": "2026-01-01T00:00:00Z",
                                        },
                                        {
                                            "body": "second",
                                            "author": {"login": "bob"},
                                            "createdAt": "2026-01-02T00:00:00Z",
                                        },
                                    ]
                                },
                            }
                        ]
                    }
                }
            )
        )
        assert comments == [
            PRComment(
                author="ann",
                body="first",
                created_at="2026-01-01T00:00:00Z",
                kind="thread",
                path="src/a.py",
                line=12,
                thread_id="T1",
            ),
            PRComment(
                author="bob",
                body="second",
                created_at="2026-01-02T00:00:00Z",
                kind="thread",
                path="src/a.py",
                line=12,
                thread_id="T1",
            ),
        ]

    def test_a_resolved_thread_is_dropped(self):
        comments, _ = parse_pr_comments(
            self._payload(
                {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "T1",
                                "isResolved": True,
                                "path": "src/a.py",
                                "line": 1,
                                "comments": {
                                    "nodes": [
                                        {
                                            "body": "done",
                                            "author": {"login": "ann"},
                                            "createdAt": "x",
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            )
        )
        assert comments == []

    def test_a_deleted_author_reads_as_unknown(self):
        comments, _ = parse_pr_comments(
            self._payload(
                {
                    "comments": {
                        "nodes": [
                            {"body": "hi", "author": None, "createdAt": "x"},
                        ]
                    }
                }
            )
        )
        assert comments[0].author == "unknown"

    def test_top_level_comments_are_kind_issue(self):
        comments, _ = parse_pr_comments(
            self._payload(
                {
                    "comments": {
                        "nodes": [
                            {"body": "hi", "author": {"login": "ann"}, "createdAt": "x"}
                        ]
                    }
                }
            )
        )
        assert comments == [
            PRComment(author="ann", body="hi", created_at="x", kind="issue")
        ]

    def test_a_review_with_a_body_is_kind_review(self):
        comments, _ = parse_pr_comments(
            self._payload(
                {
                    "reviews": {
                        "nodes": [
                            {
                                "body": "LGTM",
                                "author": {"login": "ann"},
                                "submittedAt": "x",
                            }
                        ]
                    }
                }
            )
        )
        assert comments == [
            PRComment(author="ann", body="LGTM", created_at="x", kind="review")
        ]

    def test_a_bodiless_review_is_dropped(self):
        """An approve with no words carries nothing worth showing."""
        comments, _ = parse_pr_comments(
            self._payload(
                {
                    "reviews": {
                        "nodes": [
                            {
                                "body": "  ",
                                "author": {"login": "ann"},
                                "submittedAt": "x",
                            },
                            {
                                "body": None,
                                "author": {"login": "bob"},
                                "submittedAt": "x",
                            },
                        ]
                    }
                }
            )
        )
        assert comments == []

    def test_the_last_push_time_comes_from_pushed_date(self):
        _, last_push = parse_pr_comments(
            self._payload(
                {
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "pushedDate": "2026-01-03T00:00:00Z",
                                    "committedDate": "2026-01-01T00:00:00Z",
                                }
                            }
                        ]
                    }
                }
            )
        )
        assert last_push == "2026-01-03T00:00:00Z"

    def test_a_missing_pushed_date_falls_back_to_the_commit_date(self):
        _, last_push = parse_pr_comments(
            self._payload(
                {
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "pushedDate": None,
                                    "committedDate": "2026-01-01T00:00:00Z",
                                }
                            }
                        ]
                    }
                }
            )
        )
        assert last_push == "2026-01-01T00:00:00Z"

    def test_no_commits_leaves_the_push_time_unknown(self):
        assert parse_pr_comments(self._payload({})) == ([], None)

    def test_a_null_pull_request_yields_nothing(self):
        assert parse_pr_comments(self._payload(None)) == ([], None)


class TestParseCheckRuns:
    """`parse_check_runs` — the `gh pr checks --json` array becomes CheckRuns."""

    def test_reads_name_state_and_link(self):
        checks = parse_check_runs(
            json.dumps([{"name": "lint", "state": "SUCCESS", "link": "https://x"}])
        )
        assert checks == [
            CheckRun(name="lint", state="SUCCESS", run_id=None, link="https://x")
        ]

    def test_a_run_link_carries_its_run_id(self):
        checks = parse_check_runs(
            json.dumps(
                [
                    {
                        "name": "test",
                        "state": "FAILURE",
                        "link": "https://github.com/o/r/actions/runs/12345678/job/9",
                    }
                ]
            )
        )
        assert checks[0].run_id == "12345678"

    def test_missing_fields_read_as_empty(self):
        assert parse_check_runs(json.dumps([{}])) == [
            CheckRun(name="", state="", run_id=None, link="")
        ]


class TestRunIdFromLink:
    """`run_id_from_link` — the Actions run id embedded in a check link."""

    def test_a_job_link_yields_the_run_id(self):
        assert (
            run_id_from_link("https://github.com/o/r/actions/runs/12345678/job/99")
            == "12345678"
        )

    def test_a_bare_run_link_yields_the_run_id(self):
        assert run_id_from_link("https://github.com/o/r/actions/runs/42") == "42"

    def test_a_link_with_no_run_yields_nothing(self):
        assert run_id_from_link("https://example.com/status") is None

    def test_an_empty_link_yields_nothing(self):
        assert run_id_from_link("") is None


class TestParseArtifacts:
    """`parse_artifacts` — the artifacts API array becomes Artifacts."""

    def test_reads_name_and_size(self):
        assert parse_artifacts(
            json.dumps([{"name": "traces", "size_in_bytes": 2048}])
        ) == [Artifact(name="traces", size=2048)]

    def test_missing_fields_read_as_empty(self):
        assert parse_artifacts(json.dumps([{}])) == [Artifact(name="", size=0)]

    def test_no_artifacts_is_an_empty_list(self):
        assert parse_artifacts("[]") == []


class TestParseOpenPrsPage:
    """`parse_open_prs_page` — one page of the open-PR GraphQL query."""

    @staticmethod
    def _payload(nodes, has_next=False, cursor=None):
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequests": {
                            "nodes": nodes,
                            "pageInfo": {
                                "hasNextPage": has_next,
                                "endCursor": cursor,
                            },
                        }
                    }
                }
            }
        )

    def test_maps_each_node_by_branch(self):
        page = parse_open_prs_page(
            self._payload(
                [
                    {
                        "number": 7,
                        "headRefName": "feat/a",
                        "commits": {"totalCount": 3},
                    }
                ]
            )
        )
        assert page == OpenPrsPage(prs={"feat/a": (7, 3)}, has_next=False, cursor=None)

    def test_a_page_with_more_carries_its_cursor(self):
        page = parse_open_prs_page(self._payload([], has_next=True, cursor="C1"))
        assert (page.has_next, page.cursor) == (True, "C1")

    def test_an_empty_repo_maps_to_nothing(self):
        assert parse_open_prs_page(self._payload([])).prs == {}

    def test_a_graphql_error_payload_raises(self):
        """gh exits 0 on a rate limit, so the payload is the only signal."""
        payload = json.dumps({"errors": [{"message": "rate limited"}], "data": None})
        with pytest.raises(ValueError):
            parse_open_prs_page(payload)

    def test_unparseable_output_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_open_prs_page("not json")


class TestGitHubErrors:
    """The typed errors the transport layer raises, and what they render as."""

    def test_every_github_error_is_a_runtime_error(self):
        """`cli.py` still has `except RuntimeError` sites this must not break."""
        for err in (
            GitHubCliMissing("gh"),
            NoPullRequest(),
            NoChecksFound(),
            GitHubCommandFailed("get PR info", "boom"),
            PullRequestNotMergeable("PR #7 was closed without merging"),
        ):
            assert isinstance(err, GitHubError)
            assert isinstance(err, RuntimeError)

    def test_a_missing_gh_names_gh(self):
        assert str(GitHubCliMissing("gh")) == "GitHub CLI (gh) is not installed"

    def test_a_missing_git_names_git(self):
        assert str(GitHubCliMissing("git")) == "git is not installed"

    def test_no_pull_request_reads_the_same_for_every_site(self):
        assert str(NoPullRequest()) == "No pull request found for current branch"

    def test_a_failed_command_names_the_action_and_the_stderr(self):
        err = GitHubCommandFailed("get PR info", "gh: not authenticated")
        assert str(err) == "Failed to get PR info: gh: not authenticated"

    def test_a_failed_command_keeps_its_parts(self):
        err = GitHubCommandFailed("push branch", "rejected")
        assert (err.action, err.stderr) == ("push branch", "rejected")

    def test_a_failed_command_survives_a_round_trip(self):
        """An explicit __init__ must still leave `args` picklable."""
        err = GitHubCommandFailed("push branch", "rejected")
        assert str(copy.copy(err)) == str(err)

    def test_no_checks_found_reads_as_it_always_did(self):
        """Nothing errored — gh answered with an empty list, so this is not a
        command failure and must not render as one."""
        assert str(NoChecksFound()) == "No checks found for this PR"

    def test_a_not_mergeable_pr_carries_its_own_words(self):
        err = PullRequestNotMergeable("PR #7 has failing checks: test")
        assert str(err) == "PR #7 has failing checks: test"


class TestIsMissingPrError:
    """`is_missing_pr_error` — gh's way of saying the branch has no PR."""

    def test_ghs_no_pull_requests_message_is_a_missing_pr(self):
        assert is_missing_pr_error("no pull requests found for branch feat/x")

    def test_the_match_ignores_case(self):
        assert is_missing_pr_error("No pull requests found")

    def test_any_other_failure_is_not_a_missing_pr(self):
        assert not is_missing_pr_error("gh: not authenticated")

    def test_empty_stderr_is_not_a_missing_pr(self):
        assert not is_missing_pr_error("")


class TestTheModelStaysPure:
    """`github_model` is a leaf: no transport, no printing, no Click.

    This is convention 2 of ``docs/dev/architecture-patterns.md`` made
    executable. It is what stops the next person putting a subprocess call back
    into the model, which would take the parsers out of reach of a plain unit
    test again.
    """

    # Both spellings: this package imports its siblings relatively, so the AST
    # yields ".shell", never "maelstrom.shell".
    BANNED = {"subprocess", "click", ".shell", "maelstrom.shell"}

    @staticmethod
    def _imported_modules(path):
        tree = ast.parse(pathlib.Path(path).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                prefix = "." * node.level
                names.add(f"{prefix}{node.module}")
                names.update(f"{prefix}{node.module}.{a.name}" for a in node.names)
        return names

    def test_it_imports_no_transport(self):
        imported = self._imported_modules(github_model.__file__)
        assert self.BANNED.isdisjoint(imported), imported & self.BANNED

    def test_its_one_maelstrom_import_is_another_leaf(self):
        """`worktree_model` is stdlib-only, so this arrow cannot cycle."""
        imported = self._imported_modules(github_model.__file__)
        siblings = {n for n in imported if n.startswith(".") or "worktree" in n}
        assert siblings == {".worktree_model", ".worktree_model.MAIN_BRANCH"}
        assert self.BANNED.isdisjoint(self._imported_modules(worktree_model.__file__))


class TestSyncFailed:
    """`SyncFailed` — a rebase that could not finish, raised by `create_pr`."""

    def test_it_is_a_runtime_error(self):
        """`cli.py` still has `except RuntimeError` sites this must not break."""
        assert isinstance(SyncFailed("conflicts"), RuntimeError)

    def test_it_is_not_a_github_error(self):
        """The rebase is local git work; calling it a GitHub failure misnames it."""
        assert not isinstance(SyncFailed("conflicts"), GitHubError)

    def test_it_carries_its_own_words(self):
        assert str(SyncFailed("Sync failed: detached HEAD")) == (
            "Sync failed: detached HEAD"
        )
