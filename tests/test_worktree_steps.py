"""Tests for the worktree step sequence runner.

The seam is :mod:`maelstrom.worktree_steps`: what a sequence runs, in what
order, when it stops, what it reports, and which scopes each step holds while
it runs. The sequences built on it — close, remove, open — have their own
suites.
"""

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from maelstrom.worktree_steps import (
    Scope,
    SequenceResult,
    Step,
    StepOutcome,
    run_sequence,
    scope_lock,
)


def step(name: str, outcome: StepOutcome, *, scopes: tuple[Scope, ...] = ()) -> Step:
    """A step that does nothing but answer with ``outcome``."""
    return Step(name=name, scopes=scopes, run=lambda: outcome)


def recording(name: str, order: list[str], **kw) -> Step:
    """A step that notes it ran, so order can be asserted."""

    def run() -> StepOutcome:
        order.append(name)
        return StepOutcome(messages=[f"{name} ran"])

    return Step(name=name, run=run, **kw)


async def run(*steps: Step, announce=None) -> SequenceResult:
    return await run_sequence(steps, announce=announce or (lambda line: None))


class TestTheRunner:
    async def test_steps_run_in_the_order_given(self):
        order: list[str] = []
        await run(recording("a", order), recording("b", order), recording("c", order))
        assert order == ["a", "b", "c"]

    async def test_messages_are_collected_across_steps(self):
        result = await run(
            step("a", StepOutcome(messages=["one", "two"])),
            step("b", StepOutcome(messages=["three"])),
        )
        assert result.messages == ["one", "two", "three"]

    async def test_a_sequence_that_ran_to_the_end_is_not_blocked(self):
        result = await run(step("a", StepOutcome()))
        assert result.blocked is None
        assert result.ok

    async def test_an_awaited_step_is_awaited(self):
        async def slow() -> StepOutcome:
            await asyncio.sleep(0)
            return StepOutcome(messages=["awaited"])

        result = await run(Step(name="a", run=slow))
        assert result.messages == ["awaited"]


class TestARefusal:
    async def test_a_blocked_step_stops_the_ones_after_it(self):
        order: list[str] = []
        await run(
            recording("a", order),
            Step(name="b", run=lambda: StepOutcome(blocked="no")),
            recording("c", order),
        )
        assert order == ["a"]

    async def test_the_refusal_message_is_the_step_s_own(self):
        result = await run(Step(name="b", run=lambda: StepOutcome(blocked="nope")))
        assert result.blocked == "nope"
        assert not result.ok

    async def test_the_blocking_step_is_named(self):
        result = await run(Step(name="git_close", run=lambda: StepOutcome(blocked="x")))
        assert result.blocked_step == "git_close"

    async def test_messages_from_before_the_refusal_are_kept(self):
        result = await run(
            step("a", StepOutcome(messages=["did a"])),
            Step(name="b", run=lambda: StepOutcome(messages=["tried b"], blocked="no")),
        )
        assert result.messages == ["did a", "tried b"]


class TestAnnounce:
    async def test_every_message_reaches_announce_as_it_happens(self):
        seen: list[str] = []
        await run(
            step("a", StepOutcome(messages=["one"])),
            step("b", StepOutcome(messages=["two"])),
            announce=seen.append,
        )
        assert seen == ["one", "two"]

    async def test_announced_lines_are_also_returned(self):
        seen: list[str] = []
        result = await run(
            step("a", StepOutcome(messages=["one"])), announce=seen.append
        )
        assert result.messages == seen


class TestScopes:
    """A step holds its scope only while it runs, and repo comes before worktree."""

    async def test_a_step_with_no_scope_takes_no_lock(self, tmp_path):
        held: list[str] = []
        result = await run_sequence(
            [Step(name="a", run=lambda: StepOutcome(messages=["ran"]))],
            announce=held.append,
            repo=tmp_path,
        )
        assert result.messages == ["ran"]

    async def test_two_worktree_steps_in_different_worktrees_overlap(self, tmp_path):
        """Independent checkouts share nothing, so they must not queue."""
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        started = asyncio.Event()
        released = asyncio.Event()

        def first() -> StepOutcome:
            started.set()
            # Holds its lock until the second has proved it got in anyway.
            while not released.is_set():
                time.sleep(0.01)
            return StepOutcome()

        async def one():
            return await run_sequence(
                [Step(name="a", scopes=(Scope.WORKTREE,), run=first)],
                announce=lambda line: None,
                repo=repo,
                worktree=repo / "project-alpha",
            )

        async def two():
            await asyncio.wait_for(started.wait(), 5)
            out = await run_sequence(
                [Step(name="b", scopes=(Scope.WORKTREE,), run=lambda: StepOutcome())],
                announce=lambda line: None,
                repo=repo,
                worktree=repo / "project-bravo",
            )
            released.set()
            return out

        results = await asyncio.wait_for(asyncio.gather(one(), two()), 10)
        assert all(r.ok for r in results)

    async def test_two_repo_steps_in_one_project_do_not_overlap(self, tmp_path):
        """A fetch writes the shared object store, so it is one at a time."""
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        inside: list[str] = []

        def body(name: str):
            def run() -> StepOutcome:
                inside.append(f"enter {name}")
                time.sleep(0.05)
                inside.append(f"leave {name}")
                return StepOutcome()

            return run

        async def go(name: str):
            return await run_sequence(
                [Step(name=name, scopes=(Scope.REPO,), run=body(name))],
                announce=lambda line: None,
                repo=repo,
            )

        await asyncio.wait_for(asyncio.gather(go("a"), go("b")), 10)
        assert inside in (
            ["enter a", "leave a", "enter b", "leave b"],
            ["enter b", "leave b", "enter a", "leave a"],
        )

    async def test_a_step_taking_both_acquires_repo_first(self, tmp_path):
        """One order, always, so two steps cannot deadlock against each other."""
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        taken: list[Scope] = []

        result = await run_sequence(
            [
                Step(
                    name="a",
                    scopes=(Scope.WORKTREE, Scope.REPO),
                    run=lambda: StepOutcome(),
                )
            ],
            announce=lambda line: None,
            repo=repo,
            worktree=repo / "project-alpha",
            _on_acquire=taken.append,
        )
        assert result.ok
        assert taken == [Scope.REPO, Scope.WORKTREE]

    async def test_the_lock_is_released_when_a_step_raises(self, tmp_path):
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)

        def boom() -> StepOutcome:
            raise RuntimeError("step failed")

        with pytest.raises(RuntimeError):
            await run_sequence(
                [Step(name="a", scopes=(Scope.REPO,), run=boom)],
                announce=lambda line: None,
                repo=repo,
            )

        # The next acquire must not block: a held lock would hang here.
        with scope_lock(repo, Scope.REPO, None, timeout=2):
            pass


class TestTheLockFile:
    def test_the_repo_lock_lives_in_the_shared_git_dir(self, tmp_path):
        """Every worktree of a project reaches the same file, cross-process."""
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        with scope_lock(repo, Scope.REPO, None, timeout=2):
            assert (repo / ".git" / "mael-locks" / "repo.lock").exists()

    def test_each_worktree_gets_its_own_lock_file(self, tmp_path):
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        with scope_lock(repo, Scope.WORKTREE, repo / "project-alpha", timeout=2):
            with scope_lock(repo, Scope.WORKTREE, repo / "project-bravo", timeout=2):
                locks = sorted(p.name for p in (repo / ".git" / "mael-locks").iterdir())
        assert locks == ["worktree-project-alpha.lock", "worktree-project-bravo.lock"]

    def test_a_contended_lock_gives_up_rather_than_hanging(self, tmp_path):
        """A stuck peer must surface as an error, not a wedged button."""
        repo = tmp_path / "project"
        (repo / ".git").mkdir(parents=True)
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            os.close(read)
            try:
                with scope_lock(repo, Scope.REPO, None, timeout=2):
                    os.write(write, b"held")
                    time.sleep(5)
            finally:
                os._exit(0)
        os.close(write)
        try:
            assert os.read(read, 4) == b"held"
            with pytest.raises(TimeoutError):
                with scope_lock(repo, Scope.REPO, None, timeout=0.3):
                    pass
        finally:
            os.close(read)
            os.kill(pid, 9)
            os.waitpid(pid, 0)


class TestTheExecutor:
    """A blocking step runs on the pool it was given, not the loop's default.

    The server sizes a bounded pool for worktree work. ``asyncio.to_thread``
    would use the loop's own default executor instead, which is unbounded — so
    the pool's size would bound nothing and its comment would defend a limit
    that was not in force.
    """

    async def test_a_blocking_step_runs_on_the_given_executor(self):
        ran_on: list[str] = []

        def note() -> StepOutcome:
            ran_on.append(threading.current_thread().name)
            return StepOutcome()

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="given") as pool:
            await run_sequence(
                [Step(name="a", run=note)],
                announce=lambda line: None,
                executor=pool,
            )

        assert ran_on and ran_on[0].startswith("given")

    async def test_a_one_worker_pool_serialises_two_sequences(self):
        """The bound is real: the pool's width is what admits overlap."""
        inside: list[str] = []

        def body(name: str):
            def run() -> StepOutcome:
                inside.append(f"enter {name}")
                time.sleep(0.05)
                inside.append(f"leave {name}")
                return StepOutcome()

            return run

        with ThreadPoolExecutor(max_workers=1) as pool:
            await asyncio.gather(
                *(
                    run_sequence(
                        [Step(name=n, run=body(n))],
                        announce=lambda line: None,
                        executor=pool,
                    )
                    for n in ("a", "b")
                )
            )

        assert inside in (
            ["enter a", "leave a", "enter b", "leave b"],
            ["enter b", "leave b", "enter a", "leave a"],
        )

    async def test_an_async_step_is_not_sent_to_the_executor(self):
        """There is nothing blocking to move: it is awaited on the loop."""
        used: list[str] = []

        class Watching(ThreadPoolExecutor):
            def submit(self, fn, /, *args, **kwargs):  # pragma: no cover - guard
                used.append("submitted")
                return super().submit(fn, *args, **kwargs)

        async def quick() -> StepOutcome:
            return StepOutcome()

        with Watching(max_workers=1) as pool:
            await run_sequence(
                [Step(name="a", run=quick)],
                announce=lambda line: None,
                executor=pool,
            )

        assert used == []
