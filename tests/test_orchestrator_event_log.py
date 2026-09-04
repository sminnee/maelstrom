"""The seq-stamped event log behind the orchestrator server.

A port of ``web/src/fake-backend/store.ts``: events are applied through the
shared reducer, stamped with one global counter, and kept in a ring for
``replay_from``. A resume older than the ring gets a snapshot instead.
"""

from maelstrom.orchestrator.event_log import EventLog

NOW = "2026-09-01T00:00:00Z"


def upsert(task_id: str, status: str = "todo") -> dict:
    return {
        "type": "upsert",
        "kind": "task",
        "entity": {"id": task_id, "status": status},
    }


def test_append_stamps_seq_and_applies_through_the_reducer():
    log = EventLog()
    frames = log.append([upsert("T-1"), upsert("T-2")], NOW)
    assert [f["seq"] for f in frames] == [1, 2]
    assert all(f["ts"] == NOW for f in frames)
    assert log.seq == 2
    assert set(log.state["world"]["tasks"]) == {"T-1", "T-2"}


def test_replay_from_returns_the_frames_after_the_given_seq():
    log = EventLog()
    log.append([upsert("T-1"), upsert("T-2"), upsert("T-3")], NOW)
    assert [f["seq"] for f in log.replay_from(1) or []] == [2, 3]
    assert log.replay_from(3) == []


def test_replay_from_the_seq_just_before_the_ring_still_works():
    """``from`` is the last seq the client applied, so the ring must hold ``from + 1``."""
    log = EventLog(ring_size=3)
    log.append([upsert(f"T-{i}") for i in range(1, 6)], NOW)  # keeps 3, 4, 5
    assert [f["seq"] for f in log.replay_from(2) or []] == [3, 4, 5]
    assert log.replay_from(1) is None


def test_replay_from_an_empty_log_is_a_snapshot():
    assert EventLog().replay_from(0) is None


def test_snapshot_frame_carries_the_world_at_the_current_seq_and_no_transcripts():
    """The server keeps no transcript, so a snapshot cannot carry one.

    A transcript event still stamps a seq — it was published — but nothing
    here accumulates it.
    """
    log = EventLog()
    log.append(
        [
            upsert("T-1"),
            {
                "type": "transcript.append",
                "agentId": "a1",
                "item": {
                    "id": "a1-1",
                    "ts": NOW,
                    "type": "message",
                    "role": "user",
                    "markdown": "hi",
                },
            },
        ],
        NOW,
    )
    frame = log.snapshot_frame(NOW)
    assert frame["seq"] == 2
    assert frame["event"]["type"] == "snapshot"
    assert "T-1" in frame["event"]["world"]["tasks"]
    assert "transcripts" not in frame["event"]
