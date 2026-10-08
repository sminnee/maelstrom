"""Tests for ``mael comms``: comms an agent changes, and each tracked task's landing."""

import asyncio
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from mael_cli.cli import cli
from mael_domain import task as model
from mael_domain.comm_store import Comm, SqliteCommStore
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
            return CliRunner().invoke(cli, ["comms", *args])

    return run


def _rows(output: str) -> list[list[str]]:
    """The table's cells, less its rule."""
    return [line.split() for line in output.strip().splitlines() if line.strip("-")]


def test_landings_prints_each_tracked_task_s_status_and_envs(invoke):
    result = invoke("landings")
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == [
        ["PROJECT", "TASK", "PR", "STATUS", "UAT", "LIVE"],
        ["p", "t1", "#7", "uat", "landed", "unknown"],
        ["p", "t2", "#8", "-", "-", "-"],
    ]


def test_since_lists_the_steps_recorded_after_it(invoke):
    """``Z`` and ``+00:00`` name the same instant, so the step at ONE is not after it."""
    result = invoke("landings", "--since", "2026-10-07T01:00:00Z")
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == [
        ["RECORDED", "PROJECT", "TASK", "PR", "STEP", "AT"],
        [TWO, "p", "t1", "#7", "merged", "T0"],
    ]


def test_a_since_that_is_not_a_time_is_refused(invoke):
    result = invoke("landings", "--since", "yesterday")
    assert result.exit_code == 2
    assert "not an ISO time" in result.output


# --- the comm verbs ---


def _comm(comm_id: str) -> Comm:
    async def read() -> Comm:
        db = open_state_db(get_state_db_path())
        try:
            comm = await SqliteCommStore(db).read(comm_id)
            assert comm is not None
            return comm
        finally:
            db.close()

    return asyncio.run(read())


def _task_comms(task_id: str) -> list[str]:
    async def read() -> list[str]:
        db = open_state_db(get_state_db_path())
        try:
            return (await model.load(SqliteTaskTable(db), "p", task_id)).comms
        finally:
            db.close()

    return asyncio.run(read())


def test_new_prints_the_id_and_list_shows_the_open_comm(invoke):
    made = invoke("new", "Invoice export", "--to", "#cs", "--to", "jo@acme.test")
    assert made.exit_code == 0, made.output
    assert made.output.strip() == "c1"
    invoke("link", "c1", "t1", "t2", "--project", "p")
    result = invoke("list")
    assert result.exit_code == 0, result.output
    assert _rows(result.output) == [
        ["ID", "TITLE", "RECIPIENTS", "TASKS"],
        ["c1", "Invoice", "export", "#cs,", "jo@acme.test", "2"],
    ]


def test_close_hides_a_comm_unless_all_is_asked(invoke):
    invoke("new", "One")
    invoke("new", "Two")
    assert invoke("close", "c1").exit_code == 0
    assert [r[0] for r in _rows(invoke("list").output)] == ["ID", "c2"]
    assert [r[0] for r in _rows(invoke("list", "--all").output)] == ["ID", "c1", "c2"]
    invoke("close", "c2")
    assert invoke("list").output.strip() == "No open comms."


def test_link_and_unlink_edit_the_task_comms(invoke):
    invoke("new", "One")
    result = invoke("link", "c1", "t1", "--project", "p")
    assert result.exit_code == 0, result.output
    assert _task_comms("t1") == ["c1"]
    invoke("link", "c1", "t1", "--unlink", "--project", "p")
    assert _task_comms("t1") == []


def test_link_refuses_an_unknown_comm_or_task(invoke):
    invoke("new", "One")
    unknown = invoke("link", "c9", "t1", "--project", "p")
    assert unknown.exit_code != 0
    assert "Comm not found: c9" in unknown.output
    bad = invoke("link", "c1", "t1", "nope", "--project", "p")
    assert bad.exit_code != 0
    assert _task_comms("t1") == []


def test_new_refuses_a_blank_title(invoke):
    result = invoke("new", " ")
    assert result.exit_code != 0
    assert "A comm needs a title" in result.output


def test_edit_sets_each_given_field(invoke):
    invoke("new", "Old", "--to", "#cs")
    invoke("edit", "c1", "--title", "New", "--content", "Body", "--to", "#ops")
    assert _comm("c1") == Comm(
        id="c1",
        title="New",
        content="Body",
        recipients=["#ops"],
        created_at=_comm("c1").created_at,
    )
    invoke("edit", "c1", "--clear-to")
    assert _comm("c1").recipients == []


def test_edit_with_no_option_saves_what_the_editor_returns(invoke):
    invoke("new", "T", "--content", "before")
    seen = []

    def fake_edit(text):
        seen.append(text)
        return "after"

    with patch("mael_cli.comms_cli.click.edit", side_effect=fake_edit):
        result = invoke("edit", "c1")
    assert result.exit_code == 0, result.output
    assert seen == ["before"]
    assert _comm("c1").content == "after"


def test_an_editor_closed_without_saving_changes_nothing(invoke):
    invoke("new", "T", "--content", "before")
    # click.edit returns None when the editor closes without a save.
    with patch("mael_cli.comms_cli.click.edit", return_value=None):
        result = invoke("edit", "c1")
    assert result.exit_code == 0, result.output
    assert _comm("c1").content == "before"
