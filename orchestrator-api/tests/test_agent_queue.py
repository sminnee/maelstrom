"""The Agent queue's rules, through its runner where a rule has one."""

from __future__ import annotations

import asyncio
from typing import Any

from mael_domain.document_tags import CompactRequest
from mael_domain.external_event import ExternalEvent
from mael_orchestrator.agent_queue import (
    AgentQueue,
    AgentStep,
    Compact,
    Resume,
    Say,
    Stop,
    enqueue,
    first_turn,
)

COMPACTED = ExternalEvent("orchestrator", "session.compacted", {}, "Compacted.")


def waiter() -> asyncio.Future[dict[str, Any] | None]:
    return asyncio.get_running_loop().create_future()


def dropped(why: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": "invalid", "message": f"the step was dropped: {why}"},
    }


async def settled() -> None:
    """Let the runner take what it can before the next turn end."""
    for _ in range(10):
        await asyncio.sleep(0)


class Runner:
    """Records each step; a compact waits for its outcome."""

    def __init__(self, fail: set[str] | None = None, backstop: float = 5.0) -> None:
        self.ran: list[Any] = []
        self.fail = fail or set()
        self.idle = asyncio.Event()
        self.queue = AgentQueue(
            "ag1", self.run, backstop=backstop, on_idle=lambda _: self.idle.set()
        )

    async def run(self, queue: AgentQueue, step: AgentStep) -> bool:
        if isinstance(step, Compact):
            step.mark_sent()
            self.ran.append("/compact")
            if "compact" in self.fail:
                return False
            outcome = await queue.outcome(step)
            self.ran.append(outcome and outcome.body)
            return True
        self.ran.append(step.turn if isinstance(step, Say) else step.kind)
        return step.kind not in self.fail

    async def until_idle(self) -> list[Any]:
        await asyncio.wait_for(self.idle.wait(), 1)
        return self.ran


# --- what is queued ---------------------------------------------------------------


def test_the_last_compaction_request_of_a_turn_wins():
    first = Compact(request=CompactRequest(min_tokens=1), gated=True)
    last = Compact(request=CompactRequest(min_tokens=2), gated=True)
    turn = Say("a")

    assert enqueue([first, turn], last) == [last, turn]


def test_an_agents_own_stop_makes_a_merges_stop_its_own():
    merge = Stop(merge=True, gated=True)

    steps = enqueue([merge], Stop(gated=True))

    assert steps == [merge] and not merge.merge


def test_a_resume_takes_a_say_as_its_first_turn():
    turn, rest = Say("a"), Say("b")

    assert first_turn([turn, rest]) == (turn, [rest])


def test_a_gated_step_is_no_first_turn():
    gated = Compact(gated=True)

    assert first_turn([gated]) == (None, [gated])


async def test_a_resume_marks_a_compact_sent_and_leaves_it_to_wait():
    compact = Compact()

    assert first_turn([compact]) == (compact, [compact])
    assert compact.sent


# --- the runner -------------------------------------------------------------------


async def test_steps_run_in_order():
    runner = Runner()

    runner.queue.put(Say("a"), Say("b"))

    assert await runner.until_idle() == ["a", "b"]


async def test_a_gated_step_waits_for_a_live_turn_end():
    runner = Runner()
    runner.queue.put(Stop(gated=True))
    await asyncio.sleep(0)
    before = list(runner.ran)

    await runner.queue.turn_ended(live=True)

    assert before == []
    assert await runner.until_idle() == ["stop"]


async def test_a_live_turn_end_returns_once_the_compact_is_sent():
    """Else the stream could read the outcome before the step waits for it."""
    runner = Runner()
    runner.queue.put(Compact(gated=True))

    await runner.queue.turn_ended(live=True)

    assert runner.ran == ["/compact"]


async def test_turns_queued_behind_a_compact_follow_its_outcome():
    runner = Runner()
    runner.queue.put(Compact(gated=True), Say("a"))

    await runner.queue.turn_ended(live=True)
    runner.queue.settle(COMPACTED)

    assert await runner.until_idle() == ["/compact", "Compacted.", "a"]


async def test_a_replayed_turn_end_drops_the_gated_steps():
    runner = Runner()
    runner.queue.put(Stop(gated=True))

    await runner.queue.turn_ended(live=False)

    assert await runner.until_idle() == []


async def test_the_end_wins_over_a_compaction_request_at_the_turn_end():
    runner = Runner()
    runner.queue.put(Compact(gated=True), Say("a"), Stop(gated=True))

    await runner.queue.turn_ended(live=True)

    assert await runner.until_idle() == ["a", "stop"]


async def test_a_cancelled_stop_lets_the_compaction_request_run():
    """A user message takes a merge's stop out before the turn ends."""
    runner = Runner()
    runner.queue.put(Compact(gated=True), Stop(merge=True, gated=True))

    runner.queue.remove(lambda s: isinstance(s, Stop) and s.merge)
    await runner.queue.turn_ended(live=True)

    assert runner.ran == ["/compact"]


async def test_a_say_told_during_a_compact_goes_ahead_of_a_gated_stop():
    """A merge told while the agent compacts reaches it before the stop."""
    runner = Runner()
    runner.queue.put(Compact(), Stop(gated=True), Say("a"))
    await asyncio.sleep(0)

    runner.queue.settle(COMPACTED)
    await settled()
    told = list(runner.ran)
    await runner.queue.turn_ended(live=True)

    assert told == ["/compact", "Compacted.", "a"]
    assert await runner.until_idle() == ["/compact", "Compacted.", "a", "stop"]


async def test_only_a_compact_or_a_say_holds_turns():
    runner = Runner()
    runner.queue.put(Stop(gated=True))
    with_stop = runner.queue.holds_turns

    runner.queue.put(Compact(gated=True))

    assert (with_stop, runner.queue.holds_turns) == (False, True)


async def test_a_failed_resume_drops_the_rest_and_tells_its_waiters():
    runner = Runner(fail={"resume"})
    done = waiter()

    runner.queue.put(Resume({}), Say("a", done=done))

    assert await runner.until_idle() == ["resume"]
    assert await done == dropped("resume failed")


async def test_a_refused_compact_still_delivers_the_turns():
    runner = Runner(fail={"compact"})

    runner.queue.put(Compact(), Say("a"))

    assert await runner.until_idle() == ["/compact", "a"]


async def test_a_compact_with_no_outcome_releases_at_the_backstop():
    runner = Runner(backstop=0.01)

    runner.queue.put(Compact(), Say("a"))

    assert await runner.until_idle() == ["/compact", None, "a"]


async def test_a_drop_tells_a_waiting_caller_and_runs_nothing_more():
    runner = Runner()
    done = waiter()
    runner.queue.put(Compact(), Say("a", done=done))
    await asyncio.sleep(0)

    runner.queue.drop()

    assert await runner.until_idle() == ["/compact"]
    assert await done == dropped("the agent stopped")
