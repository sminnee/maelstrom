"""``TranscriptLog``: one agent's transcript, its seq, and the ring behind a resume."""

from maelstrom.orchestrator.transcript_log import TranscriptLog


def append(item_id: str, **fields) -> dict:
    return {
        "type": "transcript.append",
        "agentId": "ag1",
        "item": {"id": item_id, "type": "message", **fields},
    }


def update(item_id: str, **patch) -> dict:
    return {
        "type": "transcript.update",
        "agentId": "ag1",
        "itemId": item_id,
        "patch": patch,
    }


TRUNCATED = {"type": "transcript.truncated", "agentId": "ag1"}


def test_append_stamps_each_frame_and_builds_the_items():
    log = TranscriptLog()
    frames = [log.append(append("i1", markdown="a")), log.append(append("i2"))]
    assert [f["seq"] for f in frames] == [1, 2]
    assert frames[0]["event"] == append("i1", markdown="a")
    assert log.seq == 2
    assert [i["id"] for i in log.items] == ["i1", "i2"]


def test_an_update_patches_the_item_in_place_and_truncated_sets_the_flag():
    log = TranscriptLog()
    log.append(append("i1", markdown="a"))
    log.append(update("i1", markdown="b", stale=True))
    log.append(TRUNCATED)
    assert log.items == [
        {"id": "i1", "type": "message", "markdown": "b", "stale": True}
    ]
    assert log.snapshot() == {
        "items": log.items,
        "truncatedBefore": True,
        "seq": 3,
    }


def test_replay_from_gives_the_frames_after_the_seq_while_the_ring_holds_them():
    log = TranscriptLog(ring=3)
    for n in range(1, 6):
        log.append(append(f"i{n}"))  # the ring keeps 3, 4, 5
    assert [f["seq"] for f in log.replay_from(3) or []] == [4, 5]
    assert [f["seq"] for f in log.replay_from(2) or []] == [3, 4, 5]
    assert log.replay_from(5) == []
    assert log.replay_from(1) is None


def test_replay_from_a_seq_this_log_never_reached_is_a_snapshot():
    """A cursor from another life of the server means nothing here."""
    log = TranscriptLog()
    log.append(append("i1"))
    assert log.replay_from(7) is None


def test_an_empty_log_replays_nothing_from_zero():
    log = TranscriptLog()
    assert log.replay_from(0) == []
    assert log.snapshot() == {"items": [], "truncatedBefore": False, "seq": 0}


def test_the_snapshot_list_is_a_copy():
    log = TranscriptLog()
    log.append(append("i1"))
    log.snapshot()["items"].clear()
    assert len(log.items) == 1


def test_items_past_the_cap_drop_oldest_first_and_mark_the_transcript_truncated():
    log = TranscriptLog(max_items=2)
    for n in range(1, 4):
        log.append(append(f"i{n}"))
    assert [i["id"] for i in log.items] == ["i2", "i3"]
    assert log.truncated_before is True
    # The frames are the ring's business: every seq is still replayable.
    assert [f["seq"] for f in log.replay_from(0) or []] == [1, 2, 3]
