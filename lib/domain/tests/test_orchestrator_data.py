"""The orchestrator data store: one contract on every backend, and the column it shares."""

import pytest
from pydantic import ValidationError

from mael_domain.agent_store import SqliteAgentStore, new_agent_record
from mael_domain.orchestrator_data import (
    EMPTY,
    InMemoryOrchestratorDataStore,
    OrchestratorData,
    SqliteOrchestratorDataStore,
)
from mael_domain.state_db.migrate import open_state_db


@pytest.fixture
async def db():
    state_db = open_state_db(":memory:")
    await state_db.migrate()
    yield state_db
    state_db.close()


@pytest.fixture(params=["memory", "sqlite"])
def store(request, db):
    if request.param == "memory":
        return InMemoryOrchestratorDataStore()
    return SqliteOrchestratorDataStore(db)


class TestOrchestratorDataStoreContract:
    async def test_an_update_reads_back_and_lists(self, store):
        await store.update("ag1", end_on="pr.merged")
        assert await store.read("ag1") == OrchestratorData(end_on="pr.merged")
        assert await store.read("ag2") == EMPTY
        assert await store.list() == {"ag1": OrchestratorData(end_on="pr.merged")}

    async def test_data_set_back_to_empty_is_not_listed(self, store):
        await store.update("ag1", end_on="pr.merged")
        await store.update("ag1", end_on=None)
        assert await store.read("ag1") == EMPTY
        assert await store.list() == {}

    async def test_an_invalid_value_raises_and_stores_nothing(self, store):
        with pytest.raises(ValidationError):
            await store.update("ag1", end_on="pr.merge")
        assert await store.read("ag1") == EMPTY

    async def test_an_unknown_field_raises(self, store):
        with pytest.raises(TypeError):
            await store.update("ag1", end_onn=None)


def a_record(agent_id: str = "ag1") -> dict:
    return new_agent_record(
        agent_id,
        harness="claude",
        task_session_id="s1",
        task_id="",
        cwd="/w",
        model="",
        mode="",
        started_at="",
    )


class TestTheSharedRow:
    """``body`` and ``orchestrator_data`` share the ``agents`` row."""

    async def test_a_body_save_keeps_the_orchestrator_data(self, db):
        records = SqliteAgentStore(db)
        data = SqliteOrchestratorDataStore(db)
        record = a_record()
        await records.save(record)
        await data.update("ag1", end_on="pr.merged")
        assert await records.read("ag1") == record
        await records.save({**record, "status": "ended"})
        assert (await data.read("ag1")).end_on == "pr.merged"
        assert (await records.read("ag1"))["status"] == "ended"

    async def test_a_row_that_does_not_validate_reads_as_empty(self, db):
        await db.upsert("agents", "ag1", orchestrator_data='{"end_on": "soon"}')
        assert await SqliteOrchestratorDataStore(db).read("ag1") == EMPTY

    async def test_data_for_an_agent_with_no_record_is_no_record(self, db):
        records = SqliteAgentStore(db)
        data = SqliteOrchestratorDataStore(db)
        await data.update("ag9", end_on="pr.merged")
        assert await records.list() == []
        assert await records.read("ag9") is None
        assert (await records.changed_since(0))[0] == []
        await records.save(a_record("ag9"))
        assert await records.read("ag9") == a_record("ag9")
        assert (await data.read("ag9")).end_on == "pr.merged"
