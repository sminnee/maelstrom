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

from maelstrom import task as task_model
from maelstrom.orchestrator.document_tags import read_worktree_file
from maelstrom.orchestrator.file_registry import FileRegistry
from maelstrom.orchestrator.normalise import (
    NormaliseContext,
    _skill_loaded,
    apply_agent_detail,
    context_for_agent,
    mark_exited,
    normalise_gap,
    normalise_stream_event,
)
from maelstrom.orchestrator.protocol import (
    ClientState,
    apply_event,
    empty_world,
    state_with,
)

from .agent_fixtures import RECEIVED, read_stamped_fixture

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
        "lastMessageAt": "",
        "costUsd": 0,
        "totalTokens": 0,
        "taskId": "NORT-7",
        "project": "northwind",
        "worktreeId": "northwind-alpha",
        "exitCode": None,
        "pendingRequestIds": [],
        "pid": None,
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


read_fixture = read_stamped_fixture


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


#: The worktree file the ``<doc-file>`` fixtures name. Every other name reads
#: as missing.
DRAFT_FILES = {
    "draft-iter1.md": "# Iteration 1\n\n- Parse the tag.\n- Mint the document.\n"
}


#: The files the fixtures name, as the registry sees them. The names the
#: reader serves are present; anything else is missing, so a tag naming a file
#: that is not there is refused exactly as it would be on a real worktree.
FIXTURE_FILES = frozenset(DRAFT_FILES) | {"shot.png"}


def fake_registry(names=FIXTURE_FILES) -> FileRegistry:
    """A registry that treats ``names`` as the files that exist."""
    return FileRegistry(is_file=lambda path: path.name in names)


def fake_reader(files: dict[str, str] | None = None):
    """A ``read_file`` serving ``files`` by name, and nothing else."""
    served = DRAFT_FILES if files is None else files

    def read(cwd: str, filename: str) -> str | None:
        return served.get(filename)

    return read


def file_ids_of(doc) -> list[str]:
    """The files a document names, by name alone.

    A ``draft_files`` source carries a registered id, which is an item id and
    the filename. The id is the registry's business, so a test asks which file
    the document names, not how the source spells it.
    """
    # An id is `<agent>-<n>-<filename>`, and a filename may itself hold a `-`,
    # so only the two id fields are dropped.
    return [
        path.rsplit("/", 1)[-1].split("-", 2)[-1] if path.count("-") >= 2 else path
        for path in doc["source"].get("paths", [])
    ]


def replay(
    name: str,
    *,
    stop_before_control_response: bool = False,
    parent_tool_use_id: str | None = None,
    agent: dict | None = None,
    read_file=None,
    files: FileRegistry | None = None,
) -> Replayed:
    """Replay ``name`` into ``agent`` (a seed idle agent by default).

    ``parent_tool_use_id`` keeps only the lines a subagent produced under that
    call — the stream the host serves for an ``attach`` to its dotted id.

    ``read_file`` stands in for the worktree a ``<doc-file>`` tag names, and
    ``files`` for which of that worktree's files are there.
    """
    files = files if files is not None else fake_registry()
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
            and ctx.pending
        ):
            break
        out = normalise_stream_event(
            out_state.state,
            ctx,
            raw,
            NOW,
            read_file=read_file or fake_reader(),
            files=files,
        )
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


def test_an_items_time_is_when_its_event_happened_not_when_it_was_replayed():
    """The reattach case. A backlog replayed at ``NOW`` must keep the times of
    the turns it carries; only an item with no source event takes ``NOW``."""
    state = replay("normal-turn.jsonl")
    said = [i for i in state.items if i["type"] == "message"]
    assert [i["ts"] for i in said] == [
        "2026-09-01T01:42:16.651Z",
        "2026-09-01T01:42:19.652Z",
    ]
    # A `result` frame carries no clock of its own, so it keeps the daemon's.
    [result] = items_of(state, "turn_result")
    assert result["ts"] == RECEIVED


def test_an_attention_item_is_raised_and_cleared_at_its_events_own_time():
    """The same rule as a transcript item: the source event's own stamp, not
    the reattach clock. `web/src/selectors/attention.ts` sorts by ``raisedAt``,
    so a reattach that restamped every item would lose the true order."""
    state = replay("permission-request.jsonl")
    [item] = list(state["world"]["attention"].values())
    # A `control_*` frame carries no clock of its own, so both take the
    # daemon's — never the reattach instant, which is what `NOW` stands for.
    assert item["raisedAt"] == RECEIVED
    assert item["clearedAt"] == RECEIVED

    # Every wait is raised by a `control_request`, so the daemon's clock is the
    # right one throughout. The rule is that the source event decides, not that
    # an attention item always takes a conversation turn's time.
    plan = replay("plan-review-with-plan.jsonl")
    [raised] = list(plan["world"]["attention"].values())
    assert raised["raisedAt"] == RECEIVED


def test_an_item_with_no_source_event_is_stamped_now():
    """A gap is not something the agent did — it is happening as we say so."""
    state = seed([make_agent(id="ag1")])
    out = normalise_gap(state, context_for_agent("ag1"), 3, NOW)
    [event] = out.events
    assert event["item"]["ts"] == NOW


def test_a_completed_turn_ends_idle_with_the_cost_and_one_result_line():
    state = replay("normal-turn.jsonl")
    assert types(state) == ["system", "message", "message", "turn_result"]
    assert agent_of(state)["state"] == "idle"
    assert agent_of(state)["costUsd"] == 0.1495855
    first = state["transcripts"]["ag1"]["items"][0]
    assert first["sessionId"] == "029ed263-b318-4d4e-a661-32f9c9f23f19"


def test_a_completed_turn_adds_its_tokens_to_the_agents_running_total():
    """The turn's four counts, summed: 2 + 14429 + 10121 + 9 off the fixture."""
    state = replay("normal-turn.jsonl")
    assert agent_of(state)["totalTokens"] == 24561


def test_a_second_turn_adds_to_the_token_total_rather_than_replacing_it():
    """The stream must move the number the same way the daemon's state does."""
    state = replay("normal-turn.jsonl")
    out = normalise_stream_event(
        state.state,
        state.ctx,
        {
            "type": "result",
            "subtype": "success",
            "usage": {"input_tokens": 5, "output_tokens": 7},
        },
        NOW,
    )
    state.take(out.events)
    assert agent_of(state)["totalTokens"] == 24561 + 12


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
    assert agent_of(state)["pendingRequestIds"] == []
    doc = next(iter(state["world"]["documents"].values()))
    assert doc["status"] == "approved"
    assert open_attention(state) == []
    assert items_of(state, "plan_review")[0]["decision"] == "approve"


def test_an_unanswered_question_leaves_the_agent_awaiting_a_question():
    state = replay("question-unanswered.jsonl", stop_before_control_response=True)
    agent = agent_of(state)
    assert agent["state"] == "awaiting-question"
    assert agent["pendingRequestIds"] == ["2ba1273d-d878-4923-ba21-31faa1067613"]
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
    assert agent["pendingRequestIds"] == []
    assert agent["waitingOn"] == ""
    assert open_attention(state) == []


def test_a_turn_that_ends_mid_question_marks_the_question_stale_without_answers():
    state = ended_mid_wait(
        "question-unanswered.jsonl", stop_before_control_response=True
    )
    question = items_of(state, "question")[0]
    assert question["stale"] is True
    assert "answers" not in question
    assert agent_of(state)["pendingRequestIds"] == []


def test_a_turn_that_ends_mid_plan_review_marks_it_stale_and_ends_the_documents_review():
    state = ended_mid_wait("plan-review-with-plan.jsonl")
    review = items_of(state, "plan_review")[0]
    assert review["stale"] is True
    assert "decision" not in review
    doc = next(iter(state["world"]["documents"].values()))
    assert doc["status"] == "stale"
    assert agent_of(state)["pendingRequestIds"] == []


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
    assert ctx.pending == {}


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
    assert agent_of(replayed)["pendingRequestIds"] == ["req-9"]
    assert types(replayed) == ["question"]
    assert open_attention(replayed)[0]["requestId"] == "req-9"


def test_the_detail_frame_does_not_re_raise_a_wait_the_world_already_holds():
    """The request id names one wait; raising it twice would duplicate it."""
    state = seed(
        [make_agent(id="ag1", state="awaiting-question", pendingRequestIds=["req-9"])]
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
    state = seed([make_agent(id="ag1", state="idle", pendingRequestIds=["req-9"])])
    replayed = Replayed(state)
    out = apply_agent_detail(state, context_for_agent("ag1"), {"request_id": ""}, NOW)
    replayed.take(out.events)
    assert agent_of(replayed)["pendingRequestIds"] == []


def test_mark_exited_clears_the_wait_and_raises_attention_on_a_bad_exit():
    waiting = replay("question-unanswered.jsonl", stop_before_control_response=True)
    ctx = waiting.ctx
    out = mark_exited(waiting.state, ctx, 1, NOW)
    waiting.take(out.events)
    agent = agent_of(waiting)
    assert agent["state"] == "exited"
    assert agent["exitCode"] == 1
    assert agent["pendingRequestIds"] == []
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


def test_a_shell_command_and_its_output_become_one_item():
    """The two turns the host injects for a ``!`` fold into a single item.

    The command arrives first and the output follows, so the item appends on
    the input turn and is updated by the output turn — the same shape a
    ``tool_call`` and its ``tool_result`` already use.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    out = normalise_stream_event(
        state.state,
        ctx,
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<bash-input>git status</bash-input>",
            },
        },
        NOW,
    )
    state.take(out.events)
    out = normalise_stream_event(
        state.state,
        out.ctx,
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<bash-stdout>on main</bash-stdout><bash-stderr></bash-stderr>",
            },
        },
        NOW,
    )
    state.take(out.events)
    assert items_of(state, "message") == []
    shells = items_of(state, "shell")
    assert [(i["command"], i["output"]) for i in shells] == [("git status", "on main")]
    # The shell turns ask the agent for nothing, so the normaliser must not
    # mark it working. An assistant event that follows moves the state on its
    # own — the point is that these two turns do not.
    assert agent_of(state)["state"] == "idle"


def test_a_shell_command_keeps_stderr():
    """A failing command reaches the agent as its stderr text."""
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    out = normalise_stream_event(
        state.state,
        ctx,
        {
            "type": "user",
            "message": {"role": "user", "content": "<bash-input>nope</bash-input>"},
        },
        NOW,
    )
    state.take(out.events)
    out = normalise_stream_event(
        state.state,
        out.ctx,
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<bash-stdout></bash-stdout><bash-stderr>not found</bash-stderr>",
            },
        },
        NOW,
    )
    state.take(out.events)
    assert [i["output"] for i in items_of(state, "shell")] == ["not found"]


def test_shell_output_with_no_command_before_it_still_shows():
    """The ring can truncate away the input turn. A gap must not eat output."""
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    out = normalise_stream_event(
        state.state,
        ctx,
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "<bash-stdout>orphaned</bash-stdout><bash-stderr></bash-stderr>",
            },
        },
        NOW,
    )
    state.take(out.events)
    assert [i["output"] for i in items_of(state, "shell")] == ["orphaned"]


def test_an_interrupted_shell_pair_does_not_capture_a_later_command():
    """A command turn with no output turn must not swallow the next one.

    The daemon sends the pair together, so only a restart between the two
    breaks it. When that happens the later command needs its own item.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    turns = [
        "<bash-input>first</bash-input>",
        "an ordinary message",
        "<bash-stdout>late</bash-stdout><bash-stderr></bash-stderr>",
    ]
    for content in turns:
        out = normalise_stream_event(
            state.state,
            ctx,
            {"type": "user", "message": {"role": "user", "content": content}},
            NOW,
        )
        state.take(out.events)
        ctx = out.ctx
    # The orphaned output gets its own item; the first command keeps its own.
    assert [(i["command"], i["output"]) for i in items_of(state, "shell")] == [
        ("first", ""),
        ("", "late"),
    ]


def test_a_user_turn_that_only_quotes_the_bash_tag_stays_a_message():
    """As with a skill, the prefix alone is not the test.

    This repo's own docs carry the tag, so a user pasting one must not have
    their message folded away behind a shell card.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    asked = "what does <bash-input> mean when it opens a turn?"
    out = normalise_stream_event(
        state.state,
        ctx,
        {"type": "user", "message": {"role": "user", "content": asked}},
        NOW,
    )
    state.take(out.events)
    assert items_of(state, "shell") == []
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
        "ts": "2026-09-04T08:27:27.953Z",
        "type": "message",
        "role": "assistant",
        "markdown": "I'll look for the `docs/dev` directory.",
    }
    assert [c["status"] for c in items_of(state, "tool_call")] == ["done", "done"]
    assert agent_of(state)["lastMessage"].startswith("`docs/dev` exists")


def test_a_subagents_stream_moves_nothing_but_its_last_message():
    state = replay_child("subagent-turn.jsonl")
    expected = {
        **child(),
        "lastMessage": agent_of(state)["lastMessage"],
        "lastMessageAt": "2026-09-04T08:28:27.870Z",
    }
    assert agent_of(state) == expected
    assert open_attention(state) == []
    assert state["world"]["documents"] == {}


def test_two_asks_are_both_held():
    """Neither displaces the other, so either can still be answered."""
    replayed = replay("subagent-permission-concurrent.jsonl")
    items = replayed.transcripts["ag1"]["items"]
    prompts = [i for i in items if i["type"] == "permission_request"]
    assert len(prompts) == 2
    # One was answered; the other is neither answered nor stale.
    decided = [i for i in prompts if i.get("decision")]
    open_prompt = [i for i in prompts if not i.get("decision")]
    assert len(decided) == 1
    assert len(open_prompt) == 1
    assert not open_prompt[0].get("stale"), "the unanswered ask was retired"


def test_the_unanswered_ask_stays_on_the_agent():
    """The world must not read as free while a caller is still blocked."""
    replayed = replay("subagent-permission-concurrent.jsonl")
    agent = replayed.state["world"]["agents"]["ag1"]
    assert agent["pendingRequestIds"], "the agent looks free"
    assert agent["state"] == "awaiting-permission"


def test_answering_one_ask_clears_only_its_attention():
    """Two open asks raise two items; one answer clears one."""
    replayed = replay("subagent-permission-concurrent.jsonl")
    items = list(replayed.state["world"]["attention"].values())
    permission = [a for a in items if a["kind"] == "permission"]
    assert len(permission) == 2
    assert [a["clearedAt"] is None for a in permission] == [True, False]


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
    assert agent_of(replayed)["pendingRequestIds"] == ["req-10"]
    assert [i.get("stale", False) for i in items_of(replayed, "question")] == [
        True,
        False,
    ]
    assert [a["requestId"] for a in open_attention(replayed)] == ["req-10"]


# -- documents an agent tags in its own message --


def documents_of(replayed: Replayed) -> list[dict]:
    return list(replayed.state["world"]["documents"].values())


def test_a_doc_content_tag_mints_a_draft_document_from_the_message():
    state = replay("document-content.jsonl")
    [doc] = documents_of(state)
    assert doc["kind"] == "other"
    assert doc["title"] == "Changelog draft"
    assert doc["markdown"].startswith("## 1.4.0")
    assert doc["version"] == 1
    # A draft blocks nothing, so nothing waits on the user.
    assert doc["status"] == "draft"
    assert open_attention(state) == []


def test_a_doc_content_tag_names_the_message_it_came_from():
    state = replay("document-content.jsonl")
    [doc] = documents_of(state)
    [message] = items_of(state, "message")
    assert doc["source"] == {"type": "message", "transcriptItemId": message["id"]}


def test_the_tag_is_stripped_from_the_message_the_transcript_shows():
    """The user reads the document in its tab; the raw tag would be noise."""
    state = replay("document-content.jsonl")
    [message] = items_of(state, "message")
    assert "<doc-content" not in message["markdown"]
    assert "## 1.4.0" not in message["markdown"]
    assert message["markdown"].startswith("Here is the changelog")
    assert message["markdown"].endswith("Tell me what to change.")


def test_a_message_that_is_only_a_tag_still_leaves_the_document():
    state = replay("document-file.jsonl", read_file=fake_reader())
    assert len(documents_of(state)) == 1
    assert "<doc-file" not in items_of(state, "message")[0]["markdown"]


def test_a_doc_file_tag_mints_a_document_holding_the_files_content():
    state = replay("document-file.jsonl")
    [doc] = documents_of(state)
    assert doc["kind"] == "tasks"
    assert doc["title"] == "Iteration 1"
    assert doc["markdown"] == DRAFT_FILES["draft-iter1.md"]
    assert doc["source"]["type"] == "draft_files"
    assert file_ids_of(doc) == ["draft-iter1.md"]


def test_a_doc_file_tag_may_name_a_whole_set_of_files():
    """A task set is one document, so one tag names every draft in the chain."""
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        tag_message(
            '<doc-file kind="tasks" filename="draft-one.md, draft-two.md" '
            'title="Iteration 1">'
        ),
        NOW,
        read_file=fake_reader({"draft-one.md": "# One\n", "draft-two.md": "# Two\n"}),
    )
    replayed.take(out.events)
    [doc] = documents_of(replayed)
    # The order is the order the tag lists, which is the order approve promotes.
    assert doc["source"] == {
        "type": "draft_files",
        "paths": ["draft-one.md", "draft-two.md"],
    }
    # One document to read, so the bodies come through together.
    assert "# One" in doc["markdown"]
    assert "# Two" in doc["markdown"]


def show_file(kind: str, filename: str, body: str) -> dict:
    """Mint one document from a `<doc-file>` tag of ``kind`` naming ``body``."""
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        tag_message(f'<doc-file kind="{kind}" filename="{filename}">'),
        NOW,
        read_file=fake_reader({filename: body}),
    )
    replayed.take(out.events)
    [doc] = documents_of(replayed)
    return doc


def test_a_file_that_is_not_a_task_set_keeps_every_rule_it_wrote():
    """Only a task set has a recipe to drop.

    A changelog may open with a horizontal rule and carry more of them. Read as
    frontmatter, the newest entry vanishes from what the user reads.
    """
    changelog = (
        "---\n\n# Changelog\n\n## 1.4.0\n\nNewest.\n\n---\n\n## 1.3.0\n\nOlder.\n"
    )
    doc = show_file("other", "CHANGELOG.md", changelog)
    assert doc["markdown"] == changelog


def test_a_task_set_is_headed_by_its_title_not_its_filename():
    """``title:`` lives only in the frontmatter, so a strip alone loses it.

    The user approving a chain reads task titles, not draft filenames.
    """
    draft = task_model.draft_markdown(
        title="Execute: add avatar upload", mode="auto", content="The plan body."
    )
    doc = show_file("tasks", "iter1.md", draft)
    assert "Execute: add avatar upload" in doc["markdown"]
    assert "The plan body." in doc["markdown"]


def test_a_task_set_shows_the_recipe_the_user_is_approving():
    """Approving decides how each task runs, so the recipe must be readable."""
    draft = task_model.draft_markdown(
        title="Execute: demo",
        mode="auto",
        model="opus",
        command="plan-next-step",
        content="Body.",
    )
    doc = show_file("tasks", "iter1.md", draft)
    assert "auto" in doc["markdown"]
    assert "opus" in doc["markdown"]
    assert "plan-next-step" in doc["markdown"]
    # The raw YAML block would draw as one giant setext heading.
    assert not doc["markdown"].lstrip().startswith("---")


def test_a_task_set_whose_draft_will_not_parse_is_still_shown():
    """A draft the user must fix is exactly the one they need to read."""
    doc = show_file("tasks", "iter1.md", '---\ntitle: "unclosed\n---\n\nBody.\n')
    assert "Body." in doc["markdown"]


def test_a_set_whose_title_is_unset_falls_back_to_the_first_filename():
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        tag_message('<doc-file kind="tasks" filename="draft-one.md, draft-two.md">'),
        NOW,
        read_file=fake_reader({"draft-one.md": "# One\n", "draft-two.md": "# Two\n"}),
    )
    replayed.take(out.events)
    [doc] = documents_of(replayed)
    assert doc["title"] == "draft-one.md"


def test_a_doc_file_tag_naming_a_file_that_cannot_be_read_still_mints_a_document():
    """An unreadable file mints a document that says so."""
    state = replay("document-file-missing.jsonl")
    [doc] = documents_of(state)
    assert doc["kind"] == "plan"
    assert doc["title"] == "no-such-draft.md"
    assert "no-such-draft.md" in doc["markdown"]
    assert doc["status"] == "draft"


def test_a_doc_file_tag_whose_filename_escapes_the_worktree_reads_nothing():
    read_calls = []

    def reader(cwd: str, filename: str) -> str | None:
        read_calls.append(filename)
        return "root:x:0:0:root:/root:/bin/sh\n"

    state = replay("document-file-escaping.jsonl", read_file=reader)
    [doc] = documents_of(state)
    assert "root:x:0:0" not in doc["markdown"]
    assert "../../../etc/passwd" in doc["markdown"]
    assert read_calls == []


def test_one_message_may_carry_more_than_one_tag():
    state = replay("document-both.jsonl")
    docs = documents_of(state)
    assert [d["title"] for d in docs] == ["Release note", "Iteration 1"]
    assert [d["kind"] for d in docs] == ["other", "tasks"]


def test_review_true_opens_the_document_awaiting_review_and_raises_attention():
    state = replay("document-both.jsonl")
    by_title = {d["title"]: d for d in documents_of(state)}
    assert by_title["Release note"]["status"] == "draft"
    assert by_title["Iteration 1"]["status"] == "awaiting-review"
    [item] = open_attention(state)
    assert item["kind"] == "document_review"
    assert item["documentId"] == by_title["Iteration 1"]["id"]
    assert item["summary"] == "Iteration 1 awaiting review"


def test_an_unrecognised_kind_reads_as_other_rather_than_dropping_the_document():
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        tag_message('<doc-content kind="taks" title="Typo">\nBody.\n</doc-content>'),
        NOW,
        read_file=fake_reader(),
    )
    replayed.take(out.events)
    [doc] = documents_of(replayed)
    assert doc["kind"] == "other"


def tag_message(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        "session_id": "sess-1",
    }


def test_a_tag_with_no_title_falls_back_to_the_filename_then_the_kind():
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    ctx = context_for_agent("ag1")
    for text in (
        '<doc-file kind="tasks" filename="draft-iter1.md">',
        '<doc-content kind="review">\nBody.\n</doc-content>',
    ):
        out = normalise_stream_event(
            replayed.state, ctx, tag_message(text), NOW, read_file=fake_reader()
        )
        ctx = out.ctx
        replayed.take(out.events)
    assert [d["title"] for d in documents_of(replayed)] == ["draft-iter1.md", "review"]


def test_a_re_mint_versions_a_changes_requested_document_forward():
    """The plan document's rule: the comments stay attached to one document."""
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    ctx = context_for_agent("ag1")
    tag = '<doc-content kind="tasks" title="Iteration 1">\nDo the first thing.\n</doc-content>'
    out = normalise_stream_event(
        replayed.state, ctx, tag_message(tag), NOW, read_file=fake_reader()
    )
    ctx = out.ctx
    replayed.take(out.events)
    [first] = documents_of(replayed)
    replayed.take(
        [
            {
                "type": "upsert",
                "kind": "document",
                "entity": {**first, "status": "changes-requested"},
            }
        ]
    )
    again = '<doc-content kind="tasks" title="Iteration 1">\nDo it properly.\n</doc-content>'
    out = normalise_stream_event(
        replayed.state, ctx, tag_message(again), NOW, read_file=fake_reader()
    )
    replayed.take(out.events)
    [doc] = documents_of(replayed)
    assert doc["id"] == first["id"]
    assert doc["version"] == 2
    assert doc["status"] == "draft"
    assert doc["markdown"].strip() == "Do it properly."


def test_a_re_mint_of_a_document_still_open_is_a_new_document():
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    ctx = context_for_agent("ag1")
    tag = (
        '<doc-content kind="tasks" title="Iteration 1">\nDo the thing.\n</doc-content>'
    )
    for _ in range(2):
        out = normalise_stream_event(
            replayed.state, ctx, tag_message(tag), NOW, read_file=fake_reader()
        )
        ctx = out.ctx
        replayed.take(out.events)
    docs = documents_of(replayed)
    assert len(docs) == 2
    assert [d["version"] for d in docs] == [1, 1]


def test_a_subagent_writes_no_document():
    """A subagent's stream is a transcript and its last message, nothing more."""
    state = seed([make_agent(id="ag1", parent="ag0", state="idle")])
    replayed = Replayed(state)
    tag = '<doc-content kind="other" title="Note">\nBody.\n</doc-content>'
    out = normalise_stream_event(
        state, context_for_agent("ag1"), tag_message(tag), NOW, read_file=fake_reader()
    )
    replayed.take(out.events)
    assert documents_of(replayed) == []


def test_the_real_reader_serves_a_file_in_the_worktree_and_refuses_one_outside(
    tmp_path,
):
    """The guard, over a real directory: only what the worktree holds is read."""
    (tmp_path / "draft.md").write_text("# Draft\n")
    outside = tmp_path.parent / "secret.md"
    outside.write_text("secret\n")
    cwd = str(tmp_path)
    assert read_worktree_file(cwd, "draft.md") == "# Draft\n"
    assert read_worktree_file(cwd, "../secret.md") is None
    assert read_worktree_file(cwd, str(outside)) is None
    assert read_worktree_file(cwd, "no-such-file.md") is None
    assert read_worktree_file("", "draft.md") is None


def test_an_image_tag_becomes_a_picture_in_the_message_it_was_written_in():
    """An image is shown where the agent put it, not cut out into a document."""
    state = replay("image-worktree.jsonl")
    [message] = items_of(state, "message")
    assert "<image" not in message["markdown"]
    assert "![The failing dialog](/api/files/" in message["markdown"]
    # The prose either side is kept, and the image sits between it.
    assert message["markdown"].index("Here is the failing dialog") < message[
        "markdown"
    ].index("![The failing dialog]")
    assert message["markdown"].index("![The failing dialog]") < message[
        "markdown"
    ].index("The button is cut off")


def test_an_image_tag_mints_no_document():
    """An image is not a versioned artefact, so it raises nothing to review."""
    state = replay("image-worktree.jsonl")
    assert documents_of(state) == []


def test_an_image_whose_path_escapes_the_worktree_is_not_shown():
    """No URL is minted, and the message says so rather than breaking."""
    state = replay("image-escaping.jsonl")
    [message] = items_of(state, "message")
    assert "/api/files/" not in message["markdown"]
    assert "could not be shown" in message["markdown"]
    assert "passwd" in message["markdown"]


def test_a_subagents_image_tag_stays_as_text():
    """A subagent writes no document and shows no picture."""
    state = seed([make_agent(id="ag1", parent="ag0", state="idle")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        tag_message('<image src="docs/shot.png" alt="Shot">'),
        NOW,
        read_file=fake_reader(),
    )
    replayed.take(out.events)
    assert "<image" in items_of(replayed, "message")[0]["markdown"]


def test_an_attribute_value_may_hold_an_angle_bracket():
    """A tag ends at the `>` that closes it, not at one inside a quoted value.

    An agent writing a placeholder — or a title with a `>` in it — otherwise
    lost its filename silently and minted an empty document under the kind.
    """
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        tag_message('<doc-file kind="tasks" filename="draft-iter1.md" title="A > B">'),
        NOW,
        read_file=fake_reader(),
    )
    replayed.take(out.events)
    [doc] = documents_of(replayed)
    assert doc["title"] == "A > B"
    assert file_ids_of(doc) == ["draft-iter1.md"]
    assert doc["markdown"] == DRAFT_FILES["draft-iter1.md"]
    assert items_of(replayed, "message")[0]["markdown"] == ""


def test_a_symlink_out_of_the_worktree_is_refused(tmp_path):
    """``resolve`` follows the link, so the guard sees where it really lands."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    secret = tmp_path / "secret.md"
    secret.write_text("secret\n")
    (worktree / "link.md").symlink_to(secret)
    assert read_worktree_file(str(worktree), "link.md") is None
