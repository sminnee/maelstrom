"""``notices_for``: which change notices a batch of server events amounts to."""

from maelstrom.orchestrator.notices import merge_notices, notices_for


def upsert(kind: str, entity_id: str, **fields) -> dict:
    return {"type": "upsert", "kind": kind, "entity": {"id": entity_id, **fields}}


def test_an_upsert_and_a_remove_each_name_their_kind_and_id():
    events = [
        upsert("task", "northwind/NORT-7"),
        {"type": "remove", "kind": "task", "id": "northwind/NORT-8"},
        upsert("agent", "ag1"),
        upsert("desk", "task:northwind/NORT-7"),
    ]
    assert notices_for(events) == {
        "task": {"northwind/NORT-7", "northwind/NORT-8"},
        "agent": {"ag1"},
        "desk": {"task:northwind/NORT-7"},
    }


def test_transcript_events_and_errors_name_nothing():
    events = [
        {"type": "transcript.append", "agentId": "ag1", "item": {"id": "i1"}},
        {"type": "transcript.update", "agentId": "ag1", "itemId": "i1", "patch": {}},
        {"type": "transcript.truncated", "agentId": "ag1"},
        {"type": "error", "message": "x"},
    ]
    assert notices_for(events) == {}


def test_a_comment_names_its_document():
    assert notices_for([upsert("comment", "c1", documentId="d1")]) == {
        "document": {"d1"}
    }
    assert notices_for([{"type": "remove", "kind": "comment", "id": "c1"}]) == {
        "document": set()
    }


def test_merging_keeps_every_id_and_an_empty_kind():
    into = {"task": {"a"}}
    merge_notices(into, {"task": {"b"}, "document": set()})
    assert into == {"task": {"a", "b"}, "document": set()}
