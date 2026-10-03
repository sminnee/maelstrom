"""``TranscriptHub``: one agent's frames to each socket, and what a slow one loses."""

import asyncio

from mael_orchestrator.hubs import LAGGING, TranscriptHub, TranscriptSubscriber
from mael_orchestrator.transcript_log import TranscriptFrame, TranscriptPartial


def frame(seq: int) -> TranscriptFrame:
    event = {"type": "transcript.update", "agentId": "ag1", "itemId": "i1"}
    return {"seq": seq, "event": {**event, "patch": {}}}


def partial(markdown: str) -> TranscriptPartial:
    return {
        "type": "transcript.partial",
        "agentId": "ag1",
        "itemId": "i1",
        "markdown": markdown,
    }


def unread(subscriber: TranscriptSubscriber) -> list:
    """Everything the reader would take, in order, without waiting for more."""

    async def drain() -> list:
        taken = []
        while True:
            try:
                taken.append(await asyncio.wait_for(subscriber.next(), 0.01))
            except TimeoutError:
                return taken

    return asyncio.run(drain())


def test_an_idle_reader_gets_a_partial():
    hub = TranscriptHub(queue_limit=2)
    with hub.subscribe("ag1") as subscriber:
        hub.push("ag1", [partial("a")])
        assert unread(subscriber) == [partial("a")]


def test_a_busy_reader_gets_its_frames_then_only_the_newest_partial():
    hub = TranscriptHub(queue_limit=2)
    with hub.subscribe("ag1") as subscriber:
        hub.push("ag1", [frame(1), *[partial("a" * n) for n in range(1, 50)]])
        assert unread(subscriber) == [frame(1), partial("a" * 49)]


def test_a_partial_never_makes_a_reader_lag():
    hub = TranscriptHub(queue_limit=2)
    with hub.subscribe("ag1") as subscriber:
        hub.push("ag1", [partial("a"), frame(1), partial("ab"), frame(2)])
        assert not subscriber.lagging
        assert unread(subscriber) == [frame(1), frame(2), partial("ab")]


def test_seq_frames_still_fill_the_queue_to_lagging():
    hub = TranscriptHub(queue_limit=2)
    with hub.subscribe("ag1") as subscriber:
        hub.push("ag1", [frame(1), partial("a"), frame(2), frame(3)])
        assert subscriber.lagging
        assert unread(subscriber) == [LAGGING]
