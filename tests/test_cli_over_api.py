"""The task commands as a thin client: ``mael task`` against a real orchestrator.

Each case serves a real :func:`~mael_orchestrator.routes.build_app` on loopback,
over an in-memory task table, and points ``orchestrator_url`` at it through a
real ``config.yaml`` in the notebook root. The CLI runs on the test's thread and the
server on its own, as two processes would. So the wire is proven, not a fake.
"""

import asyncio
import json

import pytest
from click.testing import CliRunner
from served_orchestrator import (
    NOW,
    OTHER_PROJECT,
    PROJECT,
    mael_task,
    point_config_at,
    seed,
)

from mael_cli.cli import cli
from mael_domain import task as model
from mael_domain.task_table import InMemoryTaskTable
from mael_orchestrator.sources import InMemoryWorktreeSource
from mael_orchestrator.world_build import project_entity, worktree_entity

pytestmark = pytest.mark.binds_socket


def test_show_prints_the_task_the_server_holds(table, start_server):
    seed(
        table,
        title="Add order export",
        id="NORT-7",
        mode="auto",
        priority="high",
        branch="feat/orders",
        content="Write the CSV.",
    )
    start_server()

    result = mael_task("show", "NORT-7", "--project", PROJECT)

    assert result.exit_code == 0, result.output
    assert result.output == (
        "id:      NORT-7\n"
        "title:   Add order export\n"
        "status:  todo\n"
        "project: northwind\n"
        "command: \n"
        "mode:    auto\n"
        "priority: high\n"
        "branch:  feat/orders\n"
        "created: 2026-09-01T00:00:00Z\n"
        "updated: 2026-09-01T00:00:00Z\n"
        "actionable: yes\n"
        "\n## Content\n\n"
        "Write the CSV.\n"
    )


def test_show_prints_the_fields_only_some_tasks_have(table, start_server):
    seed(table, title="Ship it", id="NORT-1", branch="feat/orders")
    seed(
        table,
        title="Nightly sweep",
        id="NORT-2",
        status=model.STATUS_TEMPLATE,
        mode="auto",
        priority="low",
        branch="feat/orders",
        parent="NORT-1",
        follows=["NORT-1"],
        model="opus",
        execute_model="sonnet",
        comms=["c3"],
        schedule="0 3 * * *",
        trigger="build:main",
        last_run="2026-08-31T03:00:00Z",
    )
    start_server()

    result = mael_task("show", "NORT-2", "--project", PROJECT)

    assert result.exit_code == 0, result.output
    assert result.output == (
        "id:      NORT-2\n"
        "title:   Nightly sweep\n"
        "status:  template\n"
        "project: northwind\n"
        "command: \n"
        "mode:    auto\n"
        "model:   opus\n"
        "execute-model: sonnet\n"
        "priority: low\n"
        "branch:  feat/orders\n"
        "parent:  NORT-1\n"
        "follows: NORT-1\n"
        "schedule: 0 3 * * *\n"
        "trigger: build:main\n"
        "comms: c3\n"
        "last-run: 2026-08-31T03:00:00Z\n"
        "created: 2026-09-01T00:00:00Z\n"
        "updated: 2026-09-01T00:00:00Z\n"
        "actionable: no\n"
    )


def test_read_prints_the_markdown_the_notebook_renders(table, start_server):
    """The same text ``Task.to_markdown`` gives the notebook's own row."""
    seed(table, title="Before it", id="NORT-1", branch="feat/orders")
    seed(
        table,
        title="Add order export",
        id="NORT-7",
        mode="auto",
        branch="feat/orders",
        base="feat/base",
        follows=["NORT-1"],
        schedule="0 3 * * *",
        trigger="build:main",
        last_run="2026-08-31T03:00:00Z",
        pre_action="bin/setup",
        post_action="bin/notify",
        model="opus",
        execute_model="sonnet",
        comms=["c3"],
        content="Write the CSV.",
    )
    asyncio.run(model.register_pr(table, PROJECT, "NORT-7", 118, "https://pr/118"))
    for line in ("Started.", "Half done."):
        asyncio.run(model.append_log(table, PROJECT, "NORT-7", line, now=NOW))
    local = asyncio.run(model.load(table, PROJECT, "NORT-7")).to_markdown()
    start_server()

    result = mael_task("read", "NORT-7", "--project", PROJECT)

    assert result.exit_code == 0, result.output
    assert result.output == local
    assert "pre-action: bin/setup" in local
    assert "follows: [NORT-1]" in local
    assert f"- {NOW} Half done." in local


def listed(result) -> list[list[str]]:
    """The table's rows as cells split on whitespace, with the header first.

    The rule under the header is left out.
    """
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    return [line.split() for line in lines if line.strip().strip("-")]


def seed_list(table: InMemoryTaskTable) -> None:
    """One task in each state ``list`` tells apart, and one in another project.

    ``NORT-3`` is created a day after the others, so it sorts last in its
    priority band: the list sorts by ``created`` before id.
    """
    seed(table, title="Low", id="NORT-1", priority="low", branch="b1")
    seed(table, title="High", id="NORT-2", priority="high", branch="b2")
    seed(
        table,
        title="Waits",
        id="NORT-3",
        follows=["NORT-1"],
        branch="b3",
        now="2026-09-02T00:00:00Z",
    )
    seed(table, title="Done", id="NORT-4", status=model.STATUS_DONE, branch="b4")
    seed(
        table, title="Dropped", id="NORT-5", status=model.STATUS_CANCELLED, branch="b5"
    )
    in_progress = model.STATUS_IN_PROGRESS
    seed(
        table,
        title="Runs",
        id="NORT-6",
        follows=["NORT-4"],
        status=in_progress,
        branch="b6",
    )
    seed(
        table,
        title="Stuck",
        id="NORT-7",
        follows=["NORT-1"],
        status=in_progress,
        branch="b7",
    )
    seed(
        table,
        title="Parked",
        id="NORT-8",
        follows=["NORT-4"],
        status=model.STATUS_BLOCKED,
        branch="b8",
    )
    seed(table, title="Nightly", id="NORT-9", status=model.STATUS_TEMPLATE, branch="b9")
    asyncio.run(
        model.create(
            table, project=OTHER_PROJECT, title="Theirs", id="ACME-1", branch="a1"
        )
    )


def test_list_shows_the_project_s_actionable_tasks_in_pick_order(table, start_server):
    """A task waiting on another, parked, a template or finished is not shown."""
    seed_list(table)
    start_server()

    rows = listed(mael_task("list", "--project", PROJECT))

    assert rows == [
        ["ID", "STATUS", "PRIORITY", "BRANCH", "TITLE"],
        ["NORT-2", "todo", "high", "b2", "High"],
        ["NORT-6", "in-progress", "medium", "b6", "Runs"],
        ["NORT-1", "todo", "low", "b1", "Low"],
    ]


def test_list_all_todo_adds_what_cannot_start_and_the_actionable_column(
    table, start_server
):
    seed_list(table)
    start_server()

    rows = listed(mael_task("list", "--project", PROJECT, "--all-todo"))

    assert [row[:4] for row in rows] == [
        ["ID", "STATUS", "PRIORITY", "ACTIONABLE"],
        ["NORT-2", "todo", "high", "yes"],
        ["NORT-6", "in-progress", "medium", "yes"],
        ["NORT-7", "in-progress", "medium", "no"],
        ["NORT-8", "blocked", "medium", "no"],
        ["NORT-9", "template", "medium", "no"],
        ["NORT-3", "todo", "medium", "no"],
        ["NORT-1", "todo", "low", "yes"],
    ]


def test_list_all_adds_the_finished_tasks(table, start_server):
    seed_list(table)
    start_server()

    rows = listed(mael_task("list", "--project", PROJECT, "--all"))

    assert [row[:4] for row in rows] == [
        ["ID", "STATUS", "PRIORITY", "ACTIONABLE"],
        ["NORT-2", "todo", "high", "yes"],
        ["NORT-4", "done", "medium", "no"],
        ["NORT-5", "cancelled", "medium", "no"],
        ["NORT-6", "in-progress", "medium", "yes"],
        ["NORT-7", "in-progress", "medium", "no"],
        ["NORT-8", "blocked", "medium", "no"],
        ["NORT-9", "template", "medium", "no"],
        ["NORT-3", "todo", "medium", "no"],
        ["NORT-1", "todo", "low", "yes"],
    ]


def test_list_with_no_tasks_says_so(start_server):
    start_server()

    result = mael_task("list", "--project", PROJECT)

    assert result.output == "No tasks.\n"


def test_list_filters_by_status_and_parent(table, start_server):
    seed_list(table)
    seed(table, title="Child", id="NORT-10", parent="NORT-2", branch="b10")
    start_server()

    by_status = listed(
        mael_task("list", "--project", PROJECT, "--all", "--status", "done")
    )
    by_parent = listed(mael_task("list", "--project", PROJECT, "--parent", "NORT-2"))

    assert [row[0] for row in by_status[1:]] == ["NORT-4"]
    assert [row[0] for row in by_parent[1:]] == ["NORT-10"]


def test_list_status_template_shows_the_schedule(table, start_server):
    seed(
        table,
        title="Nightly",
        id="NORT-9",
        status=model.STATUS_TEMPLATE,
        schedule="0 3 * * *",
        branch="b9",
    )
    start_server()

    rows = listed(mael_task("list", "--project", PROJECT, "--status", "template"))

    assert rows[0] == [
        "ID",
        "STATUS",
        "PRIORITY",
        "SCHEDULE",
        "NEXT-FIRE",
        "BRANCH",
        "TITLE",
    ]
    assert rows[1][:3] == ["NORT-9", "template", "medium"]
    assert rows[1][3:8] == ["0", "3", "*", "*", "*"]


def status_of(table: InMemoryTaskTable, id: str) -> str:
    return asyncio.run(model.load(table, PROJECT, id)).status


def test_status_done_moves_the_row_and_names_the_next_follower(table, start_server):
    seed(table, title="First", id="NORT-1", branch="b")
    seed(table, title="Second", id="NORT-2", follows=["NORT-1"], branch="b")
    start_server()

    result = mael_task("status", "done", "NORT-1", "--project", PROJECT)

    assert result.exit_code == 0, result.output
    assert result.output == (
        "NORT-1 -> done\n"
        "\n"
        "mael task next --run will run the following task in a new session:\n"
        "  NORT-2 - Second\n"
    )
    assert status_of(table, "NORT-1") == model.STATUS_DONE


def test_status_done_names_a_follower_already_running(table, start_server):
    seed(table, title="First", id="NORT-1", branch="b")
    seed(
        table,
        title="Second",
        id="NORT-2",
        follows=["NORT-1"],
        status=model.STATUS_IN_PROGRESS,
        branch="b",
    )
    start_server()

    result = mael_task("status", "done", "NORT-1", "--project", PROJECT)

    assert result.output == (
        "NORT-1 -> done\n"
        "\n"
        "The following task is already in-progress:\n"
        "  NORT-2 - Second\n"
    )


@pytest.mark.parametrize(
    ("verb", "status"),
    [
        ("todo", model.STATUS_TODO),
        ("start", model.STATUS_IN_PROGRESS),
        ("done", model.STATUS_DONE),
        ("cancel", model.STATUS_CANCELLED),
        ("block", model.STATUS_BLOCKED),
        ("template", model.STATUS_TEMPLATE),
    ],
)
def test_each_status_verb_moves_the_row_and_says_only_that(
    table, start_server, verb, status
):
    """Only ``done`` names a follower, and here even ``done`` has none."""
    seed(table, title="First", id="NORT-1", branch="b")
    seed(table, title="Second", id="NORT-2", follows=["NORT-1"], branch="b")
    seed(table, title="Alone", id="NORT-3", status=model.STATUS_IN_PROGRESS, branch="c")
    start_server()
    moved = "NORT-3" if verb == "done" else "NORT-1"

    result = mael_task("status", verb, moved, "--project", PROJECT)

    assert result.output == f"{moved} -> {status}\n"
    assert status_of(table, moved) == status


def test_status_start_takes_the_session_s_task(table, start_server, monkeypatch):
    seed(table, title="First", id="NORT-1", branch="b")
    start_server()
    monkeypatch.setenv("MAEL_TASK_ID", "NORT-1")

    result = mael_task("status", "start", "--project", PROJECT)

    assert result.output == "NORT-1 -> in-progress\n"
    assert status_of(table, "NORT-1") == model.STATUS_IN_PROGRESS


@pytest.mark.parametrize("command", [["show"], ["read"], ["status", "done"]])
def test_an_unknown_task_is_refused(start_server, command):
    start_server()

    result = mael_task(*command, "NORT-404", "--project", PROJECT)

    assert result.exit_code == 1
    assert result.output == "Error: Task not found: NORT-404\n"


def test_an_id_that_is_not_one_names_no_other_task(table, start_server):
    """Unquoted, ``NORT-1?x`` would ask for ``NORT-1`` with a query string."""
    seed(table, title="First", id="NORT-1", branch="b")
    start_server()

    result = mael_task("show", "NORT-1?x", "--project", PROJECT)

    assert result.exit_code == 1
    assert result.output == "Error: Task not found: NORT-1?x\n"


def test_status_reports_the_line_its_action_wrote(table, start_server, monkeypatch):
    """The server runs the action; the line it reports still reaches the user."""
    from mael_domain.integrations import linear

    calls = []

    def set_issue_status(issue_id, status):
        calls.append((issue_id, status))
        return f"{issue_id}: Todo -> {status}"

    monkeypatch.setattr(linear, "set_issue_status", set_issue_status)
    seed(
        table,
        title="E",
        id="NORT-1",
        branch="b",
        parent="linear.NORT-12",
        post_action="linear.done",
    )
    start_server()

    result = mael_task("status", "done", "NORT-1", "--project", PROJECT)

    assert calls == [("NORT-12", "done")]
    assert result.stdout == "NORT-1 -> done\n"
    assert "NORT-12: Todo -> done" in result.stderr


def test_a_task_written_to_the_notebook_is_seen_at_once(table, start_server):
    """``mael task add`` still writes the notebook itself. The server's next
    task read must not wait for its poll, or ``add`` then ``status start``
    finds no task."""
    start_server()
    seed(table, title="Just added", id="NORT-1", branch="b")

    shown = mael_task("show", "NORT-1", "--project", PROJECT)
    listed_rows = listed(mael_task("list", "--project", PROJECT))
    seed(table, title="Added next", id="NORT-2", branch="b")
    started = mael_task("status", "start", "NORT-2", "--project", PROJECT)

    assert shown.exit_code == 0, shown.output
    assert [row[0] for row in listed_rows[1:]] == ["NORT-1"]
    assert started.output == "NORT-2 -> in-progress\n"


def test_read_keeps_a_hand_wrapped_log_entry_and_an_unset_branch(table, start_server):
    """What the wire task cannot carry: the log as written, and no branch."""
    task = seed(table, title="Wrapped", id="NORT-7", branch="feat/x")
    task.log = f"- {NOW} A long entry\n  wrapped by hand."
    task.branch = ""
    asyncio.run(table.save(task))
    local = asyncio.run(model.load(table, PROJECT, "NORT-7")).to_markdown()
    start_server()

    result = mael_task("read", "NORT-7", "--project", PROJECT)

    assert result.output == local
    assert "  wrapped by hand." in local


# --- mael list and mael list-all ---------------------------------------------


def a_world() -> InMemoryWorktreeSource:
    """A project with an open worktree whose PR state is stale and a closed
    one, and another project with one open worktree."""
    project = project_entity({"name": PROJECT, "stack_tip": "main"})
    other = project_entity({"name": OTHER_PROJECT})
    alpha = worktree_entity(
        PROJECT,
        {
            "name": "alpha",
            "path": f"/p/{PROJECT}/{PROJECT}-alpha",
            "branch": "feat/orders",
            "base": "main",
            "dirty_files": 2,
            "local_commits": 1,
            "pr_number": 7,
            "pr_commits": 3,
            "pr_state": "ready",
            "pr_stale": True,
            "app_url": "https://dev.example:3010",
            "app_running": True,
            "session_count": 1,
        },
    )
    bravo = worktree_entity(
        PROJECT,
        {"name": "bravo", "path": f"/p/{PROJECT}/{PROJECT}-bravo", "is_closed": True},
    )
    acme = worktree_entity(
        OTHER_PROJECT,
        {"name": "alpha", "path": f"/p/{OTHER_PROJECT}/{OTHER_PROJECT}-alpha"},
    )
    return InMemoryWorktreeSource(
        projects=[project, other], worktrees=[alpha, bravo, acme]
    )


def mael(*args: str):
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


def test_list_all_draws_the_servers_worktrees(start_server):
    start_server(a_world())

    result = mael("list-all")

    assert result.exit_code == 0, result.output
    header, _rule, row, other = [
        line.split() for line in result.output.splitlines()[:4]
    ]
    assert header[0] == "PROJECT"
    assert row == [
        PROJECT,
        f"{PROJECT}-alpha",
        "feat/orders",
        "\u2190",
        "main",
        "2",
        "1",
        "#7",
        "(3)",
        "(stale)",
        "https://dev.example:3010",
        "1",
    ]
    assert other == [OTHER_PROJECT, f"{OTHER_PROJECT}-alpha", "(detached)"]
    assert result.output.endswith(f"Closed environments:\n - {PROJECT}: bravo\n")


def test_list_all_json_prints_the_wire_entities_by_project(start_server):
    world = a_world()
    start_server(world)

    result = mael("--json", "list-all")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "projects": [
            {**world.projects[0], "worktrees": world.worktrees[:2]},
            {**world.projects[1], "worktrees": world.worktrees[2:]},
        ]
    }


def test_list_prints_one_projects_open_rows_then_the_closed_names(start_server):
    start_server(a_world())

    result = mael("list", PROJECT)

    assert result.exit_code == 0, result.output
    assert [line.split() for line in result.output.splitlines()] == [
        ["WORKTREE", "BRANCH", "DIRTY", "FILES", "LOCAL", "COMMITS"]
        + ["PR", "(COMMITS)", "APP", "SESSION"],
        [result.output.splitlines()[1].strip()],
        ["alpha", "feat/orders", "\u2190", "main", "2", "1", "#7", "(3)", "(stale)"]
        + ["https://dev.example:3010", "1"],
        [],
        ["Closed", "environments:", "bravo"],
    ]


def test_list_of_a_project_the_server_does_not_know_says_so(start_server):
    start_server(a_world())

    result = CliRunner().invoke(cli, ["list", "globex"])

    assert result.exit_code == 1
    assert "The orchestrator knows no project 'globex'" in result.output


def test_list_all_with_no_server_says_so(home):
    point_config_at("http://127.0.0.1:9")

    result = CliRunner().invoke(cli, ["list-all"])

    assert result.exit_code == 1
    assert "No orchestrator answered at http://127.0.0.1:9" in result.output
