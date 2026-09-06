# Subagent permissions: what Claude Code does

An agent that maelstrom drives can stop making progress when a subagent asks for a permission.
The prompt stays in the transcript, the desk says the agent needs the user, and an approval
fails with `Agent <id> is not waiting`.

This document records what a live agent does, and where the three symptoms come from. It is
evidence for a design change, not a design. The fixtures under `tests/fixtures/agent_events/`
hold the recordings.

## What Claude Code allows

Claude Code allows a subagent to ask for a permission. The ask is an ordinary `can_use_tool`
`control_request` on the parent's stream.

**Claude Code does not serialise the asks.** Two subagents can block on a permission at the same
time. `subagent-permission-concurrent.jsonl` records a parent that launched two subagents in one
message. Each subagent called `WebFetch`, and both asks were open together:

```
REQ  9d42d8f9-…  agent_id=a60520f98d2f924c9  https://example.com
REQ  3ff25205-…  agent_id=a47b40396944cfc27  https://www.iana.org/domains/reserved
RESP 3ff25205-…
```

Only the second ask was ever answered. The subagent that raised the first one stayed blocked.

The live run showed the effect. After the approval, `mael agent list` reported the parent as
`processing` with an empty `waiting_on`, the answered subagent as `exited(0)`, and the other
subagent as `processing` — still, and with no way left to release it:

```
13ab4d2e                  processing   (nothing waiting)
13ab4d2e.1  Fetch example.com title    processing   <- blocked, unanswerable
13ab4d2e.2  Fetch IANA reserved title  exited(0)
```

Nothing in the system reports the hung subagent. The parent looks busy, so a user waits.

## The wire shape

A `control_request` carries no `parent_tool_use_id`, so it lands on the parent's stream. It does
carry `request.agent_id`, which is the `task_id` of the subagent's `task_started`:

| `request.agent_id` | `task_started.task_id` | Subagent |
|---|---|---|
| `a60520f98d2f924c9` | `a60520f98d2f924c9` | Fetch example.com title |
| `a47b40396944cfc27` | `a47b40396944cfc27` | Fetch IANA reserved title |

The identity of the asking subagent is therefore on the wire, in every ask. Nothing reads it.

`agent_id` names a nested subagent as clearly as a direct one.
`subagent-permission-nested.jsonl` records a subagent that spawned its own subagent, which then
asked. The live daemon reported `waiting_subagent` as `70dbeea8.1.1`.

## Where each symptom comes from

### The approval fails

`validate.py` refuses the command:

```
{'code': 'not_waiting', 'message': 'Agent ag1 is not waiting'}
```

`AgentState.pending` holds one request (`agent_model.py`). A second ask replaces the first, and
the reply to the second clears the slot. The world then has `pendingRequestId: null` while the
transcript still shows both prompts. `validate.py` reads that empty id and refuses every
approval, whichever prompt the user clicked.

The first `request_id` is gone from the state, so no reply can carry it. The subagent that
raised it never gets an answer and never finishes.

### The desk says the agent needs the user

The normaliser raises one attention item per ask and clears it on the matching answer. With two
asks and one answer, one item stays open. Replaying the concurrent fixture through the
normaliser shows the world the user sees:

```
control_request   state=awaiting-permission  pending=9d42d8f9   openAttention=1
control_request   state=awaiting-permission  pending=3ff25205   openAttention=2
control_response  state=processing           pending=None       openAttention=2
```

The second ask overwrites the first under `pendingRequestId`. One answer then clears the field
while both attention items stay open. The desk asks the user to act twice, the world holds no
pending request, and so every approval is refused.

### Nothing says which subagent asked

`build_agent_detail` reports `waiting_subagent`, and `mael agent show` prints
`Waiting on: WebFetch (from a1b2c3d4.1)`. The field stops there. The orchestrator normaliser
never carries it, so the `permission_request` transcript item has no field naming the subagent.

`waiting_subagent` is also unreliable at its source. `_ring_holding_call` finds the subagent by
scanning each subagent's ring for the `tool_use` block that opened the call. The ring holds
`RECENT_LIMIT` (200) events. A subagent that does more than that before it asks has lost the
block, so the ask reads as the parent's own. That limit is read from the code, not recorded from
a live agent.

The two new fixtures show that failure, for a different reason: a recording of a parent holds
none of its subagents' events, because the daemon routes a parented event to that subagent's
ring. Both asks replay with an empty `waiting_subagent`, while the live daemon named the
subagent correctly. Read the empty value in these fixtures as a property of the recording, not
of the running daemon.

## What the evidence supports

| Requirement | Supported | Cost |
|---|---|---|
| Permission blocks per subagent | Yes. Two asks are open at once, and one slot loses one of them. | `AgentState.pending` becomes a map. Every reader of it changes. |
| The UI names the asking subagent | Yes. `agent_id` is on every ask. | Carry the identity through the normaliser to the transcript item. |
| The UI allows several open asks | Yes. Two attention items already exist with one place to answer. | The decision card holds a list, not one prompt. |

`request.agent_id` is a better join than the ring scan. The scan is a search for a fact the ask
already states.

**The attribution fix ships on its own.** Reading `agent_id` in place of the ring scan needs no
change to how many waits the state holds, so it does not wait on the concurrency work. It is the
smaller of the two changes and fixes the second and third symptoms above.

## What this document does not decide

Whether a user can answer from the subagent's own session tab. `CONTEXT.md` says a subagent is
read, never driven, and its asks are the parent's waits. The reply must reach the parent's pipe
either way, because a subagent has no process of its own. Whether the *user interface* keeps that
rule is a design question.
