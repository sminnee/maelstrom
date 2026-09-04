"""The desk: which tasks the user has put on the canvas.

Pure table maths. Every function returns a new table, so the caller decides
when a change is published and saved.
"""

import pytest

from maelstrom.orchestrator.desk import (
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
