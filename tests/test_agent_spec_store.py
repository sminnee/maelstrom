"""The spawn-record store, through both backends.

The records that let a daemon restart bring its agents back. Shaped like
``tests/test_env_store.py``: the same assertions run against the in-memory and
the JSON backend, because the Protocol is the contract, not the file layout.
"""

import pytest

from maelstrom.agent_model import AgentSpec
from maelstrom.agent_spec_store import InMemoryAgentSpecStore, JsonAgentSpecStore


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryAgentSpecStore()
    return JsonAgentSpecStore(tmp_path / "agents")


def spec(agent_id: str = "a1", **kw) -> AgentSpec:
    fields = {
        "agent_id": agent_id,
        "cwd": "/w",
        "session_id": "1be13567-68d9-46d4-a081-3813b552ba59",
    }
    fields.update(kw)
    return AgentSpec(**fields)


def test_a_written_record_reads_back_unchanged(store):
    written = spec(
        permission_mode="auto",
        model="opus",
        env={"MAEL_TASK_ID": "t1"},
        prompt="go",
        status="exited",
        exit_code=-9,
    )
    store.write(written)
    assert store.read("a1") == written


def test_reading_a_record_that_was_never_written_gives_none(store):
    assert store.read("nope") is None


def test_write_replaces_the_record_it_finds(store):
    store.write(spec(prompt="first"))
    store.write(spec(prompt="second"))
    read = store.read("a1")
    assert read is not None and read.prompt == "second"


def test_list_returns_every_record(store):
    store.write(spec("a1"))
    store.write(spec("a2"))
    assert {s.agent_id for s in store.list()} == {"a1", "a2"}


def test_list_is_empty_before_anything_is_written(store):
    assert store.list() == []


def test_delete_removes_the_record(store):
    store.write(spec())
    store.delete("a1")
    assert store.read("a1") is None
    assert store.list() == []


def test_deleting_a_record_that_is_not_there_is_not_an_error(store):
    store.delete("nope")


def test_the_json_store_keeps_one_file_per_agent(tmp_path):
    # The on-disk layout is what a human debugging a daemon reads, so it is part
    # of the contract, not an implementation detail.
    store = JsonAgentSpecStore(tmp_path / "agents")
    store.write(spec("a1"))
    assert (tmp_path / "agents" / "a1.json").is_file()


def test_the_json_store_ignores_a_file_it_cannot_parse(tmp_path):
    # A record truncated by a power cut must not stop the daemon restoring the
    # agents whose records survived.
    root = tmp_path / "agents"
    store = JsonAgentSpecStore(root)
    store.write(spec("a1"))
    (root / "broken.json").write_text("{not json")
    assert [s.agent_id for s in store.list()] == ["a1"]


def test_a_record_is_not_readable_by_other_users(tmp_path):
    """A record persists the caller's env, which may hold a secret.

    The socket contract puts no allowlist on ``env``, so whatever a client
    passes to ``start`` lands in the record. Before the record existed that env
    only lived in process memory.
    """
    store = JsonAgentSpecStore(tmp_path / "agents")
    store.write(spec(env={"SOME_TOKEN": "s3cret"}))
    assert (tmp_path / "agents" / "a1.json").stat().st_mode & 0o077 == 0
    assert (tmp_path / "agents").stat().st_mode & 0o077 == 0
