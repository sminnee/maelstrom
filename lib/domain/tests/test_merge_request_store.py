"""Tests for mael_domain.merge_request_store backends."""

from dataclasses import replace

import pytest

from mael_domain.merge_request import Build, MergeRequest
from mael_domain.merge_request_store import (
    InMemoryBuildStore,
    InMemoryMergeRequestStore,
    SqliteBuildStore,
    SqliteMergeRequestStore,
)

T0, T1 = "2026-11-10T09:00:00Z", "2026-11-10T09:05:00Z"

MR = MergeRequest(
    id="mr1",
    task="demo/k1",
    project="demo",
    task_id="k1",
    branch="feat/x",
    sha="a" * 40,
    base="c" * 40,
    state="open",
    build="b1",
    opened=T0,
    updated=T1,
    # Set, so the round trip covers the columns the second rung adds.
    description="## Why\n\nFaster builds.",
    merged=T1,
    told="merged",
)
BUILD = Build(
    id="b1",
    merge_request="mr1",
    project="demo",
    sha="a" * 40,
    base="c" * 40,
    state="running",
    queued_at=T0,
    started=T1,
    ended="",
    slot="_build1",
    job=7,
    detail="",
    told="queued",
)


@pytest.fixture(params=["memory", "sqlite"])
def requests(request, state_db):
    """Run each contract test against every backend."""
    if request.param == "memory":
        return InMemoryMergeRequestStore()
    return SqliteMergeRequestStore(state_db)


@pytest.fixture(params=["memory", "sqlite"])
def builds(request, state_db):
    """Run each contract test against every backend."""
    if request.param == "memory":
        return InMemoryBuildStore()
    return SqliteBuildStore(state_db)


class TestMergeRequestStoreContract:
    async def test_save_then_read_round_trips_every_field(self, requests):
        await requests.save(MR)
        assert await requests.read("mr1") == MR
        assert await requests.read("mr9") is None

    async def test_current_is_the_updated_request(self, requests):
        await requests.save(MR)
        await requests.save(replace(MR, sha="b" * 40))
        assert (await requests.current("demo/k1")).sha == "b" * 40

    async def test_current_after_a_merge_is_the_new_request(self, requests):
        await requests.save(replace(MR, state="merged"))
        await requests.save(replace(MR, id="mr2"))
        await requests.save(replace(MR, id="mr3", task="demo/k2"))
        assert (await requests.current("demo/k1")).id == "mr2"
        assert [mr.id for mr in await requests.for_task("demo/k1")] == ["mr2", "mr1"]
        assert [mr.id for mr in await requests.open()] == ["mr3", "mr2"]

    async def test_next_id_counts_past_the_highest(self, requests):
        assert await requests.next_id() == "mr1"
        await requests.save(replace(MR, id="mr9"))
        await requests.save(replace(MR, id="mr10"))
        assert await requests.next_id() == "mr11"


class TestBuildStoreContract:
    async def test_save_then_read_round_trips_every_field(self, builds):
        await builds.save(BUILD)
        assert await builds.read("b1") == BUILD

    async def test_for_request_is_newest_first(self, builds):
        for id in ("b2", "b10", "b1"):
            await builds.save(replace(BUILD, id=id))
        await builds.save(replace(BUILD, id="b3", merge_request="mr2"))
        assert [b.id for b in await builds.for_request("mr1")] == ["b10", "b2", "b1"]

    async def test_unfinished_is_queued_and_running(self, builds):
        await builds.save(replace(BUILD, id="b1", state="passed", job=5))
        await builds.save(replace(BUILD, id="b2", state="running", job=7))
        await builds.save(replace(BUILD, id="b3", state="queued", job=0))
        assert [b.id for b in await builds.unfinished()] == ["b3", "b2"]
        assert await builds.next_id() == "b4"
