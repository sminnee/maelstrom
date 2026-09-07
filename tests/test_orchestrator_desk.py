"""The desk: which tasks the user has put on the canvas.

Pure table maths. Every function returns a new table, so the caller decides
when a change is published and saved.
"""

import pytest

from maelstrom.orchestrator.desk import (
    active_branches,
    add,
    desk_id_for_agent,
    desk_id_for_task,
    drop_unknown_agents,
    prune,
    remove,
    split_desk_id,
)

NOW = "2026-09-04T09:00:00Z"
LATER = "2026-09-05T09:00:00Z"


TASK_1 = "task:a/1"
TASK_2 = "task:a/2"


def test_add_puts_an_entry_on_the_desk_with_the_time_it_arrived():
    assert add({}, "task:askastro/2026-06-11.1", NOW) == {
        "task:askastro/2026-06-11.1": {
            "id": "task:askastro/2026-06-11.1",
            "addedAt": NOW,
        }
    }


def test_add_is_idempotent_and_keeps_the_first_time():
    once = add({}, TASK_1, NOW)
    assert add(once, TASK_1, LATER) == once


def test_add_does_not_mutate_the_table_it_is_given():
    table = {}
    add(table, TASK_1, NOW)
    assert table == {}


def test_remove_takes_an_entry_off_the_desk():
    assert remove(add({}, TASK_1, NOW), TASK_1) == {}


def test_remove_raises_for_an_entry_that_is_not_on_the_desk():
    with pytest.raises(KeyError):
        remove({}, TASK_1)


def test_remove_does_not_mutate_the_table_it_is_given():
    table = add({}, TASK_1, NOW)
    remove(table, TASK_1)
    assert TASK_1 in table


def test_prune_drops_entries_for_tasks_that_are_gone():
    table = add(add({}, TASK_1, NOW), TASK_2, NOW)
    assert prune(table, {"a/2"}, ["a"]) == {TASK_2: {"id": TASK_2, "addedAt": NOW}}


def test_prune_keeps_the_entries_of_a_project_the_reading_missed():
    """A project the scan did not see said nothing about its tasks."""
    table = add(add({}, TASK_1, NOW), "task:b/1", NOW)
    assert prune(table, {"a/1"}, ["a"]) == table


def test_prune_does_not_mutate_the_table_it_is_given():
    table = add({}, TASK_1, NOW)
    prune(table, set(), ["a"])
    assert TASK_1 in table


def test_prune_never_drops_a_free_agent_entry():
    """Nothing removes an agent from the world, so the entry always draws."""
    table = add({}, "agent:ag-1", NOW)
    assert prune(table, set(), ["a"]) == table


class TestDropUnknownAgents:
    """The load-time rule: an entry naming an agent the world lost is dropped."""

    def test_drops_an_agent_entry_the_world_has_no_agent_for(self):
        table = add({}, "agent:gone", NOW)
        assert drop_unknown_agents(table, set()) == {}

    def test_keeps_an_agent_entry_whose_agent_is_in_the_world(self):
        table = add({}, "agent:ag1", NOW)
        assert drop_unknown_agents(table, {"ag1"}) == table

    def test_keeps_every_task_entry_whatever_the_agents_are(self):
        table = add({}, TASK_1, NOW)
        assert drop_unknown_agents(table, set()) == table

    def test_does_not_mutate_the_table_it_is_given(self):
        table = add({}, "agent:gone", NOW)
        drop_unknown_agents(table, set())
        assert "agent:gone" in table


class TestDeskIds:
    """The prefixed id scheme: a desk entry names a task or a free agent."""

    def test_a_task_desk_id_carries_the_task_prefix(self):
        assert desk_id_for_task("a/1") == "task:a/1"

    def test_an_agent_desk_id_carries_the_agent_prefix(self):
        assert desk_id_for_agent("ag-1") == "agent:ag-1"

    def test_split_gives_back_the_kind_and_the_id(self):
        assert split_desk_id("task:a/1") == ("task", "a/1")
        assert split_desk_id("agent:ag-1") == ("agent", "ag-1")

    def test_split_raises_for_an_id_with_no_kind(self):
        with pytest.raises(ValueError):
            split_desk_id("a/1")

    def test_split_raises_for_an_unknown_kind(self):
        with pytest.raises(ValueError):
            split_desk_id("worktree:a-alpha")


class TestActiveBranches:
    """Which branches are worth asking GitHub about.

    GitHub charges by node count and the budget refills hourly, so the poll
    asks only about branches someone is working on. What counts is an agent on
    the desk: the desk is what the user put on the canvas, and it is kept by
    the task and agent polls, neither of which reads GitHub.
    """

    def _worktree(self, wt_id, branch, *, path="/p/alpha", pr_state="ready"):
        return {"id": wt_id, "path": path, "branch": branch, "prState": pr_state}

    def _agent(self, agent_id, *, worktree_id="w1", cwd="/p/alpha"):
        return {"id": agent_id, "worktreeId": worktree_id, "cwd": cwd}

    def test_a_desk_agents_branch_is_asked_about(self):
        table = add({}, desk_id_for_agent("a1"), NOW)
        branches = active_branches(
            table,
            agents={"a1": self._agent("a1")},
            worktrees={"w1": self._worktree("w1", "feat/orders")},
            tasks={},
        )
        assert branches == {"feat/orders"}

    def test_a_branch_with_nobody_at_it_is_not_asked_about(self):
        """The whole point: a project nobody is working on costs nothing."""
        branches = active_branches(
            {},
            agents={"a1": self._agent("a1")},
            worktrees={"w1": self._worktree("w1", "feat/orders")},
            tasks={},
        )
        assert branches == set()

    def test_a_new_worktree_is_asked_about_through_its_cwd(self):
        """An agent links to its worktree through the worktrees table a
        previous poll built, so a brand-new worktree has no `worktreeId` yet.
        Dropping it would skip the branch that just started work — the one case
        that matters most."""
        table = add({}, desk_id_for_agent("a1"), NOW)
        branches = active_branches(
            table,
            agents={"a1": self._agent("a1", worktree_id="", cwd="/p/bravo")},
            worktrees={"w2": self._worktree("w2", "feat/new", path="/p/bravo")},
            tasks={},
        )
        assert branches == {"feat/new"}

    def test_a_desk_tasks_branch_is_asked_about(self):
        """A task on the desk names its branch in the notebook, which the task
        poll reads. No agent has to be running yet."""
        table = add({}, desk_id_for_task("a/1"), NOW)
        branches = active_branches(
            table,
            agents={},
            worktrees={},
            tasks={"a/1": {"id": "a/1", "branch": "feat/planned"}},
        )
        assert branches == {"feat/planned"}
