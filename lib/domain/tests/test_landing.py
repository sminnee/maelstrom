"""Tests for mael_domain.landing — a task's status past done, and the sync that records it."""

from dataclasses import replace

import pytest

from mael_domain.config import MaelstromConfig
from mael_domain.landing import (
    Deploy,
    LandingSignals,
    Merge,
    TrackedTask,
    project_config,
    status_of,
    sync,
)
from mael_domain.landing_store import (
    EnvLanding,
    InMemoryPullRequestStore,
    InMemoryTaskStepStore,
    PullRequest,
    SqlitePullRequestStore,
    SqliteTaskStepStore,
    StepEvent,
)
from mael_domain.state_db.migrate import open_state_db

UAT_AND_LIVE = MaelstromConfig(deploy_environments={"uat": "uat", "live": "prod"})
NO_DEPLOY = MaelstromConfig()

MERGE = Merge(
    url="u/7",
    title="Add x",
    merged_at="2026-10-01T00:00:00Z",
    merge_sha="m7",
    state="MERGED",
)


class FakeSignals(LandingSignals):
    """GitHub as a test sets it: merges by number, deploys by env, and ancestry.

    ``None`` for a deploy, or a missing ``(merge, deploy)`` pair, is a read that
    failed. Every call is counted, so a test can say what a tick spent.
    """

    def __init__(self) -> None:
        self.merged: dict[int, Merge] = {}
        self.deploys: dict[str, Deploy | None] = {}
        self.landed: dict[tuple[str, str], bool] = {}
        self.calls: list[tuple] = []

    async def merges(self, project: str, numbers: list[int]) -> dict[int, Merge]:
        self.calls.append(("merges", project, tuple(numbers)))
        return {n: m for n, m in self.merged.items() if n in numbers}

    async def deploy(self, project: str, environment: str) -> Deploy | None:
        self.calls.append(("deploy", project, environment))
        return self.deploys.get(environment)

    async def contains(
        self, project: str, merge_sha: str, deploy_sha: str
    ) -> bool | None:
        self.calls.append(("contains", project, merge_sha, deploy_sha))
        return self.landed.get((merge_sha, deploy_sha))


def task(status: str = "done", pr_number: int = 7) -> TrackedTask:
    return TrackedTask(project="p", task_id="t1", status=status, pr_number=pr_number)


def pr(**envs: EnvLanding) -> PullRequest:
    return PullRequest(
        project="p",
        number=7,
        url="u/7",
        title="Add x",
        merged_at="2026-10-01T00:00:00Z",
        merge_sha="m7",
        envs=envs,
    )


LANDED = EnvLanding(
    state="landed", deploy_sha="d1", first_seen_at="2026-10-02T00:00:00Z"
)


class TestStatusOf:
    """The highest step reached, in the order done → merged → uat → live."""

    def test_a_task_not_done_has_no_status(self):
        assert status_of(task(status="in-progress"), pr(), UAT_AND_LIVE) is None

    def test_a_done_task_with_no_pr_is_done(self):
        assert status_of(task(pr_number=0), None, UAT_AND_LIVE) == "done"

    def test_an_unmerged_pr_leaves_the_task_done(self):
        assert status_of(task(), replace(pr(), merged_at=""), UAT_AND_LIVE) == "done"

    def test_merged_then_uat_then_live(self):
        assert status_of(task(), pr(), UAT_AND_LIVE) == "merged"
        assert status_of(task(), pr(uat=LANDED), UAT_AND_LIVE) == "uat"
        assert status_of(task(), pr(uat=LANDED, live=LANDED), UAT_AND_LIVE) == "live"

    def test_an_unknown_env_is_not_reached(self):
        unknown = EnvLanding(state="unknown")
        assert status_of(task(), pr(uat=unknown), UAT_AND_LIVE) == "merged"

    def test_a_step_waits_for_the_one_before_it(self):
        assert status_of(task(), pr(live=LANDED), UAT_AND_LIVE) == "merged"

    def test_no_deploy_block_stops_at_merged(self):
        assert status_of(task(), pr(uat=LANDED, live=LANDED), NO_DEPLOY) == "merged"

    def test_an_env_the_project_does_not_deploy_to_is_passed_over(self):
        live_only = MaelstromConfig(deploy_environments={"live": "production"})
        assert status_of(task(), pr(live=LANDED), live_only) == "live"


@pytest.fixture(params=["memory", "sqlite"])
async def stores(request):
    """Both backends, so the sqlite rows round-trip what the sync wrote."""
    if request.param == "memory":
        yield InMemoryPullRequestStore(), InMemoryTaskStepStore()
        return
    db = open_state_db(":memory:")
    await db.migrate()
    yield SqlitePullRequestStore(db), SqliteTaskStepStore(db)
    db.close()


async def run_sync(stores, signals, tracked, config=UAT_AND_LIVE, now="T1"):
    prs, steps = stores
    return await sync(prs, steps, signals, tracked, lambda project: config, now)


async def recorded(stores) -> list[tuple[str, str]]:
    return [(e.step, e.at) for e in await stores[1].list()]


class TestSync:
    """One tick: refresh the unsettled pull requests, then write the missing steps."""

    async def test_a_pr_moves_merged_then_uat_then_live(self, stores):
        signals = FakeSignals()
        await run_sync(stores, signals, [task()], now="T1")
        assert await recorded(stores) == [("done", "T1")]

        signals.merged[7] = MERGE
        await run_sync(stores, signals, [task()], now="T2")
        signals.deploys["uat"] = Deploy(sha="d1", created_at="T3")
        signals.landed[("m7", "d1")] = True
        await run_sync(stores, signals, [task()], now="T4")
        signals.deploys["prod"] = Deploy(sha="d2", created_at="T5")
        signals.landed[("m7", "d2")] = True
        await run_sync(stores, signals, [task()], now="T6")

        assert await stores[1].list() == [
            StepEvent("p", "t1", "done", 7, at="T1", recorded_at="T1"),
            StepEvent("p", "t1", "merged", 7, at=MERGE.merged_at, recorded_at="T2"),
            StepEvent("p", "t1", "uat", 7, at="T3", recorded_at="T4"),
            StepEvent("p", "t1", "live", 7, at="T5", recorded_at="T6"),
        ]
        assert await stores[0].read("p", 7) == PullRequest(
            project="p",
            number=7,
            url="u/7",
            title="Add x",
            merged_at=MERGE.merged_at,
            merge_sha="m7",
            state="MERGED",
            envs={
                "uat": EnvLanding("landed", "d1", "T3"),
                "live": EnvLanding("landed", "d2", "T5"),
            },
            fetched_at="T6",
        )

    async def test_a_failed_deploy_read_is_unknown_and_not_reached(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys["uat"] = None
        await run_sync(stores, signals, [task()])
        stored = await stores[0].read("p", 7)
        assert stored is not None and stored.envs == {
            "uat": EnvLanding("unknown"),
            "live": EnvLanding("unknown"),
        }
        assert [s for s, _ in await recorded(stores)] == ["done", "merged"]

    async def test_a_deploy_without_the_merge_is_not_yet(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys["uat"] = Deploy(sha="d0", created_at="T0")
        signals.landed[("m7", "d0")] = False
        await run_sync(stores, signals, [task()])
        stored = await stores[0].read("p", 7)
        assert stored is not None and stored.envs["uat"] == EnvLanding("not_yet", "d0")

    async def test_a_new_deploy_holding_the_merge_lands_it(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys["uat"] = Deploy(sha="d0", created_at="T0")
        signals.landed[("m7", "d0")] = False
        await run_sync(stores, signals, [task()], now="T1")
        signals.deploys["uat"] = Deploy(sha="d1", created_at="T2")
        signals.landed[("m7", "d1")] = True
        await run_sync(stores, signals, [task()], now="T3")
        stored = await stores[0].read("p", 7)
        assert stored is not None and stored.envs["uat"] == EnvLanding(
            "landed", "d1", "T2"
        )
        assert [s for s, _ in await recorded(stores)] == ["done", "merged", "uat"]

    async def test_an_unread_compare_is_unknown_and_keeps_the_sha(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys["uat"] = Deploy(sha="d0", created_at="T0")
        await run_sync(stores, signals, [task()])
        stored = await stores[0].read("p", 7)
        assert stored is not None and stored.envs["uat"] == EnvLanding("unknown", "d0")

    async def test_an_unchanged_deploy_sha_skips_compare(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys["uat"] = Deploy(sha="d0", created_at="T0")
        signals.landed[("m7", "d0")] = False
        await run_sync(stores, signals, [task()])
        signals.calls.clear()
        await run_sync(stores, signals, [task()])
        # uat's sha is unchanged, so no compare; prod answers nothing to compare.
        assert signals.calls == [("deploy", "p", "uat"), ("deploy", "p", "prod")]

    async def test_landed_is_final(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys = {"uat": Deploy("d1", "T3"), "prod": Deploy("d1", "T3")}
        signals.landed[("m7", "d1")] = True
        await run_sync(stores, signals, [task()])
        signals.calls.clear()
        signals.deploys = {"uat": None, "prod": None}
        await run_sync(stores, signals, [task()])
        assert signals.calls == []
        stored = await stores[0].read("p", 7)
        assert stored is not None and stored.envs == {
            "uat": EnvLanding("landed", "d1", "T3"),
            "live": EnvLanding("landed", "d1", "T3"),
        }

    async def test_a_pr_closed_unmerged_is_never_read_again(self, stores):
        signals = FakeSignals()
        signals.merged[7] = Merge(
            "u/7", "Add x", merged_at="", merge_sha="", state="CLOSED"
        )
        await run_sync(stores, signals, [task()])
        signals.calls.clear()
        await run_sync(stores, signals, [task()])
        assert signals.calls == []

    async def test_a_merge_with_no_commit_yet_is_read_again(self, stores):
        signals = FakeSignals()
        signals.merged[7] = replace(MERGE, merge_sha="")
        await run_sync(stores, signals, [task()])
        signals.merged[7] = MERGE
        signals.calls.clear()
        await run_sync(stores, signals, [task()])
        assert signals.calls[0] == ("merges", "p", (7,))
        stored = await stores[0].read("p", 7)
        assert stored is not None and stored.merge_sha == "m7"

    async def test_no_deploy_block_reads_no_deploys(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        await run_sync(stores, signals, [task()], config=NO_DEPLOY)
        assert signals.calls == [("merges", "p", (7,))]
        assert [s for s, _ in await recorded(stores)] == ["done", "merged"]

    async def test_each_step_is_written_once(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        await run_sync(stores, signals, [task()], now="T1")
        await run_sync(stores, signals, [task()], now="T2")
        assert await recorded(stores) == [
            ("done", "T1"),
            ("merged", "2026-10-01T00:00:00Z"),
        ]

    async def test_a_gap_catches_up_every_step_at_once(self, stores):
        """The server was down while the PR merged and reached both envs."""
        signals = FakeSignals()
        signals.merged[7] = MERGE
        signals.deploys = {"uat": Deploy("d1", "T3"), "prod": Deploy("d1", "T5")}
        signals.landed[("m7", "d1")] = True
        await run_sync(stores, signals, [task()], now="T9")
        assert await recorded(stores) == [
            ("done", "T9"),
            ("merged", "2026-10-01T00:00:00Z"),
            ("uat", "T3"),
            ("live", "T5"),
        ]

    async def test_a_task_not_done_writes_nothing(self, stores):
        signals = FakeSignals()
        signals.merged[7] = MERGE
        await run_sync(stores, signals, [task(status="in-progress")])
        assert await recorded(stores) == []

    async def test_each_project_is_read_once_under_its_own_config(self, stores):
        signals = FakeSignals()
        signals.merged = {7: MERGE, 8: replace(MERGE, url="u/8", merge_sha="m8")}
        signals.deploys["uat"] = Deploy(sha="d1", created_at="T3")
        signals.landed = {("m7", "d1"): True, ("m8", "d1"): True}
        prs, steps = stores
        tracked = [
            TrackedTask("p", "t1", "done", 7),
            TrackedTask("p", "t2", "done", 8),
            TrackedTask("q", "t1", "done", 7),
        ]
        configs = {
            "p": MaelstromConfig(deploy_environments={"uat": "uat"}),
            "q": NO_DEPLOY,
        }
        await sync(prs, steps, signals, tracked, configs.__getitem__, "T9")

        assert [c for c in signals.calls if c[0] == "merges"] == [
            ("merges", "p", (7, 8)),
            ("merges", "q", (7,)),
        ]
        assert sorted((e.project, e.task_id, e.step) for e in await steps.list()) == [
            ("p", "t1", "done"),
            ("p", "t1", "merged"),
            ("p", "t1", "uat"),
            ("p", "t2", "done"),
            ("p", "t2", "merged"),
            ("p", "t2", "uat"),
            ("q", "t1", "done"),
            ("q", "t1", "merged"),
        ]


class TestProjectConfig:
    def test_a_broken_config_reads_as_the_default(self, tmp_path):
        main = tmp_path / "p" / "_main"
        main.mkdir(parents=True)
        (main / ".maelstrom.yaml").write_text("deploy: [this is not a map\n")
        assert project_config(tmp_path, "p") == MaelstromConfig()

    def test_a_project_s_deploy_block_is_read(self, tmp_path):
        main = tmp_path / "p" / "_main"
        main.mkdir(parents=True)
        (main / ".maelstrom.yaml").write_text(
            "deploy:\n  environments:\n    live: production\n"
        )
        assert project_config(tmp_path, "p").deploy_environments == {
            "live": "production"
        }
