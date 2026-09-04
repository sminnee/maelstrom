"""Building wire entities from the notebook, ``list-all`` rows and agent rows."""

import json
from pathlib import Path

import pytest

from maelstrom import task as model
from maelstrom.agent_model import AgentState, apply_event, build_agent_row, mark_exited
from maelstrom.orchestrator.world_build import (
    agent_entity,
    diff_kind,
    link_agent,
    parse_agent_state,
    project_entity,
    split_task_key,
    task_entity,
    task_key,
    worktree_entity,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"


TASK_MD = """---
id: NORT-7.2
title: Add order export
project: northwind
command: plan-task
mode: auto
branch: feat/orders
parent: NORT-7
follows: [NORT-7.1]
created: "2026-09-01T00:00:00"
updated: "2026-09-02T00:00:00"
priority: high
model: claude-opus-5
base: feat/base
---

## Content

Do the export.

## Steps

- [x] Write the test
- [ ] Make it pass

## Log

- 2026-09-01T10:00:00 started
- 2026-09-01T11:00:00 blocked on CI
"""


def test_task_entity_mirrors_the_frontmatter_and_derives_actionable():
    task = model.Task.from_markdown(TASK_MD, status="in-progress")
    entity = task_entity(task, actionable=False)
    assert entity["id"] == "northwind/NORT-7.2"
    assert entity["notebookId"] == "NORT-7.2"
    assert entity["project"] == "northwind"
    assert entity["status"] == "in-progress"
    assert entity["command"] == "plan-task"
    assert entity["actionable"] is False
    assert entity["follows"] == ["northwind/NORT-7.1"]
    assert entity["parent"] == "NORT-7"
    assert entity["priority"] == "high"
    assert entity["model"] == "claude-opus-5"
    assert entity["base"] == "feat/base"
    assert entity["content"] == "Do the export."
    assert entity["steps"] == [
        {"text": "Write the test", "done": True},
        {"text": "Make it pass", "done": False},
    ]
    assert entity["log"] == [
        {"ts": "2026-09-01T10:00:00", "text": "started"},
        {"ts": "2026-09-01T11:00:00", "text": "blocked on CI"},
    ]
    assert entity["created"] == "2026-09-01T00:00:00"
    assert entity["updated"] == "2026-09-02T00:00:00"


def test_a_wrapped_log_line_continues_its_entry_and_prose_steps_stay_open():
    from maelstrom.orchestrator.world_build import parse_log, parse_steps

    assert parse_log(
        "- 2026-09-01T10:00:00 started the\n  long job\n- 2026-09-01T11:00:00 done"
    ) == [
        {"ts": "2026-09-01T10:00:00", "text": "started the long job"},
        {"ts": "2026-09-01T11:00:00", "text": "done"},
    ]
    assert parse_log("no timestamp here") == [{"ts": "", "text": "no timestamp here"}]
    assert parse_steps("Just prose\n- [x] Done one") == [
        {"text": "Just prose", "done": False},
        {"text": "Done one", "done": True},
    ]


def test_task_entity_defaults_a_missing_branch_to_the_default_branch():
    task = model.Task(id="NORT-9", title="x", project="northwind")
    assert task_entity(task, actionable=True)["branch"] == model.default_branch(
        "NORT-9", ""
    )


LIST_ALL_ROW = {
    "name": "alpha",
    "folder": "northwind-alpha",
    "path": "/Users/dev/Projects/northwind/northwind-alpha",
    "branch": "feat/orders",
    "base": "main",
    "is_closed": False,
    "dirty_files": 2,
    "local_commits": 1,
    "pr_number": 42,
    "pr_commits": 5,
    "pushed_commits": None,
    "app_url": "http://localhost:3070",
    "app_running": True,
    "session_count": 1,
}


def test_worktree_entity_mirrors_a_list_all_row():
    entity = worktree_entity("northwind", LIST_ALL_ROW)
    assert entity == {
        "id": "northwind-alpha",
        "project": "northwind",
        "nato": "alpha",
        "path": "/Users/dev/Projects/northwind/northwind-alpha",
        "branch": "feat/orders",
        "base": "main",
        "isClosed": False,
        "dirtyFiles": 2,
        "localCommits": 1,
        "prNumber": 42,
        "appUrl": "http://localhost:3070",
        "appRunning": True,
        "sessionCount": 1,
    }


def test_worktree_entity_blanks_the_nulls_of_a_closed_row():
    row = {
        **LIST_ALL_ROW,
        "branch": None,
        "base": None,
        "is_closed": True,
        "app_url": None,
    }
    entity = worktree_entity("northwind", row)
    assert entity["branch"] == ""
    assert entity["base"] == ""
    assert entity["isClosed"] is True
    assert entity["appUrl"] == ""


def test_project_entity_carries_the_stack_tip():
    entity = project_entity(
        {"name": "northwind", "path": "/p", "stack_tip": "feat/base"}
    )
    assert entity == {"id": "northwind", "name": "northwind", "stackTip": "feat/base"}


def replay(name: str) -> AgentState:
    state = AgentState(
        agent_id="a1", cwd="/Users/dev/Projects/northwind/northwind-alpha"
    )
    for line in (FIXTURES / name).read_text().splitlines():
        if line.strip():
            state = apply_event(state, json.loads(line))
    return state


def test_agent_entity_from_a_live_row():
    row = build_agent_row(replay("normal-turn.jsonl"))
    entity = agent_entity(
        row,
        task_id="NORT-7",
        project="northwind",
        worktree_id="northwind-alpha",
    )
    assert entity["id"] == "a1"
    assert entity["state"] == "idle"
    assert entity["session"] == "029ed263-b318-4d4e-a661-32f9c9f23f19"
    assert entity["cwd"] == "/Users/dev/Projects/northwind/northwind-alpha"
    assert entity["lastMessage"] == "Hello there, friend"
    assert entity["costUsd"] == pytest.approx(0.1496)
    assert entity["exitCode"] is None
    assert entity["pendingRequestId"] is None
    assert entity["taskId"] == "NORT-7"


def test_agent_entity_parses_the_exit_code_out_of_the_row_state():
    row = build_agent_row(mark_exited(replay("normal-turn.jsonl"), 3))
    entity = agent_entity(row, task_id="", project="", worktree_id="")
    assert entity["state"] == "exited"
    assert entity["exitCode"] == 3


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("idle", ("idle", None)),
        ("exited(0)", ("exited", 0)),
        ("exited", ("exited", None)),
    ],
)
def test_parse_agent_state(raw, expected):
    assert parse_agent_state(raw) == expected


def test_link_agent_finds_the_worktree_by_cwd_and_the_task_by_session():
    worktrees = {"northwind-alpha": worktree_entity("northwind", LIST_ALL_ROW)}
    task = task_entity(
        model.Task(id="NORT-7", title="x", project="northwind", command="plan-task"),
        actionable=True,
    )
    session = model.session_id_for("northwind", "NORT-7")
    link = link_agent(
        {"cwd": LIST_ALL_ROW["path"], "session": session},
        worktrees=worktrees,
        tasks={"northwind/NORT-7": task},
    )
    assert link.task_id == "northwind/NORT-7"
    assert link.project == "northwind"
    assert link.worktree_id == "northwind-alpha"


def test_link_agent_with_no_match_is_unlinked():
    link = link_agent(
        {"cwd": "/private/tmp", "session": "nope"}, worktrees={}, tasks={}
    )
    assert (link.task_id, link.project, link.worktree_id) == ("", "", "")


def test_diff_kind_upserts_new_and_changed_and_removes_gone():
    old = {"a": {"id": "a", "v": 1}, "b": {"id": "b", "v": 1}, "c": {"id": "c", "v": 1}}
    new = {"a": {"id": "a", "v": 1}, "b": {"id": "b", "v": 2}, "d": {"id": "d", "v": 1}}
    events = diff_kind("task", old, new)
    assert events == [
        {"type": "upsert", "kind": "task", "entity": {"id": "b", "v": 2}},
        {"type": "upsert", "kind": "task", "entity": {"id": "d", "v": 1}},
        {"type": "remove", "kind": "task", "id": "c"},
    ]


def test_task_key_round_trips_a_project_and_a_notebook_id():
    key = task_key("northwind", "NORT-7.2")
    assert key == "northwind/NORT-7.2"
    assert split_task_key(key) == ("northwind", "NORT-7.2")


def test_split_task_key_rejects_a_bare_id():
    with pytest.raises(ValueError):
        split_task_key("NORT-7.2")


def test_agent_entity_of_a_top_level_row_has_no_parent():
    row = build_agent_row(replay("normal-turn.jsonl"))
    entity = agent_entity(row, task_id="", project="", worktree_id="")
    assert entity["parent"] == ""
    assert entity["description"] == ""


def test_agent_entity_of_a_subagent_row_names_its_parent_and_description():
    from maelstrom.agent_model import build_subagent_rows

    [row] = build_subagent_rows(replay("subagent-turn.jsonl"))
    entity = agent_entity(row, task_id="NORT-7", project="northwind", worktree_id="w")
    keys = ("id", "parent", "description", "state", "exitCode", "taskId")
    assert {k: entity[k] for k in keys} == {
        "id": "a1.1",
        "parent": "a1",
        "description": "List and summarise docs/dev",
        "state": "exited",
        "exitCode": 0,
        "taskId": "NORT-7",
    }
