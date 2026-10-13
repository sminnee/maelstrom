"""``NotebookTaskSource``: what the server reads tasks through.

A new file rather than a section of ``test_orchestrator_server.py``, because
that suite's ``Harness`` injects its own ``version`` counter and so cannot
reach the revision path at all. The seam here is the source itself, over a real
table.

The partial read is the point. A poll that learns *that* something moved still
has to read every task in every project; one that learns *which rows* moved
reads those rows alone. Both backends answer it, so both are tested.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from mael_domain import task as model
from mael_domain.agent_store import SqliteAgentStore, new_agent_record
from mael_domain.github_model import PrStatus, RateLimited
from mael_domain.list_all import ProjectPrs
from mael_domain.session_discovery import LiveSessionSet
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_launch import LaunchBlocked
from mael_domain.task_table import InMemoryTaskTable, SqliteTaskTable
from mael_domain.worktree import WorktreeSetup
from mael_domain.worktree_steps import StepHook
from mael_orchestrator.sources import (
    CloseBlocked,
    ListAllWorktreeSource,
    NotebookTaskSource,
    PrStateCache,
)

PROJECT = "northwind"

#: The default for an argument where ``None`` is itself a value.
_NOT_SET = object()


@pytest.fixture(params=["memory", "sqlite"])
async def table(request):
    """Run each test against every backend, as the contract suite does."""
    if request.param == "memory":
        yield InMemoryTaskTable()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteTaskTable(db)
    db.close()


def a_source(table) -> NotebookTaskSource:
    """A source over ``table``, reading one project and no worktrees."""
    return NotebookTaskSource(table, lambda: [PROJECT])


async def test_a_read_since_the_current_revision_finds_nothing(table):
    """An idle poll does no work: nothing moved, so nothing is read."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    source = a_source(table)
    revision = await table.revision()

    changed = await source.read_since(revision)

    assert changed.tasks == []
    assert changed.removed == []
    assert changed.revision == revision


async def test_only_the_task_that_moved_comes_back(table):
    """The whole point: one row moves, one row is read.

    The other task is untouched and must not appear, or the server is still
    diffing whole tables with extra steps.
    """
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    await model.create(table, project=PROJECT, title="Leave it", id="NORT-8")
    source = a_source(table)
    before = await table.revision()

    await model.move(table, PROJECT, "NORT-7", model.STATUS_IN_PROGRESS)
    changed = await source.read_since(before)

    assert [t["id"] for t in changed.tasks] == [f"{PROJECT}/NORT-7"]
    assert changed.tasks[0]["status"] == model.STATUS_IN_PROGRESS
    assert changed.revision > before


@pytest.fixture
async def agents():
    """A real Agent store, on its own state database."""
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteAgentStore(db)
    db.close()


async def an_agent(agents, task_id: str, started_at: str, **over) -> None:
    """Save one Agent record for ``task_id``'s session."""
    record = new_agent_record(
        f"agent-{task_id}-{started_at}",
        harness="claude",
        task_session_id=model.session_id_for(PROJECT, task_id),
        task_id="",
        cwd="/worktree",
        model="claude:opus",
        mode="auto",
        started_at=started_at,
    )
    await agents.save({**record, **over})


async def test_a_task_starts_when_its_first_agent_did(table, agents):
    """Ended records count, and the earliest wins, so a re-run keeps the
    task's own PR. A record with no readable start or no session says nothing."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    await model.create(table, project=PROJECT, title="Not yet", id="NORT-8")
    await an_agent(agents, "NORT-7", "2026-09-22T10:00:00+00:00")
    await an_agent(agents, "NORT-7", "2026-09-21T10:00:00+00:00", status="ended")
    await an_agent(agents, "NORT-7", "")
    await an_agent(agents, "NORT-7", "2026-09-20T10:00:00")
    await an_agent(agents, "NORT-8", "2026-09-19T10:00:00+00:00", task_session_id="")
    source = NotebookTaskSource(table, lambda: [PROJECT], agents=agents)

    read = {t["id"]: t["startedAt"] for t in await source.read()}

    assert read == {
        f"{PROJECT}/NORT-7": "2026-09-21T10:00:00+00:00",
        f"{PROJECT}/NORT-8": "",
    }


async def test_a_task_comes_back_when_its_first_agent_starts(table, agents):
    """An Agent record does not move the task row, so the source names the
    task itself. A later agent moves nothing: the first start stands."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    await model.create(table, project=PROJECT, title="Leave it", id="NORT-8")
    source = NotebookTaskSource(table, lambda: [PROJECT], agents=agents)
    await source.read()
    before = await table.revision()

    await an_agent(agents, "NORT-7", "2026-09-21T10:00:00+00:00")
    started = await source.read_since(before)
    await an_agent(agents, "NORT-7", "2026-09-22T10:00:00+00:00")
    again = await source.read_since(started.revision)

    assert {t["id"]: t["startedAt"] for t in started.tasks} == {
        f"{PROJECT}/NORT-7": "2026-09-21T10:00:00+00:00"
    }
    assert again.tasks == []


async def test_a_deleted_task_comes_back_as_a_removal(table):
    """Absence never means deletion; ``removed`` is the only authority.

    A partial reading cannot infer a delete from a row it did not ask about —
    that would remove every task the poll left out.
    """
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    await model.create(table, project=PROJECT, title="Leave it", id="NORT-8")
    source = a_source(table)
    before = await table.revision()

    await model.delete(table, PROJECT, "NORT-7")
    changed = await source.read_since(before)

    assert changed.removed == [f"{PROJECT}/NORT-7"]
    assert [t["id"] for t in changed.tasks] == []


async def test_a_changed_task_carries_its_whole_entity(table):
    """The row carries the prose, so a partial read is not a partial entity."""
    await model.create(
        table, project=PROJECT, title="Ship it", id="NORT-7", content="The plan."
    )
    source = a_source(table)
    before = await table.revision()

    await model.update(table, PROJECT, "NORT-7", title="Ship it now")
    changed = await source.read_since(before)

    entity = changed.tasks[0]
    assert entity["title"] == "Ship it now"
    assert entity["content"] == "The plan."
    assert entity["notebookId"] == "NORT-7"


async def test_actionability_is_decided_for_a_changed_task(table):
    """``actionable`` is the notebook's rule, and a partial read still applies it.

    ``NORT-8`` follows ``NORT-7``, so it is not actionable until that one is
    done. Finishing ``NORT-7`` moves both rows, and the reading must say so.
    """
    await model.create(table, project=PROJECT, title="First", id="NORT-7")
    await model.create(
        table, project=PROJECT, title="Second", id="NORT-8", follows=["NORT-7"]
    )
    source = a_source(table)
    before = await table.revision()

    await model.move(table, PROJECT, "NORT-7", model.STATUS_DONE)
    changed = await source.read_since(before)

    by_id = {t["id"]: t for t in changed.tasks}
    assert by_id[f"{PROJECT}/NORT-7"]["actionable"] is False, "a done task is terminal"


async def test_a_task_outside_the_read_projects_is_left_out(table):
    """The source reads the projects it was given, partial or not."""
    await model.create(table, project=PROJECT, title="Mine", id="NORT-7")
    await model.create(table, project="askastro", title="Theirs", id="ASK-1")
    source = a_source(table)
    before = await table.revision()

    await model.move(table, "askastro", "ASK-1", model.STATUS_IN_PROGRESS)
    changed = await source.read_since(before)

    assert [t["id"] for t in changed.tasks] == []


async def test_update_renames_the_wire_s_execute_model_key(table):
    """``executeModel`` on the wire lands on ``execute_model`` in the notebook.

    The one field whose wire and model spelling differ — see
    ``WIRE_RENAMES`` in ``validate.py``.
    """
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    source = a_source(table)

    await source.update(f"{PROJECT}/NORT-7", {"executeModel": "claude:opus"})

    task = await model.load(table, PROJECT, "NORT-7")
    assert task.execute_model == "claude:opus"


async def test_update_drops_a_field_outside_editable(table):
    """A field the wire filter does not name never reaches the notebook."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    source = a_source(table)

    await source.update(f"{PROJECT}/NORT-7", {"status": "done", "title": "Shipped"})

    task = await model.load(table, PROJECT, "NORT-7")
    assert task.title == "Shipped"
    assert task.status != "done"


async def test_create_renames_the_wire_s_execute_model_key(table):
    """``task.create`` renames ``executeModel`` the same way ``update`` does."""
    source = a_source(table)

    wire_id = await source.create(
        PROJECT, {"title": "Ship it", "executeModel": "claude:sonnet"}
    )

    _, notebook_id = wire_id.split("/", 1)
    task = await model.load(table, PROJECT, notebook_id)
    assert task.execute_model == "claude:sonnet"


def test_a_row_id_is_already_the_wire_id_for_a_task():
    """A task's row id and its wire id are the same string, and that is load-bearing.

    ``task_table.row_id`` joins project and id with a slash, and
    ``world_build.task_key`` does the same. The poll relies on it: the ids in
    ``removed`` come straight off the database and are matched against the
    world's own keys without translation. Pinned because nothing else would
    notice if either one changed its separator — the removals would simply stop
    matching, and deleted tasks would linger on the canvas.
    """
    from mael_domain.task_table import row_id
    from mael_orchestrator.world_build import task_key

    assert row_id("northwind", "NORT-7") == task_key("northwind", "NORT-7")


async def test_an_injected_version_is_not_a_revision(table):
    """A source handed its own counter must not be read from a cursor.

    The test harness injects one, so this is what keeps every existing
    orchestrator test on the whole-notebook read rather than on a cursor its
    counter cannot honour.
    """
    assert a_source(table).version_is_revision is True
    injected = NotebookTaskSource(table, lambda: [PROJECT], version=lambda: "7")
    assert injected.version_is_revision is False


# --- the server's own poll ---
#
# The suite in ``test_orchestrator_server.py`` builds every source with an
# injected version, so none of its 178 tests enters the partial path at all. A
# green suite that never runs the branch proves nothing about it, so the poll
# is driven here instead, over a real table.


def an_orchestrator(table):
    """An orchestrator whose task source reads ``table`` for real."""
    from mael_orchestrator.server import Orchestrator
    from mael_orchestrator.sources import InMemoryWorktreeSource

    return Orchestrator(a_source(table), InMemoryWorktreeSource(), _NoDaemon())


async def _nothing():
    """An async iterator over nothing, for a stream with no events."""
    for event in ():
        yield event


class _NoDaemon:
    """An agent host that lists nothing. The poll under test never asks it."""

    async def request(self, payload: dict) -> dict:
        return {"agents": []}

    def attach(self, agent_id: str, from_seq: int = 0, epoch: str = ""):
        """No agent to attach to, so the stream is empty.

        Built from an empty iterable rather than written as a generator with an
        unreachable ``yield``, which the dead-code gate reads as dead code —
        correctly, since that is exactly what it is.
        """
        return _nothing()


async def test_the_poll_publishes_only_the_task_that_moved(table):
    """The move this whole change exists for: one row moves, one event lands."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    await model.create(table, project=PROJECT, title="Leave it", id="NORT-8")
    orch = an_orchestrator(table)
    await orch.refresh_tasks()

    published: list[dict] = []
    orch.notices.notify = lambda notices: published.append(notices)

    await model.move(table, PROJECT, "NORT-7", model.STATUS_IN_PROGRESS)
    await orch.refresh_tasks()

    assert published == [{"task": {f"{PROJECT}/NORT-7"}}]
    assert orch.world["tasks"][f"{PROJECT}/NORT-8"]["title"] == "Leave it"
    assert (
        orch.world["tasks"][f"{PROJECT}/NORT-7"]["status"] == model.STATUS_IN_PROGRESS
    )


async def test_the_poll_keeps_the_tasks_it_did_not_read(table):
    """The ``diff_kind`` hazard, pinned: a partial read must delete nothing.

    ``diff_kind`` removes any id absent from the reading it is given, so a poll
    that fed it one tick's rows would drop the other tasks. Nothing about the
    world says this went wrong except the tasks quietly vanishing.
    """
    for n in range(5):
        await model.create(table, project=PROJECT, title=f"Task {n}", id=f"NORT-{n}")
    orch = an_orchestrator(table)
    await orch.refresh_tasks()
    assert len(orch.world["tasks"]) == 5

    await model.move(table, PROJECT, "NORT-0", model.STATUS_DONE)
    await orch.refresh_tasks()

    assert len(orch.world["tasks"]) == 5, "a partial read deleted what it did not read"


async def test_the_poll_drops_a_deleted_task(table):
    """A removal is published, so the client stops showing what is gone."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    await model.create(table, project=PROJECT, title="Leave it", id="NORT-8")
    orch = an_orchestrator(table)
    await orch.refresh_tasks()

    await model.delete(table, PROJECT, "NORT-7")
    await orch.refresh_tasks()

    assert set(orch.world["tasks"]) == {f"{PROJECT}/NORT-8"}


async def test_an_idle_poll_publishes_nothing(table):
    """Nothing moved, so no notice goes out and no client is woken."""
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    orch = an_orchestrator(table)
    await orch.refresh_tasks()

    published: list[dict] = []
    orch.notices.notify = lambda notices: published.append(notices)
    await orch.refresh_tasks()

    assert published == []


async def test_the_poll_really_takes_the_partial_path(table, monkeypatch):
    """The tests above would pass on the whole-notebook read too, so pin the path.

    A two-task table read whole produces the same one upsert as a partial read
    of the one row that moved, which is how a fallback passes for the feature.
    This asserts the mechanism rather than the outcome: the cursor advances,
    and the source is asked ``read_since`` rather than ``read``.
    """
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    orch = an_orchestrator(table)

    await orch.refresh_tasks()
    first = orch._task_cursor
    assert first is not None, "the first read must leave a cursor to poll from"

    asked: list[int] = []
    real_read_since = orch.tasks.read_since

    async def recording_read_since(since: int):
        asked.append(since)
        return await real_read_since(since)

    # Restored by pytest: a poisoned ``read`` leaking into another test would
    # surface there as an unrelated assertion failure.
    monkeypatch.setattr(orch.tasks, "read_since", recording_read_since)
    monkeypatch.setattr(orch.tasks, "read", _refuse_whole_read)

    await model.move(table, PROJECT, "NORT-7", model.STATUS_IN_PROGRESS)
    await orch.refresh_tasks()

    assert asked == [first], "the poll read from the cursor it stored"
    assert orch._task_cursor is not None and orch._task_cursor > first


async def test_a_forced_refresh_still_reads_the_whole_notebook(table, monkeypatch):
    """A command has just written the row a client waits for, so it reads it all.

    The partial path is the poll's alone. Forcing is what every command does,
    and it must not depend on a cursor that a first start has not set yet.
    """
    await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
    orch = an_orchestrator(table)
    await orch.refresh_tasks()

    monkeypatch.setattr(orch.tasks, "read_since", _refuse_partial_read)
    await model.create(table, project=PROJECT, title="Another", id="NORT-8")
    await orch.refresh_tasks(force=True)

    assert set(orch.world["tasks"]) == {f"{PROJECT}/NORT-7", f"{PROJECT}/NORT-8"}


# --- the export drain ---
#
# The server is the one drainer: a CLI write queues its export and exits. The
# ``Harness`` in ``test_orchestrator_server.py`` builds no exporter, so every
# test there runs with ``exporter=None`` and never enters this branch at all.
# It is driven here, over a real table and a real git-backed store.


def an_exporting_orchestrator(table, root, **options):
    """An orchestrator that drains its export queue to ``root``."""
    from mael_domain.task_export import SqliteExportQueue, TaskExporter
    from mael_domain.task_store import GitFileStore
    from mael_orchestrator.server import Orchestrator
    from mael_orchestrator.sources import InMemoryWorktreeSource

    exporter = TaskExporter(
        SqliteExportQueue(table._db), table, GitFileStore(root=root)
    )
    return Orchestrator(
        a_source(table),
        InMemoryWorktreeSource(),
        _NoDaemon(),
        exporter=exporter,
        **options,
    )


async def test_the_poll_drains_the_export_queue(tmp_path):
    """A queued export reaches the files without anyone asking for it.

    The drain is a poller like the others, so a task written while the server
    runs appears in the export tree on the next tick.
    """
    db = open_state_db(":memory:")
    await db.migrate()
    table = SqliteTaskTable(db)
    try:
        orch = an_exporting_orchestrator(
            table, tmp_path, task_poll=0.01, export_poll=0.01, agent_poll=0.01
        )
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        await orch.start()
        try:
            await _until(lambda: (tmp_path / PROJECT / "todo" / "NORT-7.md").is_file())
        finally:
            await orch.stop()
        assert (tmp_path / PROJECT / "todo" / "NORT-7.md").is_file()
    finally:
        db.close()


async def test_stopping_drains_what_the_export_still_owes(tmp_path):
    """A server going down writes out what it queued, or the file is lost.

    Nothing else drains: the next start would, but a notebook whose server
    never comes back would keep a task that exists in the table and nowhere on
    disk. The drain runs after the pollers are cancelled, so it cannot race the
    one that was doing the same work.
    """
    db = open_state_db(":memory:")
    await db.migrate()
    table = SqliteTaskTable(db)
    try:
        # A poll interval longer than the test, so only the stop can drain.
        orch = an_exporting_orchestrator(
            table, tmp_path, task_poll=0.01, export_poll=3600.0, agent_poll=0.01
        )
        await orch.start()
        await model.create(table, project=PROJECT, title="Ship it", id="NORT-7")
        assert not (tmp_path / PROJECT / "todo" / "NORT-7.md").exists()

        await orch.stop()

        assert (tmp_path / PROJECT / "todo" / "NORT-7.md").is_file()
    finally:
        db.close()


async def test_a_server_with_no_exporter_stops_cleanly():
    """``stop`` drains unconditionally, so a build with no export must not fail.

    The assertion is that the drain is reached and returns: an orchestrator
    built without an exporter has nowhere to write, and ``stop`` calls
    ``drain_exports`` regardless.
    """
    db = open_state_db(":memory:")
    await db.migrate()
    table = SqliteTaskTable(db)
    try:
        orch = an_orchestrator(table)
        assert orch.exporter is None
        await orch.start()
        await orch.stop()
        await orch.drain_exports()
    finally:
        db.close()


async def _until(ready, timeout: float = 5.0) -> None:
    """Wait for ``ready()``, rather than sleeping a guessed interval."""
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while not ready():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("the drain did not run inside the timeout")
        await asyncio.sleep(0.01)


async def _refuse_whole_read():
    raise AssertionError("the poll read the whole notebook instead of what moved")


async def _refuse_partial_read(since: int):
    raise AssertionError("a forced refresh took the partial path")


async def test_the_board_refuses_a_non_claude_execute_model(table):
    """The board's launch never passes through ``validate``, which guards the
    free-agent path only. Without its own check a task carrying an inert execute
    model reaches the daemon, and the user learns it did nothing only after the
    plan is approved and the context is already cleared."""
    await model.create(
        table,
        project=PROJECT,
        title="Ship it",
        id="NORT-7",
        execute_model="codex:sol",
    )
    source = NotebookTaskSource(
        table, lambda: [PROJECT], open_worktree=lambda *a, **k: None
    )
    with pytest.raises(LaunchBlocked, match="must be a Claude model"):
        await source.prepare_launch(f"{PROJECT}/NORT-7", None)


async def test_a_task_source_with_no_worktree_opener_refuses_to_open_one(table):
    """Both ways in refuse: a task's launch, and a free agent's start."""
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    source = a_source(table)
    with pytest.raises(LaunchBlocked, match="cannot open worktrees"):
        await source.prepare_launch(f"{PROJECT}/NORT-7", None)
    assert (await model.load(table, PROJECT, "NORT-7")).status == "todo"
    with pytest.raises(LaunchBlocked, match="cannot open worktrees"):
        await source.worktree_for(PROJECT, "feat/x")


def a_launching_source(table, *, has_transcript) -> NotebookTaskSource:
    """A source over ``table`` whose worktree and transcript check are fixed."""
    return NotebookTaskSource(
        table,
        lambda: [PROJECT],
        open_worktree=lambda project, branch, base: WorktreeSetup(
            path=Path("/w/alpha"), name="alpha", action="reused"
        ),
        live_sessions=lambda: LiveSessionSet([]),
        has_transcript=has_transcript,
    )


async def test_launch_resumes_a_task_that_has_already_run(table):
    """Relaunching a stopped task must continue its session, not claim its id."""
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    source = a_launching_source(table, has_transcript=lambda path, sid: True)
    prepared = await source.prepare_launch(f"{PROJECT}/NORT-7", None)
    assert source.finish_launch(prepared)["resume"] is True


async def test_launch_of_a_task_that_never_ran_claims_a_fresh_session(table):
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    source = a_launching_source(table, has_transcript=lambda path, sid: False)
    prepared = await source.prepare_launch(f"{PROJECT}/NORT-7", None)
    assert source.finish_launch(prepared)["resume"] is False


async def test_launch_asks_about_the_worktree_the_session_will_run_in(table):
    """The transcript lives under the worktree path, so the check needs it."""
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    seen: list[tuple] = []

    def has_transcript(path, session_id):
        seen.append((path, session_id))
        return False

    source = a_launching_source(table, has_transcript=has_transcript)
    prepared = await source.prepare_launch(f"{PROJECT}/NORT-7", None)
    payload = source.finish_launch(prepared)
    assert seen == [(Path("/w/alpha"), payload["session"])]


# --- ListAllWorktreeSource: the terminal's link -----------------------------


async def test_a_worktree_row_carries_its_shell_url(monkeypatch):
    """The link lands on its own row, and the others get ``''``."""
    from mael_orchestrator import sources

    async def list_all(*_args, **_kwargs):
        rows = [
            {"name": "alpha", "path": "/p/alpha"},
            {"name": "bravo", "path": "/p/bravo"},
            {"name": "charlie", "path": "/p/charlie", "is_closed": True},
        ]
        return {"projects": [{"name": PROJECT, "worktrees": rows}]}

    monkeypatch.setattr(sources, "build_list_all_data", list_all)
    asked: list[list[tuple[str, str]]] = []

    def terminal_urls(pairs):
        asked.append(list(pairs))
        return {(PROJECT, "alpha"): "cmux://workspace/W/pane/P"}

    source = sources.ListAllWorktreeSource(Path("/p"), terminal_urls=terminal_urls)
    _, worktrees = await source.read()

    assert {w["nato"]: w["shellUrl"] for w in worktrees} == {
        "alpha": "cmux://workspace/W/pane/P",
        "bravo": "",
        "charlie": "",
    }
    # Only the open rows are asked about.
    assert asked == [[(PROJECT, "alpha"), (PROJECT, "bravo")]]


# --- ListAllWorktreeSource: merging a pull request --------------------------


def _ready_pr(**over):
    """alpha's pull request, ready to merge, as the last PR read saw it."""
    fields = {
        "number": 118,
        "commits": 2,
        "url": "https://github.com/acme/repo/pull/118",
        "state": "ready",
        "is_draft": False,
        "head_oid": "deadbee",
        **over,
    }
    return PrStatus(**fields)


async def _source_that_read(monkeypatch, pr, merge_pr, head="deadbee"):
    """A source whose PR read saw ``pr`` on feat/orders, in a worktree whose
    local ``HEAD`` is ``head``. ``pr`` ``None`` is a branch with no PR."""
    from mael_orchestrator import sources

    async def head_sha(_path):
        return head

    monkeypatch.setattr(sources, "get_head_sha_async", head_sha)
    read = _GatedRead({"feat/orders": pr} if pr else {})
    source = sources.ListAllWorktreeSource(
        Path("/p"), merge_pr=merge_pr, prs=sources.PrStateCache(read=read)
    )
    await source.prs.refresh(Path("/p") / PROJECT, {"feat/orders"})
    return source, read


async def test_a_merge_names_the_pr_and_head_commit_the_last_read_saw(monkeypatch):
    """The head commit is not on the wire, so the source supplies it."""
    merged: list[tuple[str, int, str]] = []
    source, read = await _source_that_read(
        monkeypatch, _ready_pr(), lambda *args: merged.append(args)
    )
    assert source.merge is not None
    read.answer = {"feat/orders": _ready_pr(state="merged")}
    await source.merge(PROJECT, "feat/orders", "/p/alpha")
    assert merged == [("/p/alpha", 118, "deadbee")]
    # The merge changed the PR, so its state is read again.
    assert source.prs.get(PROJECT, "feat/orders") == _ready_pr(state="merged")


@pytest.mark.parametrize(
    ("pr", "head", "said"),
    [
        (None, "deadbee", "No pull request is known for feat/orders"),
        # A read that landed after the server validated the command.
        (
            _ready_pr(state="ci-running"),
            "deadbee",
            "PR #118 is not ready to merge (ci-running)",
        ),
        (_ready_pr(is_draft=True), "deadbee", "PR #118 is not ready to merge (draft)"),
        (
            _ready_pr(head_oid=""),
            "deadbee",
            "The head commit of PR #118 is not known yet",
        ),
        # A local commit made after the last read.
        (
            _ready_pr(),
            "c0ffee1",
            "The local branch differs from PR #118. Sync it first.",
        ),
        # A worktree git cannot read; a sync would not help.
        (_ready_pr(), None, "The local head of PR #118's branch cannot be read"),
    ],
)
async def test_a_merge_the_last_read_does_not_support_is_refused(
    monkeypatch, pr, head, said
):
    merged: list[tuple] = []
    source, _read = await _source_that_read(
        monkeypatch, pr, lambda *args: merged.append(args), head=head
    )
    assert source.merge is not None
    with pytest.raises(CloseBlocked) as refused:
        await source.merge(PROJECT, "feat/orders", "/p/alpha")
    assert str(refused.value) == said
    assert merged == []


# --- PrStateCache: the one reader of pull request state ---------------------


def _pr(number: int, head_oid: str = "deadbee") -> PrStatus:
    return PrStatus(
        number=number,
        commits=1,
        url=f"https://github.com/acme/repo/pull/{number}",
        state="ready",
        is_draft=False,
        head_oid=head_oid,
    )


class _GatedRead:
    """A ``get_open_prs`` stand-in that records its calls and answers on cue.

    ``answer`` is a dict, ``None`` (an ordinary failure) or an exception.
    """

    def __init__(self, answer=_NOT_SET, gate: asyncio.Event | None = None) -> None:
        self.calls: list[set[str]] = []
        self.answer = {} if answer is _NOT_SET else answer
        self.gate = gate

    async def __call__(self, _cwd: Path, branches: set[str]):
        self.calls.append(set(branches))
        if self.gate is not None:
            await self.gate.wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return None if self.answer is None else dict(self.answer)


ACME = Path("/p/acme")


async def test_two_refreshes_of_one_project_share_one_read():
    gate = asyncio.Event()
    read = _GatedRead({"feat/a": _pr(1)}, gate=gate)
    cache = PrStateCache(read=read)
    first = asyncio.create_task(cache.refresh(ACME, {"feat/a"}))
    second = asyncio.create_task(cache.refresh(ACME, {"feat/a"}))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)
    assert read.calls == [{"feat/a"}]
    assert cache.snapshot() == {"acme": ProjectPrs({"feat/a": _pr(1)})}


async def test_a_refresh_the_read_in_flight_does_not_cover_reads_again():
    gate = asyncio.Event()
    read = _GatedRead({"feat/a": _pr(1), "feat/b": _pr(2)}, gate=gate)
    cache = PrStateCache(read=read)
    first = asyncio.create_task(cache.refresh(ACME, {"feat/a"}))
    await asyncio.sleep(0)
    wider = asyncio.create_task(cache.refresh(ACME, {"feat/a", "feat/b"}))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, wider)
    assert read.calls == [{"feat/a"}, {"feat/a", "feat/b"}]


async def test_a_cancelled_caller_does_not_cancel_a_read_another_shares():
    gate = asyncio.Event()
    read = _GatedRead({"feat/a": _pr(1)}, gate=gate)
    cache = PrStateCache(read=read)
    first = asyncio.create_task(cache.refresh(ACME, {"feat/a"}))
    second = asyncio.create_task(cache.refresh(ACME, {"feat/a"}))
    await asyncio.sleep(0)
    first.cancel()
    gate.set()
    await second
    assert cache.get("acme", "feat/a") == _pr(1)


async def test_a_refresh_after_a_write_reads_again_after_one_in_flight():
    """A read that began before the write cannot hold its result."""
    gate = asyncio.Event()
    read = _GatedRead({"feat/a": _pr(1)}, gate=gate)
    cache = PrStateCache(read=read)
    poll = asyncio.create_task(cache.refresh(ACME, {"feat/a"}))
    await asyncio.sleep(0)
    written = asyncio.create_task(cache.refresh(ACME, {"feat/a"}, after_write=True))
    await asyncio.sleep(0)
    read.answer = {"feat/a": _pr(1, head_oid="c0ffee1")}
    gate.set()
    await asyncio.gather(poll, written)
    assert read.calls == [{"feat/a"}, {"feat/a"}]
    assert cache.get("acme", "feat/a") == _pr(1, head_oid="c0ffee1")


async def test_a_refresh_during_a_stand_off_does_not_read_and_marks_stale():
    read = _GatedRead({"feat/a": _pr(1)})
    cache = PrStateCache(read=read)
    await cache.refresh(ACME, {"feat/a"})
    read.answer = RateLimited("spent")
    await cache.refresh(ACME, {"feat/a"})
    assert cache.standing_off()

    await cache.refresh(ACME, {"feat/a"})

    assert read.calls == [{"feat/a"}, {"feat/a"}]
    # The last known state stands.
    assert cache.snapshot() == {
        "acme": ProjectPrs({"feat/a": _pr(1)}, stale_branches=frozenset({"feat/a"}))
    }


async def test_the_stand_off_ends_after_the_cooldown():
    """A budget that refills must be picked up."""
    now = [0.0]
    read = _GatedRead(RateLimited("spent"))
    cache = PrStateCache(read=read, cooldown=600.0, clock=lambda: now[0])
    await cache.refresh(ACME, {"feat/a"})
    now[0] = 599.0
    assert cache.standing_off()
    now[0] = 600.0
    read.answer = {"feat/a": _pr(1)}

    await cache.refresh(ACME, {"feat/a"})

    assert read.calls == [{"feat/a"}, {"feat/a"}]
    assert cache.snapshot() == {"acme": ProjectPrs({"feat/a": _pr(1)})}


@pytest.mark.parametrize("failure", ["never answers", "answers None"])
async def test_a_failed_read_keeps_the_last_state_and_marks_it_stale(failure):
    read = _GatedRead({"feat/a": _pr(1)})
    cache = PrStateCache(read=read, timeout=0.05)
    await cache.refresh(ACME, {"feat/a"})
    if failure == "never answers":
        read.gate = asyncio.Event()  # never set
    else:
        read.answer = None

    await cache.refresh(ACME, {"feat/a"})

    assert cache.snapshot() == {
        "acme": ProjectPrs({"feat/a": _pr(1)}, stale_branches=frozenset({"feat/a"}))
    }
    assert not cache.standing_off()


async def test_a_narrow_read_leaves_the_other_branches_stale():
    """A merge's one-branch read must not vouch for the branches a failed
    read left old."""
    read = _GatedRead({"feat/a": _pr(1), "feat/b": _pr(2)})
    cache = PrStateCache(read=read)
    await cache.refresh(ACME, {"feat/a", "feat/b"})
    read.answer = None
    await cache.refresh(ACME, {"feat/a", "feat/b"})
    read.answer = {"feat/a": _pr(1)}

    await cache.refresh(ACME, {"feat/a"}, after_write=True)

    assert cache.snapshot()["acme"].stale_branches == {"feat/b"}


async def test_a_read_answers_only_the_branches_it_asked_about():
    """A branch not asked keeps its entry; one asked with no answer has no PR."""
    read = _GatedRead({"feat/a": _pr(1), "feat/b": _pr(2)})
    cache = PrStateCache(read=read)
    await cache.refresh(ACME, {"feat/a", "feat/b"})
    read.answer = {}

    await cache.refresh(ACME, {"feat/a"})

    assert cache.snapshot() == {"acme": ProjectPrs({"feat/b": _pr(2)})}


def _listening(cache: PrStateCache) -> list[tuple]:
    heard: list[tuple] = []
    cache.on_change = lambda *change: heard.append(change)
    return heard


async def test_on_change_hears_each_branch_whose_pr_moved():
    read = _GatedRead({"feat/a": _pr(1), "feat/b": _pr(2)})
    cache = PrStateCache(read=read)
    heard = _listening(cache)
    await cache.refresh(ACME, {"feat/a", "feat/b"})
    assert sorted(heard) == [
        ("acme", "feat/a", None, _pr(1)),
        ("acme", "feat/b", None, _pr(2)),
    ]
    heard.clear()
    read.answer = {"feat/a": _pr(1, head_oid="c0ffee1"), "feat/b": _pr(2)}

    await cache.refresh(ACME, {"feat/a", "feat/b"})

    assert heard == [("acme", "feat/a", _pr(1), _pr(1, head_oid="c0ffee1"))]


@pytest.mark.parametrize("failure", ["never answers", "answers None", "rate limited"])
async def test_on_change_hears_nothing_from_a_read_that_failed(failure):
    """Stale PR state is no news: the entry did not change."""
    read = _GatedRead({"feat/a": _pr(1)})
    cache = PrStateCache(read=read, timeout=0.05)
    await cache.refresh(ACME, {"feat/a"})
    heard = _listening(cache)
    if failure == "never answers":
        read.gate = asyncio.Event()
    elif failure == "answers None":
        read.answer = None
    else:
        read.answer = RateLimited("spent")

    await cache.refresh(ACME, {"feat/a"})
    await cache.refresh(ACME, {"feat/a"})  # stood off, when rate limited

    assert heard == []


async def test_one_projects_pr_is_not_answered_for_another():
    """Every project's ``_main`` sits on ``main``."""
    cache = PrStateCache(read=_GatedRead({"main": _pr(1)}))
    await cache.refresh(ACME, {"main"})
    assert cache.get("acme", "main") == _pr(1)
    assert cache.get("globex", "main") is None


# --- ListAllWorktreeSource: a hung gh -----------------------------------------


_GH_ANSWER = {
    "data": {
        "repository": {
            "b0": {
                "nodes": [
                    {
                        "number": 7,
                        "headRefName": "feature/work",
                        "url": "https://github.com/acme/test-repo/pull/7",
                        "isDraft": False,
                        "state": "OPEN",
                        "mergeable": "MERGEABLE",
                        "commits": {
                            "totalCount": 1,
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
                ]
            }
        }
    }
}


async def test_a_hung_gh_costs_the_rows_their_freshness_not_their_read(
    project_with_worktree, tmp_path, monkeypatch
):
    """The local state still reads, and the PR keeps its last known state."""
    project_path, worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(_GH_ANSWER))
    hang = tmp_path / "hang"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\n[ -e {hang} ] && exec sleep 3600\ncat {answer}\n")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    source = ListAllWorktreeSource(project_path.parent, prs=PrStateCache(timeout=0.5))
    _, before = await source.read()
    assert [(w["prNumber"], w["prStale"]) for w in before] == [(7, False)]

    hang.touch()
    (worktree_path / "new.txt").write_text("x")
    _, after = await source.read()

    [row] = after
    assert row["dirtyFiles"] == 1
    assert (row["prNumber"], row["prStale"]) == (7, True)


# --- ListAllWorktreeSource: which branches the PR read asks about -------------


async def test_the_pr_read_asks_only_about_the_active_branches(
    project_with_worktree,
):
    """GraphQL is charged by node count, so the poll narrows its ask."""
    project_path, _worktree_path, _remote = project_with_worktree
    (project_path / ".mael").touch()
    read = _GatedRead()
    source = ListAllWorktreeSource(project_path.parent, prs=PrStateCache(read=read))

    await source.read(active_branches=set())
    assert read.calls == []

    await source.read(active_branches={"feature/work", "feat/elsewhere"})
    assert read.calls == [{"feature/work"}]


# --- ListAllWorktreeSource: a trash reads its branch's PR again --------------


async def test_a_trash_reads_the_pr_of_the_branch_it_trashed(project_with_worktree):
    """The branch is read before the trash, which removes it."""
    project_path, worktree_path, _remote = project_with_worktree
    read = _GatedRead()
    trashed: list[tuple[str, int]] = []

    async def trash(project, nato, path, *, hook):
        # How many PR reads came before the trash: none should.
        trashed.append((nato, len(read.calls)))

    source = ListAllWorktreeSource(
        project_path.parent, trash=trash, prs=PrStateCache(read=read)
    )
    assert source.trash is not None
    await source.trash("test-repo", "alpha", str(worktree_path), hook=StepHook())

    assert trashed == [("alpha", 0)]
    assert read.calls == [{"feature/work"}]


async def test_a_refused_trash_reads_no_pr(project_with_worktree):
    project_path, worktree_path, _remote = project_with_worktree
    read = _GatedRead()

    async def trash(project, nato, path, *, hook):
        raise CloseBlocked("PR #7 is open")

    source = ListAllWorktreeSource(
        project_path.parent, trash=trash, prs=PrStateCache(read=read)
    )
    assert source.trash is not None
    with pytest.raises(CloseBlocked):
        await source.trash("test-repo", "alpha", str(worktree_path), hook=StepHook())

    assert read.calls == []
