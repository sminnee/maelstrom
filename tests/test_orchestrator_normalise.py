"""The normaliser, replayed over the recorded daemon streams, against its goldens.

The normaliser turns the agent host's raw stream-json into transcript items,
agent upserts, documents and attention items, so the wire never carries raw
stream-json. The goldens under ``tests/fixtures/agent_events/normalised/``
record what each fixture replays to; this file owns them, and
``UPDATE_GOLDEN=1 uv run pytest tests/test_orchestrator_normalise.py``
re-records. The TypeScript normaliser is held to the same files until it goes.
"""

import json
import os
from pathlib import Path

import pytest

from maelstrom.orchestrator.normalise import (
    NormaliseContext,
    _skill_loaded,
    apply_agent_detail,
    context_for_agent,
    mark_exited,
    normalise_stream_event,
)
from maelstrom.orchestrator.protocol import (
    ClientState,
    apply_event,
    empty_world,
    state_with,
)

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"
GOLDEN = FIXTURES / "normalised"
NOW = "2026-09-01T00:00:00Z"


def make_agent(**over) -> dict:
    """The seed agent ``web/src/test/fixtures.ts`` replays every fixture into."""
    agent = {
        "id": "agent-1",
        "parent": "",
        "description": "",
        "state": "processing",
        "session": "sess-1",
        "cwd": "/Users/dev/Projects/northwind/northwind-alpha",
        "model": "claude-opus-5",
        "permissionMode": "",
        "waitingOn": "",
        "lastMessage": "",
        "costUsd": 0,
        "taskId": "NORT-7",
        "project": "northwind",
        "worktreeId": "northwind-alpha",
        "exitCode": None,
        "pendingRequestId": None,
    }
    agent.update(over)
    return agent


def make_document(**over) -> dict:
    doc = {
        "id": "doc-1",
        "agentId": "agent-1",
        "taskId": "NORT-7",
        "kind": "plan",
        "title": "plan.md",
        "markdown": "# Plan\n\nDo the thing.\n",
        "version": 1,
        "status": "awaiting-review",
        "source": {"type": "plan_review", "requestId": "req-1", "planFilePath": ""},
    }
    doc.update(over)
    return doc


def read_fixture(name: str) -> list[dict]:
    lines = (FIXTURES / name).read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def seed(agents: list[dict], documents: list[dict] = ()) -> ClientState:
    world = empty_world()
    for agent in agents:
        world["agents"][agent["id"]] = agent
    for doc in documents:
        world["documents"][doc["id"]] = doc
    return state_with(world)


class Replayed:
    """A world plus the transcripts a client would have built from the events.

    The server keeps no transcript, so these tests accumulate one the way the
    browser's reducer does. That is what the goldens hold, and holding it here
    rather than in ``ClientState`` is the point: the projection is relayed,
    not stored.
    """

    def __init__(self, state: ClientState) -> None:
        self.state = state
        self.transcripts: dict[str, dict] = {}

    def take(self, events: list[dict]) -> None:
        for event in events:
            self.state = apply_event(self.state, event)
            self._transcribe(event)

    def _transcribe(self, event: dict) -> None:
        kind = event.get("type")
        if not str(kind).startswith("transcript."):
            return
        agent_id = event["agentId"]
        current = self.transcripts.setdefault(
            agent_id, {"agentId": agent_id, "items": [], "truncatedBefore": False}
        )
        if kind == "transcript.append":
            current["items"].append(event["item"])
        elif kind == "transcript.update":
            current["items"] = [
                {**i, **event["patch"]} if i["id"] == event["itemId"] else i
                for i in current["items"]
            ]
        else:
            current["truncatedBefore"] = True

    @property
    def items(self) -> list[dict]:
        return self.transcripts.get("ag1", {"items": []})["items"]

    def __getitem__(self, key: str):
        """``world`` and ``transcripts``, so assertions read as a client's state."""
        if key == "transcripts":
            return self.transcripts
        return self.state[key]


def replay(
    name: str,
    *,
    stop_before_control_response: bool = False,
    parent_tool_use_id: str | None = None,
    agent: dict | None = None,
) -> Replayed:
    """Replay ``name`` into ``agent`` (a seed idle agent by default).

    ``parent_tool_use_id`` keeps only the lines a subagent produced under that
    call — the stream the host serves for an ``attach`` to its dotted id.
    """
    out_state = Replayed(seed([agent or make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    for raw in read_fixture(name):
        if (
            parent_tool_use_id is not None
            and raw.get("parent_tool_use_id") != parent_tool_use_id
        ):
            continue
        if (
            stop_before_control_response
            and raw.get("type") == "control_response"
            and ctx.pending is not None
        ):
            break
        out = normalise_stream_event(out_state.state, ctx, raw, NOW)
        ctx = out.ctx
        out_state.take(out.events)
    out_state.ctx = ctx
    return out_state


def types(replayed: Replayed) -> list[str]:
    return [item["type"] for item in replayed.items]


def agent_of(replayed: Replayed) -> dict:
    return replayed.state["world"]["agents"]["ag1"]


def open_attention(replayed: Replayed) -> list[dict]:
    world = replayed.state["world"]
    return [a for a in world["attention"].values() if a["clearedAt"] is None]


def items_of(replayed: Replayed, kind: str) -> list[dict]:
    return [i for i in replayed.items if i["type"] == kind]


FIXTURE_NAMES = sorted(p.name for p in FIXTURES.glob("*.jsonl"))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_replays_to_its_golden(name):
    """A normaliser change is a deliberate re-record, never a silent drift."""
    replayed = replay(name)
    actual = {"world": replayed.state["world"], "transcripts": replayed.transcripts}
    path = GOLDEN / name.replace(".jsonl", ".json")
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    assert actual == json.loads(path.read_text())


def test_a_completed_turn_ends_idle_with_the_cost_and_one_result_line():
    state = replay("normal-turn.jsonl")
    assert types(state) == ["system", "message", "message", "turn_result"]
    assert agent_of(state)["state"] == "idle"
    assert agent_of(state)["costUsd"] == 0.1495855
    first = state["transcripts"]["ag1"]["items"][0]
    assert first["sessionId"] == "029ed263-b318-4d4e-a661-32f9c9f23f19"


def test_plan_review_with_a_plan_yields_a_document_and_one_attention_item():
    state = replay("plan-review-with-plan.jsonl")
    assert agent_of(state)["state"] == "awaiting-plan-review"
    docs = list(state["world"]["documents"].values())
    assert len(docs) == 1
    assert docs[0]["kind"] == "plan"
    assert docs[0]["status"] == "awaiting-review"
    assert docs[0]["markdown"].startswith("# Create hello.txt")
    assert docs[0]["source"]["requestId"] == "9df2f603-da86-44cf-ac99-4e102c7f7add"
    assert len(open_attention(state)) == 1
    assert open_attention(state)[0]["kind"] == "plan_review"
    assert open_attention(state)[0]["documentId"] == docs[0]["id"]
    last = state["transcripts"]["ag1"]["items"][-1]
    assert last["type"] == "plan_review"
    assert last["documentId"] == docs[0]["id"]


def test_plan_review_without_a_plan_takes_the_last_message_as_the_plan():
    state = replay("plan-review.jsonl", stop_before_control_response=True)
    assert agent_of(state)["state"] == "awaiting-plan-review"
    doc = next(iter(state["world"]["documents"].values()))
    assert len(doc["markdown"]) > 20
    assert doc["source"]["planFilePath"] == ""


def test_the_init_event_carries_the_permission_mode():
    """Every recorded transcript names the mode its agent runs in."""
    assert agent_of(replay("plan-review-with-plan.jsonl"))["permissionMode"] == "plan"
    # `default` on the wire is the mode maelstrom calls `normal`.
    assert agent_of(replay("normal-turn.jsonl"))["permissionMode"] == "normal"


def test_a_status_event_changes_the_permission_mode():
    """Approving the plan leaves plan mode, and the child says so itself."""
    before = replay("plan-review.jsonl", stop_before_control_response=True)
    assert agent_of(before)["permissionMode"] == "plan"
    assert agent_of(replay("plan-review.jsonl"))["permissionMode"] == "normal"


def test_an_approved_plan_review_resumes_the_agent_and_approves_the_document():
    state = replay("plan-review.jsonl")
    assert agent_of(state)["state"] == "idle"
    assert agent_of(state)["pendingRequestId"] is None
    doc = next(iter(state["world"]["documents"].values()))
    assert doc["status"] == "approved"
    assert open_attention(state) == []
    assert items_of(state, "plan_review")[0]["decision"] == "approve"


def test_an_unanswered_question_leaves_the_agent_awaiting_a_question():
    state = replay("question-unanswered.jsonl", stop_before_control_response=True)
    agent = agent_of(state)
    assert agent["state"] == "awaiting-question"
    assert agent["pendingRequestId"] == "2ba1273d-d878-4923-ba21-31faa1067613"
    assert agent["waitingOn"] == "Which colour do you prefer?"
    assert open_attention(state)[0]["kind"] == "question"
    question = items_of(state, "question")[0]
    assert question["questions"][0]["question"] == "Which colour do you prefer?"
    assert "answers" not in question


def test_an_answered_question_records_the_answers_on_the_item():
    state = replay("question-answered.jsonl")
    question = items_of(state, "question")[0]
    assert question["answers"] == {"Which colour do you prefer?": "Green"}
    assert agent_of(state)["state"] == "idle"


def test_a_permission_request_awaits_permission_and_its_allow_is_recorded():
    waiting = replay("permission-request.jsonl", stop_before_control_response=True)
    assert agent_of(waiting)["state"] == "awaiting-permission"
    assert open_attention(waiting)[0]["kind"] == "permission"
    done = replay("permission-request.jsonl")
    request = items_of(done, "permission_request")[0]
    assert request["tool"] == "WebFetch"
    assert request["decision"] == "allow"
    assert agent_of(done)["state"] == "idle"


RESULT = {
    "type": "result",
    "subtype": "success",
    "total_cost_usd": 0.25,
    "duration_ms": 1200,
    "session_id": "sess-1",
}


def ended_mid_wait(
    name: str, *, stop_before_control_response: bool = False
) -> "Replayed":
    """Replay ``name`` up to its pending request, then end the turn on it."""
    replayed = replay(name, stop_before_control_response=stop_before_control_response)
    out = normalise_stream_event(replayed.state, replayed.ctx, RESULT, NOW)
    replayed.take(out.events)
    replayed.ctx = out.ctx
    return replayed


def test_a_turn_that_ends_mid_permission_marks_the_request_stale_and_clears_the_row():
    state = ended_mid_wait(
        "permission-request.jsonl", stop_before_control_response=True
    )
    request = items_of(state, "permission_request")[0]
    assert request["stale"] is True
    assert "decision" not in request
    agent = agent_of(state)
    assert agent["state"] == "idle"
    assert agent["pendingRequestId"] is None
    assert agent["waitingOn"] == ""
    assert open_attention(state) == []


def test_a_turn_that_ends_mid_question_marks_the_question_stale_without_answers():
    state = ended_mid_wait(
        "question-unanswered.jsonl", stop_before_control_response=True
    )
    question = items_of(state, "question")[0]
    assert question["stale"] is True
    assert "answers" not in question
    assert agent_of(state)["pendingRequestId"] is None


def test_a_turn_that_ends_mid_plan_review_marks_it_stale_and_ends_the_documents_review():
    state = ended_mid_wait("plan-review-with-plan.jsonl")
    review = items_of(state, "plan_review")[0]
    assert review["stale"] is True
    assert "decision" not in review
    doc = next(iter(state["world"]["documents"].values()))
    assert doc["status"] == "stale"
    assert agent_of(state)["pendingRequestId"] is None


def test_a_request_the_user_interrupts_is_marked_stale():
    state = replay("interrupt-while-waiting.jsonl")
    bash = next(i for i in items_of(state, "permission_request") if i["tool"] == "Bash")
    assert bash["stale"] is True
    assert "decision" not in bash


def test_a_request_that_was_answered_is_never_marked_stale():
    request = items_of(replay("permission-request.jsonl"), "permission_request")[0]
    assert request["decision"] == "allow"
    assert "stale" not in request
    question = items_of(replay("question-answered.jsonl"), "question")[0]
    assert question["answers"] == {"Which colour do you prefer?": "Green"}
    assert "stale" not in question


def test_a_denied_tool_call_ends_denied_and_the_agent_is_not_left_waiting():
    state = replay("permission-denied.jsonl")
    call = items_of(state, "tool_call")[0]
    assert call["tool"] == "Bash"
    assert call["status"] == "denied"
    assert agent_of(state)["state"] == "idle"


def test_a_tool_use_and_its_result_merge_into_one_item():
    state = replay("plan-review.jsonl")
    calls = items_of(state, "tool_call")
    assert len(calls) > 2
    assert all(c["status"] in ("done", "error") for c in calls)
    errored = next(c for c in calls if c["status"] == "error")
    assert "EPERM" in errored["output"]


def test_a_plan_sent_back_comes_around_as_the_next_version_of_the_same_document():
    doc = make_document(
        id="doc-1", agentId="ag1", version=1, status="changes-requested"
    )
    state = seed([make_agent(id="ag1", state="processing")], [doc])
    ctx = context_for_agent("ag1")
    out = normalise_stream_event(
        state,
        ctx,
        {
            "type": "control_request",
            "request_id": "req-2",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "ExitPlanMode",
                "input": {"plan": "# Revised", "planFilePath": "/p.md"},
                "tool_use_id": "toolu_2",
            },
        },
        NOW,
    )
    for event in out.events:
        state = apply_event(state, event)
    docs = list(state["world"]["documents"].values())
    assert len(docs) == 1
    assert docs[0]["id"] == "doc-1"
    assert docs[0]["version"] == 2
    assert docs[0]["status"] == "awaiting-review"
    assert docs[0]["markdown"] == "# Revised"


def test_a_fresh_context_seeds_its_ids_past_the_ones_already_handed_out():
    """A re-attach must not mint an id an earlier item already has.

    The counter is the only source of item ids. Nothing on the server holds
    the items, so the high-water mark is carried rather than derived.
    """
    ctx = context_for_agent("ag1", seed=7)
    assert ctx.next_id == 8
    assert ctx.pending is None


def test_the_hosts_detail_frame_raises_a_wait_the_world_does_not_hold():
    """A client that attached after the request went out must still answer it."""
    state = seed([make_agent(id="ag1", state="awaiting-question")])
    replayed = Replayed(state)
    out = apply_agent_detail(
        state,
        context_for_agent("ag1"),
        {
            "request_id": "req-9",
            "waiting_tool": "AskUserQuestion",
            "waiting_input": {"questions": [{"question": "Which?", "options": []}]},
            "waiting_on": "Which?",
        },
        NOW,
    )
    replayed.take(out.events)
    assert agent_of(replayed)["pendingRequestId"] == "req-9"
    assert types(replayed) == ["question"]
    assert open_attention(replayed)[0]["requestId"] == "req-9"


def test_the_detail_frame_does_not_re_raise_a_wait_the_world_already_holds():
    """The request id names one wait; raising it twice would duplicate it."""
    state = seed(
        [make_agent(id="ag1", state="awaiting-question", pendingRequestId="req-9")]
    )
    out = apply_agent_detail(
        state,
        context_for_agent("ag1"),
        {"request_id": "req-9", "waiting_tool": "AskUserQuestion"},
        NOW,
    )
    assert out.events == []


def test_the_detail_frame_agrees_with_a_world_that_holds_no_wait():
    state = seed([make_agent(id="ag1", state="idle")])
    out = apply_agent_detail(state, context_for_agent("ag1"), {"request_id": ""}, NOW)
    assert out.events == []


def test_the_detail_frame_of_an_idle_host_ends_the_wait_the_world_still_holds():
    """The frame is the host's whole fold, so an empty request id is a value.

    The wait ended while this server was not reading the stream, and it holds
    no event that says so. The re-attach is where it finds out.
    """
    state = seed([make_agent(id="ag1", state="idle", pendingRequestId="req-9")])
    replayed = Replayed(state)
    out = apply_agent_detail(state, context_for_agent("ag1"), {"request_id": ""}, NOW)
    replayed.take(out.events)
    assert agent_of(replayed)["pendingRequestId"] is None


def test_mark_exited_clears_the_wait_and_raises_attention_on_a_bad_exit():
    waiting = replay("question-unanswered.jsonl", stop_before_control_response=True)
    ctx = waiting.ctx
    out = mark_exited(waiting.state, ctx, 1, NOW)
    waiting.take(out.events)
    agent = agent_of(waiting)
    assert agent["state"] == "exited"
    assert agent["exitCode"] == 1
    assert agent["pendingRequestId"] is None
    assert items_of(waiting, "question")[0]["stale"] is True
    kinds = sorted(a["kind"] for a in open_attention(waiting))
    assert kinds == ["agent_exited"]


def test_mark_exited_with_a_clean_exit_raises_nothing():
    state = replay("normal-turn.jsonl")
    out = mark_exited(state.state, state.ctx, 0, NOW)
    state.take(out.events)
    assert agent_of(state)["state"] == "exited"
    assert open_attention(state) == []


def test_an_unknown_agent_normalises_to_nothing():
    state = seed([])
    ctx = NormaliseContext(agent_id="ghost")
    out = normalise_stream_event(state, ctx, {"type": "result"}, NOW)
    assert out.events == []


def test_a_user_turn_whose_content_is_a_plain_string_still_shows():
    """A user turn carries its text as a list of blocks or as a plain string.

    A turn the agent replays uses the block form, but one the harness injects
    — a task notification, the echo of a slash command — uses the string form.
    Both are on the transcript, so reading only the block form shows a turn the
    agent acted on as nothing at all. The turn also starts the agent working,
    so an unread one leaves the view idle as though nothing was sent.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    out = normalise_stream_event(
        state.state,
        ctx,
        {"type": "user", "message": {"role": "user", "content": "Present the plan"}},
        NOW,
    )
    state.take(out.events)
    assert [(i["role"], i["markdown"]) for i in items_of(state, "message")] == [
        ("user", "Present the plan")
    ]
    assert agent_of(state)["state"] == "processing"


def test_a_skill_body_becomes_a_folded_skill_item():
    """A user turn opening with the base-directory line is a loaded skill.

    Read as an ordinary message the whole skill file fills the transcript, so
    it becomes its own item instead.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    body = (
        "Base directory for this skill: /Users/dev/.claude/skills/mael"
        "\n\n# Maelstrom CLI Skill\n\nThe conventions behind mael."
    )
    out = normalise_stream_event(
        state.state,
        ctx,
        {"type": "user", "message": {"role": "user", "content": body}},
        NOW,
    )
    state.take(out.events)
    assert items_of(state, "message") == []
    loaded = items_of(state, "skill")
    assert [i["skill"] for i in loaded] == ["mael"]
    assert loaded[0]["markdown"] == body
    # A skill body is still a turn the agent acts on.
    assert agent_of(state)["state"] == "processing"


def test_a_skill_name_survives_an_odd_base_directory():
    """A plugin skill keeps its qualifier, and a nameless path still folds.

    The harness names a plugin skill ``plugin:skill``, and two plugins may
    ship one leaf name. A path with no name left to read is still a skill
    load, so it folds rather than dumping the file back on the transcript.
    """
    plugin = "/d/.claude/plugins/figma/skills/figma-use"
    cases = [
        (plugin, "figma:figma-use"),
        ("/d/.claude/skills/mael/", "mael"),
        ("///", "skill"),
    ]
    for path, name in cases:
        body = f"Base directory for this skill: {path}\n\nBody"
        assert _skill_loaded(body) == name, path
    assert _skill_loaded("Please run the tests") is None


def test_a_user_turn_that_only_quotes_the_skill_line_stays_a_message():
    """Prefix alone is not the test: the whole opening shape is.

    The repo's own docs carry the phrase, so a user pasting one would have
    their message folded out of sight behind a card named after a stray token.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    asked = (
        "Base directory for this skill: what does that line mean when it opens a turn?"
    )
    out = normalise_stream_event(
        state.state,
        ctx,
        {"type": "user", "message": {"role": "user", "content": asked}},
        NOW,
    )
    state.take(out.events)
    assert items_of(state, "skill") == []
    assert [i["markdown"] for i in items_of(state, "message")] == [asked]


# --- subagents ----------------------------------------------------------------

AGENT_CALL = "toolu_01GYXSgBQ1wcW9LA8SSvM5uJ"


def test_a_parents_replay_carries_none_of_its_subagents_items():
    """The golden holds the full item list; this names the one thing it means:
    the ``Agent`` call is there, and nothing said under it is."""
    state = replay("subagent-turn.jsonl")
    assert [c["tool"] for c in items_of(state, "tool_call")] == ["Agent"]
    assert not any("I'll look for" in i.get("markdown", "") for i in state.items)


def child() -> dict:
    """A seed subagent, idle so a stream that wrongly moved its state would show."""
    return make_agent(
        id="ag1",
        parent="parent-1",
        description="List and summarise docs/dev",
        state="idle",
    )


def replay_child(name: str, call: str = AGENT_CALL) -> Replayed:
    """``name``'s lines under ``call``, replayed into a seed subagent.

    The stream the host serves for an ``attach`` to the dotted id, which is
    the only stream a subagent's normaliser context ever sees.
    """
    return replay(name, parent_tool_use_id=call, agent=child())


def test_a_subagents_replay_is_its_own_transcript():
    state = replay_child("subagent-turn.jsonl")
    assert types(state) == [
        "message",
        "message",
        "tool_call",
        "message",
        "tool_call",
        "message",
    ]
    assert state.items[0]["role"] == "user"
    assert state.items[1] == {
        "id": "ag1-2",
        "ts": NOW,
        "type": "message",
        "role": "assistant",
        "markdown": "I'll look for the `docs/dev` directory.",
    }
    assert [c["status"] for c in items_of(state, "tool_call")] == ["done", "done"]
    assert agent_of(state)["lastMessage"].startswith("`docs/dev` exists")


def test_a_subagents_stream_moves_nothing_but_its_last_message():
    state = replay_child("subagent-turn.jsonl")
    expected = {**child(), "lastMessage": agent_of(state)["lastMessage"]}
    assert agent_of(state) == expected
    assert open_attention(state) == []
    assert state["world"]["documents"] == {}


def test_a_control_request_on_a_subagents_stream_is_ignored():
    """The wait is the parent's: the host puts it on the parent's stream."""
    lines = read_fixture("subagent-permission.jsonl")
    request = next(e for e in lines if e.get("type") == "control_request")
    state = Replayed(seed([child()]))
    ctx = context_for_agent("ag1")
    out = normalise_stream_event(state.state, ctx, request, NOW)
    assert out.events == []
    assert out.ctx.pending is None


def test_a_subagents_detail_frame_raises_no_wait():
    """The host names no request on a subagent's detail; if it ever did, it is still not ours."""
    state = Replayed(seed([child()]))
    detail = {"request_id": "req-9", "waiting_tool": "WebFetch", "waiting_on": "x"}
    out = apply_agent_detail(state.state, context_for_agent("ag1"), detail, NOW)
    assert out.events == []


def test_a_subagent_that_exits_non_zero_raises_no_attention():
    state = Replayed(seed([child()]))
    out = mark_exited(state.state, context_for_agent("ag1"), 1, NOW)
    state.take(out.events)
    assert agent_of(state)["state"] == "exited"
    assert agent_of(state)["exitCode"] == 1
    assert open_attention(state) == []


def test_the_detail_frame_of_a_new_wait_retires_the_one_the_world_held():
    """The host moved on to a second wait, and the first one is over.

    Overwriting the wait without ending it would leave the first item live
    and its attention item on the desk with nothing left to retire it.
    """
    state = seed([make_agent(id="ag1", state="awaiting-question")])
    replayed = Replayed(state)
    first = apply_agent_detail(
        state,
        context_for_agent("ag1"),
        {
            "request_id": "req-9",
            "waiting_tool": "AskUserQuestion",
            "waiting_input": {"questions": [{"question": "Which?", "options": []}]},
            "waiting_on": "Which?",
        },
        NOW,
    )
    replayed.take(first.events)
    second = apply_agent_detail(
        replayed.state,
        first.ctx,
        {
            "request_id": "req-10",
            "waiting_tool": "AskUserQuestion",
            "waiting_input": {"questions": [{"question": "And now?", "options": []}]},
            "waiting_on": "And now?",
        },
        NOW,
    )
    replayed.take(second.events)
    assert agent_of(replayed)["pendingRequestId"] == "req-10"
    assert [i.get("stale", False) for i in items_of(replayed, "question")] == [
        True,
        False,
    ]
    assert [a["requestId"] for a in open_attention(replayed)] == ["req-10"]
