"""Tests for ``mael comms`` — the landing of each tracked task, read from the state db."""

import asyncio
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain import task as model
from mael_domain.context import GlobalConfig
from mael_domain.landing_store import (
    EnvLanding,
    PullRequest,
    SqlitePullRequestStore,
    SqliteTaskStepStore,
    StepEvent,
)
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path
from mael_domain.task_table import SqliteTaskTable

ONE = "2026-10-07T01:00:00+00:00"
TWO = "2026-10-07T02:00:00+00:00"


async def _seed() -> None:
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        await db.migrate()
        tasks = SqliteTaskTable(db)
        # t1 and t2 have a Registered PR; t3 has none, so it is not tracked.
        for task_id, status, pr in [
            ("t1", "done", 7),
            ("t2", "todo", 8),
            ("t3", "done", 0),
        ]:
            await model.create(
                tasks, project="p", title=task_id, id=task_id, status=status
            )
            if pr:
                await model.register_pr(tasks, "p", task_id, pr, f"u/{pr}")
        await SqlitePullRequestStore(db).write(
            PullRequest(
                project="p",
                number=7,
                merged_at="T0",
                merge_sha="m7",
                envs={
                    "uat": EnvLanding("landed", "d1", "T3"),
                    "live": EnvLanding("unknown"),
                },
                fetched_at="T1",
            )
        )
        steps = SqliteTaskStepStore(db)
        await steps.add(StepEvent("p", "t1", "done", 7, at="T0", recorded_at=ONE))
        await steps.add(StepEvent("p", "t1", "merged", 7, at="T0", recorded_at=TWO))
    finally:
        db.close()


@pytest.fixture
def invoke(tmp_path):
    asyncio.run(_seed())
    project = tmp_path / "projects" / "p"
    (project / "_main").mkdir(parents=True)
    (project / ".mael").touch()
    main = project / "_main"
    (main / ".maelstrom.yaml").write_text(
        "deploy:\n  environments:\n    uat: uat\n    live: production\n"
    )

    def run(*args: str):
        with (
            patch(
                "mael_cli.comms_cli.load_global_config",
                return_value=GlobalConfig(projects_dir=tmp_path / "projects"),
            ),
        ):
            return CliRunner().invoke(cli, ["comms", "list", *args])

    return run


def _rows(output: str) -> list[list[str]]:
    """The table's cells, less its rule."""
    return [line.split() for line in output.strip().splitlines() if line.strip("-")]


def test_list_prints_each_tracked_task_s_status_and_envs(invoke):
    result = invoke()
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == [
        ["PROJECT", "TASK", "PR", "STATUS", "UAT", "LIVE"],
        ["p", "t1", "#7", "uat", "landed", "unknown"],
        ["p", "t2", "#8", "-", "-", "-"],
    ]


def test_since_lists_the_steps_recorded_after_it(invoke):
    """``Z`` and ``+00:00`` name the same instant, so the step at ONE is not after it."""
    result = invoke("--since", "2026-10-07T01:00:00Z")
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == [
        ["RECORDED", "PROJECT", "TASK", "PR", "STEP", "AT"],
        [TWO, "p", "t1", "#7", "merged", "T0"],
    ]


def test_a_since_that_is_not_a_time_is_refused(invoke):
    result = invoke("--since", "yesterday")
    assert result.exit_code == 2
    assert "not an ISO time" in result.output
