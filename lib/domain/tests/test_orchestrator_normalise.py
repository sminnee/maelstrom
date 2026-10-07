"""The normaliser, replayed over the recorded daemon streams, against its goldens.

The normaliser turns the agent host's raw stream-json into transcript items,
agent upserts, documents and attention items, so the wire never carries raw
stream-json. The goldens under ``lib/domain/fixtures/normalised/``
record what each fixture replays to; this file owns them, and
``UPDATE_GOLDEN=1 uv run pytest lib/domain/tests/test_orchestrator_normalise.py``
re-records. The TypeScript normaliser is held to the same files until it goes.
"""

import json
import os
import re
from pathlib import Path

import pytest
from agent_fixtures import (
    FIXTURES,
    RECEIVED,
    make_agent,
    make_document,
    read_stamped_fixture,
)

from mael_daemon import agent_model
from mael_domain import document_tags
from mael_domain import task as task_model
from mael_domain.attachments import attachment_urls
from mael_domain.document_tags import read_worktree_file
from mael_domain.file_registry import FileRegistry
from mael_domain.normalise import (
    NormaliseContext,
    _skill_loaded,
    apply_agent_detail,
    close_partial_message,
    context_for_agent,
    mark_exited,
    normalise_gap,
    normalise_stream_event,
)
from mael_domain.protocol import ClientState, apply_event, empty_world, state_with

GOLDEN = Path(__file__).parents[1] / "fixtures" / "normalised"
NOW = "2026-09-01T00:00:00Z"


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
        elif kind == "transcript.partial":
            current["items"] = [
                {**i, "markdown": event["markdown"]}
                if i["id"] == event["itemId"] and i.get("partial")
                else i
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
    "draft-iter1.md": "# Iteration 1\n\n- Parse the tag.\n- Mint the document.\n",
    "release-note.md": "The exporter is faster.\n",
    ".drafts/verification.md": (
        "# Login flow\n\n"
        "![The dashboard](docs/shot.png)\n\n"
        "![The flow](test-results/login.webm)\n\n"
        "![Outside](../../../etc/passwd)\n"
    ),
}


#: The files the fixtures name, as the registry sees them. The names the
#: reader serves are present; anything else is missing, so a tag naming a file
#: that is not there is refused exactly as it would be on a real worktree.
FIXTURE_FILES = frozenset(DRAFT_FILES) | {
    "shot.png",
    "verification.md",
    "login.webm",
    "passwd",
}


def fake_registry(names=FIXTURE_FILES) -> FileRegistry:
    """A registry that treats ``names`` as the files that exist."""
    return FileRegistry(is_file=lambda path: path.name in names)


def fake_reader(files: dict[str, str] | None = None):
    """A ``read_file`` serving ``files`` by name, and nothing else."""
    served = DRAFT_FILES if files is None else files

    def read(cwd: str, filename: str) -> str | None:
        return served.get(filename)

    return read


def file_id_names(doc) -> str:
    """The file a document's registered id names, by name alone.

    A ``draft_file`` source carries a registered id, which is an item id and
    the filename. The id is the registry's business, so a test asks which file
    the id stands for, not how it is spelled.
    """
    # An id is `<agent>-<n>-<filename>`, and a filename may itself hold a `-`,
    # so only the two id fields are dropped.
    return doc["source"]["fileId"].split("-", 2)[-1]


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


def test_codex_event_is_a_raw_transcript_fallback() -> None:
    state = seed([make_agent(id="ag1")])

    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        {
            "type": "codex_raw",
            "method": "item/started",
            "params": {"threadId": "thread-1", "item": {"type": "agentMessage"}},
        },
        NOW,
    )

    assert out.events[0]["item"] == {
        "type": "raw_event",
        "method": "item/started",
        "params": {
            "threadId": "thread-1",
            "item": {"type": "agentMessage"},
        },
        "id": "ag1-1",
        "ts": NOW,
    }


def agent_of(replayed: Replayed) -> dict:
    return replayed.state["world"]["agents"]["ag1"]


def open_attention(replayed: Replayed) -> list[dict]:
    world = replayed.state["world"]
    return [a for a in world["attention"].values() if a["clearedAt"] is None]


def items_of(replayed: Replayed, kind: str) -> list[dict]:
    return [i for i in replayed.items if i["type"] == kind]


FIXTURE_NAMES = sorted(p.name for p in FIXTURES.glob("*.jsonl"))
# An empty glob would turn every golden test into a skip, not a failure.
assert FIXTURE_NAMES, f"no recordings under {FIXTURES}"


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
    the reattach clock. `orchestrator-ui/src/selectors/attention.ts` sorts by ``raisedAt``,
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


def test_a_turn_leaves_the_session_the_launch_pinned_alone():
    """The transcript item still reports the live session, because that names
    the conversation it belongs to. The agent keeps the pinned one: the task
    link joins on it, and a ``/clear`` moves the live id out from under it."""
    state = replay("normal-turn.jsonl")
    assert state["transcripts"]["ag1"]["items"][0]["sessionId"] != "sess-1"
    assert agent_of(state)["session"] == "sess-1"


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


def test_an_assistant_event_reports_what_the_prompt_held():
    """The prompt's three counts, summed: 2 + 10121 + 14429 off the fixture.

    The stream must land on the same number the daemon's own state does, or the
    header would jump each time a poll overtook the stream.
    """
    state = replay("normal-turn.jsonl")
    assert agent_of(state)["contextTokens"] == 24552


def test_a_later_assistant_event_replaces_the_context_rather_than_adding():
    """Occupancy is a level, not a total — unlike the running total above."""
    state = replay("normal-turn.jsonl")
    out = normalise_stream_event(
        state.state,
        state.ctx,
        {
            "type": "assistant",
            "message": {
                "content": [],
                "usage": {"input_tokens": 3, "cache_read_input_tokens": 18159},
            },
        },
        NOW,
    )
    state.take(out.events)
    assert agent_of(state)["contextTokens"] == 18162


def test_a_compact_boundary_marks_the_transcript_and_brings_the_context_down():
    """The boundary is the only thing that says a compact finished.

    A refusal — too short a conversation — ends the turn exactly as a success
    does, so nothing downstream may read "the turn ended" as "it compacted".
    The item is the signal, and it carries both figures because the rule the
    UI draws names the fall.
    """
    state = replay("compact.jsonl")
    [item] = items_of(state, "compact")
    assert item["trigger"] == "manual"
    assert item["preTokens"] == 23238
    assert item["postTokens"] == 3046
    # The occupancy moves in the same step, so the header does not wait for
    # the agent to speak again — which, after a compact, it may never do.
    assert agent_of(state)["contextTokens"] == 3046


def test_an_autocompact_nobody_clicked_for_is_marked_the_same_way():
    """``trigger`` is the only thing that tells the two apart, and the
    operator needs the boundary drawn either way."""
    state = seed([make_agent(id="ag1", state="processing")])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        {
            "type": "system",
            "subtype": "compact_boundary",
            "compact_metadata": {"trigger": "auto", "pre_tokens": 9, "post_tokens": 4},
        },
        NOW,
    )
    replayed.take(out.events)
    [item] = items_of(replayed, "compact")
    assert item["trigger"] == "auto"


def test_a_compact_boundary_with_no_counts_still_marks_the_boundary():
    """The rule says a compact happened; the figures only describe it.

    Dropping the item on a missing count would lose the one signal the button
    waits on, so it would spin until its backstop instead.
    """
    state = seed([make_agent(id="ag1", state="processing", contextTokens=500)])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state,
        context_for_agent("ag1"),
        {"type": "system", "subtype": "compact_boundary"},
        NOW,
    )
    replayed.take(out.events)
    assert len(items_of(replayed, "compact")) == 1
    # Nothing said what the context now holds, so the last reading stands.
    assert agent_of(replayed)["contextTokens"] == 500


def test_the_summary_a_compact_injects_folds_instead_of_filling_the_transcript():
    """The continuation prompt is written by the harness, not by the operator.

    It runs to thousands of characters and lands directly under the rule that
    already says a compact happened, so read as an ordinary user turn it buries
    the boundary it belongs to. It folds, as a loaded skill does.
    """
    state = replay("compact.jsonl")
    summary = items_of(state, "compact_summary")
    assert len(summary) == 1
    assert summary[0]["markdown"].startswith("This session is being continued")
    # Not left as a user message as well.
    said = [i["markdown"] for i in items_of(state, "message") if i["role"] == "user"]
    assert not any("session is being continued" in m for m in said)


def test_a_user_turn_that_only_quotes_the_summary_opening_stays_a_message():
    """As with a skill, the prefix alone is not the test.

    An operator asking about the line must not have their message folded away.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    asked = "This session is being continued from a previous conversation — what does that mean?"
    out = normalise_stream_event(
        state.state,
        context_for_agent("ag1"),
        {"type": "user", "message": {"role": "user", "content": asked}},
        NOW,
    )
    state.take(out.events)
    assert items_of(state, "compact_summary") == []
    assert [i["markdown"] for i in items_of(state, "message")] == [asked]


def test_the_echo_a_slash_command_leaves_behind_is_not_shown():
    """`<local-command-stdout>Compacted</local-command-stdout>` is plumbing.

    The host injects it as a user turn, so it draws as though the operator
    typed a raw tag. The rule above it already says the compact happened.
    """
    state = replay("compact.jsonl")
    said = [i["markdown"] for i in items_of(state, "message")]
    assert not any("local-command-stdout" in m for m in said)


def user_turn(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def replay_turn(text: str, **agent_over) -> Replayed:
    """One user turn, into a seed agent."""
    state = Replayed(seed([make_agent(id="ag1", state="idle", **agent_over)]))
    out = normalise_stream_event(
        state.state, context_for_agent("ag1"), user_turn(text), NOW
    )
    state.take(out.events)
    return state


def test_an_echo_asks_the_agent_for_nothing_so_it_does_not_start_a_turn():
    """Dropping the item must not also drop the state rule with it: an echo
    is the host talking to itself, so nothing is owed a reply."""
    state = replay_turn("<local-command-stdout>Compacted </local-command-stdout>")
    assert state.items == []
    assert agent_of(state)["state"] == "idle"


def test_an_echo_either_side_of_a_message_does_not_take_the_message_with_it():
    """The match must not span from the first tag to the last.

    A greedy body swallows everything between two echoes, so an operator's
    words vanish with no turn left to show anything was there.
    """
    text = (
        "<local-command-stdout>out</local-command-stdout>\n\n"
        "the operator's own words\n"
        "<local-command-stdout>more</local-command-stdout>"
    )
    state = replay_turn(text)
    assert [i["markdown"] for i in items_of(state, "message")] == [text]


def test_a_subagent_notification_folds_to_its_summary():
    """The turn is plumbing wrapped around one fact: how the work went.

    Read as a message it draws an opaque id as though the operator typed it.
    Folded, the line keeps the fact and drops the addresses.
    """
    text = (
        "<task-notification>\n<task-id>a9ed786c2916ddbb0</task-id>\n"
        "<status>completed</status>\n"
        '<summary>Agent "Review commit e24fa3a" finished</summary>\n'
        "</task-notification>"
    )
    state = replay_turn(text)
    assert items_of(state, "message") == []
    [item] = items_of(state, "task_notification")
    assert {k: v for k, v in item.items() if k not in ("id", "ts")} == {
        "type": "task_notification",
        "status": "completed",
        "summary": 'Agent "Review commit e24fa3a" finished',
    }


def test_a_background_command_notification_folds_the_same_way():
    """The reported shape: space-separated, with a temp path and two ids.

    Neither id addresses anything the reader can open from the UI, and the
    path names a file on the agent's host. The summary is the whole signal.
    """
    text = (
        "<task-notification> <task-id>b7ypbbna6</task-id> "
        "<tool-use-id>toolu_01TtNvwf</tool-use-id> "
        "<output-file>/private/tmp/claude-501/tasks/b7ypbbna6.output</output-file> "
        "<status>completed</status> "
        '<summary>Background command "Run the Python gate" completed (exit code 0)'
        "</summary> </task-notification>"
    )
    state = replay_turn(text)
    assert items_of(state, "message") == []
    [item] = items_of(state, "task_notification")
    assert {k: v for k, v in item.items() if k not in ("id", "ts")} == {
        "type": "task_notification",
        "status": "completed",
        "summary": 'Background command "Run the Python gate" completed (exit code 0)',
    }
    drawn = json.dumps(item)
    for address in ("b7ypbbna6", "toolu_01TtNvwf", "/private/tmp/claude-501"):
        assert address not in drawn


def test_a_notification_asks_the_agent_for_something_so_it_starts_a_turn():
    """The mirror of the echo rule above. An echo is the host talking to
    itself, but the agent acts on a notification, so the turn has started."""
    state = replay_turn(
        "<task-notification>\n<status>completed</status>\n"
        "<summary>Done</summary>\n</task-notification>"
    )
    assert agent_of(state)["state"] == "processing"


def test_a_notification_either_side_of_a_message_does_not_take_the_message_with_it():
    """The match must not span from the first tag to the last.

    A greedy body swallows everything between two notifications, so an
    operator's words vanish with no turn left to show anything was there.
    """
    text = (
        "<task-notification><summary>one</summary></task-notification>\n\n"
        "the operator's own words\n"
        "<task-notification><summary>two</summary></task-notification>"
    )
    state = replay_turn(text)
    assert [i["markdown"] for i in items_of(state, "message")] == [text]


def test_a_turn_quoting_a_notification_is_not_swallowed():
    """An operator asking about a notification writes prose around it. Folding
    that away hides a real message behind a line they did not write."""
    text = "why did I get <task-notification><summary>x</summary></task-notification> twice?"
    state = replay_turn(text)
    assert [i["markdown"] for i in items_of(state, "message")] == [text]
    # The turn still reaches the upsert below the chain: it is a real message.
    assert agent_of(state)["state"] == "processing"


def test_a_notification_reads_its_own_summary_not_one_nested_in_another_child():
    """`re.search` over the whole turn takes the first `<summary>` it meets.

    A summary nested in another child would then win over the notification's
    own, so the line would report the wrong result for finished work — and the
    right one is unrecoverable, because the server keeps no transcript.
    """
    state = replay_turn(
        "<task-notification><wrapper><summary>INNER</summary></wrapper>"
        "<status>completed</status><summary>REAL</summary></task-notification>"
    )
    [item] = items_of(state, "task_notification")
    assert item["summary"] == "REAL"


def test_a_notification_naming_neither_field_is_not_worth_a_row():
    """Folding it would draw an empty line, with a time printed beside it.

    `drawsNothing` covers a waiting tool call and nothing else, so an item
    with no text still claims a gutter mark. A turn the fold has nothing to
    say about is better left as the message it is.
    """
    text = "<task-notification><task-id>b7ypbbna6</task-id></task-notification>"
    state = replay_turn(text)
    assert items_of(state, "task_notification") == []
    assert [i["markdown"] for i in items_of(state, "message")] == [text]


def test_a_notification_with_no_summary_still_folds():
    """It is still plumbing. Falling back would put the raw tag in a bubble."""
    state = replay_turn(
        "<task-notification><task-id>b7ypbbna6</task-id>"
        "<status>failed</status></task-notification>"
    )
    assert items_of(state, "message") == []
    [item] = items_of(state, "task_notification")
    assert item["status"] == "failed"
    assert item["summary"] == ""


def test_a_failed_local_command_keeps_its_output():
    """Only `stdout` is plumbing. A command that failed is the one whose
    output the operator is looking for, so `stderr` stays on the transcript."""
    text = "<local-command-stderr>no such command</local-command-stderr>"
    state = replay_turn(text)
    assert [i["markdown"] for i in items_of(state, "message")] == [text]


def test_a_boundary_whose_count_is_not_a_number_leaves_the_context_alone():
    """`_num` would read a string as 0 and draw the context as empty."""
    state = Replayed(
        seed([make_agent(id="ag1", state="processing", contextTokens=500)])
    )
    out = normalise_stream_event(
        state.state,
        context_for_agent("ag1"),
        {
            "type": "system",
            "subtype": "compact_boundary",
            "compact_metadata": {"post_tokens": "3046"},
        },
        NOW,
    )
    state.take(out.events)
    assert len(items_of(state, "compact")) == 1
    assert agent_of(state)["contextTokens"] == 500


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


def test_a_question_the_interrupt_denies_is_declined_not_stale():
    # A deny is a reply, and the interrupt sends one to every open ask.
    state = replay("interrupt-while-waiting.jsonl")
    question = items_of(state, "question")[0]
    assert question["declined"] is True
    assert question["reason"] == "Interrupted by user"
    assert "stale" not in question


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
    # The title is spelled out rather than left to `make_document`'s default.
    # `previous_version` matches a stored plan on its title, so this seed is
    # one half of the match: an independent literal says which title the
    # production code has to look for.
    doc = make_document(
        id="doc-1", agentId="ag1", version=1, status="changes-requested", title="Plan"
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


def test_the_detail_frame_restores_the_shells_the_backlog_lost():
    """A re-attach whose ring rolled past the snapshot learns the shells here."""
    shell = {"id": "b1", "description": "Run the tests"}
    state = seed([make_agent(id="ag1", state="idle")])
    replayed = Replayed(state)
    out = apply_agent_detail(
        state, context_for_agent("ag1"), {"request_id": "", "background": [shell]}, NOW
    )
    replayed.take(out.events)
    agent = agent_of(replayed)
    assert (agent["state"], agent["backgroundShells"]) == ("background", [shell])
    # The shell's end, on the stream that follows, is still heard.
    after = normalise_stream_event(replayed.state, out.ctx, tasks_changed(), NOW)
    replayed.take(after.events)
    assert agent_of(replayed)["state"] == "idle"


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


def test_mark_exited_raises_nothing_when_nobody_saw_the_exit():
    """A ``None`` code is an unobserved exit, which is not evidence of failure.

    Only a real exit carries a code. A synthesised one — a daemon that cannot
    confirm the agent — carries ``None``, and must not be reported as a fault.
    """
    state = replay("normal-turn.jsonl")
    out = mark_exited(state.state, state.ctx, None, NOW)
    state.take(out.events)
    assert agent_of(state)["state"] == "exited"
    assert agent_of(state)["exitCode"] is None
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
    """An older transcript's two turns for a ``!`` fold into a single item.

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


def test_a_shell_command_and_its_output_in_one_turn_become_one_item():
    """The daemon sends the command and its output as two blocks of one turn.

    The input block opens the item and the output block closes it within the
    same event, so the card arrives done. The two-turn shape above stays,
    because older transcripts still hold it.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    out = normalise_stream_event(
        state.state,
        context_for_agent("ag1"),
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "<bash-input>git status</bash-input>"},
                    {
                        "type": "text",
                        "text": "<bash-stdout>on main</bash-stdout>"
                        "<bash-stderr></bash-stderr>",
                    },
                ],
            },
        },
        NOW,
    )
    state.take(out.events)
    assert items_of(state, "message") == []
    assert [
        (i["command"], i["output"], i["status"]) for i in items_of(state, "shell")
    ] == [("git status", "on main", "done")]
    assert out.ctx.open_shell is None


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

    Transcripts written before the daemon sent one turn hold the pair as two,
    and a restart between them broke it. The later command needs its own item.
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


def feed(events, state: str = "idle") -> Replayed:
    """``events`` normalised in order into a seed agent in ``state``."""
    replayed = Replayed(seed([make_agent(id="ag1", state=state)]))
    ctx = context_for_agent("ag1")
    for raw in events:
        out = normalise_stream_event(replayed.state, ctx, raw, NOW)
        ctx = out.ctx
        replayed.take(out.events)
    return replayed


def task_started(tool_use_id: str, task_type: str = "local_agent") -> dict:
    return {
        "type": "system",
        "subtype": "task_started",
        "tool_use_id": tool_use_id,
        "task_type": task_type,
    }


def task_notification(tool_use_id: str) -> dict:
    return {
        "type": "system",
        "subtype": "task_notification",
        "tool_use_id": tool_use_id,
    }


#: The two background tasks in ``subagent-background.jsonl``, by ``tool_use_id``.
TASK_NAMES = {
    "toolu_01Psu3ew4jVXe96yarRAujE5": "subagent ends",
    "toolu_015penBXps91JNaUmB5hKzj3": "shell ends",
}


def test_a_turn_that_ends_under_a_running_subagent_is_delegating_until_it_ends():
    """The row's rule, on the stream: ``delegating`` from the ``result`` to the
    subagent's notification. The ``local_bash`` notification between changes
    nothing, and the next turn's assistant event sets ``processing``."""
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    seen = []
    for raw in read_fixture("subagent-background.jsonl"):
        out = normalise_stream_event(state.state, ctx, raw, NOW)
        ctx = out.ctx
        state.take(out.events)
        if raw.get("type") == "result":
            seen.append(("result", agent_of(state)["state"]))
        elif raw.get("subtype") == "task_notification":
            seen.append((TASK_NAMES[raw["tool_use_id"]], agent_of(state)["state"]))
        elif raw.get("type") == "assistant" and not raw.get("parent_tool_use_id"):
            seen.append(("assistant", agent_of(state)["state"]))
    assert seen == [
        ("assistant", "processing"),
        ("assistant", "processing"),
        ("assistant", "processing"),
        ("result", "delegating"),
        ("shell ends", "delegating"),
        ("subagent ends", "idle"),
        ("assistant", "processing"),
        ("result", "idle"),
    ]


#: The one shell ``bash-background.jsonl`` runs in the background.
SLEEP_SHELL = {"id": "bbs1cjtq4", "description": "Sleep 15 seconds then print done"}


def test_a_turn_that_ends_under_a_background_shell_is_background_until_it_ends():
    """The row's rule, on the stream: ``background`` from the ``result`` until the
    snapshot drops the shell. The foreground ``ls`` raises no task."""
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    seen = []
    for raw in read_fixture("bash-background.jsonl"):
        out = normalise_stream_event(state.state, ctx, raw, NOW)
        ctx = out.ctx
        state.take(out.events)
        if raw.get("type") == "result":
            label = "result"
        elif raw.get("subtype") == "background_tasks_changed":
            label = "changed"
        else:
            continue
        agent = agent_of(state)
        seen.append((label, agent["state"], agent["backgroundShells"]))
    assert seen == [
        ("changed", "processing", [SLEEP_SHELL]),
        ("result", "background", [SLEEP_SHELL]),
        ("changed", "idle", []),
        ("result", "idle", []),
    ]


def tasks_changed(*tasks: tuple[str, str]) -> dict:
    return {
        "type": "system",
        "subtype": "background_tasks_changed",
        "tasks": [
            {"task_id": task_id, "task_type": task_type, "description": "a task"}
            for task_id, task_type in tasks
        ],
    }


def test_a_subagent_that_ends_under_a_running_shell_leaves_background():
    """``delegating`` outranks ``background``; the subagent's list entry is not a shell."""
    events = [
        task_started("t1"),
        tasks_changed(("task-1", "local_agent"), ("b1", "local_bash")),
        {"type": "result"},
    ]
    agent = agent_of(feed(events, "processing"))
    assert (agent["state"], agent["backgroundShells"]) == (
        "delegating",
        [{"id": "b1", "description": "a task"}],
    )
    events.append(task_notification("t1"))
    assert agent_of(feed(events, "processing"))["state"] == "background"


def test_a_snapshot_after_the_turn_ended_reads_background():
    """The daemon derives the state whatever order the events came in."""
    events = [{"type": "result"}, tasks_changed(("b1", "local_bash"))]
    assert agent_of(feed(events, "processing"))["state"] == "background"


def test_a_new_turn_keeps_the_shell_that_still_runs():
    """``init`` opens every turn, so it must not clear the set: a user message
    sent while the shell runs would drop it."""
    events = [
        tasks_changed(("b1", "local_bash")),
        {"type": "result"},
        {"type": "system", "subtype": "init"},
        {"type": "result"},
    ]
    agent = agent_of(feed(events, "processing"))
    assert (agent["state"], agent["backgroundShells"]) == (
        "background",
        [{"id": "b1", "description": "a task"}],
    )


def test_an_exited_agent_holds_no_background_shell():
    state = feed([tasks_changed(("b1", "local_bash"))], "processing")
    out = mark_exited(state.state, context_for_agent("ag1"), 0, NOW)
    state.take(out.events)
    assert agent_of(state)["backgroundShells"] == []


def test_delegating_lasts_until_the_last_subagent_ends():
    events = [task_started("t1"), task_started("t2"), {"type": "result"}]
    assert agent_of(feed(events, "processing"))["state"] == "delegating"
    events.append(task_notification("t1"))
    assert agent_of(feed(events, "processing"))["state"] == "delegating"
    events.append(task_notification("t2"))
    assert agent_of(feed(events, "processing"))["state"] == "idle"


def test_a_subagents_answered_ask_goes_back_to_delegating():
    """A subagent's ask arrives on the parent's stream, as the parent's wait.
    Once it is answered no turn is open, so the agent is delegating again."""
    ask = {
        "type": "control_request",
        "request_id": "r1",
        "request": {"subtype": "can_use_tool", "tool_name": "WebFetch", "input": {}},
    }
    answer = {
        "type": "control_response",
        "response": {"request_id": "r1", "response": {"behavior": "allow"}},
    }
    events = [task_started("t1"), {"type": "result"}, ask]
    assert agent_of(feed(events, "processing"))["state"] == "awaiting-permission"
    events.append(answer)
    assert agent_of(feed(events, "processing"))["state"] == "delegating"
    events.append(task_notification("t1"))
    assert agent_of(feed(events, "processing"))["state"] == "idle"


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


def test_a_doc_content_tag_mints_nothing_and_stays_in_the_text():
    """Every document is a file, so an inline body is prose like any other."""
    state = replay("document-content.jsonl")
    assert documents_of(state) == []
    [message] = items_of(state, "message")
    assert "<doc-content" in message["markdown"]
    assert "## 1.4.0" in message["markdown"]


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
    assert doc["source"]["type"] == "draft_file"
    assert doc["source"]["filename"] == "draft-iter1.md"
    assert file_id_names(doc) == "draft-iter1.md"


def present(replayed: Replayed, ctx, text: str, files: dict[str, str]):
    """Normalise one tagged message into ``replayed``, and return the new context."""
    out = normalise_stream_event(
        replayed.state, ctx, tag_message(text), NOW, read_file=fake_reader(files)
    )
    replayed.take(out.events)
    return out.ctx


THREE_DRAFTS = {
    "one.md": task_model.draft_markdown(title="Execute: one", content="First."),
    "two.md": task_model.draft_markdown(title="Execute: two", content="Second."),
    "three.md": task_model.draft_markdown(title="Execute: three", content="Third."),
}

TASK_SET = (
    '<doc-file kind="tasks" filename="one.md, two.md, three.md" '
    'title="Iteration 1" review="true">'
)


def test_a_doc_file_tag_naming_three_files_mints_three_documents_in_one_group():
    """Each file is its own document; the tag makes them one review group."""
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    present(replayed, context_for_agent("ag1"), TASK_SET, THREE_DRAFTS)
    docs = documents_of(replayed)
    assert [d["source"]["filename"] for d in docs] == ["one.md", "two.md", "three.md"]
    # A task file is titled by its draft, not the tag.
    assert [d["title"] for d in docs] == [
        "Execute: one",
        "Execute: two",
        "Execute: three",
    ]
    # One file each: no document holds another's body.
    assert "First." in docs[0]["markdown"]
    assert "Second." not in docs[0]["markdown"]
    assert len({d["group"]["id"] for d in docs}) == 1
    assert {d["group"]["title"] for d in docs} == {"Iteration 1"}
    # The tag's order is the chain's order, which approve promotes in.
    assert [d["group"]["position"] for d in docs] == [0, 1, 2]
    assert all(d["status"] == "awaiting-review" for d in docs)


def test_re_presenting_an_open_file_is_its_next_version():
    """The path is the identity, whatever the status, so the card holds no duplicate."""
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = present(replayed, context_for_agent("ag1"), TASK_SET, THREE_DRAFTS)
    first = {d["source"]["filename"]: d for d in documents_of(replayed)}
    revised = {
        **THREE_DRAFTS,
        "two.md": task_model.draft_markdown(title="Execute: two", content="Revised."),
    }
    present(replayed, ctx, TASK_SET, revised)
    docs = {d["source"]["filename"]: d for d in documents_of(replayed)}
    assert len(docs) == 3
    assert docs["two.md"]["id"] == first["two.md"]["id"]
    assert docs["two.md"]["version"] == 2
    assert "Revised." in docs["two.md"]["markdown"]
    assert docs["two.md"]["group"]["id"] == first["two.md"]["group"]["id"]


def test_a_file_dropped_from_a_re_presented_group_is_superseded():
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = present(replayed, context_for_agent("ag1"), TASK_SET, THREE_DRAFTS)
    present(
        replayed,
        ctx,
        '<doc-file kind="tasks" filename="one.md, three.md" title="Iteration 1">',
        THREE_DRAFTS,
    )
    status = {d["source"]["filename"]: d["status"] for d in documents_of(replayed)}
    assert status == {"one.md": "draft", "two.md": "superseded", "three.md": "draft"}
    # A draft re-present asks for no verdict, so the group's old item goes.
    assert open_attention(replayed) == []


def test_a_group_raises_one_attention_item_and_a_re_present_replaces_it():
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = present(replayed, context_for_agent("ag1"), TASK_SET, THREE_DRAFTS)
    [first] = open_attention(replayed)
    head = documents_of(replayed)[0]
    assert first["documentId"] == head["id"]
    assert first["summary"] == "Iteration 1 awaiting review"
    present(replayed, ctx, TASK_SET, THREE_DRAFTS)
    [again] = open_attention(replayed)
    assert again["id"] != first["id"]


def test_a_path_moved_into_another_group_takes_its_old_group_with_it():
    """The tag replaces every group a path it names came from."""
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    for tag in (
        '<doc-file kind="tasks" filename="one.md" title="A" review="true">',
        '<doc-file kind="tasks" filename="two.md, three.md" title="B" review="true">',
        '<doc-file kind="tasks" filename="one.md, two.md" title="A" review="true">',
    ):
        ctx = present(replayed, ctx, tag, THREE_DRAFTS)
    status = {d["source"]["filename"]: d["status"] for d in documents_of(replayed)}
    assert status == {
        "one.md": "awaiting-review",
        "two.md": "awaiting-review",
        "three.md": "superseded",
    }
    [item] = open_attention(replayed)
    assert item["summary"] == "A awaiting review"


def test_two_spellings_of_one_path_are_one_document():
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = present(
        replayed,
        context_for_agent("ag1"),
        '<doc-file kind="other" filename="./notes.md">',
        {"notes.md": "N.\n"},
    )
    present(
        replayed,
        ctx,
        '<doc-file kind="other" filename="notes.md">',
        {"notes.md": "N.\n"},
    )
    [doc] = documents_of(replayed)
    assert (doc["source"]["filename"], doc["version"]) == ("notes.md", 2)


def test_a_doc_file_tag_naming_no_file_stays_in_the_text():
    """It presents nothing, so cutting it would hide the mistake."""
    replayed = replay_note('Look: <doc-file kind="other" title="Nothing">')
    assert documents_of(replayed) == []
    [message] = items_of(replayed, "message")
    assert '<doc-file kind="other" title="Nothing">' in message["markdown"]


def presented_by(agent: dict, others: list[dict]) -> Replayed:
    """``agent`` presents the task set into a world already holding ``others``."""
    replayed = Replayed(seed([agent], others))
    present(replayed, context_for_agent(agent["id"]), TASK_SET, THREE_DRAFTS)
    return replayed


def an_earlier_one_md(**over) -> dict:
    """An earlier `one.md` another agent presented, at v1 and approved."""
    return {
        "id": "ag0-1",
        "agentId": "ag0",
        "taskId": "NORT-7",
        "kind": "tasks",
        "title": "Execute: one",
        "markdown": "",
        "version": 1,
        "status": "approved",
        "source": {"type": "draft_file", "fileId": None, "filename": "one.md"},
        "group": {"id": "ag0-0", "title": "Iteration 1", "position": 0},
        **over,
    }


def test_a_second_agent_on_the_same_task_presents_the_same_document():
    """Scoped to the task, not the agent: a resumed session keeps its entries."""
    replayed = presented_by(make_agent(id="ag1", state="idle"), [an_earlier_one_md()])
    one = replayed.state["world"]["documents"]["ag0-1"]
    assert (one["version"], one["status"]) == (2, "awaiting-review")
    assert len(documents_of(replayed)) == 3


def test_a_file_presented_by_another_task_is_a_different_document():
    """`.drafts/pr.md` is a new pull request for each task."""
    earlier = an_earlier_one_md(taskId="NORT-8")
    replayed = presented_by(make_agent(id="ag1", state="idle"), [earlier])
    assert replayed.state["world"]["documents"]["ag0-1"] == earlier
    assert len(documents_of(replayed)) == 4


def test_an_agent_with_no_task_scopes_to_itself():
    """A free agent has no task, so another free agent's path is not its own."""
    earlier = an_earlier_one_md(taskId="")
    replayed = presented_by(make_agent(id="ag1", state="idle", taskId=""), [earlier])
    assert replayed.state["world"]["documents"]["ag0-1"] == earlier
    assert len(documents_of(replayed)) == 4


def test_re_presenting_an_approved_file_reopens_it_as_its_next_version():
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = present(replayed, context_for_agent("ag1"), TASK_SET, THREE_DRAFTS)
    approved = [
        {"type": "upsert", "kind": "document", "entity": {**d, "status": "approved"}}
        for d in documents_of(replayed)
    ]
    replayed.take(approved)
    present(replayed, ctx, TASK_SET, THREE_DRAFTS)
    assert [(d["version"], d["status"]) for d in documents_of(replayed)] == [
        (2, "awaiting-review")
    ] * 3


def test_a_reordered_re_present_moves_each_member_to_its_new_place():
    """The tag's order is the chain's order, whatever order they were first shown in."""
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = present(replayed, context_for_agent("ag1"), TASK_SET, THREE_DRAFTS)
    present(
        replayed,
        ctx,
        '<doc-file kind="tasks" filename="three.md, one.md, two.md" title="Iteration 1">',
        THREE_DRAFTS,
    )
    position = {
        d["source"]["filename"]: d["group"]["position"] for d in documents_of(replayed)
    }
    assert position == {"three.md": 0, "one.md": 1, "two.md": 2}


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


def test_a_task_set_shows_the_execute_model_in_its_recipe():
    """It decides how the task runs -- which model builds it -- so a reader
    approving the chain has to see it alongside the model it plans on."""
    draft = task_model.draft_markdown(
        title="Execute: demo",
        model="opus",
        execute_model="sonnet",
        content="Body.",
    )
    doc = show_file("tasks", "iter1.md", draft)
    assert "execute-model: sonnet" in doc["markdown"]


def test_a_task_set_whose_draft_will_not_parse_is_still_shown():
    """A draft the user must fix is exactly the one they need to read."""
    doc = show_file("tasks", "iter1.md", '---\ntitle: "unclosed\n---\n\nBody.\n')
    assert "Body." in doc["markdown"]


def test_a_set_whose_title_is_unset_is_titled_by_its_first_filename():
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    present(
        replayed,
        context_for_agent("ag1"),
        '<doc-file kind="other" filename="draft-one.md, draft-two.md">',
        {"draft-one.md": "# One\n", "draft-two.md": "# Two\n"},
    )
    docs = documents_of(replayed)
    assert {d["group"]["title"] for d in docs} == {"draft-one.md"}
    # Two files that are not drafts are titled by their names.
    assert [d["title"] for d in docs] == ["draft-one.md", "draft-two.md"]


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
        tag_message('<doc-file kind="taks" filename="draft-iter1.md" title="Typo">'),
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


def test_a_re_mint_versions_a_changes_requested_document_forward():
    """The plan document's rule: the comments stay attached to one document."""
    replayed = Replayed(seed([make_agent(id="ag1", state="idle")]))
    tag = '<doc-file kind="other" filename="notes.md" title="Notes">'
    ctx = present(replayed, context_for_agent("ag1"), tag, {"notes.md": "First.\n"})
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
    present(replayed, ctx, tag, {"notes.md": "Properly.\n"})
    [doc] = documents_of(replayed)
    assert doc["id"] == first["id"]
    assert doc["version"] == 2
    assert doc["status"] == "draft"
    assert doc["markdown"].strip() == "Properly."


def test_a_subagent_writes_no_document():
    """A subagent's stream is a transcript and its last message, nothing more."""
    state = seed([make_agent(id="ag1", parent="ag0", state="idle")])
    replayed = Replayed(state)
    tag = '<doc-file kind="other" filename="draft-iter1.md" title="Note">'
    out = normalise_stream_event(
        state, context_for_agent("ag1"), tag_message(tag), NOW, read_file=fake_reader()
    )
    replayed.take(out.events)
    assert documents_of(replayed) == []


# -- the note an agent writes to say what it is doing --


def note_of(replayed: Replayed) -> str:
    return agent_of(replayed).get("lastNote", "")


def replay_note(text: str, **agent_over) -> Replayed:
    """One tagged message into a seed agent, for what the note becomes."""
    state = seed([make_agent(id="ag1", state="idle", **agent_over)])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state, context_for_agent("ag1"), tag_message(text), NOW, read_file=fake_reader()
    )
    replayed.take(out.events)
    return replayed


def test_user_attention_stays_in_the_transcript_but_not_the_node_summary():
    replayed = replay_note("<user-attention low>\nChecking the allocator first.")
    [message] = items_of(replayed, "message")
    assert message["markdown"] == "<user-attention low>\nChecking the allocator first."
    assert agent_of(replayed)["lastMessage"] == "Checking the allocator first."


def test_a_note_tag_becomes_the_agents_note():
    """The answer to "what is this agent up to", in the agent's own words."""
    replayed = replay_note("<note>Rebasing onto main</note>")
    assert note_of(replayed) == "Rebasing onto main"


def test_the_note_tag_is_cut_from_the_message_the_transcript_shows():
    """A note is a field, not prose the reader sees twice."""
    replayed = replay_note("Working on it.\n\n<note>Rebasing onto main</note>")
    [message] = items_of(replayed, "message")
    assert "<note>" not in message["markdown"]
    assert "Rebasing onto main" not in message["markdown"]
    assert message["markdown"] == "Working on it."


def test_the_last_note_in_a_message_wins():
    """Latest only: the note replaces, as the last message does."""
    replayed = replay_note("<note>Reading the test</note>\n<note>Fixing it</note>")
    assert note_of(replayed) == "Fixing it"


def test_a_message_with_no_note_leaves_the_note_alone():
    """An agent that never notes reads exactly as it does today."""
    replayed = replay_note("Just talking.")
    assert note_of(replayed) == ""


def test_a_subagent_writes_no_note():
    """Same reason a subagent mints no document: its tags stay as text."""
    replayed = replay_note("<note>Reading the reducer</note>", parent="ag0")
    assert note_of(replayed) == ""
    [message] = items_of(replayed, "message")
    assert "<note>Reading the reducer</note>" in message["markdown"]


def test_the_note_is_dated_by_the_event_that_carried_it():
    replayed = replay_note("<note>Rebasing onto main</note>")
    assert agent_of(replayed)["lastNoteAt"] == NOW


def test_a_note_does_not_become_what_the_agent_last_said():
    """The note is cut, so it cannot also stand as the last message."""
    replayed = replay_note("Working on it.\n\n<note>Rebasing onto main</note>")
    assert agent_of(replayed)["lastMessage"] == "Working on it."


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


TOKEN_REF = "Look: ![shot]({{MAEL_TASK_DIR}}/images/t1/shot.png)"


def test_a_user_turn_shows_its_attachment_refs_as_the_caller_rewrites_them():
    """The server passes ``attachment_urls``, with the agent's project for the token."""
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    out = normalise_stream_event(
        state.state,
        context_for_agent("ag1"),
        user_turn(TOKEN_REF),
        NOW,
        show_refs=attachment_urls,
    )
    state.take(out.events)
    [message] = items_of(state, "message")
    assert (
        message["markdown"] == "Look: ![shot](/api/attachments/northwind/t1/shot.png)"
    )


def test_a_user_turn_keeps_its_attachment_refs_by_default():
    """A terminal reader can open the path a first prompt holds; a URL it cannot."""
    [message] = items_of(replay_turn(TOKEN_REF), "message")
    assert message["markdown"] == TOKEN_REF


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
    assert doc["source"]["filename"] == "draft-iter1.md"
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


# -- the milestone an agent writes to mark a stage of the work --


def replay_milestone(text: str, **agent_over) -> tuple[Replayed, object]:
    """One tagged message, for the replay and the normaliser's own output.

    A milestone is not a world change, so it does not travel as a
    ``ServerEvent``: it comes back on :class:`Normalised` for the server to
    write to the ledger. Both are returned because the tests assert on each.
    """
    state = seed([make_agent(id="ag1", state="idle", **agent_over)])
    replayed = Replayed(state)
    out = normalise_stream_event(
        state, context_for_agent("ag1"), tag_message(text), NOW, read_file=fake_reader()
    )
    replayed.take(out.events)
    return replayed, out


def test_a_milestone_tag_is_reported_beside_the_events():
    replayed, out = replay_milestone("<milestone>built</milestone>")
    assert out.milestone is not None
    assert out.milestone.name == "built"
    assert out.milestone.at == NOW
    assert out.milestone.recognised is True


def test_the_milestone_tag_is_cut_from_the_message_the_transcript_shows():
    """A milestone is a marker, not prose the reader sees."""
    replayed, _ = replay_milestone("Tests pass.\n\n<milestone>built</milestone>")
    [message] = items_of(replayed, "message")
    assert "<milestone>" not in message["markdown"]
    assert message["markdown"] == "Tests pass."


def test_an_unrecognised_milestone_name_is_kept_and_flagged():
    """Stored as written, never dropped: a typo must be visible, not silent."""
    _, out = replay_milestone("<milestone>deployed</milestone>")
    assert out.milestone.name == "deployed"
    assert out.milestone.recognised is False


def test_the_last_milestone_in_a_message_wins():
    """Latest only, as a note is."""
    _, out = replay_milestone(
        "<milestone>built</milestone>\n<milestone>reviewed</milestone>"
    )
    assert out.milestone.name == "reviewed"


def test_a_message_with_no_milestone_reports_none():
    _, out = replay_milestone("Just talking.")
    assert out.milestone is None


def test_a_subagent_writes_no_milestone():
    """Same reason it mints no document: its tags stay as text."""
    replayed, out = replay_milestone("<milestone>built</milestone>", parent="ag0")
    assert out.milestone is None
    [message] = items_of(replayed, "message")
    assert "<milestone>built</milestone>" in message["markdown"]


def test_a_milestone_does_not_become_what_the_agent_last_said():
    replayed, _ = replay_milestone("Tests pass.\n\n<milestone>built</milestone>")
    assert agent_of(replayed)["lastMessage"] == "Tests pass."


# -- the milestone Maelstrom writes when the user approves a plan --


def replay_plan_decision(
    *,
    allow: bool,
    tool: str = "ExitPlanMode",
    request_id: str = "req-1",
    answer_id: str = "req-1",
    parent: str = "",
) -> object:
    """A request and the decision that answers it, for the normaliser's output.

    Two events rather than one, because the decision is only recognised as a
    plan's when its request is still pending. ``answer_id`` differs from
    ``request_id`` to answer a request the normaliser never saw, and ``parent``
    makes the agent a subagent — the gate reads the agent's own parent, not
    anything on the event.
    """
    state = seed([make_agent(id="ag1", parent=parent, state="processing")])
    ctx = context_for_agent("ag1")
    request = {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "can_use_tool",
            "tool_name": tool,
            "input": {"plan": "# The plan", "planFilePath": "/p.md"},
            "tool_use_id": "toolu_1",
        },
    }
    out = normalise_stream_event(state, ctx, request, NOW)
    for event in out.events:
        state = apply_event(state, event)
    return normalise_stream_event(
        state,
        out.ctx,
        {
            "type": "control_response",
            "response": {
                "request_id": answer_id,
                "response": {"behavior": "allow" if allow else "deny"},
            },
        },
        NOW,
    )


def test_approving_a_plan_reports_the_planned_milestone():
    """Maelstrom marks the stage itself: the agent is cleared right afterwards.

    An agent cannot write the marker for a plan it just had approved, because
    the approval interrupts it and clears its context before it says anything.
    """
    out = replay_plan_decision(allow=True)
    assert out.milestone is not None
    assert out.milestone.name == "planned"
    assert out.milestone.at == NOW
    assert out.milestone.recognised is True


def test_denying_a_plan_reports_no_milestone():
    """No stage was reached: the agent goes back to planning."""
    assert replay_plan_decision(allow=False).milestone is None


def test_approving_something_other_than_a_plan_reports_no_milestone():
    out = replay_plan_decision(allow=True, tool="Bash")
    assert out.milestone is None


def test_a_decision_for_a_request_the_normaliser_never_saw_reports_no_milestone():
    """`response` returns early with nothing pending, so no stage is invented."""
    out = replay_plan_decision(allow=True, answer_id="req-unseen")
    assert out.milestone is None


def test_a_subagents_plan_approval_reports_no_milestone():
    """`request` is gated on the top-level agent, so a subagent holds no pending.

    A subagent plans within its parent's stage. Its approval is not a stage of
    the session's own work.
    """
    out = replay_plan_decision(allow=True, parent="ag0")
    assert out.milestone is None


def test_both_note_patterns_are_still_in_step():
    """Each reader holds its own copy, so neither depends on the other.

    The patterns only: what each reader *does* with a match differs on purpose.
    `read_tags` skips a `<note>` inside a tag it has already cut, where this
    module's `read_note` has no spans to skip and takes the last match. Only the
    tag's spelling is shared, and duplicated text drifts silently.
    """
    assert agent_model._NOTE_TAG.pattern == document_tags._NOTE_TAG.pattern
    assert agent_model._NOTE_TAG.flags == document_tags._NOTE_TAG.flags


# --- a partial message -----------------------------------------------------

PARTIAL_FIXTURES = [name for name in FIXTURE_NAMES if name.startswith("partial-")]


def replay_partial(name: str, *, chunks: bool = True):
    """Replay ``name``, and return the state and every batch with its raw event.

    ``chunks=False`` leaves the ``stream_event`` lines out, which is the
    stream as it was before the daemon asked for partial messages.
    """
    state = Replayed(seed([make_agent(id="ag1", state="idle")]))
    ctx = context_for_agent("ag1")
    batches: list[tuple[dict, list[dict]]] = []
    for raw in read_fixture(name):
        if not chunks and raw["type"] == "stream_event":
            continue
        out = normalise_stream_event(
            state.state, ctx, raw, NOW, read_file=fake_reader(), files=fake_registry()
        )
        ctx = out.ctx
        state.take(out.events)
        batches.append((raw, out.events))
    return state, batches


def _whole(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "partial"}


@pytest.mark.parametrize("name", PARTIAL_FIXTURES)
def test_a_partial_message_ends_as_the_whole_message_would_have(name):
    """Same items, same ids, same world: the chunks change only the journey."""
    with_chunks, _ = replay_partial(name)
    without, _ = replay_partial(name, chunks=False)
    assert [_whole(i) for i in with_chunks.items] == without.items
    assert with_chunks.state["world"] == without.state["world"]
    assert not any(i.get("partial") for i in with_chunks.items)


def _partial_frames(batches) -> list[dict]:
    """The transcript events the chunks alone produced."""
    return [
        e for raw, events in batches if raw["type"] == "stream_event" for e in events
    ]


def test_a_partial_message_is_appended_once_and_then_grows_in_place():
    _, batches = replay_partial("partial-turn.jsonl")
    frames = _partial_frames(batches)
    appended = [f for f in frames if f["type"] == "transcript.append"]
    # Two messages in the turn: the long answer, and the closing sentence.
    assert [f["item"]["partial"] for f in appended] == [True, True]
    first = appended[0]["item"]["id"]
    grown = [
        f["markdown"]
        for f in frames
        if f["type"] == "transcript.partial" and f["itemId"] == first
    ]
    assert len(grown) > 100
    # The whole text so far each time, so a dropped refresh loses nothing.
    assert all(
        later.startswith(earlier.rstrip()) for earlier, later in zip(grown, grown[1:])
    )


def test_the_complete_message_replaces_the_partial_one_under_its_id():
    _, batches = replay_partial("partial-turn.jsonl")
    first = _partial_frames(batches)[0]["item"]["id"]
    [closing] = [
        e
        for raw, events in batches
        if raw["type"] == "assistant"
        for e in events
        if e.get("itemId") == first
    ]
    assert closing["type"] == "transcript.update"
    assert closing["patch"]["partial"] is False
    assert closing["patch"]["markdown"].startswith("<user-attention high>")


def test_no_tag_shows_and_none_takes_effect_before_the_message_is_complete():
    """The fixture's message carries a note, a document and an attention tag."""
    state, batches = replay_partial("partial-markers.jsonl")
    frames = _partial_frames(batches)
    shown = [
        f["item"]["markdown"] if f["type"] == "transcript.append" else f["markdown"]
        for f in frames
    ]
    assert len(shown) > 30
    for markdown in shown:
        for marker in ("<note", "</note", "<doc-file", "writing about tea"):
            assert marker not in markdown
        assert not re.search(r"<[^>]*$", markdown), markdown
    # A chunk moves the transcript and nothing else: no document, no agent.
    assert {f["type"] for f in frames} == {"transcript.append", "transcript.partial"}
    assert agent_of(state)["lastNote"] == "writing about tea"
    assert len(state.state["world"]["documents"]) == 1


def test_each_message_shows_its_whole_text_before_it_closes():
    """No chunk waits for a later one: when a burst ends, the card is current."""
    _, batches = replay_partial("partial-turn.jsonl")
    shown: dict[str, str] = {}
    closed: dict[str, str] = {}
    for raw, events in batches:
        for event in events:
            if event["type"] == "transcript.append" and event["item"].get("partial"):
                shown[event["item"]["id"]] = event["item"]["markdown"]
            elif event["type"] == "transcript.partial":
                shown[event["itemId"]] = event["markdown"]
            elif raw["type"] == "assistant" and event.get("itemId") in shown:
                closed[event["itemId"]] = event["patch"]["markdown"]
    assert len(closed) == 2
    assert {item_id: shown[item_id] for item_id in closed} == closed


def test_an_interrupted_partial_message_keeps_the_text_it_has():
    """The child sends the text so far as a whole message before the result."""
    state, _ = replay_partial("partial-interrupt.jsonl")
    said = [i for i in items_of(state, "message") if i["role"] == "assistant"]
    assert said[-1]["partial"] is False
    assert len(said[-1]["markdown"]) > 500


def _open_partial() -> tuple[Replayed, NormaliseContext]:
    """A turn cut off after its first chunks, with no whole message to follow."""
    state = Replayed(seed([make_agent(id="ag1", state="processing")]))
    ctx = context_for_agent("ag1")
    for raw in read_fixture("partial-turn.jsonl"):
        if raw["type"] == "assistant":
            break
        out = normalise_stream_event(state.state, ctx, raw, NOW)
        ctx = out.ctx
        state.take(out.events)
    assert items_of(state, "message")[-1]["partial"] is True
    return state, ctx


def test_a_turn_that_ends_with_no_whole_message_closes_the_partial_one():
    state, ctx = _open_partial()
    result = {"type": "result", "subtype": "error_during_execution"}
    state.take(normalise_stream_event(state.state, ctx, result, NOW).events)
    message = items_of(state, "message")[-1]
    assert message["partial"] is False
    assert message["markdown"]


def test_an_agent_that_dies_closes_its_partial_message():
    state, ctx = _open_partial()
    state.take(mark_exited(state.state, ctx, 1, NOW).events)
    assert items_of(state, "message")[-1]["partial"] is False


def test_a_stream_that_is_lost_closes_its_partial_message():
    """A daemon restart ends the attach with no exit, and replays no chunk. The
    context goes with the watch, so the item is closed while its id is known."""
    state, ctx = _open_partial()
    out = close_partial_message(state.state, ctx, NOW)
    state.take(out.events)
    assert items_of(state, "message")[-1]["partial"] is False
    assert out.ctx.partial is None


def _said(state: Replayed) -> list[dict]:
    return [i for i in items_of(state, "message") if i["role"] == "assistant"]


def test_a_message_joined_part_way_through_shows_only_when_it_is_whole():
    """An attach replays no chunk, so one that begins in the middle of a
    message gets text with no ``message_start`` to say whose it is."""
    events = read_fixture("partial-turn.jsonl")
    first_text = next(
        n
        for n, e in enumerate(events)
        if e["type"] == "stream_event" and "delta" in e["event"]
    )
    state = Replayed(seed([make_agent(id="ag1", state="processing")]))
    ctx = context_for_agent("ag1")
    partial_frames = 0
    for raw in events[first_text + 1 :]:
        out = normalise_stream_event(state.state, ctx, raw, NOW)
        ctx = out.ctx
        state.take(out.events)
        if raw["type"] == "assistant":
            break
        partial_frames += len(out.events)
    assert partial_frames == 0
    [whole] = _said(state)
    assert "partial" not in whole


def test_a_whole_message_that_is_not_the_partial_one_is_appended_beside_it():
    """The chunks named one message and a different one arrived whole: a
    ``message_start`` was lost. The partial message keeps its text and closes."""
    state, ctx = _open_partial()
    other = {
        "type": "assistant",
        "message": {"id": "msg_other", "content": [{"type": "text", "text": "Other."}]},
    }
    state.take(normalise_stream_event(state.state, ctx, other, NOW).events)
    partial, whole = _said(state)
    assert partial["partial"] is False
    assert whole["markdown"] == "Other."
    assert "partial" not in whole


# -- the PR an agent registers on its task --


@pytest.mark.parametrize("ref", ["118", "https://github.com/o/r/pull/118"])
def test_a_pr_link_tag_is_reported_beside_the_events(ref):
    _, out = replay_milestone(f'Pushed.\n\n<link rel="gh-pr">{ref}</link>')
    assert out.pr_link == ref


def test_the_pr_link_tag_is_cut_from_the_message_the_transcript_shows():
    replayed, _ = replay_milestone('Pushed.\n\n<link rel="gh-pr">118</link>')
    [message] = items_of(replayed, "message")
    assert message["markdown"] == "Pushed."


def test_the_last_pr_link_in_a_message_wins():
    _, out = replay_milestone(
        '<link rel="gh-pr">1</link> then <link rel="gh-pr">2</link>'
    )
    assert out.pr_link == "2"


def test_a_pr_link_quoted_in_code_registers_nothing():
    """A PR link writes to the task store, so an example must not."""
    _, out = replay_milestone('Write `<link rel="gh-pr">118</link>` to register.')
    assert out.pr_link == ""


def test_an_unclosed_html_link_does_not_swallow_a_pr_link():
    _, out = replay_milestone(
        '<link rel="stylesheet" href="x.css"> then <link rel="gh-pr">118</link>'
    )
    assert out.pr_link == "118"


def test_a_link_of_another_rel_is_left_as_text():
    replayed, out = replay_milestone('<link rel="stylesheet">x</link>')
    assert out.pr_link == ""
    [message] = items_of(replayed, "message")
    assert "<link" in message["markdown"]
