"""The Build queue: submits, slots, Build ends, cancels and restart recovery.

In-memory stores, a fake host for the git work, and a fake ``run`` for tangier
that records each argv and env and starts job 7, 8, … in turn. Its replies are
built from tangier output recorded under ``fixtures/``.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from mael_agent.host_ids import IdSpace
from mael_domain.config import MaelstromConfig
from mael_domain.merge_request import MergeRequestError
from mael_domain.merge_request_store import (
    InMemoryBuildStore,
    InMemoryMergeRequestStore,
)
from mael_domain.operation_store import InMemoryOperationStore
from mael_orchestrator.build_queue import BuildQueue
from mael_orchestrator.operations import OperationRunner

FIXTURES = Path(__file__).parent / "fixtures"
#: ``tangier gate run`` without ``--wait``, as tangier 0.3.5 printed it for job 212.
RUN_STARTED = (FIXTURES / "gate-run-started.txt").read_text()
#: A finished job off ``tangier gate status --json``: job 211, its one gate failed.
JOB_FAILED = json.loads((FIXTURES / "gate-job-failed.json").read_text())

BASE = "c" * 40
SHA_A, SHA_B = "a" * 40, "b" * 40


class FakeHost:
    def __init__(self, slots: int = 2) -> None:
        self.tip = BASE
        self.slots = slots
        self.checkouts: list[tuple[str, str]] = []

    def config(self, project: str) -> MaelstromConfig:
        return MaelstromConfig(merge="local", build_slots=self.slots)

    def origin_main(self, project: str) -> str:
        return self.tip

    def checkout(self, project: str, slot: str, sha: str) -> Path:
        self.checkouts.append((slot, sha))
        return Path("/projects") / project / slot

    def install(self, project: str, path: Path) -> None:
        pass


class FakeTangier:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self.next_job = 7
        self.status: dict = {}
        #: Runs as the run returns, before the queue reads the job id.
        self.on_run: Any = None
        #: ``tangier gate cancel``'s exit: 0 once the job has stopped.
        self.cancel_code = 0

    async def __call__(self, argv, cwd, env):
        self.calls.append((argv, env))
        if argv[:3] == ["tangier", "gate", "run"]:
            job, self.next_job = self.next_job, self.next_job + 1
            if self.on_run is not None:
                self.on_run(job)
            return 3, RUN_STARTED.replace("212", str(job))
        if argv[:3] == ["tangier", "gate", "cancel"]:
            return self.cancel_code, ""
        if argv[:3] == ["tangier", "gate", "status"]:
            return 0, json.dumps(self.status)
        return 0, ""

    def runs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls if argv[2] == "run"]

    def cancels(self) -> list[str]:
        return [argv[4] for argv, _ in self.calls if argv[2] == "cancel"]


class Queue:
    def __init__(self, *, slots: int = 2, backstop: float = 3600) -> None:
        self.requests = InMemoryMergeRequestStore()
        self.builds = InMemoryBuildStore()
        self.host = FakeHost(slots)
        self.tangier = FakeTangier()
        self.told: list[tuple[str, str]] = []
        self.deliver = True
        self.operations = self._runner()
        self.queue = self._queue(backstop)

    def _runner(self) -> OperationRunner:
        return OperationRunner(
            InMemoryOperationStore(), lambda events: None, lambda: "now", IdSpace()
        )

    def _queue(self, backstop: float = 3600) -> BuildQueue:
        async def tell(mr, event):
            self.told.append((event.type, event.attrs["build"]))
            return self.deliver

        return BuildQueue(
            self.requests,
            self.builds,
            operations=self.operations,
            tell=tell,
            host=self.host,  # type: ignore[arg-type]
            clock=lambda: "now",
            run=self.tangier,
            backstop=backstop,
        )

    async def submit(self, task="k1", sha=SHA_A, branch=None):
        return await self.queue.submit(
            "demo", task, branch or f"feat/{task}", sha, BASE
        )

    async def build(self, id: str):
        return await self.builds.read(id)

    async def settle(self, predicate, timeout: float = 2.0) -> None:
        async with asyncio.timeout(timeout):
            while not await predicate():
                await asyncio.sleep(0.005)

    async def gates_started(self, id: str) -> None:
        async def started():
            build = await self.build(id)
            return build is not None and build.job != 0

        await self.settle(started)

    async def state_is(self, id: str, state: str) -> None:
        async def reached():
            return (await self.build(id)).state == state

        await self.settle(reached)

    async def idle(self) -> None:
        """Every Build operation has ended."""

        async def none_running():
            return not any(
                self.operations.holds(f"build:demo/_build{n}") for n in (1, 2, 3)
            )

        await self.settle(none_running)


def ended(job: int, state: str, gates=None) -> dict:
    """The recorded job 211, as job ``job`` that ended ``state``."""
    found = {**JOB_FAILED, "id": job, "state": state, "log": f"/tmp/{job}.log"}
    if gates is not None:
        found["gates"] = gates
    elif state != "failed":
        found["gates"] = [{**g, "state": state, "code": 0} for g in JOB_FAILED["gates"]]
    return found


async def test_a_submit_queues_a_build_tells_it_and_starts_the_gates():
    q = Queue()
    mr, build = await q.submit()
    await q.gates_started(build.id)

    assert ("build.queued", build.id) in q.told
    running = await q.build(build.id)
    assert (running.state, running.slot, running.job) == ("running", "_build1", 7)
    argv, env = q.tangier.calls[0]
    assert argv == ["tangier", "gate", "run", "--all"]
    assert env["TANGIER_RUNNER_KIND"] == "queue"
    assert q.host.checkouts == [("_build1", SHA_A)]
    assert q.queue.current[("demo", "feat/k1")].status == "building"


async def test_more_builds_than_slots_wait_for_a_free_slot():
    q = Queue(slots=1)
    _, first = await q.submit("k1")
    _, second = await q.submit("k2")
    await q.gates_started(first.id)
    assert (await q.build(second.id)).state == "queued"

    q.queue.job_ended(ended(7, "passed"))
    await q.gates_started(second.id)
    assert (await q.build(second.id)).slot == "_build1"


async def test_a_moved_main_ends_the_build_stale_with_no_gate_run():
    q = Queue()
    q.host.tip = "e" * 40
    _, build = await q.submit()
    await q.state_is(build.id, "stale")
    await q.idle()

    assert ("build.rebase", build.id) in q.told
    assert q.tangier.runs() == []
    assert q.host.checkouts == []


async def test_a_job_end_passes_the_build_and_tells_it():
    q = Queue()
    _, build = await q.submit()
    await q.gates_started(build.id)
    q.queue.job_ended(ended(7, "passed"))
    await q.state_is(build.id, "passed")

    assert ("build.passed", build.id) in q.told
    assert (await q.build(build.id)).told == "passed"


async def test_a_failed_job_fails_the_build_with_its_gates_and_log():
    q = Queue()
    _, build = await q.submit()
    await q.gates_started(build.id)
    q.queue.job_ended(
        ended(7, "failed", [{"name": "test", "state": "failed", "code": 1}])
    )
    await q.state_is(build.id, "failed")
    await q.idle()

    failed = await q.build(build.id)
    assert failed.detail == "- test: failed (exit 1)\nLog: /tmp/7.log"
    assert ("build.failed", build.id) in q.told


async def test_a_second_submit_cancels_the_running_job_and_links_a_new_build():
    q = Queue(slots=1)
    mr, first = await q.submit(sha=SHA_A)
    await q.gates_started(first.id)

    mr2, second = await q.submit(sha=SHA_B)
    assert mr2.id == mr.id
    assert (await q.build(first.id)).state == "cancelled"
    assert q.tangier.cancels() == ["7"]
    # Tangier confirmed the cancel, so the slot frees with no job end.
    await q.gates_started(second.id)
    assert (await q.requests.read(mr.id)).build == second.id
    assert [t for t in q.told if t[1] == first.id] == [("build.queued", first.id)]


async def test_an_unconfirmed_cancel_holds_the_slot_until_the_job_ends():
    q = Queue(slots=1)
    q.tangier.cancel_code = 1
    _, first = await q.submit(sha=SHA_A)
    await q.gates_started(first.id)
    _, second = await q.submit(sha=SHA_B)
    await asyncio.sleep(0.05)
    assert (await q.build(second.id)).state == "queued"

    q.queue.job_ended(ended(7, "cancelled"))
    await q.gates_started(second.id)


async def test_the_late_end_of_a_superseded_job_tells_nothing():
    q = Queue(slots=2)
    q.tangier.cancel_code = 1
    _, first = await q.submit(sha=SHA_A)
    await q.gates_started(first.id)
    _, second = await q.submit(sha=SHA_B)
    await q.gates_started(second.id)
    told_before = list(q.told)

    q.queue.job_ended(ended(7, "passed"))
    # The end reached the cancelled Queue build: its operation ended.
    await q.settle(lambda: _none_waits_on(q, 7))
    assert (await q.build(first.id)).state == "cancelled"
    assert q.told == told_before


async def test_withdraw_cancels_the_running_job():
    q = Queue()
    mr, build = await q.submit()
    await q.gates_started(build.id)

    withdrawn = await q.queue.withdraw(mr.task)
    assert withdrawn.state == "withdrawn"
    assert q.tangier.cancels() == ["7"]
    assert (await q.build(build.id)).state == "cancelled"
    assert q.queue.current[("demo", "feat/k1")].status == "withdrawn"
    with pytest.raises(MergeRequestError):
        await q.queue.withdraw(mr.task)


async def test_a_start_cancels_a_running_build_and_queues_a_new_one():
    q = Queue()
    mr, build = await q.submit()
    await q.gates_started(build.id)
    await q.queue.stop()
    await q.operations.stop()

    # The next server life: new runner and queue, same stores.
    q.operations = q._runner()
    q.queue = q._queue()
    await q.queue.start()

    old = await q.build(build.id)
    assert (old.state, old.detail) == ("cancelled", "server restarted")
    assert q.tangier.cancels() == ["7"]
    linked = (await q.requests.read(mr.id)).build
    assert linked != build.id
    await q.gates_started(linked)
    assert (await q.build(linked)).sha == SHA_A


async def test_a_start_re_tells_a_linked_build_whose_tell_lags():
    q = Queue()
    q.deliver = False
    _, build = await q.submit()
    await q.gates_started(build.id)
    q.queue.job_ended(ended(7, "passed"))
    await q.state_is(build.id, "passed")
    await q.idle()
    assert (await q.build(build.id)).told == ""

    q.deliver = True
    q.told.clear()
    await q.queue.start()
    assert q.told == [("build.passed", build.id)]
    assert (await q.build(build.id)).told == "passed"


async def test_the_slot_frees_when_a_step_fails():
    q = Queue(slots=1)

    def broken(project, slot, sha):
        raise RuntimeError("unknown sha")

    q.host.checkout = broken  # type: ignore[method-assign]
    _, first = await q.submit("k1")
    _, second = await q.submit("k2")
    await q.state_is(first.id, "failed")
    await q.state_is(second.id, "failed")
    assert (await q.build(first.id)).detail == "checkout: unknown sha"


async def test_a_lost_job_end_fails_the_build_after_the_backstop():
    q = Queue(backstop=0.05)
    q.tangier.status = {"running": [], "queued": [], "recent": [], "not_found": [7]}
    _, build = await q.submit()
    await q.state_is(build.id, "failed")
    assert (await q.build(build.id)).detail == "gates: job end not seen"


async def _set(event: asyncio.Event) -> None:
    event.set()


async def _none_waits_on(q: Queue, job: int) -> bool:
    return job not in q.queue._waiting


async def test_a_job_that_ends_before_its_run_returns_still_ends_the_build():
    q = Queue()
    q.tangier.on_run = lambda job: q.queue.job_ended(ended(job, "passed"))
    _, build = await q.submit()
    await q.state_is(build.id, "passed")


async def test_the_backstop_ends_the_build_from_tangiers_own_record():
    q = Queue(backstop=0.05)
    q.tangier.status = {"running": [], "queued": [], "recent": [ended(7, "passed")]}
    _, build = await q.submit()
    await q.state_is(build.id, "passed")


async def test_a_linked_build_cancelled_outside_the_queue_is_told_as_failed():
    q = Queue()
    _, build = await q.submit()
    await q.gates_started(build.id)
    q.queue.job_ended(ended(7, "cancelled", gates=[]))
    await q.state_is(build.id, "cancelled")
    await q.idle()
    assert ("build.failed", build.id) in q.told
    assert q.queue.current[("demo", "feat/k1")].status == "failed"


async def test_a_project_whose_config_fails_holds_only_its_own_builds():
    q = Queue()

    def config(project):
        if project == "broken":
            raise ValueError("build_slots must be an integer of 1 or more, not 0")
        return MaelstromConfig(merge="local", build_slots=2)

    q.host.config = config  # type: ignore[method-assign]
    _, stuck = await q.queue.submit("broken", "k9", "feat/k9", SHA_A, BASE)
    _, build = await q.submit()
    await q.gates_started(build.id)
    assert (await q.build(stuck.id)).state == "queued"


async def test_a_cancel_between_steps_ends_the_operation_refused():
    q = Queue()
    gate = asyncio.Event()

    def slow_checkout(project, slot, sha):
        asyncio.run_coroutine_threadsafe(_set(gate), loop).result()
        return Path("/projects") / project / slot

    loop = asyncio.get_running_loop()
    q.host.checkout = slow_checkout  # type: ignore[method-assign]
    mr, build = await q.submit()
    await gate.wait()
    await q.queue.withdraw(mr.task)
    await q.idle()
    (op,) = await q.operations.store.list()
    assert op["state"] == "refused"
