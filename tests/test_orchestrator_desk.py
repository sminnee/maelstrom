"""The desk: which tasks the user has put on the canvas.

Pure table maths. Every function returns a new table, so the caller decides
when a change is published and saved.
"""

import pytest

from maelstrom.orchestrator.desk import add, prune, remove

NOW = "2026-09-04T09:00:00Z"
LATER = "2026-09-05T09:00:00Z"


def test_add_puts_a_task_on_the_desk_with_the_time_it_arrived():
    assert add({}, "askastro/2026-06-11.1", NOW) == {
        "askastro/2026-06-11.1": {"id": "askastro/2026-06-11.1", "addedAt": NOW}
    }


def test_add_is_idempotent_and_keeps_the_first_time():
    once = add({}, "a/1", NOW)
    assert add(once, "a/1", LATER) == once


def test_add_does_not_mutate_the_table_it_is_given():
    table = {}
    add(table, "a/1", NOW)
    assert table == {}


def test_remove_takes_a_task_off_the_desk():
    assert remove(add({}, "a/1", NOW), "a/1") == {}


def test_remove_raises_for_a_task_that_is_not_on_the_desk():
    with pytest.raises(KeyError):
        remove({}, "a/1")


def test_remove_does_not_mutate_the_table_it_is_given():
    table = add({}, "a/1", NOW)
    remove(table, "a/1")
    assert "a/1" in table


def test_prune_drops_entries_for_tasks_that_are_gone():
    table = add(add({}, "a/1", NOW), "a/2", NOW)
    assert prune(table, {"a/2"}, ["a"]) == {"a/2": {"id": "a/2", "addedAt": NOW}}


def test_prune_keeps_the_entries_of_a_project_the_reading_missed():
    """A project the scan did not see said nothing about its tasks."""
    table = add(add({}, "a/1", NOW), "b/1", NOW)
    assert prune(table, {"a/1"}, ["a"]) == table


def test_prune_does_not_mutate_the_table_it_is_given():
    table = add({}, "a/1", NOW)
    prune(table, set(), ["a"])
    assert "a/1" in table
