"""Tests for mael_domain.landing_github — the GitHub reads behind a landing."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mael_domain.github_model import RateLimited
from mael_domain.landing import Deploy, Merge
from mael_domain.landing_github import GhLandingSignals, parse_compare


def _graphql(**prs):
    """A ``gh api graphql`` answer: one alias per pull request, ``None`` for a miss."""
    return json.dumps({"data": {"repository": prs}})


def _pr(merged_at=None, oid=None, state="OPEN"):
    return {
        "url": "https://github.com/o/r/pull/7",
        "title": "Add x",
        "state": state,
        "mergedAt": merged_at,
        "mergeCommit": {"oid": oid} if oid else None,
    }


def _answer(returncode, stdout):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


async def _merges(*answers, numbers=(7,)):
    """What the merge read gives for ``numbers`` when gh answers ``answers``."""
    signals = GhLandingSignals(Path("/projects"))
    with patch("mael_domain.github.run_cmd_async", side_effect=answers):
        return await signals.merges("p", list(numbers))


class TestMerges:
    async def test_a_merged_pr_carries_its_merge_commit(self):
        payload = _graphql(p7=_pr("2026-10-01T00:00:00Z", "m7", "MERGED"))
        assert await _merges(_answer(0, payload)) == {
            7: Merge(
                url="https://github.com/o/r/pull/7",
                title="Add x",
                merged_at="2026-10-01T00:00:00Z",
                merge_sha="m7",
                state="MERGED",
            )
        }

    async def test_each_number_is_answered_under_its_own_alias(self):
        payload = _graphql(p7=_pr(), p12=_pr("T", "m12", "MERGED"))
        merges = await _merges(_answer(0, payload), numbers=(7, 12))
        assert {n: m.merge_sha for n, m in merges.items()} == {7: "", 12: "m12"}

    async def test_a_pr_github_cannot_find_is_left_out(self):
        assert await _merges(_answer(0, _graphql(p7=None))) == {}

    async def test_a_refused_field_beside_data_still_answers(self):
        """gh exits 1 when one field is refused; the payload decides."""
        payload = json.dumps(
            {"data": {"repository": {"p7": _pr()}}, "errors": [{"path": ["x"]}]}
        )
        assert set(await _merges(_answer(1, payload))) == {7}

    async def test_a_spent_budget_raises(self):
        payload = json.dumps({"errors": [{"type": "RATE_LIMIT"}]})
        with pytest.raises(RateLimited):
            await _merges(_answer(0, payload))

    async def test_any_other_failure_is_unknown(self):
        payload = json.dumps({"errors": [{"type": "FORBIDDEN"}]})
        assert await _merges(_answer(1, payload)) == {}


class TestCompare:
    @pytest.mark.parametrize(
        ("status", "landed"),
        [("ahead", True), ("identical", True), ("behind", False), ("diverged", False)],
    )
    def test_the_compare_status_says_whether_the_deploy_holds_the_merge(
        self, status, landed
    ):
        assert parse_compare(json.dumps({"status": status})) is landed

    @pytest.mark.parametrize("payload", ['{"message": "Not Found"}', "not json"])
    def test_any_other_answer_is_unknown(self, payload):
        assert parse_compare(payload) is None


class TestTransport:
    async def test_a_refused_deployments_read_is_unknown(self):
        refused = _answer(1, '{"message": "Resource not accessible", "status": "403"}')
        signals = GhLandingSignals(Path("/projects"))
        with patch("mael_domain.github.run_cmd_async", return_value=refused) as run:
            assert await signals.deploy("p", "uat") is None
        assert run.call_args.kwargs["cwd"] == Path("/projects/p/_main")

    async def test_the_newest_successful_deploy_is_the_one_read(self):
        answers = [
            _answer(0, json.dumps([{"id": 3, "sha": "d3"}, {"id": 2, "sha": "d2"}])),
            _answer(0, json.dumps([{"state": "in_progress", "created_at": "T3"}])),
            _answer(0, json.dumps([{"state": "success", "created_at": "T2"}])),
        ]
        signals = GhLandingSignals(Path("/projects"))
        with patch("mael_domain.github.run_cmd_async", side_effect=answers):
            assert await signals.deploy("p", "uat") == Deploy(sha="d2", created_at="T2")
