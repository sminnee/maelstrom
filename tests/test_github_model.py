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
    PRComment,
    PRInfo,
    PrStatus,
    PullRequestNotMergeable,
    RateLimited,
    SyncFailed,
    is_missing_pr_error,
    parse_artifacts,
    parse_check_runs,
    parse_open_prs,
    parse_pr_comments,
    parse_pr_info,
    parse_run_states,
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


class TestParseRunStates:
    """`parse_run_states` — the Actions answer, when the check rollup is refused.

    The fine-grained PAT permission the rollup needs, `checks=read`, cannot be
    granted: GitHub no longer lists it. `Actions` can, and answers the same
    question, so a refused rollup falls back to the workflow runs.
    """

    @staticmethod
    def _payload(*runs):
        """The `.workflow_runs` array, newest first as the API returns it."""
        return json.dumps(
            {
                "workflow_runs": [
                    {
                        "head_sha": run.get("sha", "abc123"),
                        "status": run.get("status", "completed"),
                        "conclusion": run.get("conclusion"),
                    }
                    for run in runs
                ]
            }
        )

    def test_a_green_run_reads_ready(self):
        payload = self._payload({"conclusion": "success"})
        assert parse_run_states(payload)["abc123"] == "ready"

    def test_a_failed_run_reads_ci_failed(self):
        payload = self._payload({"conclusion": "failure"})
        assert parse_run_states(payload)["abc123"] == "ci-failed"

    def test_a_timed_out_run_is_a_failure(self):
        """A job killed by the clock is a red build, not an absent one."""
        payload = self._payload({"conclusion": "timed_out"})
        assert parse_run_states(payload)["abc123"] == "ci-failed"

    @pytest.mark.parametrize("status", ["in_progress", "queued"])
    def test_a_running_job_reads_ci_running(self, status):
        payload = self._payload({"status": status, "conclusion": None})
        assert parse_run_states(payload)["abc123"] == "ci-running"

    def test_a_running_job_outranks_a_sibling_that_passed(self):
        """One workflow finishing does not make the commit green while another
        is still going. The commit is only as done as its slowest job."""
        payload = self._payload(
            {"status": "in_progress", "conclusion": None},
            {"conclusion": "success"},
        )
        assert parse_run_states(payload)["abc123"] == "ci-running"

    def test_a_failure_outranks_a_run_still_going(self):
        """A red build is the thing to answer, as it is for the rollup — see
        the state order in CONTEXT.md."""
        payload = self._payload(
            {"conclusion": "failure"},
            {"status": "in_progress", "conclusion": None},
        )
        assert parse_run_states(payload)["abc123"] == "ci-failed"

    @pytest.mark.parametrize(
        "conclusion", ["cancelled", "skipped", "neutral", "action_required", "stale"]
    )
    def test_a_run_that_judged_nothing_answers_nothing(self, conclusion):
        """None of these says the commit is good, so none may read as green.

        A cancelled run is the common one: a push superseding the last, or a
        concurrency group, and 39 of 100 runs on one repo here ended this way.
        Calling those `ready` would put a green tick on a commit nothing
        finished checking — the false green the head-commit match exists to
        prevent, arriving by another door.
        """
        payload = self._payload({"conclusion": conclusion})
        assert parse_run_states(payload) == {}

    def test_a_run_that_judged_nothing_yields_to_one_that_did(self):
        """Absent, not green — so a sibling run that reached a verdict still
        answers for the commit."""
        payload = self._payload(
            {"conclusion": "cancelled"},
            {"conclusion": "success"},
        )
        assert parse_run_states(payload)["abc123"] == "ready"

    def test_each_commit_keeps_its_own_state(self):
        """One page covers many branches, so the runs must not pool: a commit
        answers from its own runs or from none."""
        payload = self._payload(
            {"sha": "aaa", "conclusion": "failure"},
            {"sha": "bbb", "conclusion": "success"},
        )
        assert parse_run_states(payload) == {"aaa": "ci-failed", "bbb": "ready"}

    def test_a_commit_with_no_run_is_absent(self):
        """Absent, not green. The caller must tell "no run yet" from "passed",
        or a PR pushed seconds ago reads as ready before anything has run."""
        assert parse_run_states(self._payload()) == {}

    def test_an_unreadable_payload_answers_nothing(self):
        """A refused or malformed Actions read leaves the caller to say it
        could not find out, rather than inventing a state."""
        assert parse_run_states("") == {}
        assert parse_run_states('{"message": "Not Found"}') == {}


class TestParseOpenPrs:
    """`parse_open_prs` — the open-PR query's answer, one connection per branch."""

    @staticmethod
    def _node(**over):
        """A GraphQL PR node: open, mergeable, checks green. Override per case."""
        node = {
            "number": 7,
            "headRefName": "feat/a",
            "url": "https://github.com/acme/repo/pull/7",
            "isDraft": False,
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "commits": {
                "totalCount": 3,
                "nodes": [
                    {
                        "commit": {
                            "oid": "deadbee",
                            "statusCheckRollup": {"state": "SUCCESS"},
                        }
                    }
                ],
            },
        }
        if "rollup" in over:
            rollup = over.pop("rollup")
            node["commits"]["nodes"] = [
                {"commit": {"oid": "deadbee", "statusCheckRollup": rollup}}
            ]
        node.update(over)
        return node

    @staticmethod
    def _payload(*per_branch):
        """One aliased connection per branch, in the order they were asked."""
        return json.dumps(
            {
                "data": {
                    "repository": {
                        f"b{i}": {"nodes": nodes} for i, nodes in enumerate(per_branch)
                    }
                }
            }
        )

    def _one(self, **over):
        """The `PrStatus` for a single branch carrying a single PR."""
        return parse_open_prs(self._payload([self._node(**over)]), {"b0": "feat/a"})[
            "feat/a"
        ]

    def test_maps_each_answer_to_the_branch_that_was_asked(self):
        payload = self._payload([self._node()])
        assert parse_open_prs(payload, {"b0": "feat/a"}) == {
            "feat/a": PrStatus(
                number=7,
                commits=3,
                url="https://github.com/acme/repo/pull/7",
                state="ready",
                is_draft=False,
            )
        }

    def test_the_branch_is_the_one_asked_about_not_the_one_echoed(self):
        """The alias is positional. A `headRefName` that disagrees — a rename
        mid-poll — must not key the answer under a branch nobody asked for."""
        payload = self._payload([self._node(headRefName="stale/name")])
        assert list(parse_open_prs(payload, {"b0": "feat/a"})) == ["feat/a"]

    def test_a_branch_with_no_pr_is_absent(self):
        assert parse_open_prs(self._payload([]), {"b0": "feat/a"}) == {}

    def test_each_branch_reads_its_own_connection(self):
        payload = self._payload([self._node(number=1)], [], [self._node(number=3)])
        prs = parse_open_prs(payload, {"b0": "feat/a", "b1": "feat/b", "b2": "feat/c"})
        assert {b: s.number for b, s in prs.items()} == {"feat/a": 1, "feat/c": 3}

    def test_a_draft_pr_says_so(self):
        assert self._one(isDraft=True).is_draft is True

    @pytest.mark.parametrize(
        ("node", "expected"),
        [
            pytest.param({"state": "MERGED"}, "merged", id="merged"),
            pytest.param({"rollup": {"state": "PENDING"}}, "ci-running", id="pending"),
            pytest.param(
                {"rollup": {"state": "EXPECTED"}}, "ci-running", id="expected"
            ),
            pytest.param({"rollup": {"state": "FAILURE"}}, "ci-failed", id="failure"),
            pytest.param({"rollup": {"state": "ERROR"}}, "ci-failed", id="error"),
            pytest.param({"mergeable": "CONFLICTING"}, "conflict", id="conflicting"),
            pytest.param({}, "ready", id="mergeable-and-green"),
            pytest.param(
                {"mergeable": "UNKNOWN"}, "unknown", id="mergeability-pending"
            ),
            pytest.param({"rollup": None}, "ready", id="a-repo-with-no-ci"),
        ],
    )
    def test_the_state_reads_the_pr(self, node, expected):
        """One state per PR, from the merge, the checks and the mergeability."""
        assert self._one(**node).state == expected

    def test_a_merged_pr_reads_merged_however_its_last_ci_run_went(self):
        assert self._one(state="MERGED", rollup={"state": "FAILURE"}).state == "merged"

    def test_a_merged_pr_reads_merged_though_github_forgets_its_mergeability(self):
        """GitHub answers `UNKNOWN` for a PR that is already in. Reading that
        first would turn every merged PR into an `unknown`."""
        assert self._one(state="MERGED", mergeable="UNKNOWN").state == "merged"

    def test_a_failed_check_beats_a_conflict(self):
        """CI first, then conflicts: a red build is the thing to answer."""
        assert self._one(
            mergeable="CONFLICTING", rollup={"state": "FAILURE"}
        ).state == ("ci-failed")

    def test_a_conflict_outranks_unknown_mergeability(self):
        """`UNKNOWN` only reads as unknown; a definite `CONFLICTING` is definite."""
        assert self._one(mergeable="CONFLICTING").state == "conflict"

    def test_an_open_pr_beats_a_merged_one_on_the_same_branch(self):
        """A recycled branch is normal here: `create-pr` opens a new PR on a
        branch whose last PR merged. The open one is the work in hand."""
        nodes = [
            self._node(number=9, state="MERGED"),
            self._node(number=3, state="OPEN"),
        ]
        prs = parse_open_prs(self._payload(nodes), {"b0": "feat/a"})
        assert prs["feat/a"].number == 3

    def test_the_newest_merged_pr_wins_when_none_is_open(self):
        """The query orders newest first, so the head of the list is the last word."""
        nodes = [
            self._node(number=9, state="MERGED"),
            self._node(number=3, state="MERGED"),
        ]
        prs = parse_open_prs(self._payload(nodes), {"b0": "feat/a"})
        assert prs["feat/a"].number == 9

    def test_a_graphql_error_payload_raises(self):
        """gh exits 0 on a rate limit, so the payload is the only signal."""
        payload = json.dumps({"errors": [{"message": "rate limited"}], "data": None})
        with pytest.raises(ValueError):
            parse_open_prs(payload, {"b0": "feat/a"})

    def test_a_rate_limit_is_told_apart_from_other_failures(self):
        """A rate limit must not be answered by falling back per branch: that
        turns one refused call into one call per worktree. The payload is the
        only signal, so the error type has to carry it.

        The payload is the one GitHub really sent, not a hand-written shape.
        """
        payload = json.dumps(
            {
                "errors": [
                    {
                        "type": "RATE_LIMIT",
                        "code": "graphql_rate_limit",
                        "message": "API rate limit already exceeded for user ID 59968.",
                    }
                ],
                "data": None,
            }
        )
        with pytest.raises(RateLimited):
            parse_open_prs(payload, {"b0": "feat/a"})

    @pytest.mark.parametrize(
        "error",
        [
            {"type": "RATE_LIMIT"},
            {"code": "graphql_rate_limit"},
        ],
        ids=["type-only", "code-only"],
    )
    def test_either_rate_limit_marker_is_enough(self, error):
        """The real payload carries both markers, so one test covering it cannot
        say whether either alone is read. GitHub is not obliged to send both."""
        payload = json.dumps({"errors": [error], "data": None})
        with pytest.raises(RateLimited):
            parse_open_prs(payload, {"b0": "feat/a"})

    def test_a_non_rate_limit_failure_is_not_a_rate_limit(self):
        """A missing scope still falls back per branch, so it must stay an
        ordinary failure."""
        payload = json.dumps(
            {"errors": [{"message": "Could not resolve to a Repository"}], "data": None}
        )
        with pytest.raises(ValueError) as caught:
            parse_open_prs(payload, {"b0": "feat/a"})
        assert not isinstance(caught.value, RateLimited)

    def test_a_denied_rollup_keeps_the_rest_of_the_answer(self):
        """A token without the checks scope is refused `statusCheckRollup` per
        node, and GitHub answers the rest. Losing the whole read over that would
        drop the number, the URL and the merge state, which the token may read.
        """
        payload = json.dumps(
            {
                "data": {"repository": {"b0": {"nodes": [self._node(rollup=None)]}}},
                "errors": [
                    {
                        "type": "FORBIDDEN",
                        "path": ["repository", "b0", "nodes", 0, "commits"],
                        "message": "Resource not accessible by personal access token",
                    }
                ],
            }
        )
        prs = parse_open_prs(payload, {"b0": "feat/a"})
        assert prs["feat/a"].number == 7

    def test_a_denied_rollup_is_not_a_false_ready(self):
        """A refused rollup and a repo with no CI both answer `null`. Reading
        the refusal as `ready` would call a red PR ready to merge, which is the
        worst way to be wrong. The errors array is what tells them apart.

        With nothing to fall back on, the refusal reads as `checks-unreadable`
        rather than `unknown`: the token will not gain the permission by
        waiting, so a reader must not be left watching a spinner for ever.
        """
        payload = json.dumps(
            {
                "data": {"repository": {"b0": {"nodes": [self._node(rollup=None)]}}},
                "errors": [
                    {
                        "type": "FORBIDDEN",
                        "path": ["repository", "b0", "nodes", 0, "commits"],
                        "message": "Resource not accessible by personal access token",
                    }
                ],
            }
        )
        state = parse_open_prs(payload, {"b0": "feat/a"})["feat/a"].state
        assert state == "checks-unreadable"

    def test_a_denied_rollup_reads_the_state_actions_gives_instead(self):
        """`checks=read` cannot be granted, but `Actions` can, and it answers
        the same question. A run against this PR's head commit is the state."""
        payload = json.dumps(
            {
                "data": {"repository": {"b0": {"nodes": [self._node(rollup=None)]}}},
                "errors": [
                    {
                        "type": "FORBIDDEN",
                        "path": ["repository", "b0", "nodes", 0, "commits"],
                        "message": "Resource not accessible by personal access token",
                    }
                ],
            }
        )
        prs = parse_open_prs(
            payload, {"b0": "feat/a"}, run_states={"deadbee": "ci-failed"}
        )
        assert prs["feat/a"].state == "ci-failed"

    def test_a_run_against_an_older_commit_is_not_this_prs_answer(self):
        """Matched on the head commit, so a run from the push before this one
        says nothing. Reading it would show a green tick for code that has been
        replaced — the stale-green this fallback must never produce."""
        payload = json.dumps(
            {
                "data": {"repository": {"b0": {"nodes": [self._node(rollup=None)]}}},
                "errors": [
                    {
                        "type": "FORBIDDEN",
                        "path": ["repository", "b0", "nodes", 0, "commits"],
                        "message": "Resource not accessible by personal access token",
                    }
                ],
            }
        )
        prs = parse_open_prs(payload, {"b0": "feat/a"}, run_states={"0lder": "ready"})
        assert prs["feat/a"].state == "checks-unreadable"

    def test_a_readable_rollup_ignores_the_actions_answer(self):
        """The rollup is the better source and is asked first. Actions only
        answers where it was refused, so a repo that grants checks never pays
        the extra read nor risks the two disagreeing."""
        prs = parse_open_prs(
            self._payload([self._node(rollup={"state": "SUCCESS"})]),
            {"b0": "feat/a"},
            run_states={"deadbee": "ci-failed"},
        )
        assert prs["feat/a"].state == "ready"

    def test_a_repo_with_no_ci_and_no_errors_is_still_ready(self):
        """No rollup and no refusal means the repo runs no checks. A spinner
        that never stops would be worse than silence."""
        payload = self._payload([self._node(rollup=None)])
        assert parse_open_prs(payload, {"b0": "feat/a"})["feat/a"].state == "ready"

    def test_a_denied_rollup_does_not_mask_a_merged_pr(self):
        """A merged PR is done, whatever the token could not read."""
        payload = json.dumps(
            {
                "data": {
                    "repository": {
                        "b0": {"nodes": [self._node(rollup=None, state="MERGED")]}
                    }
                },
                "errors": [
                    {"message": "Resource not accessible by personal access token"}
                ],
            }
        )
        assert parse_open_prs(payload, {"b0": "feat/a"})["feat/a"].state == "merged"

    def test_an_error_with_no_data_at_all_still_raises(self):
        """A rate limit answers errors and nothing else. That is a failed read,
        and the caller must fall back rather than render every PR as absent."""
        payload = json.dumps(
            {"data": {"repository": None}, "errors": [{"message": "rate limited"}]}
        )
        with pytest.raises(ValueError):
            parse_open_prs(payload, {"b0": "feat/a"})

    def test_unparseable_output_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_open_prs("not json", {"b0": "feat/a"})


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
