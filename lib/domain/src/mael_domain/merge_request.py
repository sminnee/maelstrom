"""The **Merge request** and its **Builds**: how a ``merge: local`` task merges.

The model layer. A Merge request is a task branch's request to merge into
``main``; each submit updates it and queues a Build, one gate run of its SHA in
a Build worktree. Each function takes a record and returns the next one; none
mutates its argument. :mod:`mael_domain.merge_request_store` keeps them, and
the orchestrator server's Build queue runs them. See ``CONTEXT.md``, "Merge
request" and "Queue build", and ``docs/dev/build-queue.md``.
"""

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

from .external_event import gate_failure_lines
from .worktree_model import BUILD_WORKTREE_PREFIX

RequestState = Literal["open", "withdrawn", "merged"]
BuildState = Literal["queued", "running", "passed", "failed", "stale", "cancelled"]
#: What the node, the CLI and the Merge button read.
Status = Literal[
    "queued", "building", "passed", "stale", "failed", "withdrawn", "merged"
]

#: The Build states that still hold, or wait for, a slot.
UNFINISHED: frozenset[str] = frozenset({"queued", "running"})


class MergeRequestError(Exception):
    """A move the record's state does not allow."""


@dataclass(frozen=True)
class MergeRequest:
    #: ``mr<n>``.
    id: str
    #: ``<project>/<task id>``.
    task: str
    project: str
    task_id: str
    branch: str
    #: The head SHA of the last submit.
    sha: str
    #: The ``origin/main`` SHA the head was based on at that submit.
    base: str
    state: RequestState
    #: The linked Build: the id of the newest Build. Only it counts.
    build: str
    opened: str
    updated: str
    #: The text of ``.drafts/pr.md`` at the last submit that sent one.
    description: str = ""
    #: When the Merge pushed it to ``main``, or ``""``.
    merged: str = ""
    #: ``"merged"`` once the chaperone was told of the merge, or ``""``.
    told: str = ""


@dataclass(frozen=True)
class Build:
    #: ``b<n>``.
    id: str
    merge_request: str
    project: str
    sha: str
    #: The base as submitted.
    base: str
    state: BuildState
    queued_at: str
    started: str = ""
    ended: str = ""
    #: The Build worktree it ran in, ``_buildN``, or ``""`` before it started.
    slot: str = ""
    #: The tangier job id, or 0 before the gates started.
    job: int = 0
    #: The failed gates and log path, the failed step, or the cancel reason.
    detail: str = ""
    #: The last state the chaperone was told, or ``""``.
    told: str = ""


@dataclass(frozen=True)
class Current:
    """A branch's current request, its linked Build, and the status they give."""

    request: MergeRequest
    build: Build | None
    status: Status


def task_key(project: str, task_id: str) -> str:
    return f"{project}/{task_id}"


def submit(
    existing: MergeRequest | None,
    *,
    request_id: str,
    project: str,
    task_id: str,
    branch: str,
    sha: str,
    base: str,
    build_id: str,
    now: str,
    description: str | None = None,
) -> tuple[MergeRequest, Build]:
    """The request after a submit, and its new linked Build.

    ``existing`` is the task's current request. An ``open`` or ``withdrawn``
    one is updated and reopened, keeping its id; with none, or a ``merged``
    one, a new request opens as ``request_id``. A ``description`` of ``None``
    keeps the existing text.
    """
    if existing is None or existing.state == "merged":
        mr = MergeRequest(
            id=request_id,
            task=task_key(project, task_id),
            project=project,
            task_id=task_id,
            branch=branch,
            sha=sha,
            base=base,
            state="open",
            build=build_id,
            opened=now,
            updated=now,
            description=description or "",
        )
    else:
        mr = replace(
            existing,
            branch=branch,
            sha=sha,
            base=base,
            state="open",
            build=build_id,
            updated=now,
            description=(existing.description if description is None else description),
        )
    return mr, _queued(mr, build_id, now)


def _queued(mr: MergeRequest, build_id: str, now: str) -> Build:
    return Build(
        id=build_id,
        merge_request=mr.id,
        project=mr.project,
        sha=mr.sha,
        base=mr.base,
        state="queued",
        queued_at=now,
    )


def withdraw(mr: MergeRequest, now: str) -> MergeRequest:
    """``mr`` withdrawn. Only an open request can be."""
    if mr.state != "open":
        raise MergeRequestError(f"Merge request {mr.id} is {mr.state}, not open")
    return replace(mr, state="withdrawn", updated=now)


def merged(mr: MergeRequest, now: str) -> MergeRequest:
    """``mr`` merged: its SHA is in ``main`` on origin."""
    if mr.state != "open":
        raise MergeRequestError(f"Merge request {mr.id} is {mr.state}, not open")
    return replace(mr, state="merged", merged=now, updated=now)


def mark_merge_told(mr: MergeRequest) -> MergeRequest:
    """``mr`` with its merge recorded as told."""
    return replace(mr, told="merged")


def requeue(
    mr: MergeRequest, build: Build, build_id: str, now: str
) -> tuple[MergeRequest, Build]:
    """A new queued Build of ``build``'s SHA, linked to ``mr``.

    Restart recovery, where the server stopped during ``build`` and the
    caller has cancelled it, and a Rebuild of a ``failed`` request.
    """
    queued = replace(_queued(mr, build_id, now), sha=build.sha, base=build.base)
    return replace(mr, build=build_id, updated=now), queued


def _expect(build: Build, *states: str) -> None:
    if build.state not in states:
        raise MergeRequestError(
            f"Build {build.id} is {build.state}, not {' or '.join(states)}"
        )


def start(build: Build, slot: str, now: str) -> Build:
    """``build`` running in ``slot``."""
    _expect(build, "queued")
    return replace(build, state="running", slot=slot, started=now)


def job_started(build: Build, job: int) -> Build:
    """``build`` with its tangier job id."""
    _expect(build, "running")
    if build.job:
        raise MergeRequestError(f"Build {build.id} already runs job {build.job}")
    return replace(build, job=job)


def stale(build: Build, now: str) -> Build:
    """``build`` ended ``stale``: ``main`` moved, so no gate ran."""
    _expect(build, "running")
    return replace(
        build,
        state="stale",
        ended=now,
        detail="main moved since the submit, so no gate ran",
    )


def job_ended(build: Build, job: dict[str, Any], now: str) -> Build:
    """``build`` ended by its gate job, as ``tangier gate status --json`` gives it.

    ``passed`` passes. A ``cancelled`` job with no failed gate cancels. Any
    other end fails, with the failed gates and the log as the detail.
    """
    _expect(build, "running")
    state = job.get("state")
    if state == "passed":
        return replace(build, state="passed", ended=now, detail="")
    failed = gate_failure_lines(job)
    if state == "cancelled" and not failed:
        return replace(build, state="cancelled", ended=now, detail="job cancelled")
    detail = "\n".join([*failed, f"Log: {job.get('log')}"])
    return replace(build, state="failed", ended=now, detail=detail)


def step_failed(build: Build, step: str, error: str, now: str) -> Build:
    """``build`` failed before or around its gates, at ``step``."""
    _expect(build, "running")
    return replace(build, state="failed", ended=now, detail=f"{step}: {error}")


def cancel(build: Build, reason: str, now: str) -> Build:
    """``build`` cancelled before it ended."""
    _expect(build, *UNFINISHED)
    return replace(build, state="cancelled", ended=now, detail=reason)


def mark_told(build: Build) -> Build:
    """``build`` with its state recorded as told."""
    return replace(build, told=build.state)


def status(mr: MergeRequest, linked: Build | None, tip: str = "") -> Status:
    """The status a reader sees, from the request and its linked Build.

    ``tip`` is the last ``origin/main`` SHA the queue saw, or ``""`` when it
    has seen none. A ``passed`` request whose base is no longer the tip is
    ``stale``: the Merge would not be a fast-forward. Nothing stores that.
    """
    if mr.state in ("withdrawn", "merged"):
        return mr.state  # type: ignore[return-value]
    if linked is None or linked.state == "queued":
        return "queued"
    if linked.state == "running":
        return "building"
    if linked.state == "cancelled":
        # Cancelled outside the queue: nothing runs it again until a submit.
        return "failed"
    if linked.state == "passed" and tip and tip != mr.base:
        return "stale"
    return linked.state  # type: ignore[return-value]


def can_merge(mr: MergeRequest, linked: Build | None, sha: str) -> str | None:
    """Why the Merge of ``sha`` is refused, or ``None`` when it may run.

    The Merge is pinned to the SHA the user saw: a submit between the view
    and the click must not merge a SHA no gate passed.
    """
    if mr.state != "open":
        return f"Merge request {mr.id} is {mr.state}"
    if sha != mr.sha:
        return f"Merge request {mr.id} is now at {mr.sha[:7]}, not {sha[:7]}"
    if linked is None or linked.state != "passed":
        return f"Merge request {mr.id} has not passed its Queue build"
    return None


def can_rebuild(mr: MergeRequest, linked: Build | None) -> str | None:
    """Why a Rebuild is refused, or ``None``: only a ``failed`` request rebuilds.

    A ``stale`` Build is not rebuilt, as its base check would fail again.
    """
    if mr.state != "open":
        return f"Merge request {mr.id} is {mr.state}"
    if linked is None or linked.state not in ("failed", "cancelled"):
        state = "queued" if linked is None else linked.state
        return f"Merge request {mr.id}'s Queue build is {state}, not failed"
    return None


def next_builds(
    queued: list[Build], linked: frozenset[str], free_slots: list[str]
) -> list[tuple[Build, str]]:
    """The Builds to start now, each with its slot: the oldest linked ones first.

    A queued Build that is no longer linked never starts.
    """
    waiting = sorted(
        (b for b in queued if b.state == "queued" and b.id in linked),
        key=lambda b: (b.queued_at, record_number(b.id)),
    )
    return list(zip(waiting, free_slots, strict=False))


def build_slot_names(n: int) -> list[str]:
    """The Build worktree names of ``n`` slots: ``_build1`` … ``_buildN``."""
    return [f"{BUILD_WORKTREE_PREFIX}{i}" for i in range(1, n + 1)]


def record_number(id: str) -> int:
    """The ``n`` of ``mr<n>`` or ``b<n>``, so ``b10`` sorts after ``b2``."""
    return int(id.lstrip("mrb") or 0)


def merge_request_json(
    mr: MergeRequest, builds: list[Build], tip: str = ""
) -> dict[str, Any]:
    """A Merge request as the API answers it: its fields, status and Builds.

    ``builds`` is newest first; the linked one is found by id. ``tip`` is as
    :func:`status` takes it.
    """
    linked = next((b for b in builds if b.id == mr.build), None)
    return {
        **asdict(mr),
        "status": status(mr, linked, tip),
        "builds": [asdict(b) for b in builds],
    }
