"""The steps a worktree mutation is made of, and the runner that sequences them.

Every worktree mutation is the same small set of steps in a different subset
and order. Close stops the environment, the agents and the sessions, rescues
``.env``, closes in git and closes the workspace. Remove is the same list with
a different git step and no rescue. Open finds or makes the checkout, records
a base, rebases, writes ``CLAUDE.local.md`` and installs. Written out by hand
each time, a step goes missing: ``mael remove`` never stopped the daemon's
agents, so every remove over a driven agent left a phantom ``exited`` row.

This module holds the vocabulary once. A sequence is a list of :class:`Step`;
:func:`run_sequence` runs them in order, collects their lines, and stops at the
first refusal.

It sits above the adapters rather than inside one, as ``worktree_close.py``
does: ``env.py`` already imports ``worktree.py``, so the sequence cannot live
in either without a cycle.

**The git algorithms stay whole.** ``close_worktree``, ``sync_worktree`` and
``create_worktree`` each look like a sequence, but their flags branch inside
their own steps and those steps share mutable local state. Each is one step
here, entire.
"""

import asyncio
import fcntl
import os
import time
from collections.abc import Awaitable, Callable, Generator, Sequence
from concurrent.futures import Executor
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from inspect import isawaitable, iscoroutinefunction
from pathlib import Path

#: How long a step waits for a scope before it gives up. A worktree operation
#: is seconds of git, so a wait this long means a peer is stuck, not busy — and
#: a button that reports that beats a button that spins forever.
LOCK_TIMEOUT = 120.0


class Scope(Enum):
    """What a step must hold alone while it runs.

    Ordered: a step taking both acquires in declaration order here, always, so
    no two steps can deadlock against each other.
    """

    #: The project's shared bare ``.git``. A fetch writes the object store and
    #: the remote refs every other worktree rebases against, and
    #: ``fetch --prune`` deletes refs another worktree may be mid-rebase on.
    REPO = "repo"
    #: One checkout's index and ``HEAD``. A rebase and a detach at once is
    #: corruption, not slowness.
    WORKTREE = "worktree"


@dataclass
class StepOutcome:
    """What one step did.

    ``blocked`` stops the sequence, and its message is what the user reads —
    on a button, or on stderr.
    """

    messages: list[str] = field(default_factory=list)
    blocked: str | None = None


@dataclass
class Step:
    """One named unit of a worktree mutation.

    ``run`` takes no arguments: a step closes over what it needs, so the runner
    stays ignorant of every step's shape. It may be sync or async — the one
    awaited step today is stopping the daemon's agents, which is a socket call.
    A sync ``run`` is moved to a thread, because it is blocking git and must
    not hold the event loop while another sequence waits on a lock.
    """

    name: str
    run: Callable[[], StepOutcome | Awaitable[StepOutcome]]
    scopes: tuple[Scope, ...] = ()


@dataclass
class SequenceResult:
    """What a whole sequence amounted to.

    ``messages`` are the progress lines in the order they happened, for a CLI
    to echo or a reply to carry. They are the record of the run; ``announce``
    is the live edge, and both see the same lines.
    """

    messages: list[str] = field(default_factory=list)
    blocked: str | None = None
    #: Which step refused, so a caller can say what stopped rather than only
    #: relaying a line.
    blocked_step: str | None = None

    @property
    def ok(self) -> bool:
        return self.blocked is None


def _lock_path(repo: Path, scope: Scope, worktree: Path | None) -> Path:
    """Where a scope's lock file lives.

    Under the project's shared ``.git``, so every worktree of the project — and
    every process, the server, a ``mael sync`` in a terminal, another
    worktree's tooling — reaches the same file.
    """
    locks = repo / ".git" / "mael-locks"
    if scope is Scope.REPO:
        return locks / "repo.lock"
    assert worktree is not None, "a worktree-scoped step needs a worktree"
    return locks / f"worktree-{worktree.name}.lock"


@contextmanager
def scope_lock(
    repo: Path,
    scope: Scope,
    worktree: Path | None,
    *,
    timeout: float = LOCK_TIMEOUT,
) -> Generator[None]:
    """Hold ``scope`` exclusively, across processes, for the block.

    ``flock`` rather than an :class:`asyncio.Lock`: the server is not the only
    writer. A user running ``mael sync`` in a terminal is a peer, and so is
    another worktree's tooling. This follows ``task_store.py`` and
    ``agent_server.py``, which lock the same way.

    Deliberately not :func:`mael_common.util.locked_file`: that is a read/rewrite
    transaction over a file's contents. Nothing is stored here — the file is
    only somewhere to put the lock.

    Raises:
        TimeoutError: If the scope is not free within ``timeout`` seconds.
    """
    path = _lock_path(repo, scope, worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = time.monotonic() + timeout
    # Non-blocking acquire plus sleep-poll, so the deadline is portable: a
    # blocking LOCK_EX cannot be time-bounded across platforms.
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise TimeoutError(
                    f"Another mael process holds the {scope.value} lock for "
                    f"{repo.name}; gave up after {int(timeout)}s."
                )
            time.sleep(0.05)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _lockable(repo: Path) -> bool:
    """Whether ``repo`` is a project whose shared ``.git`` can hold a lock.

    A lock lives in a file, so it needs a real path and a real ``.git`` to
    live in. Anything else — a stand-in, a project that is not a checkout —
    locks nothing, because a lock that cannot be taken must never be the
    reason an operation fails.
    """
    try:
        return isinstance(repo, Path) and (repo / ".git").is_dir()
    except OSError:
        return False


@contextmanager
def _scopes_held(
    repo: Path | None,
    worktree: Path | None,
    scopes: Sequence[Scope],
    on_acquire: Callable[[Scope], None] | None,
) -> Generator[None]:
    """Hold every scope a step declared, repo before worktree.

    A project with no shared ``.git`` locks nothing. The lock exists to keep
    real git work off real git work; where there is no repository there is
    none to protect, and a sequence driven over stub steps runs unimpeded.
    """
    ordered = [s for s in Scope if s in scopes]
    if not ordered or repo is None or not _lockable(repo):
        yield
        return
    head, rest = ordered[0], ordered[1:]
    if on_acquire:
        on_acquire(head)
    with scope_lock(repo, head, worktree):
        with _scopes_held(repo, worktree, rest, on_acquire):
            yield


async def _run_step(
    step: Step,
    repo: Path | None,
    worktree: Path | None,
    on_acquire: Callable[[Scope], None] | None,
    executor: Executor | None,
) -> StepOutcome:
    """Run one step, holding its scopes for exactly as long as it runs.

    The lock belongs to the step, not the sequence. Locking the whole worktree
    for a whole run would serialise more than the work requires, and could not
    express a step needing two scopes.

    A blocking step runs on ``executor`` when one is given. That is how the
    server's bounded worktree pool is the pool the git work actually lands on:
    :func:`asyncio.to_thread` would use the loop's own default executor, which
    is unbounded, so the pool's size would bound nothing.
    """

    def blocking() -> StepOutcome | Awaitable[StepOutcome]:
        with _scopes_held(repo, worktree, step.scopes, on_acquire):
            return step.run()

    # An async step is awaited on the loop; the scopes are held around the
    # await rather than in a thread, because there is nothing blocking to move.
    if iscoroutinefunction(step.run):
        with _scopes_held(repo, worktree, step.scopes, on_acquire):
            outcome = await step.run()
    else:
        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(executor, blocking)
        if isawaitable(outcome):
            outcome = await outcome
    # A step that answered with nothing did its work and had nothing to say.
    return outcome if isinstance(outcome, StepOutcome) else StepOutcome()


async def run_sequence(
    steps: Sequence[Step],
    *,
    announce: Callable[[str], None],
    repo: Path | None = None,
    worktree: Path | None = None,
    executor: Executor | None = None,
    _on_acquire: Callable[[Scope], None] | None = None,
) -> SequenceResult:
    """Run ``steps`` in order, stopping at the first refusal.

    ``announce`` is the live edge: every line reaches it as it happens, and the
    same lines are returned. It is the argument
    :func:`mael_domain.worktree.sync_worktree_with_autorepair` and
    ``setup_worktree_for_branch`` already take, so this is the existing
    convention. The CLI passes ``click.echo``; the server passes a collector,
    and will pass a notice publisher when per-step progress lands.

    ``executor`` is where a blocking step runs. ``None`` uses the loop's
    default, which is right for the CLI — one operation, one process. The
    server passes its bounded worktree pool.

    Never raises for a refusal: read ``result.ok``.
    """
    result = SequenceResult()
    for step in steps:
        outcome = await _run_step(step, repo, worktree, _on_acquire, executor)
        for line in outcome.messages:
            announce(line)
            result.messages.append(line)
        if outcome.blocked is not None:
            result.blocked = outcome.blocked
            result.blocked_step = step.name
            break
    return result
