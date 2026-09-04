"""Raw stream-json from the agent host, as the events the web UI wants.

A port of ``web/src/protocol/normalise.ts``. The state machine follows
:func:`maelstrom.agent_model.apply_event`: a pending request outranks assistant
output, a ``control_response`` for the pending request ends the wait, a
``result`` ends the turn idle. Pure: no I/O, no clock.

The TypeScript module is the reference; see "Normaliser parity" in
``docs/dev/orchestrator-server.md``.
"""

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ..agent_model import PLAN_TOOL, QUESTION_TOOL
from .protocol import Agent, Attention, ClientState, Document, ServerEvent

Dict = dict[str, Any]


@dataclass(frozen=True)
class PendingContext:
    """The request the agent is blocked on and what the UI made of it."""

    request_id: str
    #: The tool_use block the request belongs to; ``""`` when the stream did not say.
    tool_use_id: str
    tool: str
    input: Dict
    item_id: str
    attention_id: str
    document_id: str | None


@dataclass(frozen=True)
class NormaliseContext:
    """What the normaliser remembers between events for one agent.

    Ids it handed out, the tool calls still open, the pending request, and the
    last thing the agent said (a plan review without a plan falls back to it).
    """

    agent_id: str
    next_id: int = 1
    open_tool_calls: dict[str, str] = field(default_factory=dict)
    pending: PendingContext | None = None
    last_assistant_text: str = ""
    #: Tool uses the CLI refused by rule; their tool_result arrives as ``denied``.
    denied_tool_uses: tuple[str, ...] = ()


@dataclass(frozen=True)
class Normalised:
    events: list[ServerEvent]
    ctx: NormaliseContext


def context_for_agent(state: ClientState, agent_id: str) -> NormaliseContext:
    """A fresh context, or one rebuilt from what the world already holds.

    ``denied_tool_uses`` is not rebuilt: the world does not record a denial
    until its ``tool_result`` lands, so a context rebuilt between the two
    marks that one call ``done``. The TypeScript context has the same window.
    """
    agent = state["world"]["agents"].get(agent_id)
    transcript = state["transcripts"].get(agent_id)
    items = transcript["items"] if transcript else []
    open_calls: dict[str, str] = {}
    last_text = ""
    for item in items:
        if item["type"] == "tool_call" and item["status"] in ("running", "pending"):
            open_calls[item["toolUseId"]] = item["id"]
        if item["type"] == "message" and item["role"] == "assistant":
            last_text = item["markdown"]
    pending = None
    request_id = agent.get("pendingRequestId") if agent else None
    if agent and request_id:
        item = next(
            (i for i in reversed(items) if i.get("requestId") == request_id), None
        )
        attention = next(
            (
                a
                for a in state["world"]["attention"].values()
                if a["clearedAt"] is None and a["requestId"] == request_id
            ),
            None,
        )
        if item is not None:
            kind = item["type"]
            if kind == "question":
                tool, inp = QUESTION_TOOL, {"questions": item["questions"]}
            elif kind == "plan_review":
                tool, inp = PLAN_TOOL, {}
            elif kind == "permission_request":
                tool, inp = item["tool"], item["input"]
            else:
                tool, inp = "", {}
            pending = PendingContext(
                request_id=request_id,
                tool_use_id="",
                tool=tool,
                input=inp,
                item_id=item["id"],
                attention_id=attention["id"] if attention else "",
                document_id=item["documentId"] if kind == "plan_review" else None,
            )
    return NormaliseContext(
        agent_id=agent_id,
        next_id=len(items) + 1,
        open_tool_calls=open_calls,
        pending=pending,
        last_assistant_text=last_text,
    )


def normalise_stream_event(
    state: ClientState, ctx: NormaliseContext, raw: Dict, now: str
) -> Normalised:
    """One raw agent-host event, as the events the UI wants."""
    agent = state["world"]["agents"].get(ctx.agent_id)
    if agent is None:
        return Normalised([], ctx)
    out = _Emitter(state, agent, ctx, now)
    kind = raw.get("type")

    if kind == "system":
        if raw.get("subtype") == "init":
            session_id = _str(raw.get("session_id"))
            model = _str(raw.get("model"))
            out.append(
                {
                    "type": "system",
                    "subtype": "init",
                    "sessionId": session_id,
                    "model": model,
                }
            )
            out.agent(
                {
                    "session": session_id or agent["session"],
                    "model": model or agent["model"],
                }
            )
        elif raw.get("subtype") == "permission_denied":
            out.ctx = replace(
                out.ctx,
                denied_tool_uses=out.ctx.denied_tool_uses
                + (_str(raw.get("tool_use_id")),),
            )

    elif kind == "user":
        for block in _blocks(raw):
            if block.get("type") == "text" and _str(block.get("text")):
                out.append(
                    {"type": "message", "role": "user", "markdown": _str(block["text"])}
                )
                # A message to the agent is the start of a turn. Without this
                # the UI shows "idle" until the agent's first event lands,
                # which reads as though nothing was sent.
                if out.ctx.pending is None:
                    out.agent({"state": "processing"})
            elif block.get("type") == "tool_result":
                out.tool_result(block)

    elif kind == "assistant":
        for block in _blocks(raw):
            if block.get("type") == "text" and _str(block.get("text")):
                text = _str(block["text"])
                out.append({"type": "message", "role": "assistant", "markdown": text})
                out.ctx = replace(out.ctx, last_assistant_text=text)
                out.agent({"lastMessage": _one_line(text)})
            elif block.get("type") == "tool_use":
                tool_use_id = _str(block.get("id"))
                out.append(
                    {
                        "type": "tool_call",
                        "toolUseId": tool_use_id,
                        "tool": _str(block.get("name")),
                        "input": _dict(block.get("input")),
                        "status": "running",
                    },
                    tool_use_id,
                )
                out.ctx = replace(
                    out.ctx,
                    open_tool_calls={
                        **out.ctx.open_tool_calls,
                        tool_use_id: tool_use_id,
                    },
                )
        if out.ctx.pending is None and agent["state"] != "processing":
            out.agent({"state": "processing"})

    elif kind == "control_request":
        request = _dict(raw.get("request"))
        if request.get("subtype") == "can_use_tool":
            out.request(
                _str(raw.get("request_id")),
                _str(request.get("tool_use_id")),
                _str(request.get("tool_name")),
                _dict(request.get("input")),
                _str(request.get("description")),
            )

    elif kind == "control_cancel_request":
        pending = out.ctx.pending
        if pending is not None and _str(raw.get("request_id")) == pending.request_id:
            out.end_wait()
            out.agent(
                {"state": "processing", "pendingRequestId": None, "waitingOn": ""}
            )

    elif kind == "control_response":
        response = _dict(raw.get("response"))
        request_id = _str(response.get("request_id"))
        if out.ctx.pending is not None and request_id == out.ctx.pending.request_id:
            out.response(_dict(response.get("response")))

    elif kind == "result":
        out.append(
            {
                "type": "turn_result",
                "subtype": _str(raw.get("subtype")) or "success",
                "costUsd": _num(raw.get("total_cost_usd")),
                "durationMs": _num(raw.get("duration_ms")),
            }
        )
        out.end_wait()
        out.agent(
            {
                "state": "idle",
                "costUsd": _num(raw.get("total_cost_usd")),
                "session": _str(raw.get("session_id")) or agent["session"],
            }
        )

    return out.done()


def mark_exited(
    state: ClientState, ctx: NormaliseContext, exit_code: int | None, now: str
) -> Normalised:
    """The events for an agent whose process has gone. Mirrors ``agent_model.mark_exited``."""
    agent = state["world"]["agents"].get(ctx.agent_id)
    if agent is None:
        return Normalised([], ctx)
    out = _Emitter(state, agent, ctx, now)
    out.end_wait()
    out.agent(
        {
            "state": "exited",
            "exitCode": exit_code,
            "pendingRequestId": None,
            "waitingOn": "",
        }
    )
    if exit_code != 0:
        out.raise_attention("agent_exited", f"Exited with code {exit_code}", None, None)
    return out.done()


def revive_agent(
    state: ClientState,
    ctx: NormaliseContext,
    row_state: str,
    now: str,
    *,
    task_id: str,
    project: str,
    worktree_id: str,
    phase: str,
) -> Normalised:
    """The events for an exited agent that has come back under its own id.

    A resume keeps the agent id, so the row that returns names the agent the
    world already holds. The exit is over: the code is cleared and the item
    that asked someone to look at it goes with it. The inverse of
    :func:`mark_exited`.

    The links are re-resolved in the same event, not a following one. A task or
    worktree that arrived while the agent was gone would otherwise leave the
    revived agent on screen with a stale link until the next poll.
    """
    agent = state["world"]["agents"].get(ctx.agent_id)
    if agent is None:
        return Normalised([], ctx)
    out = _Emitter(state, agent, ctx, now)
    out.agent(
        {
            "state": row_state,
            "exitCode": None,
            "taskId": task_id,
            "project": project,
            "worktreeId": worktree_id,
            "phase": phase,
        }
    )
    for item in state["world"]["attention"].values():
        if (
            item["kind"] == "agent_exited"
            and item["agentId"] == ctx.agent_id
            and item["clearedAt"] is None
        ):
            out.clear(item["id"])
    return out.done()


class _Emitter:
    """Collects the events for one raw event and threads the context through."""

    def __init__(
        self, state: ClientState, agent: Agent, ctx: NormaliseContext, now: str
    ):
        self.state = state
        self.now = now
        self.agent_entity: Agent = agent
        self.agent_dirty = False
        self.ctx = ctx
        self.events: list[ServerEvent] = []
        # Entities this batch created, so a later step in the batch can update them.
        self.local_attention: dict[str, Attention] = {}
        self.local_documents: dict[str, Document] = {}

    def new_id(self) -> str:
        item_id = f"{self.ctx.agent_id}-{self.ctx.next_id}"
        self.ctx = replace(self.ctx, next_id=self.ctx.next_id + 1)
        return item_id

    def append(self, item: Dict, item_id: str | None = None) -> str:
        item_id = item_id if item_id is not None else self.new_id()
        self.events.append(
            {
                "type": "transcript.append",
                "agentId": self.ctx.agent_id,
                "item": {**item, "id": item_id, "ts": self.now},
            }
        )
        return item_id

    def update(self, item_id: str, patch: Dict) -> None:
        self.events.append(
            {
                "type": "transcript.update",
                "agentId": self.ctx.agent_id,
                "itemId": item_id,
                "patch": patch,
            }
        )

    def agent(self, patch: Dict) -> None:
        self.agent_entity = {**self.agent_entity, **patch}  # type: ignore[typeddict-item]
        self.agent_dirty = True

    def tool_result(self, block: Dict) -> None:
        tool_use_id = _str(block.get("tool_use_id"))
        item_id = self.ctx.open_tool_calls.get(tool_use_id)
        if not item_id:
            return
        denied = tool_use_id in self.ctx.denied_tool_uses
        if denied:
            self.ctx = replace(
                self.ctx,
                denied_tool_uses=tuple(
                    t for t in self.ctx.denied_tool_uses if t != tool_use_id
                ),
            )
        status = "denied" if denied else ("error" if block.get("is_error") else "done")
        self.update(
            item_id, {"status": status, "output": _result_text(block.get("content"))}
        )
        open_calls = dict(self.ctx.open_tool_calls)
        open_calls.pop(tool_use_id, None)
        self.ctx = replace(self.ctx, open_tool_calls=open_calls)

    def request(
        self, request_id: str, tool_use_id: str, tool: str, inp: Dict, description: str
    ) -> None:
        document_id: str | None = None
        if tool == QUESTION_TOOL:
            questions = _questions_of(inp)
            item_id = self.append(
                {"type": "question", "requestId": request_id, "questions": questions}
            )
            kind = "question"
            summary = questions[0]["question"] if questions else tool
            wait_state = "awaiting-question"
        elif tool == PLAN_TOOL:
            plan = _str(inp.get("plan"))
            # A plan sent back for changes comes around again as the next
            # version of the same document, so its comments stay attached.
            previous = next(
                (
                    d
                    for d in self.state["world"]["documents"].values()
                    if d["agentId"] == self.ctx.agent_id
                    and d["kind"] == "plan"
                    and d["status"] == "changes-requested"
                ),
                None,
            )
            document_id = previous["id"] if previous else self.new_id()
            doc: Document = {
                "id": document_id,
                "agentId": self.ctx.agent_id,
                "taskId": self.agent_entity["taskId"],
                "kind": "plan",
                "title": "plan.md",
                "markdown": plan or self.ctx.last_assistant_text,
                "version": (previous["version"] if previous else 0) + 1,
                "status": "awaiting-review",
                "source": {
                    "type": "plan_review",
                    "requestId": request_id,
                    "planFilePath": _str(inp.get("planFilePath")) if plan else "",
                },
            }
            self.local_documents[document_id] = doc
            self.events.append({"type": "upsert", "kind": "document", "entity": doc})
            item_id = self.append(
                {
                    "type": "plan_review",
                    "requestId": request_id,
                    "documentId": document_id,
                }
            )
            kind = "plan_review"
            summary = "Plan awaiting review"
            wait_state = "awaiting-plan-review"
        else:
            item_id = self.append(
                {
                    "type": "permission_request",
                    "requestId": request_id,
                    "tool": tool,
                    "input": inp,
                    "description": description,
                }
            )
            kind = "permission"
            summary = description or tool
            wait_state = "awaiting-permission"
        attention_id = self.raise_attention(kind, summary, request_id, document_id)
        self.ctx = replace(
            self.ctx,
            pending=PendingContext(
                request_id=request_id,
                tool_use_id=tool_use_id,
                tool=tool,
                input=inp,
                item_id=item_id,
                attention_id=attention_id,
                document_id=document_id,
            ),
        )
        self.agent(
            {"state": wait_state, "pendingRequestId": request_id, "waitingOn": summary}
        )

    def response(self, payload: Dict) -> None:
        pending = self.ctx.pending
        if pending is None:
            return
        allow = payload.get("behavior") == "allow"
        if pending.tool == QUESTION_TOOL:
            answers = _dict(_dict(payload.get("updatedInput")).get("answers"))
            if answers:
                self.update(pending.item_id, {"answers": answers})
        elif pending.tool == PLAN_TOOL:
            patch: Dict = {"decision": "approve" if allow else "deny"}
            if not allow:
                patch["reason"] = _str(payload.get("message"))
            self.update(pending.item_id, patch)
            if pending.document_id:
                self.document_status(
                    pending.document_id, "approved" if allow else "changes-requested"
                )
        else:
            patch = {"decision": "allow" if allow else "deny"}
            if not allow:
                patch["reason"] = _str(payload.get("message"))
            self.update(pending.item_id, patch)
        self.end_wait()
        self.agent({"state": "processing", "pendingRequestId": None, "waitingOn": ""})

    def end_wait(self) -> None:
        """Clear the pending request and its attention item, if any."""
        pending = self.ctx.pending
        if pending is None:
            return
        self.clear(pending.attention_id)
        self.ctx = replace(self.ctx, pending=None)

    def raise_attention(
        self, kind: str, summary: str, request_id: str | None, document_id: str | None
    ) -> str:
        attention_id = f"att-{self.new_id()}"
        item: Attention = {
            "id": attention_id,
            "kind": kind,
            "agentId": self.ctx.agent_id,
            "taskId": self.agent_entity["taskId"],
            "documentId": document_id,
            "requestId": request_id,
            "summary": summary,
            "raisedAt": self.now,
            "clearedAt": None,
        }
        self.local_attention[attention_id] = item
        self.events.append({"type": "upsert", "kind": "attention", "entity": item})
        return attention_id

    def clear(self, attention_id: str) -> None:
        item = self.local_attention.get(attention_id) or self.state["world"][
            "attention"
        ].get(attention_id)
        if item is None or item["clearedAt"] is not None:
            return
        cleared: Attention = {**item, "clearedAt": self.now}
        self.local_attention[attention_id] = cleared
        self.events.append({"type": "upsert", "kind": "attention", "entity": cleared})

    def document_status(self, document_id: str, status: str) -> None:
        doc = self.local_documents.get(document_id) or self.state["world"][
            "documents"
        ].get(document_id)
        if doc is None:
            return
        nxt: Document = {**doc, "status": status}
        self.local_documents[document_id] = nxt
        self.events.append({"type": "upsert", "kind": "document", "entity": nxt})

    def done(self) -> Normalised:
        if self.agent_dirty:
            self.events.append(
                {"type": "upsert", "kind": "agent", "entity": self.agent_entity}
            )
        return Normalised(self.events, self.ctx)


def _blocks(raw: Dict) -> list[Dict]:
    content = _dict(raw.get("message")).get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict)]


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            _str(b.get("text")) for b in content if isinstance(b, dict) and "text" in b
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _questions_of(inp: Dict) -> list[Dict]:
    raw = inp.get("questions")
    questions = raw if isinstance(raw, list) else []
    out = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        options = q.get("options")
        out.append(
            {
                "question": _str(q.get("question")),
                "header": _str(q.get("header")),
                "multiSelect": bool(q.get("multiSelect")),
                "options": [
                    {
                        "label": _str(o.get("label")),
                        "description": _str(o.get("description")),
                    }
                    for o in (options if isinstance(options, list) else [])
                    if isinstance(o, dict)
                ],
            }
        )
    return out


def _one_line(text: str, limit: int = 60) -> str:
    # ``re.split`` keeps the empty leading/trailing parts JS ``split`` keeps,
    # so a message with leading whitespace collapses identically on both sides.
    return " ".join(re.split(r"\s+", text))[:limit]


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _num(v: Any) -> float | int:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _dict(v: Any) -> Dict:
    return v if isinstance(v, dict) else {}
