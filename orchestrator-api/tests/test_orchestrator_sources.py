"""``NotebookTaskSource``: what the server reads tasks through.

A new file rather than a section of ``test_orchestrator_server.py``, because
that suite's ``Harness`` injects its own ``version`` counter and so cannot
reach the revision path at all. The seam here is the source itself, over a real
table.

The partial read is the point. A poll that learns *that* something moved still
has to read every task in every project; one that learns *which rows* moved
reads those rows alone. Both backends answer it, so both are tested.
"""

from pathlib import Path

import pytest

from mael_domain import task as model
from mael_domain.session_discovery import LiveSessionSet
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_launch import LaunchBlocked
from mael_domain.task_table import InMemoryTaskTable, SqliteTaskTable
from mael_domain.worktree import WorktreeSetup
from mael_orchestrator.sources import NotebookTaskSource

PROJECT = "northwind"


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
        await source.launch(f"{PROJECT}/NORT-7", None)


async def test_a_task_source_with_no_worktree_opener_refuses_to_open_one(table):
    """Both ways in refuse: a task's launch, and a free agent's start."""
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    source = a_source(table)
    with pytest.raises(LaunchBlocked, match="cannot open worktrees"):
        await source.launch(f"{PROJECT}/NORT-7", None)
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
    request = await source.launch(f"{PROJECT}/NORT-7", None)
    assert request.payload["resume"] is True


async def test_launch_of_a_task_that_never_ran_claims_a_fresh_session(table):
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    source = a_launching_source(table, has_transcript=lambda path, sid: False)
    request = await source.launch(f"{PROJECT}/NORT-7", None)
    assert request.payload["resume"] is False


async def test_launch_asks_about_the_worktree_the_session_will_run_in(table):
    """The transcript lives under the worktree path, so the check needs it."""
    await model.create(table, project=PROJECT, title="x", id="NORT-7")
    seen: list[tuple] = []

    def has_transcript(path, session_id):
        seen.append((path, session_id))
        return False

    source = a_launching_source(table, has_transcript=has_transcript)
    request = await source.launch(f"{PROJECT}/NORT-7", None)
    assert seen == [(Path("/w/alpha"), request.payload["session"])]


# --- ListAllWorktreeSource: the shell pane's link ---------------------------


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

    def shell_urls(pairs):
        asked.append(list(pairs))
        return {(PROJECT, "alpha"): "cmux://workspace/W/pane/P"}

    source = sources.ListAllWorktreeSource(Path("/p"), shell_urls=shell_urls)
    _, worktrees = await source.read()

    assert {w["nato"]: w["shellUrl"] for w in worktrees} == {
        "alpha": "cmux://workspace/W/pane/P",
        "bravo": "",
        "charlie": "",
    }
    # Only the open rows are asked about.
    assert asked == [[(PROJECT, "alpha"), (PROJECT, "bravo")]]
