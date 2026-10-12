"""Tests for mael_domain.comms — a comm, and the tasks that feed it."""

import pytest

from mael_domain import comms
from mael_domain import task as task_model
from mael_domain.comm_store import (
    Comm,
    CommItem,
    CommUpdate,
    InMemoryCommStore,
    InMemoryCommUpdateStore,
    SqliteCommStore,
    SqliteCommUpdateStore,
)
from mael_domain.landing_store import (
    InMemoryTaskStepStore,
    SqliteTaskStepStore,
    StepEvent,
)
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_table import InMemoryTaskTable

NOW = "2026-10-08T09:00:00+00:00"
LATER = "2026-10-09T09:00:00+00:00"


@pytest.fixture(params=["memory", "sqlite"])
async def store(request):
    """Run each test against every backend."""
    if request.param == "memory":
        yield InMemoryCommStore()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteCommStore(db)
    db.close()


class TestNew:
    async def test_a_new_comm_is_stored_whole_with_its_category_trimmed(self, store):
        await comms.new(
            store,
            "Invoice export",
            "Jo asked for CSV.",
            ["#cs", "jo@acme.test"],
            NOW,
            category=" release ",
            project="maelstrom",
        )
        assert await store.read("c1") == Comm(
            id="c1",
            title="Invoice export",
            content="Jo asked for CSV.",
            recipients=["#cs", "jo@acme.test"],
            created_at=NOW,
            closed_at="",
            category="release",
            project="maelstrom",
        )

    async def test_ids_count_up_from_c1_in_number_order(self, store):
        for n in range(11):
            await comms.new(store, f"t{n}", now=NOW)
        # c10 sorts before c2 as text; the sequence and the list are numeric.
        assert [c.id for c in await store.list()] == [f"c{n}" for n in range(1, 12)]

    async def test_a_blank_title_is_refused(self, store):
        with pytest.raises(ValueError):
            await comms.new(store, "  ", now=NOW)
        made = await comms.new(store, "t", now=NOW)
        with pytest.raises(ValueError):
            await comms.edit(store, made.id, title="")


class TestEdit:
    async def test_edit_changes_only_the_given_fields(self, store):
        made = await comms.new(store, "Old", "body", ["#cs"], NOW)
        await comms.edit(store, made.id, title="New", recipients=["#ops"])
        assert await store.read(made.id) == Comm(
            id="c1", title="New", content="body", recipients=["#ops"], created_at=NOW
        )

    async def test_edit_sets_and_clears_category_and_project(self, store):
        made = await comms.new(store, "t", category="a", project="p", now=NOW)
        await comms.edit(store, made.id, category=" b ", project="")
        stored = await store.read(made.id)
        assert (stored.category, stored.project) == ("b", "")

    async def test_editing_an_unknown_comm_raises(self, store):
        with pytest.raises(KeyError):
            await comms.edit(store, "c9", title="x")


class TestCloseAndReopen:
    async def test_close_stamps_closed_at_and_reopen_clears_it(self, store):
        made = await comms.new(store, "t", now=NOW)
        await comms.close(store, made.id, LATER)
        assert (await store.read(made.id)).closed_at == LATER
        await comms.reopen(store, made.id)
        assert (await store.read(made.id)).closed_at == ""

    async def test_closing_a_closed_comm_keeps_the_first_time(self, store):
        made = await comms.new(store, "t", now=NOW)
        await comms.close(store, made.id, NOW)
        await comms.close(store, made.id, LATER)
        assert (await store.read(made.id)).closed_at == NOW


def _comm(id: str, category: str, project: str, created_at: str = NOW) -> Comm:
    return Comm(
        id=id, title=id, category=category, project=project, created_at=created_at
    )


class TestDefaultProject:
    def test_the_most_common_project_in_the_category_wins(self):
        made = [
            _comm("c1", "release", "a"),
            _comm("c2", "release", "b"),
            _comm("c3", "release", "b"),
            _comm("c4", "support", "a"),
            _comm("c5", "support", "a"),
            # Blanks outnumber b, so counting them would pick "".
            _comm("c6", "release", ""),
            _comm("c7", "release", ""),
            _comm("c8", "release", ""),
        ]
        assert comms.default_project(made, "release") == "b"

    def test_a_tie_goes_to_the_newest_comm_in_either_order(self):
        made = [_comm("c1", "release", "a", NOW), _comm("c2", "release", "b", LATER)]
        assert comms.default_project(made, "release") == "b"
        assert comms.default_project(list(reversed(made)), "release") == "b"

    def test_no_comm_in_the_category_gives_blank(self):
        assert comms.default_project([_comm("c1", "release", "a")], "other") == ""
        assert comms.default_project([_comm("c1", "", "a")], "") == ""


def test_categories_are_distinct_sorted_and_non_blank():
    made = [_comm("c1", "b", ""), _comm("c2", "a", ""), _comm("c3", "b", "")]
    made.append(_comm("c4", "", ""))
    assert comms.categories(made) == ["a", "b"]


class TestChangedSince:
    async def test_only_written_comms_have_changed(self, store):
        a = await comms.new(store, "a", now=NOW)
        await comms.new(store, "b", now=NOW)
        mark = await store.revision()
        await comms.edit(store, a.id, title="a2")
        changes = await store.changed_since(mark)
        assert [c.title for c in changes.comms] == ["a2"]
        assert changes.revision == await store.revision()


class TestLinks:
    async def _tasks(self):
        table = InMemoryTaskTable()
        for project, id in (("p", "t1"), ("p", "t2"), ("q", "t3")):
            await task_model.create(table, project=project, title=id, id=id, now=NOW)
        return table

    async def test_link_adds_the_comm_to_each_task(self):
        table = await self._tasks()
        await comms.link(table, "c1", "p", ["t1", "t2"])
        await comms.link(table, "c1", "p", ["t1"])
        assert (await task_model.load(table, "p", "t1")).comms == ["c1"]
        assert (await task_model.load(table, "p", "t2")).comms == ["c1"]

    async def test_unlink_removes_only_that_comm(self):
        table = await self._tasks()
        await comms.link(table, "c1", "p", ["t1"])
        await comms.link(table, "c2", "p", ["t1"])
        await comms.unlink(table, "c1", "p", ["t1"])
        assert (await task_model.load(table, "p", "t1")).comms == ["c2"]

    async def test_linking_an_unknown_task_raises_and_links_nothing(self):
        table = await self._tasks()
        with pytest.raises(KeyError):
            await comms.link(table, "c1", "p", ["t1", "nope"])
        assert (await task_model.load(table, "p", "t1")).comms == []


@pytest.fixture(params=["memory", "sqlite"])
async def stores(request):
    """The comm, comm update and task step stores, on each backend."""
    if request.param == "memory":
        yield InMemoryCommStore(), InMemoryCommUpdateStore(), InMemoryTaskStepStore()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqliteCommStore(db), SqliteCommUpdateStore(db), SqliteTaskStepStore(db)
    db.close()


def _step(task_id: str, step: str, recorded_at: str = NOW) -> StepEvent:
    return StepEvent("p", task_id, step, 7, recorded_at, recorded_at)


def _task(id: str, *feeds: str) -> comms.LinkedTask:
    return comms.LinkedTask("p", id, f"Task {id}", feeds)


class TestDraftUpdates:
    async def test_a_step_of_a_task_that_feeds_two_comms_queues_for_both(self, stores):
        comm_store, updates, steps = stores
        await comms.new(comm_store, "a", now=NOW)
        await comms.new(comm_store, "b", now=NOW)
        await steps.add(_step("t1", "done"))
        tasks = [_task("t1", "c1", "c2"), _task("t2", "c1")]

        made = await comms.draft_updates(comm_store, updates, steps, tasks, LATER)

        assert made == [
            CommUpdate(
                id=f"{comm_id}.1",
                comm_id=comm_id,
                created_at=LATER,
                items=[CommItem("p", "t1", "done")],
                content="- Task t1 — done",
            )
            for comm_id in ("c1", "c2")
        ]
        assert await updates.list() == made

    async def test_a_closed_comm_queues_nothing(self, stores):
        comm_store, updates, steps = stores
        await comms.new(comm_store, "a", now=NOW)
        await comms.close(comm_store, "c1", NOW)
        await steps.add(_step("t1", "done"))
        comm = await comm_store.read("c1")

        assert comms.queue(comm, [_task("t1", "c1")], await steps.list(), []) == []
        made = await comms.draft_updates(
            comm_store, updates, steps, [_task("t1", "c1")], LATER
        )
        assert made == []

    async def test_a_second_draft_queues_only_the_new_steps(self, stores):
        comm_store, updates, steps = stores
        await comms.new(comm_store, "a", now=NOW)
        tasks = [_task("t1", "c1"), _task("t2", "c1")]
        await steps.add(_step("t1", "done"))
        await comms.draft_updates(comm_store, updates, steps, tasks, NOW)
        # Recorded at the same time as the first: a tie must not hide it.
        await steps.add(_step("t2", "done"))
        await steps.add(_step("t1", "merged"))

        made = await comms.draft_updates(comm_store, updates, steps, tasks, LATER)

        assert [u.id for u in made] == ["c1.2"]
        assert sorted(made[0].items, key=lambda i: (i.task_id, i.step)) == [
            CommItem("p", "t1", "merged"),
            CommItem("p", "t2", "done"),
        ]
        again = await comms.draft_updates(comm_store, updates, steps, tasks, LATER)
        assert again == []

    async def test_a_task_linked_late_queues_the_steps_it_reached_before(self, stores):
        comm_store, updates, steps = stores
        await comms.new(comm_store, "a", now=NOW)
        await steps.add(_step("t1", "done"))
        unlinked = await comms.draft_updates(
            comm_store, updates, steps, [_task("t1")], NOW
        )

        made = await comms.draft_updates(
            comm_store, updates, steps, [_task("t1", "c1")], LATER
        )
        assert unlinked == []
        assert [(u.id, u.items) for u in made] == [
            ("c1.1", [CommItem("p", "t1", "done")])
        ]


def test_the_content_has_one_bullet_per_task_at_its_furthest_step():
    items = [
        CommItem("p", "t1", "merged"),
        CommItem("p", "t2", "done"),
        CommItem("p", "t1", "done"),
    ]
    tasks = [_task("t1", "c1"), _task("t2", "c1")]
    assert comms.update_content(items, tasks) == (
        "- Task t1 — merged\n- Task t2 — done"
    )


class TestEditAndSend:
    async def _drafted(self, stores):
        comm_store, updates, steps = stores
        await comms.new(comm_store, "a", now=NOW)
        await steps.add(_step("t1", "done"))
        await comms.draft_updates(comm_store, updates, steps, [_task("t1", "c1")], NOW)
        return updates

    async def test_a_draft_takes_new_content_then_is_sent(self, stores):
        updates = await self._drafted(stores)
        await comms.edit_update(updates, "c1.1", "Shipped the export.")
        await comms.send_update(updates, "c1.1", LATER)
        stored = await updates.read("c1.1")
        assert (stored.content, stored.state, stored.sent_at) == (
            "Shipped the export.",
            "sent",
            LATER,
        )

    async def test_a_sent_update_cannot_be_edited_or_sent_again(self, stores):
        updates = await self._drafted(stores)
        await comms.send_update(updates, "c1.1", NOW)
        with pytest.raises(ValueError):
            await comms.edit_update(updates, "c1.1", "late")
        with pytest.raises(ValueError):
            await comms.send_update(updates, "c1.1", LATER)
        assert (await updates.read("c1.1")).sent_at == NOW

    async def test_an_unknown_update_raises(self, stores):
        _, updates, _ = stores
        with pytest.raises(KeyError):
            await comms.edit_update(updates, "c1.9", "x")
