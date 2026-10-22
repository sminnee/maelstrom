"""Tests for mael_domain.comm_store backends."""

import pytest

from mael_domain.comm_store import (
    Comm,
    CommItem,
    CommStore,
    CommUpdate,
    CommUpdateStore,
    InMemoryCommStore,
    InMemoryCommUpdateStore,
    SqliteCommStore,
    SqliteCommUpdateStore,
)

NOW = "2026-10-08T09:00:00+00:00"

WHOLE = Comm(
    id="c1",
    title="Invoice export",
    content="Jo asked for CSV.",
    recipients=["#cs", "jo@acme.test"],
    created_at=NOW,
    closed_at=NOW,
    category="release",
    project="maelstrom",
)


@pytest.fixture(params=["memory", "sqlite"])
def comms(request, state_db):
    """Run each contract test against every backend."""
    if request.param == "memory":
        return InMemoryCommStore()
    return SqliteCommStore(state_db)


@pytest.fixture(params=["memory", "sqlite"])
def updates(request, state_db):
    """Run each contract test against every backend."""
    if request.param == "memory":
        return InMemoryCommUpdateStore()
    return SqliteCommUpdateStore(state_db)


class TestCommStoreContract:
    """Shared contract both backends must satisfy."""

    def test_every_backend_subclasses_the_contract(self):
        """Explicit, so a reader sees which classes claim it."""
        assert issubclass(InMemoryCommStore, CommStore)
        assert issubclass(SqliteCommStore, CommStore)

    async def test_save_then_read_round_trips_every_field(self, comms):
        await comms.save(WHOLE)
        assert await comms.read("c1") == WHOLE

    async def test_read_of_an_unknown_id_is_none(self, comms):
        assert await comms.read("c9") is None

    async def test_a_read_does_not_share_a_mutable_reference(self, comms):
        await comms.save(WHOLE)
        read = await comms.read("c1")
        assert read is not None
        read.recipients.append("#ops")
        assert await comms.read("c1") == WHOLE

    async def test_next_id_counts_up_from_c1_and_list_is_in_number_order(self, comms):
        for _ in range(11):
            async with comms.transact():
                await comms.save(Comm(id=await comms.next_id(), title="t"))
        # c10 sorts before c2 as text; the sequence and the list are numeric.
        assert [c.id for c in await comms.list()] == [f"c{n}" for n in range(1, 12)]

    async def test_changed_since_holds_only_the_comms_written_after(self, comms):
        await comms.save(Comm(id="c1", title="a"))
        await comms.save(Comm(id="c2", title="b"))
        mark = await comms.revision()
        await comms.save(Comm(id="c1", title="a2"))
        changes = await comms.changed_since(mark)
        assert [c.title for c in changes.comms] == ["a2"]
        assert changes.revision == await comms.revision()

    async def test_saving_an_unchanged_comm_is_not_a_change(self, comms):
        await comms.save(WHOLE)
        mark = await comms.revision()
        await comms.save(WHOLE)
        assert (await comms.changed_since(mark)).comms == []


class TestCommUpdateStoreContract:
    """Shared contract both backends must satisfy."""

    def test_every_backend_subclasses_the_contract(self):
        """Explicit, so a reader sees which classes claim it."""
        assert issubclass(InMemoryCommUpdateStore, CommUpdateStore)
        assert issubclass(SqliteCommUpdateStore, CommUpdateStore)

    async def test_save_then_read_round_trips_every_field(self, updates):
        update = CommUpdate(
            id="c1.1",
            comm_id="c1",
            created_at=NOW,
            items=[CommItem("p", "t1", "done"), CommItem("q", "t2", "merged")],
            content="- Task t1 — done",
            state="sent",
            sent_at=NOW,
        )
        await updates.save(update)
        assert await updates.read("c1.1") == update

    async def test_read_of_an_unknown_id_is_none(self, updates):
        assert await updates.read("c1.9") is None

    async def test_save_replaces_the_row_with_its_id(self, updates):
        await updates.save(CommUpdate(id="c1.1", comm_id="c1", content="first"))
        await updates.save(CommUpdate(id="c1.1", comm_id="c1", content="second"))
        assert [u.content for u in await updates.list()] == ["second"]

    async def test_list_is_in_comm_order_then_oldest_first(self, updates):
        for id in ("c10.1", "c2.10", "c2.2", "c1.1"):
            await updates.save(CommUpdate(id=id, comm_id=id.split(".")[0]))
        assert [u.id for u in await updates.list()] == [
            "c1.1",
            "c2.2",
            "c2.10",
            "c10.1",
        ]

    async def test_for_comm_holds_only_that_comms_updates_oldest_first(self, updates):
        for id in ("c2.10", "c1.1", "c2.2"):
            await updates.save(CommUpdate(id=id, comm_id=id.split(".")[0]))
        assert [u.id for u in await updates.for_comm("c2")] == ["c2.2", "c2.10"]
