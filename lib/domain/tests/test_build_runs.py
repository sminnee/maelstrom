"""Tests for mael_domain.build_runs — the finished runs a trigger reads."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mael_domain.build_runs import BuildRun, BuildTrigger, parse_trigger
from mael_domain.build_runs_github import GhBuildRuns

FAILED = frozenset({"failure", "timed_out", "startup_failure"})


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("gh-action/nightly.yml", BuildTrigger("nightly.yml", "main", FAILED)),
        (
            "gh-action/nightly.yml@release",
            BuildTrigger("nightly.yml", "release", FAILED),
        ),
        (
            "gh-action/nightly.yml success",
            BuildTrigger("nightly.yml", "main", frozenset({"success"})),
        ),
        (
            "gh-action/ci.yml@dev failed,cancelled",
            BuildTrigger("ci.yml", "dev", FAILED | {"cancelled"}),
        ),
        ("cron/nightly.yml", None),
        ("nightly.yml", None),
        ("gh-action/", None),
        ("gh-action/nightly.yml@", None),
        ("gh-action/nightly.yml failure extra", None),
        ("gh-action/nightly.yml ,", None),
    ],
)
def test_parse_trigger(value, expected):
    assert parse_trigger(value) == expected


def _run(id, conclusion, updated_at):
    return {
        "id": id,
        "conclusion": conclusion,
        "updated_at": updated_at,
        "html_url": f"https://github.com/o/r/actions/runs/{id}",
        "head_sha": f"sha{id}",
        "status": "completed",
        "name": "Nightly",
    }


def _answer(returncode, stdout):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


async def _completed(answer):
    runs = GhBuildRuns(Path("/projects"))
    trigger = BuildTrigger("nightly.yml", "release", FAILED)
    with patch("mael_domain.github.run_cmd_async", return_value=answer) as gh:
        return await runs.completed("p", trigger), gh


class TestCompleted:
    async def test_runs_come_newest_first_without_unfinished_rows(self):
        payload = json.dumps(
            {
                "total_count": 3,
                "workflow_runs": [
                    _run(1, "success", "2026-10-06T03:00:00Z"),
                    _run(3, None, "2026-10-08T03:00:00Z"),
                    _run(2, "failure", "2026-10-07T03:10:00Z"),
                ],
            }
        )
        runs, _ = await _completed(_answer(0, payload))
        assert runs == [
            BuildRun(
                id=2,
                conclusion="failure",
                completed_at=datetime(2026, 10, 7, 3, 10, tzinfo=timezone.utc),
                url="https://github.com/o/r/actions/runs/2",
                head_sha="sha2",
            ),
            BuildRun(
                id=1,
                conclusion="success",
                completed_at=datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc),
                url="https://github.com/o/r/actions/runs/1",
                head_sha="sha1",
            ),
        ]

    @pytest.mark.parametrize(
        "answer",
        [
            _answer(1, '{"message": "Not Found"}'),
            _answer(0, "not json"),
            _answer(0, json.dumps({"workflow_runs": [{"id": 1, "conclusion": "x"}]})),
        ],
        ids=["refused", "not-json", "no-updated-at"],
    )
    async def test_a_read_that_gives_no_runs_is_unknown(self, answer):
        runs, _ = await _completed(answer)
        assert runs is None

    async def test_the_read_names_the_workflow_branch_and_completed_runs(self):
        _, gh = await _completed(_answer(0, '{"workflow_runs": []}'))
        assert gh.call_args.args[0] == [
            "gh",
            "api",
            "repos/:owner/:repo/actions/workflows/nightly.yml/runs"
            "?branch=release&status=completed&per_page=10",
        ]
        assert gh.call_args.kwargs["cwd"] == Path("/projects/p/_main")
