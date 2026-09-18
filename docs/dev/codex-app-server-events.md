# Codex app-server event contract

The Codex harness sends JSON-RPC notifications through the app-server proxy.
The Session tab receives them as raw events until `normalise.py` gives them a
transcript item.

This specification records a manual Session test against Codex CLI 0.154.0.
It is the source for the next normalization work.

## Routing fields

Every observed event identifies its Codex thread by `threadId`. Events with a
thread object use `thread.id` instead. A turn event also has `turnId` or
`turn.id`. Item events have a stable `item.id`.

The Codex thread id is stored against the Task session id. Do not use a turn
id as a session key: one thread can have many turns.

## Thread and turn lifecycle

`thread/started` has a `thread` object. The object includes `id`, `cwd`,
`model`, `reasoningEffort`, `status`, `canAcceptDirectInput`, and `turns`.

`thread/status/changed` has `threadId` and `status`. An active status has
`type: "active"` and optional `activeFlags`.

`turn/started` has `threadId` and `turn`. The turn includes `id`, `status`,
timing fields, `items`, and an error value. The observed active value is
`"inProgress"`.

`turn/completed` ends the turn. Preserve it as raw data until it has an
observable Session use.

## Items

`item/started` and `item/completed` contain `threadId`, `turnId`, a timestamp,
and an `item` object. The item type selects the representation.

| Item type | Important fields | Initial Session treatment |
|---|---|---|
| `userMessage` | `id`, `content[].text` | Render the user message. |
| `agentMessage` | `id`, `text`, `phase` | Render the completed agent message. |
| `reasoning` | `id`, `summary`, `content` | Reuse the existing low-attention reasoning surface when it has content. |
| `commandExecution` | `id`, `command`, `cwd`, `status`, `aggregatedOutput`, `exitCode`, `durationMs`, `commandActions` | Reuse the existing command surface. |

The observed `commandExecution` source is `"unifiedExecStartup"`. It can
contain an empty `durationMs`; do not treat zero as absent.

## Agent-message deltas

`item/agentMessage/delta` has `threadId`, `turnId`, `itemId`, and `delta`.
The sequence appends the text in `delta` to the message identified by `itemId`.
The final `item/completed` event has the full `agentMessage.text`.

The current Claude connector does not stream equivalent agent-message deltas.
The shared Session protocol therefore does not add a streaming message item in
this step. Codex normalization can ignore delta events and render the completed
message. Keep each delta as a raw event only while diagnostic output needs it.

## Diagnostics

`mcpServer/startupStatus/updated` has `threadId`, `name`, `status`, `error`,
and `failureReason`. The observed statuses are `"starting"` and `"ready"`.

`thread/tokenUsage/updated` has `threadId`, `turnId`, and `tokenUsage`.
`tokenUsage.total` and `tokenUsage.last` include total, input, cached-input,
cache-write, output, and reasoning-output token counts. `modelContextWindow`
is separate. Preserve this raw data until the usage UI defines its semantics.

Unknown methods stay in the collapsed raw JSON fallback. That fallback is a
diagnostic surface, not the normal transcript representation.

## Test seam

Test the Session transcript boundary with a notification sequence. It must
render completed user, agent, and command items. It must retain a raw fallback
for an unrecognized method. A delta-only sequence must not create a visible
streaming message item.
