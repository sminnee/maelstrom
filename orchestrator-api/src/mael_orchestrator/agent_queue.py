"""The **Agent queue**: the ordered steps the orchestrator runs on one agent.

See ``docs/dev/orchestrator-server.md``, "The agent queue". The decisions
(what runs next, what a stop removes, what a failure drops) are functions over
the step list. :class:`AgentQueue` runs the steps one at a time, and the server
supplies what each step does.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from mael_domain.document_tags import CompactRequest
from mael_domain.external_event import ExternalEvent

log = logging.getLogger(__name__)


def _refusal(message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": "invalid", "message": message}}


@dataclass(eq=False, kw_only=True)
class _Step:
    """What every step has.

    A ``gated`` step waits for the agent's current turn to end. ``done``
    receives the host's refusal, or ``None``, for a caller that waits.
    """

    kind: ClassVar[str]
    gated: bool = False
    done: asyncio.Future[dict[str, Any] | None] | None = field(default=None, repr=False)

    def finish(self, refused: dict[str, Any] | None) -> None:
        """Tell a waiting caller how the step went."""
        if self.done is not None and not self.done.done():
            self.done.set_result(refused)


@dataclass(eq=False)
class Say(_Step):
    """One turn: an External event, or a plain turn such as a jig instruction."""

    kind: ClassVar[str] = "say"
    turn: ExternalEvent | str
    attachments: list[dict[str, str]] | None = None


@dataclass(eq=False)
class Compact(_Step):
    """A compaction, and the wait for its outcome.

    ``request`` is the agent's own **Compaction request**; ``relay`` is the
    user's ``agent.say`` with ``/compact``. Neither means ``/compact`` went
    already, as a resume's first turn. With ``reply`` the agent is told how
    it went. ``outcome`` receives the reply once ``/compact`` is sent.
    """

    kind: ClassVar[str] = "compact"
    request: CompactRequest | None = None
    relay: dict[str, Any] | None = None
    reply: bool = False
    sent: bool = False
    outcome: asyncio.Future[ExternalEvent] | None = field(default=None, repr=False)

    def mark_sent(self) -> None:
        """``/compact`` goes to the host: the next outcome is this step's."""
        self.sent = True
        if self.outcome is None:
            self.outcome = asyncio.get_running_loop().create_future()


@dataclass(eq=False)
class Resume(_Step):
    """Start the exited agent again; ``payload`` is the host's ``resume``."""

    kind: ClassVar[str] = "resume"
    payload: dict[str, Any]


@dataclass(eq=False)
class Stop(_Step):
    """A **Terminate**. ``merge`` marks the stop a **Conditional end** queued.

    A user message takes a merge's stop out again; an agent's own
    ``<end-session>`` makes it the agent's.
    """

    kind: ClassVar[str] = "stop"
    merge: bool = False


AgentStep = Say | Compact | Resume | Stop


# --- the decisions --------------------------------------------------------------


def enqueue(steps: list[AgentStep], step: AgentStep) -> list[AgentStep]:
    """``steps`` with ``step`` added, by the rules in "The agent queue".

    A second stop is not added, but an agent's own stop makes a merge's stop
    its own.
    """
    if isinstance(step, Stop):
        if stops := [s for s in steps if isinstance(s, Stop)]:
            if not step.merge:
                stops[0].merge = False
            return steps
    if isinstance(step, Compact) and step.gated:
        for i, s in enumerate(steps):
            if isinstance(s, Compact) and s.gated:
                return [*steps[:i], step, *steps[i + 1 :]]
    if isinstance(step, Say):
        for i, s in enumerate(steps):
            if isinstance(s, Stop) and s.gated:
                return [*steps[:i], step, *steps[i:]]
    return [*steps, step]


def runnable(steps: list[AgentStep]) -> AgentStep | None:
    """The step to run next: the head, unless it waits for the turn end."""
    if steps and not steps[0].gated:
        return steps[0]
    return None


def open_gates(steps: list[AgentStep]) -> list[AgentStep]:
    """A live turn ended: each gated step may now run, in its place.

    The end wins: with a stop queued, each compact not yet sent is dropped.
    """
    if any(isinstance(s, Stop) for s in steps):
        steps = [s for s in steps if not isinstance(s, Compact) or s.sent]
    for step in steps:
        step.gated = False
    return steps


def drop_gated(steps: list[AgentStep]) -> list[AgentStep]:
    """A replayed turn ended: the run that read it live acted on its markers."""
    return [s for s in steps if not s.gated]


def after_failure(failed: AgentStep, steps: list[AgentStep]) -> list[AgentStep]:
    """What is left when ``failed`` fails.

    A failed resume drops the rest: no agent will hear it. Any other step
    keeps the rest: a compact that did not run still delivers the turns.
    """
    if isinstance(failed, Resume):
        return []
    return steps


def first_turn(steps: list[AgentStep]) -> tuple[Say | Compact | None, list[AgentStep]]:
    """The turn a resume gives its child at spawn, and the steps left.

    A compact at the head stays, marked sent: it waits for the outcome. A say
    at the head is taken off. Else the child gets no turn, and the host picks
    one.
    """
    head = runnable(steps)
    if isinstance(head, Compact):
        head.mark_sent()
        return head, steps
    if isinstance(head, Say):
        return head, steps[1:]
    return None, steps


def holds_turns(running: AgentStep | None, steps: list[AgentStep]) -> bool:
    """Whether a turn told now must wait in the queue.

    A compact holds turns, and a queued say keeps its place ahead of them. A
    gated stop and a resume hold none: the agent hears the turns before it
    stops, and a tell to an agent not yet resumed is refused, as before.
    """
    return any(isinstance(s, Compact | Say) for s in (running, *steps))


def compacting(running: AgentStep | None, steps: list[AgentStep]) -> Compact | None:
    """The sent compact that the next boundary or refusal settles, if any."""
    for step in (running, *steps[:1]):
        if (
            isinstance(step, Compact)
            and step.outcome is not None
            and not step.outcome.done()
        ):
            return step
    return None


def _sending(running: AgentStep | None, steps: list[AgentStep]) -> bool:
    """Whether a compact runs, or is next, and has not gone to the host yet."""
    return any(
        isinstance(s, Compact) and not s.sent for s in (running, runnable(steps))
    )


# --- the runner -------------------------------------------------------------------


RunStep = Callable[["AgentQueue", AgentStep], Awaitable[bool]]


class AgentQueue:
    """One agent's queue and its runner.

    One runner task runs the steps in order. ``run`` does one step and says
    whether it went. A compact step parks the runner on its ``outcome``
    until :meth:`settle` or the backstop. Every step that leaves the list
    without running tells its waiting caller.
    """

    def __init__(
        self,
        agent_id: str,
        run: RunStep,
        *,
        backstop: float,
        on_idle: Callable[[AgentQueue], None],
    ) -> None:
        self.agent_id = agent_id
        self._steps: list[AgentStep] = []
        self.running: AgentStep | None = None
        self._run = run
        self._backstop = backstop
        self._on_idle = on_idle
        self._runner: asyncio.Task[None] | None = None
        #: Set when no compact waits to be sent. A live turn end waits on it.
        self._sent = asyncio.Event()
        self._sent.set()

    @property
    def steps(self) -> list[AgentStep]:
        return list(self._steps)

    @property
    def idle(self) -> bool:
        return not self._steps and self.running is None

    @property
    def holds_turns(self) -> bool:
        return holds_turns(self.running, self._steps)

    def put(self, *steps: AgentStep) -> None:
        """Add ``steps`` in order, and run what can run."""
        new = self._steps
        for step in steps:
            new = enqueue(new, step)
        self._replace(new, "not queued")
        self._kick()

    def remove(self, unwanted: Callable[[AgentStep], bool]) -> None:
        """Take out each queued step that ``unwanted`` names."""
        self._replace([s for s in self._steps if not unwanted(s)], "taken out")
        self._kick()

    def take_first_turn(self) -> Say | Compact | None:
        """The turn for a resume's child; see :func:`first_turn`."""
        turn, self._steps = first_turn(self._steps)
        return turn

    async def turn_ended(self, *, live: bool) -> None:
        """The agent's turn ended; on a live one, wait until a compact is sent.

        The wait keeps the stream from reading the compact's outcome before
        the step that waits for it runs.
        """
        if not live:
            self._replace(drop_gated(self._steps), "replayed")
            self._kick()
            return
        self._replace(open_gates(self._steps), "the agent is stopping")
        if _sending(self.running, self._steps):
            self._sent.clear()
        self._kick()
        await self._sent.wait()

    def settle(self, reply: ExternalEvent) -> None:
        """A boundary or a refusal came: the sent compact is over."""
        step = compacting(self.running, self._steps)
        if step is not None and step.outcome is not None:
            step.outcome.set_result(reply)

    async def outcome(self, step: Compact) -> ExternalEvent | None:
        """Wait for the compact's outcome; ``None`` at the backstop."""
        assert step.outcome is not None
        self._sent.set()
        try:
            return await asyncio.wait_for(asyncio.shield(step.outcome), self._backstop)
        except TimeoutError:
            log.warning("agent %s: compaction hold timed out", self.agent_id)
            return None

    def drop(self) -> None:
        """The agent exited: discard each step; no agent will hear them."""
        if self.running is not None:
            self.running.finish(_refusal("the agent stopped"))
        self._replace([], "the agent stopped")
        runner = self._runner
        if runner is not None and runner is not asyncio.current_task():
            runner.cancel()

    async def close(self) -> None:
        """Stop the runner: the server stops."""
        self.drop()
        if self._runner is not None:
            await asyncio.gather(self._runner, return_exceptions=True)

    def _replace(self, new: list[AgentStep], why: str) -> None:
        """Make ``new`` the step list; tell each step that left it unrun."""
        gone = [s for s in self._steps if not any(s is n for n in new)]
        self._steps = new
        if gone:
            log.info("agent %s: %d step(s) dropped: %s", self.agent_id, len(gone), why)
        for step in gone:
            step.finish(_refusal(f"the step was dropped: {why}"))

    def _kick(self) -> None:
        if self._runner is None and runnable(self._steps) is not None:
            self._runner = asyncio.create_task(self._loop())
        elif self._runner is None and self.idle:
            self._sent.set()
            self._on_idle(self)

    async def _loop(self) -> None:
        try:
            while (step := runnable(self._steps)) is not None:
                self._steps.remove(step)
                self.running = step
                try:
                    ok = await self._run(self, step)
                except Exception:
                    log.exception("agent %s: %s step failed", self.agent_id, step.kind)
                    step.finish(_refusal("step failed"))
                    ok = False
                finally:
                    self.running = None
                    if not _sending(None, self._steps):
                        self._sent.set()
                if not ok:
                    self._replace(
                        after_failure(step, self._steps), f"{step.kind} failed"
                    )
        finally:
            self._runner = None
            self._sent.set()
            if self.idle:
                self._on_idle(self)
