"""The creating task commands as a thin client: ``add``, ``promote``, ``load-many``.

Each case runs the command against a real orchestrator on loopback, as
``test_cli_over_api.py`` does. The server writes every task, so each one joins
the desk, and the canvas shows a chain an agent creates at once.
"""

import asyncio

import pytest
from click.testing import CliRunner
from served_orchestrator import PROJECT, mael_task, point_config_at, seed

from mael_cli.orchestrator_client import OrchestratorClient
from mael_cli.task_cli import task
from mael_domain import task as model
from mael_orchestrator.sources import InMemoryWorktreeSource
from mael_orchestrator.world_build import project_entity

pytestmark = pytest.mark.binds_socket


def serve(start_server):
    """Start the server, with the project in its world: a create names one."""
    project = project_entity({"name": PROJECT, "stack_tip": "main"})
    return start_server(InMemoryWorktreeSource(projects=[project]))


def stored(table, task_id: str) -> model.Task:
    return asyncio.run(model.load(table, PROJECT, task_id))


def desk_ids(url: str) -> list[str]:
    return [e["id"] for e in OrchestratorClient(url).get("/api/desk")["desk"]]


def on_desk(*task_ids: str) -> list[str]:
    return [f"task:{PROJECT}/{task_id}" for task_id in task_ids]


# --- add ---


def test_add_prints_the_id_and_puts_the_task_on_the_desk(start_server, tmp_path):
    served = serve(start_server)
    brief = tmp_path / "brief.md"
    brief.write_text("Probe the desk.")

    result = mael_task(
        "add",
        "Desk probe",
        "-p",
        PROJECT,
        "-b",
        "feat/probe",
        "--priority",
        "high",
        "--execute-model",
        "sonnet",
        "--content-file",
        str(brief),
    )

    assert result.exit_code == 0, result.output
    task_id = result.output.strip()
    new = stored(served.table, task_id)
    assert (new.title, new.branch, new.priority) == ("Desk probe", "feat/probe", "high")
    assert (new.execute_model, new.content) == ("sonnet", "Probe the desk.")
    assert desk_ids(served.url) == on_desk(task_id)


def test_add_in_a_session_chains_under_its_parent_after_the_last_sibling(
    table, start_server, monkeypatch
):
    seed(table, title="Plan", id="NORT-1", branch="feat/orders")
    seed(table, title="First", id="NORT-1.1", parent="NORT-1", branch="feat/orders")
    served = serve(start_server)
    monkeypatch.setenv("MAEL_TASK_PARENT", "NORT-1")

    result = mael_task(
        "add",
        "Second",
        "-p",
        PROJECT,
        "--follow-end",
        "*",
        "--pre-action",
        "linear.in-progress",
    )

    assert result.exit_code == 0, result.output
    assert result.output == "NORT-1.2\n"
    new = stored(served.table, "NORT-1.2")
    assert (new.parent, new.branch, new.follows, new.pre_action) == (
        "NORT-1",
        "feat/orders",
        ["NORT-1.1"],
        "linear.in-progress",
    )


def test_add_from_copies_the_recipe_and_the_flags_override_it(table, start_server):
    seed(
        table,
        title="Sweep",
        id="NORT-7",
        command="plan-task",
        mode="auto",
        branch="feat/sweep",
        content="The body.",
    )
    served = serve(start_server)

    result = mael_task(
        "add", "-p", PROJECT, "--from", "NORT-7", "-c", "other", "-b", "feat/copy"
    )

    assert result.exit_code == 0, result.output
    copy = stored(served.table, result.output.strip())
    assert (copy.title, copy.command, copy.mode, copy.content, copy.branch) == (
        "Sweep",
        "other",
        "auto",
        "The body.",
        "feat/copy",
    )


def test_add_from_a_task_that_is_not_there_is_refused(start_server):
    serve(start_server)

    result = mael_task("add", "-p", PROJECT, "--from", "NOPE")

    assert result.exit_code == 1
    assert "No task northwind/NOPE" in result.output


def test_add_template_parks_the_task_with_its_schedule(start_server):
    served = serve(start_server)

    result = mael_task(
        "add",
        "Nightly",
        "-p",
        PROJECT,
        "-b",
        "feat/nightly",
        "--template",
        "--schedule",
        "0 3 * * *",
    )

    assert result.exit_code == 0, result.output
    new = stored(served.table, result.output.strip())
    assert (new.status, new.schedule) == (model.STATUS_TEMPLATE, "0 3 * * *")


def test_add_links_the_comm_it_names(start_server):
    served = serve(start_server)
    comm = OrchestratorClient(served.url).post("/api/comms", {"title": "Weekly"})

    result = mael_task(
        "add", "Feeds it", "-p", PROJECT, "-b", "feat/a", "--comm", comm["id"]
    )

    assert result.exit_code == 0, result.output
    assert stored(served.table, result.output.strip()).comms == [comm["id"]]


def test_add_with_a_comm_that_is_not_there_writes_nothing(start_server):
    served = serve(start_server)

    result = mael_task("add", "Feeds none", "-p", PROJECT, "--comm", "nope")

    assert result.exit_code == 1
    assert "No comm nope" in result.output
    assert asyncio.run(model.list_tasks(served.table, project=PROJECT)) == []


def test_add_with_an_empty_comm_opts_out_of_the_parent_comms(table, start_server):
    served = serve(start_server)
    comm = OrchestratorClient(served.url).post("/api/comms", {"title": "Weekly"})
    seed(table, title="Plan", id="NORT-1", branch="feat/orders", comms=[comm["id"]])

    inherits = mael_task("add", "Inherits", "-p", PROJECT, "-P", "NORT-1")
    opts_out = mael_task("add", "Opts out", "-p", PROJECT, "-P", "NORT-1", "--comm", "")

    assert opts_out.exit_code == 0, opts_out.output
    assert stored(served.table, inherits.output.strip()).comms == [comm["id"]]
    assert stored(served.table, opts_out.output.strip()).comms == []


def test_add_run_starts_the_task_on_the_server(start_server):
    served = serve(start_server)

    result = mael_task("add", "Run me", "-p", PROJECT, "-b", "feat/run", "--run")

    assert result.exit_code == 0, result.output
    task_id, started = result.output.splitlines()
    assert started.startswith(f"{task_id} started in a separate claude session")
    [operation] = OrchestratorClient(served.url).get("/api/operations")["operations"]
    assert operation["id"] in started
    assert (operation["kind"], operation["taskId"]) == (
        "create",
        f"{PROJECT}/{task_id}",
    )
    assert "start_agent" in [step["name"] for step in operation["steps"]]


def test_add_with_no_server_says_so(home):
    point_config_at("http://127.0.0.1:9")

    result = CliRunner().invoke(task, ["add", "Lost", "-p", PROJECT])

    assert result.exit_code == 1
    assert "No orchestrator answered at http://127.0.0.1:9" in result.output


# --- promote ---


def write_drafts(tmp_path, *titles: str):
    paths = []
    for i, title in enumerate(titles, 1):
        path = tmp_path / f"{i}.md"
        path.write_text(model.draft_markdown(title=title))
        paths.append(path)
    return paths


def test_promote_splices_the_drafts_in_after_the_session_task(
    table, start_server, monkeypatch, tmp_path
):
    seed(table, title="Plan", id="NORT-1", branch="feat/orders")
    seed(table, title="Ship", id="NORT-2", branch="feat/orders", follows=["NORT-1"])
    served = serve(start_server)
    monkeypatch.setenv("MAEL_TASK_ID", "NORT-1")
    monkeypatch.setenv("MAEL_TASK_PARENT", "NORT-1")
    paths = write_drafts(tmp_path, "First", "Second")

    result = mael_task("promote", *map(str, paths), "-p", PROJECT, "--mode", "auto")

    assert result.exit_code == 0, result.output
    assert result.output == "NORT-1.1\nNORT-1.2\n"
    first, second, ship = (
        stored(served.table, t) for t in ("NORT-1.1", "NORT-1.2", "NORT-2")
    )
    assert (first.title, first.mode, first.branch) == ("First", "auto", "feat/orders")
    assert (first.follows, second.follows, ship.follows) == (
        ["NORT-1"],
        ["NORT-1.1"],
        ["NORT-1.2"],
    )
    assert desk_ids(served.url) == on_desk("NORT-1.1", "NORT-1.2")
    # A promoted draft left on disk would be promoted twice.
    assert not any(path.exists() for path in paths)


def test_promote_with_a_follow_end_wires_the_first_draft_and_moves_nothing(
    table, start_server, monkeypatch, tmp_path
):
    seed(table, title="Plan", id="NORT-1", branch="feat/orders")
    seed(table, title="Old", id="NORT-1.1", parent="NORT-1", branch="feat/orders")
    seed(table, title="Ship", id="NORT-2", branch="feat/orders", follows=["NORT-1"])
    served = serve(start_server)
    monkeypatch.setenv("MAEL_TASK_ID", "NORT-1")
    monkeypatch.setenv("MAEL_TASK_PARENT", "NORT-1")
    [path] = write_drafts(tmp_path, "New")

    result = mael_task("promote", str(path), "-p", PROJECT, "--follow-end", "*")

    assert result.exit_code == 0, result.output
    assert stored(served.table, "NORT-1.2").follows == ["NORT-1.1"]
    assert stored(served.table, "NORT-2").follows == ["NORT-1"]


def test_promote_of_a_draft_that_will_not_parse_leaves_every_file(
    start_server, tmp_path
):
    served = serve(start_server)
    good, bad = write_drafts(tmp_path, "Good", "Bad")
    bad.write_text('---\ntitle: "unclosed\n---\n')

    result = mael_task("promote", str(good), str(bad), "-p", PROJECT)

    assert result.exit_code == 1
    assert "2.md" in result.output
    assert good.exists() and bad.exists()
    assert asyncio.run(model.list_tasks(served.table, project=PROJECT)) == []


def test_promote_of_a_file_that_is_not_there_names_it(start_server, tmp_path):
    serve(start_server)

    result = mael_task("promote", str(tmp_path / "absent.md"), "-p", PROJECT)

    assert result.exit_code == 1
    assert "absent.md" in result.output


# --- load-many ---


PLAN = """Preamble the parser ignores.

---CREATE TASK head---
title: Head
branch: feat/plan
follow-end: "\\*"
---
Do the first part.

---CREATE TASK tail---
title: Tail
follow: head
---
Do the rest.
"""


def test_load_many_creates_the_chain_warns_and_files_each_task(
    start_server, tmp_path, monkeypatch
):
    served = serve(start_server)
    monkeypatch.setenv("MAEL_TASK_PARENT", "linear.NORT-9")
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)

    result = mael_task("load-many", str(plan), "--project", PROJECT)

    assert result.exit_code == 0, result.output
    assert "warning: normalised invalid escape" in result.stderr
    head, tail = (line.split("\t") for line in result.stdout.splitlines())
    assert (head[1], tail[1]) == ("Head", "Tail")
    tail_task = stored(served.table, tail[0])
    assert (tail_task.parent, tail_task.follows) == ("linear.NORT-9", [head[0]])
    assert desk_ids(served.url) == on_desk(head[0], tail[0])


def test_load_many_run_starts_the_tasks_that_wait_on_nothing(start_server, tmp_path):
    served = serve(start_server)
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)

    result = mael_task("load-many", str(plan), "--project", PROJECT, "--run")

    assert result.exit_code == 0, result.output
    head_id = result.stdout.splitlines()[0].split("\t")[0]
    assert f"{head_id} started in a separate claude session" in result.output
    [operation] = OrchestratorClient(served.url).get("/api/operations")["operations"]
    assert (operation["kind"], operation["taskId"]) == (
        "launch",
        f"{PROJECT}/{head_id}",
    )
