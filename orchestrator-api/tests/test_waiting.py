"""``waiting_on_user``: what ``GET /api/waiting`` reports, over a world.

The rules are ``CONTEXT.md``'s **Attention item** and **Unanswered agent**, and
``docs/specs/tasks/CONTEXT.md``'s **Awaiting merge**.
"""

import pytest
from agent_fixtures import make_agent, make_document

from mael_domain import task as model
from mael_domain.protocol import empty_world
from mael_orchestrator.waiting import waiting_on_user
from mael_orchestrator.world_build import task_entity

TASK = "northwind/NORT-7"


def make_task(notebook_id: str = "NORT-7", status: str = "in-progress", **over):
    task = task_entity(
        model.Task(
            id=notebook_id, title="Add order export", project="northwind", status=status
        ),
        actionable=True,
    )
    task.update(over)
    return task


def idle_agent(**over):
    fields = {
        "id": "ag1",
        "state": "idle",
        "taskId": TASK,
        "lastMessage": "Which export format?",
        "lastMessageAt": "2026-09-04T09:00:00Z",
    }
    return make_agent(**(fields | over))


def make_item(item_id: str, kind: str, **over):
    item = {
        "id": item_id,
        "kind": kind,
        "agentId": "ag1",
        "taskId": TASK,
        "documentId": None,
        "requestId": None,
        "summary": f"{kind} summary",
        "raisedAt": "2026-09-04T09:00:00Z",
        "clearedAt": None,
    }
    item.update(over)
    return item


def world_of(*, tasks=(), agents=(), attention=(), documents=(), worktrees=()):
    world = empty_world()
    for table, rows in (
        ("tasks", tasks),
        ("agents", agents),
        ("attention", attention),
        ("documents", documents),
        ("worktrees", worktrees),
    ):
        for row in rows:
            world[table][row["id"]] = row  # type: ignore[literal-required]
    return world


def test_an_idle_agent_that_left_a_message_on_unfinished_work_is_unanswered():
    report = waiting_on_user(world_of(tasks=[make_task()], agents=[idle_agent()]))

    assert report["unanswered"] == [
        {
            "agentId": "ag1",
            "taskId": TASK,
            "project": "northwind",
            "taskTitle": "Add order export",
            "lastMessage": "Which export format?",
            "lastMessageAt": "2026-09-04T09:00:00Z",
        }
    ]


def test_unanswered_agents_come_oldest_message_first():
    report = waiting_on_user(
        world_of(
            tasks=[make_task("NORT-7"), make_task("NORT-8")],
            agents=[
                idle_agent(),
                idle_agent(
                    id="ag2",
                    taskId="northwind/NORT-8",
                    lastMessageAt="2026-09-03T09:00:00Z",
                ),
            ],
        )
    )

    assert [row["agentId"] for row in report["unanswered"]] == ["ag2", "ag1"]


@pytest.mark.parametrize(
    ("task", "agent", "worktree"),
    [
        pytest.param(
            make_task(), idle_agent(), {"gate": {"jobId": "4"}}, id="gate-job"
        ),
        pytest.param(make_task(), idle_agent(lastMessage=""), None, id="said-nothing"),
        pytest.param(make_task(status="merged"), idle_agent(), None, id="done-task"),
        pytest.param(make_task(status="cancelled"), idle_agent(), None, id="cancelled"),
        pytest.param(make_task(), idle_agent(state="processing"), None, id="working"),
    ],
)
def test_an_idle_agent_is_not_unanswered_when(task, agent, worktree):
    worktrees = [{"id": agent["worktreeId"], **worktree}] if worktree else []
    report = waiting_on_user(
        world_of(tasks=[task], agents=[agent], worktrees=worktrees)
    )

    assert report["unanswered"] == []


def test_a_free_agent_is_not_unanswered():
    report = waiting_on_user(world_of(agents=[idle_agent(taskId="")]))

    assert report["unanswered"] == []


def test_a_built_task_no_agent_works_on_is_awaiting_merge_with_its_prs():
    prs = [{"number": 118, "url": "https://github.com/acme/northwind/pull/118"}]
    report = waiting_on_user(world_of(tasks=[make_task(status="built", prs=prs)]))

    assert report["awaitingMerge"] == [
        {
            "taskId": TASK,
            "project": "northwind",
            "title": "Add order export",
            "prs": prs,
        }
    ]


@pytest.mark.parametrize(
    ("agents", "attention"),
    [
        pytest.param([idle_agent(state="processing")], [], id="finalising"),
        pytest.param([idle_agent()], [make_item("at1", "question")], id="asking"),
        pytest.param([idle_agent(state="exited", exitCode=1)], [], id="faulted"),
    ],
)
def test_a_built_task_is_not_awaiting_merge_when(agents, attention):
    report = waiting_on_user(
        world_of(tasks=[make_task(status="built")], agents=agents, attention=attention)
    )

    assert report["awaitingMerge"] == []


def test_an_agent_with_an_open_item_is_listed_by_its_item_not_as_unanswered():
    report = waiting_on_user(
        world_of(
            tasks=[make_task()],
            agents=[idle_agent()],
            attention=[make_item("at1", "question")],
        )
    )

    assert report["unanswered"] == []
    assert [item["id"] for item in report["attention"]] == ["at1"]


def test_open_items_come_by_kind_then_oldest_first_and_cleared_ones_are_left_out():
    items = [
        make_item("q-new", "question", raisedAt="2026-09-04T10:00:00Z"),
        make_item("q-old", "question", raisedAt="2026-09-04T08:00:00Z"),
        make_item("exit", "agent_exited", raisedAt="2026-09-01T00:00:00Z"),
        make_item("plan", "plan_review", raisedAt="2026-09-04T11:00:00Z"),
        make_item("gone", "plan_review", clearedAt="2026-09-04T12:00:00Z"),
        make_item("perm", "permission"),
        make_item("doc", "document_review"),
    ]
    report = waiting_on_user(world_of(attention=items))

    assert [item["id"] for item in report["attention"]] == [
        "plan",
        "doc",
        "q-old",
        "q-new",
        "perm",
        "exit",
    ]


def test_an_item_carries_its_project_task_title_and_document_title():
    document = make_document(id="doc-1", title="Task set", kind="tasks")
    free = make_agent(id="ag9", taskId="", project="acme")
    report = waiting_on_user(
        world_of(
            tasks=[make_task()],
            agents=[free],
            documents=[document],
            attention=[
                make_item("doc", "document_review", documentId="doc-1"),
                make_item("free", "question", agentId="ag9", taskId=None),
            ],
        )
    )

    named = {
        item["id"]: (item["project"], item["taskTitle"], item["documentTitle"])
        for item in report["attention"]
    }
    assert named == {
        "doc": ("northwind", "Add order export", "Task set"),
        "free": ("acme", "", ""),
    }


@pytest.mark.parametrize(
    ("agents", "unanswered"),
    [
        pytest.param(
            # The exited agent is the task's last, so only the live one wins it.
            [idle_agent(id="new"), idle_agent(id="old", state="exited", exitCode=0)],
            ["new"],
            id="a-live-agent-over-an-exited-one",
        ),
        pytest.param(
            # The subagent comes first, so it would be the task's live agent.
            [
                idle_agent(id="ag1.1", parent="ag1"),
                idle_agent(id="ag1", state="processing"),
            ],
            [],
            id="a-subagent-is-not-the-task-s-agent",
        ),
    ],
)
def test_a_task_is_read_by_its_agent(agents, unanswered):
    report = waiting_on_user(world_of(tasks=[make_task()], agents=agents))

    assert [row["agentId"] for row in report["unanswered"]] == unanswered


def test_an_item_that_names_only_the_agent_keeps_it_from_unanswered():
    report = waiting_on_user(
        world_of(
            tasks=[make_task()],
            agents=[idle_agent()],
            attention=[make_item("at1", "question", taskId=None)],
        )
    )

    assert report["unanswered"] == []


def test_an_agent_waits_on_the_gate_of_its_task_s_branch_worktree():
    task = make_task(branch="feat/orders")
    worktree = {
        "id": "northwind-bravo",
        "project": "northwind",
        "branch": "feat/orders",
        "isClosed": False,
        "gate": {"jobId": "4"},
    }
    agent = idle_agent(worktreeId="northwind-gone")

    report = waiting_on_user(
        world_of(tasks=[task], agents=[agent], worktrees=[worktree])
    )

    assert report["unanswered"] == []


def test_tasks_awaiting_merge_come_by_project_then_id():
    tasks = [
        make_task("M-2", status="built", project="maelstrom", id="maelstrom/M-2"),
        make_task("U-1", status="built", project="maelstrom-ui", id="maelstrom-ui/U-1"),
        make_task("M-1", status="built", project="maelstrom", id="maelstrom/M-1"),
    ]

    report = waiting_on_user(world_of(tasks=tasks))

    assert [row["taskId"] for row in report["awaitingMerge"]] == [
        "maelstrom/M-1",
        "maelstrom/M-2",
        "maelstrom-ui/U-1",
    ]


def test_an_item_on_a_task_that_is_history_is_left_out_but_an_exit_stays():
    report = waiting_on_user(
        world_of(
            tasks=[make_task(status="merged"), make_task("NORT-8")],
            agents=[idle_agent(id="ag2", taskId="northwind/NORT-8", state="exited")],
            attention=[
                make_item("stale", "question", agentId=None),
                make_item(
                    "exit", "agent_exited", agentId="ag2", taskId="northwind/NORT-8"
                ),
            ],
        )
    )

    assert [item["id"] for item in report["attention"]] == ["exit"]
