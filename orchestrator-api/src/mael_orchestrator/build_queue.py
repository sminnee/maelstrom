"""The **Build queue**: it runs each Merge request's Builds in the Build worktrees.

The server owns one. A submit updates the task's Merge request and queues a
Build; :meth:`BuildQueue.kick` starts the oldest linked Builds in the free
slots of each ``merge: local`` project. A running Build is one Operation, from
the slot claim to the gate job's end; its busy key holds the slot. The Gate
watch hands each job end to :meth:`BuildQueue.job_ended`. See
``docs/dev/build-queue.md``.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mael_domain import merge_request as model
from mael_domain.config import MaelstromConfig, load_config_or_default
from mael_domain.external_event import ExternalEvent, build_event
from mael_domain.merge_request import Build, Current, MergeRequest
from mael_domain.merge_request_store import BuildStore, MergeRequestStore
from mael_domain.worktree import (
    ensure_build_worktree,
    ensure_fetched,
    prepare_build,
    run_git,
    run_install_cmd,
)
from mael_domain.worktree_model import MAIN_BRANCH, MAIN_WORKTREE_FOLDER
from mael_domain.worktree_steps import StepEnd, StepHook

from .gate_table import UNFINISHED as JOB_UNFINISHED
from .operations import OperationPlan, OperationRunner
from .sources import CloseBlocked

log = logging.getLogger(__name__)

#: The gates a Build runs. ``@merge`` replaces it when tangier ships gate sets
#: (task q944.5); ADR 0004 forbids a second gate list in ``.maelstrom.yaml``.
BUILD_GATE_ARGS: tuple[str, ...] = ("--all",)
#: The runner kind tangier records for a Build's gate runs.
RUNNER_KIND = "queue"
#: How long a gate job may go unseen before the queue asks tangier about it.
BACKSTOP_SECS = 15 * 60
#: The Build's steps, in order.
STEPS = ("base", "checkout", "install", "gates")

#: How many job ends no Queue build claimed are kept. Every job on the
#: machine ends through the Gate watch, the agents' jobs included.
UNCLAIMED_KEPT = 64

_JOB_LINE = re.compile(r"^job (\d+):", re.MULTILINE)

#: ``(argv, cwd, env)`` → ``(exit code, stdout and stderr)``.
Run = Callable[
    [list[str], Path | None, dict[str, str] | None], Awaitable[tuple[int, str]]
]
#: Tells a Build's chaperone; true when an agent took the event.
Tell = Callable[[MergeRequest, ExternalEvent], Awaitable[bool]]


async def run_process(
    argv: list[str], cwd: Path | None, env: dict[str, str] | None
) -> tuple[int, str]:
    """Run ``argv`` to its end; its exit code and its merged output."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace")


class BuildHost:
    """The blocking git and install work of a Build, on real projects.

    Each method runs in a thread. A test gives the queue a fake.
    """

    def __init__(self, projects_dir: Path) -> None:
        self.projects_dir = projects_dir

    def config(self, project: str) -> MaelstromConfig:
        return load_config_or_default(
            self.projects_dir / project / MAIN_WORKTREE_FOLDER
        )

    def origin_main(self, project: str) -> str:
        """Fetch, then the SHA ``origin/main`` names."""
        path = self.projects_dir / project
        ensure_fetched(path, max_age=0)
        return run_git(
            ["rev-parse", f"origin/{MAIN_BRANCH}"], cwd=path, quiet=True
        ).stdout.strip()

    def checkout(self, project: str, slot: str, sha: str) -> Path:
        """The Build worktree ``slot``, reset to ``sha``."""
        path = ensure_build_worktree(self.projects_dir / project, slot)
        prepare_build(path, sha)
        return path

    def install(self, project: str, path: Path) -> None:
        run_install_cmd(path, (project, path.name))

    def slot_path(self, project: str, slot: str) -> Path:
        return self.projects_dir / project / slot


def _slot_key(project: str, slot: str) -> str:
    """The busy key of a Build worktree."""
    return f"build:{project}/{slot}"


class _Stale(Exception):
    pass


class _Cancelled(Exception):
    pass


class _GatesFailed(Exception):
    pass


class BuildQueue:
    """The server's Build queue: the one writer of Merge requests and Builds."""

    def __init__(
        self,
        requests: MergeRequestStore,
        builds: BuildStore,
        *,
        operations: OperationRunner,
        tell: Tell,
        host: BuildHost,
        clock: Callable[[], str],
        run: Run = run_process,
        on_change: Callable[[], None] = lambda: None,
        backstop: float = BACKSTOP_SECS,
    ) -> None:
        self.requests = requests
        self.builds = builds
        self.operations = operations
        self.host = host
        self.clock = clock
        self._tell = tell
        self._run = run
        self._on_change = on_change
        self._backstop = backstop
        self._lock = asyncio.Lock()
        #: The future each gates step awaits, by tangier job id.
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        #: The last job ends no gates step waited on, by job id. A job can end
        #: before ``tangier gate run`` has returned its id.
        self._unclaimed: dict[int, dict[str, Any]] = {}
        #: Builds cancelled while they ran; their operation ends at its next step.
        self._cancelled: set[str] = set()
        #: Each branch's current request, by ``(project, branch)``.
        self.current: dict[tuple[str, str], Current] = {}
        #: The kicks that wait for a Build's operation to end, kept so a stop
        #: can cancel them.
        self._waiters: set[asyncio.Task[None]] = set()

    # -- the requests --

    async def submit(
        self, project: str, task_id: str, branch: str, sha: str, base: str
    ) -> tuple[MergeRequest, Build]:
        """Open or update the task's Merge request, and queue a Build of ``sha``."""
        async with self._lock:
            existing = await self.requests.current(model.task_key(project, task_id))
            if existing is not None and existing.state != "merged":
                await self._cancel_unfinished(existing, "superseded by a new submit")
            now = self.clock()
            mr, build = model.submit(
                existing,
                request_id=await self.requests.next_id(),
                project=project,
                task_id=task_id,
                branch=branch,
                sha=sha,
                base=base,
                build_id=await self.builds.next_id(),
                now=now,
            )
            await self.builds.save(build)
            await self.requests.save(mr)
        await self._changed()
        await self._tell_build(mr, build)
        await self.kick()
        return mr, build

    async def withdraw(self, task: str) -> MergeRequest:
        """Withdraw the task's open request and cancel its unfinished Builds.

        Raises:
            MergeRequestError: If the task has no open request.
        """
        async with self._lock:
            mr = await self.requests.current(task)
            if mr is None or mr.state != "open":
                raise model.MergeRequestError(f"Task {task} has no open Merge request")
            await self._cancel_unfinished(mr, "withdrawn")
            mr = model.withdraw(mr, self.clock())
            await self.requests.save(mr)
        await self._changed()
        await self.kick()
        return mr

    async def read(
        self, task: str, *, every: bool = False
    ) -> list[tuple[MergeRequest, list[Build]]]:
        """The task's current request with its Builds, newest first.

        ``every`` adds the task's earlier requests.
        """
        found = await self.requests.for_task(task)
        if not every:
            found = found[:1]
        return [(mr, await self.builds.for_request(mr.id)) for mr in found]

    # -- the queue --

    async def start(self) -> None:
        """Recover from a restart, re-tell what was not told, and start the queue.

        Each Build the last server life left running is cancelled, and a new
        Build of its SHA becomes its request's linked Build. Its job is
        cancelled too; tangier's records make the second run cheap.
        """
        async with self._lock:
            for build in await self.builds.unfinished():
                if build.state != "running":
                    continue
                await self._cancel_job(build.job, "server restarted")
                cancelled = model.cancel(build, "server restarted", self.clock())
                await self.builds.save(cancelled)
                mr = await self.requests.read(build.merge_request)
                if mr is None or mr.state != "open" or mr.build != build.id:
                    continue
                mr, again = model.requeue(
                    mr, cancelled, await self.builds.next_id(), self.clock()
                )
                await self.builds.save(again)
                await self.requests.save(mr)
        await self._changed()
        for mr in await self.requests.open():
            linked = await self.builds.read(mr.build)
            if linked is not None and linked.told != linked.state:
                await self._tell_build(mr, linked)
        await self.kick()

    async def kick(self) -> None:
        """Start the oldest linked Builds in the free slots of each project."""
        async with self._lock:
            unfinished = await self.builds.unfinished()
            linked = frozenset(mr.build for mr in await self.requests.open())
            for project in sorted({b.project for b in unfinished}):
                mine = [b for b in unfinished if b.project == project]
                try:
                    config = await asyncio.to_thread(self.host.config, project)
                except Exception:  # noqa: BLE001 — one bad config holds one project
                    log.exception("build queue: cannot read %s's config", project)
                    continue
                if config.merge != "local":
                    continue
                # A slot is held while its Build's operation runs, which can
                # outlast the Build: a cancelled one waits for its job's end.
                free = [
                    slot
                    for slot in model.build_slot_names(config.build_slots)
                    if not self.operations.holds(_slot_key(project, slot))
                ]
                queued = [b for b in mine if b.state == "queued"]
                for build, slot in model.next_builds(queued, linked, free):
                    await self._begin(model.start(build, slot, self.clock()))

    async def _begin(self, build: Build) -> None:
        """Save ``build`` running and start its Operation."""
        await self.builds.save(build)
        mr = await self.requests.read(build.merge_request)
        assert mr is not None
        short = build.sha[:7]
        plan = OperationPlan(
            kind="build",
            keys=frozenset({_slot_key(build.project, build.slot)}),
            doing=f"Building {short} of {mr.task} in {build.slot}",
            done=f"Built {short} of {mr.task}",
            failing=f"Build {build.id} of {mr.task} failed",
            work=lambda hook: self._work(build.id, hook),
            refresh=self._changed,
            project=build.project,
            steps=STEPS,
            task_id=mr.task,
        )
        op_id = await self.operations.begin(
            {"type": "build", "buildId": build.id}, plan
        )
        waiter = asyncio.create_task(self._kick_after(op_id))
        self._waiters.add(waiter)
        waiter.add_done_callback(self._waiters.discard)
        await self._changed()

    async def _kick_after(self, op_id: str) -> None:
        """Kick once the operation ``op_id`` has ended and freed its slot."""
        await self.operations.wait(op_id)
        try:
            await self.kick()
        except Exception:  # noqa: BLE001 — nothing awaits this task
            log.exception("the build queue kick failed")

    async def stop(self) -> None:
        """Cancel the waiting kicks. The operations stop with their runner."""
        for waiter in list(self._waiters):
            waiter.cancel()
        await asyncio.gather(*self._waiters, return_exceptions=True)

    def job_ended(self, job: dict[str, Any]) -> None:
        """Hand a gate job's end to the Build that waits on it, if one does."""
        try:
            job_id = int(job["id"])
        except (KeyError, TypeError, ValueError):
            return
        future = self._waiting.get(job_id)
        if future is None:
            self._unclaimed[job_id] = job
            while len(self._unclaimed) > UNCLAIMED_KEPT:
                del self._unclaimed[next(iter(self._unclaimed))]
        elif not future.done():
            future.set_result(job)

    # -- one Build --

    async def _work(self, build_id: str, hook: StepHook) -> None:
        """Run a Build's steps. A refusal is a stale base or a cancel."""
        path: Path | None = None
        for name in STEPS:
            hook.started(name)
            try:
                build = await self._still_running(build_id)
                if name == "base":
                    await self._base(build)
                elif name == "checkout":
                    path = await asyncio.to_thread(
                        self.host.checkout, build.project, build.slot, build.sha
                    )
                elif name == "install":
                    assert path is not None
                    await asyncio.to_thread(self.host.install, build.project, path)
                else:
                    assert path is not None
                    await self._gates(build, path)
            except _Stale:
                hook.ended(StepEnd(name, "refused", [], "main moved"))
                raise CloseBlocked(
                    "main moved since the submit, so no gate ran"
                ) from None
            except _Cancelled:
                self._cancelled.discard(build_id)
                hook.ended(StepEnd(name, "refused", [], "cancelled"))
                raise CloseBlocked("The Build was cancelled") from None
            except _GatesFailed as exc:
                detail = str(exc)
                hook.ended(StepEnd(name, "failed", detail.splitlines(), "gates failed"))
                raise RuntimeError(detail.splitlines()[0]) from None
            except Exception as exc:
                hook.ended(StepEnd(name, "failed", [], str(exc)))
                current = await self.builds.read(build_id)
                if current is not None and current.state == "running":
                    await self._end(
                        model.step_failed(current, name, str(exc), self.clock())
                    )
                raise
            hook.ended(StepEnd(name, "done", [], None))

    async def _still_running(self, build_id: str) -> Build:
        build = await self.builds.read(build_id)
        if build is None or build.state != "running" or build_id in self._cancelled:
            self._cancelled.discard(build_id)
            raise _Cancelled
        return build

    async def _base(self, build: Build) -> None:
        tip = await asyncio.to_thread(self.host.origin_main, build.project)
        if tip != build.base:
            # Read again: a submit or a withdraw may have cancelled it meanwhile.
            current = await self._still_running(build.id)
            await self._end(model.stale(current, self.clock()))
            raise _Stale

    async def _gates(self, build: Build, path: Path) -> None:
        env = {**os.environ, "TANGIER_RUNNER_KIND": RUNNER_KIND}
        code, out = await self._run(
            ["tangier", "gate", "run", *BUILD_GATE_ARGS], path, env
        )
        found = _JOB_LINE.search(out)
        if found is None:
            tail = "\n".join(out.strip().splitlines()[-5:])
            raise RuntimeError(f"tangier started no gate job (exit {code}): {tail}")
        job = int(found[1])
        # The job may have ended while the run was awaited: claim that end.
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        early = self._unclaimed.pop(job, None)
        if early is not None:
            future.set_result(early)
        self._waiting[job] = future
        try:
            current = await self.builds.read(build.id)
            if current is None or current.state != "running":
                await self._cancel_job(job, "cancelled")
                raise _Cancelled
            await self.builds.save(model.job_started(current, job))
            await self._changed()
            ended = await self._await_job(job, future)
        finally:
            self._waiting.pop(job, None)
        current = await self.builds.read(build.id)
        if current is None or current.state != "running":
            raise _Cancelled
        if ended is None:
            raise RuntimeError("job end not seen")
        result = await self._end(model.job_ended(current, ended, self.clock()))
        if result.state == "cancelled":
            raise _Cancelled
        if result.state == "failed":
            raise _GatesFailed(result.detail)

    async def _await_job(
        self, job: int, future: asyncio.Future[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """The job's end, from the Gate watch; ``None`` when tangier lost it.

        A lost end must not hold a slot for ever: after each backstop, tangier
        is asked about the job.
        """
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(future), self._backstop)
            except TimeoutError:
                found = await self._job_status(job)
                if found is None:
                    return None
                if found.get("state") not in JOB_UNFINISHED:
                    return found

    async def _job_status(self, job: int) -> dict[str, Any] | None:
        """The job as ``tangier gate status --json`` shows it, or ``None`` when gone.

        A status that tangier could not give reads as the job still running:
        a brief fault must not free a slot under a running job.
        """
        still = {"id": job, "state": "running"}
        try:
            code, out = await self._run(
                ["tangier", "gate", "status", "--json", "--all", "--job", str(job)],
                None,
                None,
            )
            data = json.loads(out)
        except Exception:  # noqa: BLE001 — see the docstring
            log.warning("build queue: no status for gate job %s", job)
            return still
        if code != 0 or not isinstance(data, dict):
            return still
        for group in ("running", "queued", "recent"):
            for found in data.get(group) or []:
                if isinstance(found, dict) and found.get("id") == job:
                    return found
        return None

    # -- ends and tells --

    async def _end(self, build: Build) -> Build:
        """Save an ended Build, and tell its chaperone when it is still linked."""
        await self.builds.save(build)
        await self._changed()
        mr = await self.requests.read(build.merge_request)
        if mr is not None and mr.build == build.id and mr.state == "open":
            await self._tell_build(mr, build)
        return build

    async def _tell_build(self, mr: MergeRequest, build: Build) -> None:
        """Tell the chaperone of ``build``'s state, and record what was told."""
        event = build_event(mr, build)
        try:
            delivered = event is None or await self._tell(mr, event)
        except Exception:  # noqa: BLE001 — a tell must not end the Build
            log.exception("the tell of build %s failed", build.id)
            return
        if not delivered:
            return
        current = await self.builds.read(build.id)
        if current is not None and current.state == build.state:
            await self.builds.save(model.mark_told(current))

    async def _cancel_unfinished(self, mr: MergeRequest, reason: str) -> None:
        for build in await self.builds.for_request(mr.id):
            if build.state not in model.UNFINISHED:
                continue
            await self.builds.save(model.cancel(build, reason, self.clock()))
            if build.state != "running":
                continue
            self._cancelled.add(build.id)
            # `tangier gate cancel` returns once the job has stopped, so its
            # slot is free now; the watch's end may come late, or not at all.
            if await self._cancel_job(build.job, reason):
                future = self._waiting.get(build.job)
                if future is not None and not future.done():
                    future.set_result({"id": build.job, "state": "cancelled"})

    async def _cancel_job(self, job: int, reason: str) -> bool:
        """Cancel tangier job ``job``; true when tangier says it stopped."""
        if not job:
            return False
        try:
            code, _ = await self._run(
                ["tangier", "gate", "cancel", "--job", str(job), "--reason", reason],
                None,
                None,
            )
        except Exception:  # noqa: BLE001 — the job's end still comes from the watch
            log.warning("could not cancel gate job %s", job)
            return False
        return code == 0

    async def _changed(self) -> None:
        """Re-read each branch's current request, then tell the server."""
        current: dict[tuple[str, str], Current] = {}
        for mr in reversed(await self.requests.all()):
            linked = await self.builds.read(mr.build)
            current[(mr.project, mr.branch)] = Current(
                mr, linked, model.status(mr, linked)
            )
        self.current = current
        self._on_change()
