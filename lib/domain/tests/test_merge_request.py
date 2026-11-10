"""Tests for mael_domain.merge_request: the Merge request and its Builds."""

from dataclasses import replace

import pytest

from mael_domain import merge_request as mr_model
from mael_domain.merge_request import Build, MergeRequestError

T0, T1, T2 = "2026-11-10T09:00:00Z", "2026-11-10T09:05:00Z", "2026-11-10T09:10:00Z"
SHA_A, SHA_B, BASE = "a" * 40, "b" * 40, "c" * 40


def _submit(existing=None, *, sha=SHA_A, request_id="mr1", build_id="b1", now=T0):
    return mr_model.submit(
        existing,
        request_id=request_id,
        project="demo",
        task_id="k1",
        branch="feat/x",
        sha=sha,
        base=BASE,
        build_id=build_id,
        now=now,
    )


def _running(build: Build) -> Build:
    return mr_model.start(build, "_build1", T1)


class TestSubmit:
    def test_the_first_submit_opens_a_request_linked_to_a_queued_build(self):
        mr, build = _submit()
        assert mr == mr_model.MergeRequest(
            "mr1", "demo/k1", "demo", "k1", "feat/x", SHA_A, BASE, "open", "b1",
            T0, T0,
        )  # fmt: skip
        assert build == Build("b1", "mr1", "demo", SHA_A, BASE, "queued", T0)

    def test_a_later_submit_updates_the_open_request(self):
        first, _ = _submit()
        mr, build = _submit(first, sha=SHA_B, request_id="mr2", build_id="b2", now=T1)
        assert (mr.id, mr.sha, mr.build, mr.opened, mr.updated) == (
            "mr1",
            SHA_B,
            "b2",
            T0,
            T1,
        )
        assert (build.merge_request, build.sha) == ("mr1", SHA_B)

    def test_a_submit_reopens_a_withdrawn_request(self):
        first, _ = _submit()
        mr, _ = _submit(mr_model.withdraw(first, T1), request_id="mr2", build_id="b2")
        assert (mr.id, mr.state) == ("mr1", "open")

    def test_a_submit_after_a_merge_opens_a_new_request(self):
        first, _ = _submit()
        merged = replace(first, state="merged")
        mr, build = _submit(merged, request_id="mr2", build_id="b2")
        assert (mr.id, mr.state, build.merge_request) == ("mr2", "open", "mr2")

    def test_only_an_open_request_withdraws(self):
        first, _ = _submit()
        with pytest.raises(MergeRequestError):
            mr_model.withdraw(mr_model.withdraw(first, T1), T2)


class TestBuildTransitions:
    def test_a_build_runs_then_learns_its_job(self):
        _, build = _submit()
        running = mr_model.job_started(_running(build), 7)
        assert (running.state, running.slot, running.job) == ("running", "_build1", 7)

    def test_a_passed_job_passes_the_build(self):
        _, build = _submit()
        ended = mr_model.job_ended(_running(build), {"state": "passed"}, T2)
        assert (ended.state, ended.ended) == ("passed", T2)

    def test_a_failed_job_names_its_failed_gates_and_log(self):
        _, build = _submit()
        job = {
            "state": "failed",
            "log": "/tmp/job7.log",
            "gates": [
                {"name": "lint", "state": "passed"},
                {"name": "test", "state": "failed", "code": 1},
            ],
        }
        ended = mr_model.job_ended(_running(build), job, T2)
        assert ended.state == "failed"
        assert ended.detail == "- test: failed (exit 1)\nLog: /tmp/job7.log"

    def test_a_fail_fast_cancel_with_a_failed_gate_fails(self):
        _, build = _submit()
        job = {"state": "cancelled", "gates": [{"name": "test", "state": "failed"}]}
        assert mr_model.job_ended(_running(build), job, T2).state == "failed"

    def test_a_cancelled_job_with_no_failed_gate_cancels(self):
        _, build = _submit()
        job = {"state": "cancelled", "gates": [{"name": "test", "state": "cancelled"}]}
        assert mr_model.job_ended(_running(build), job, T2).state == "cancelled"

    def test_stale_and_step_failed_end_a_running_build(self):
        _, build = _submit()
        assert mr_model.stale(_running(build), T2).state == "stale"
        failed = mr_model.step_failed(_running(build), "checkout", "no sha", T2)
        assert (failed.state, failed.detail) == ("failed", "checkout: no sha")

    def test_a_queued_build_cancels(self):
        _, build = _submit()
        cancelled = mr_model.cancel(build, "superseded", T1)
        assert (cancelled.state, cancelled.detail) == ("cancelled", "superseded")

    def test_an_ended_build_refuses_another_end(self):
        _, build = _submit()
        passed = mr_model.job_ended(_running(build), {"state": "passed"}, T2)
        with pytest.raises(MergeRequestError):
            mr_model.cancel(passed, "late", T2)

    def test_requeue_links_a_new_build_of_the_same_sha(self):
        mr, build = _submit()
        cancelled = mr_model.cancel(_running(build), "server restarted", T1)
        mr2, again = mr_model.requeue(mr, cancelled, "b2", T2)
        assert mr2 == replace(mr, build="b2", updated=T2)
        assert again == Build("b2", "mr1", "demo", SHA_A, BASE, "queued", T2)


class TestStatus:
    @pytest.mark.parametrize(
        ("build_state", "expected"),
        [
            ("queued", "queued"),
            ("running", "building"),
            ("passed", "passed"),
            ("failed", "failed"),
            ("stale", "stale"),
            ("cancelled", "failed"),
        ],
    )
    def test_an_open_request_reads_its_linked_build(self, build_state, expected):
        mr, build = _submit()
        assert mr_model.status(mr, replace(build, state=build_state)) == expected

    @pytest.mark.parametrize("state", ["withdrawn", "merged"])
    def test_a_closed_request_reads_its_own_state(self, state):
        mr, build = _submit()
        assert mr_model.status(replace(mr, state=state), build) == state


class TestNextBuilds:
    def _queued(self, id, at):
        return Build(id, "mr1", "demo", SHA_A, BASE, "queued", at)

    def test_the_oldest_linked_builds_take_the_free_slots(self):
        b1, b2, b3 = (
            self._queued("b1", T2),
            self._queued("b2", T0),
            self._queued("b3", T1),
        )
        picked = mr_model.next_builds(
            [b1, b2, b3], frozenset({"b1", "b2", "b3"}), ["_build1", "_build2"]
        )
        assert [(b.id, slot) for b, slot in picked] == [
            ("b2", "_build1"),
            ("b3", "_build2"),
        ]

    def test_a_build_no_longer_linked_never_starts(self):
        b1, b2 = self._queued("b1", T0), self._queued("b2", T1)
        picked = mr_model.next_builds([b1, b2], frozenset({"b2"}), ["_build1"])
        assert [b.id for b, _ in picked] == ["b2"]

    def test_slot_names(self):
        assert mr_model.build_slot_names(2) == ["_build1", "_build2"]
