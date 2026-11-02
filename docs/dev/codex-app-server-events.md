# Codex app-server event contract

The Codex harness sends JSON-RPC notifications through the app-server proxy.
`normalise.py` converts supported notifications to common transcript items.
The Session tab keeps unsupported notifications as raw events.

This specification records a manual Session test against Codex CLI 0.154.0.

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

`turn/started` sets the common agent state to processing. `turn/completed` sets
it to idle after pending work ends. Keep both events in the raw fallback for
diagnostics.

## Items

`item/started` and `item/completed` contain `threadId`, `turnId`, a timestamp,
and an `item` object. The item type selects the representation.

| Item type | Important fields | Initial Session treatment |
|---|---|---|
| `userMessage` | `id`, `content[].text` | Render the user message. |
| `agentMessage` | `id`, `text`, `phase` | Render the completed agent message. |
| `reasoning` | `id`, `summary`, `content` | Reuse the existing low-attention reasoning surface when it has content. |
| `commandExecution` | `id`, `command`, `cwd`, `status`, `aggregatedOutput`, `exitCode`, `durationMs`, `commandActions` | Reuse the existing command surface. |

The normaliser reads these item types from `item/completed`. It does not draw
`item/started`. A completed command becomes a `Bash` tool call. A non-zero exit
code makes the call an error.

Reasoning becomes an assistant message under `<user-attention low>`. Empty
reasoning draws nothing. A completed agent message updates the agent's last
message as a Claude assistant message does.

The observed `commandExecution` source is `"unifiedExecStartup"`. It can
contain an empty `durationMs`; do not treat zero as absent.

## Agent-message deltas

`item/agentMessage/delta` has `threadId`, `turnId`, `itemId`, and `delta`.
The sequence appends the text in `delta` to the message identified by `itemId`.
The final `item/completed` event has the full `agentMessage.text`.

The Claude connector draws a partial message from its own chunks; see "A partial
message" in [orchestrator-server.md](orchestrator-server.md). The Codex connector
does not. Codex normalization drops delta events and renders the completed
message.

## Control requests

An app-server method ending in `/requestApproval` becomes a common
`control_request`. Command execution uses the `Bash` permission surface. File
changes use the `Edit` permission surface.

Approve answers the JSON-RPC request with `decision: "accept"`. Deny answers it
with `decision: "decline"`. Both actions send a common `control_response` to
settle the transcript item.

## Diagnostics

`mcpServer/startupStatus/updated` has `threadId`, `name`, `status`, `error`,
and `failureReason`. The observed statuses are `"starting"` and `"ready"`.

`thread/tokenUsage/updated` has `threadId`, `turnId`, and `tokenUsage`.
`tokenUsage.total` and `tokenUsage.last` include total, input, cached-input,
cache-write, output, and reasoning-output token counts. `modelContextWindow`
is separate. Preserve this raw data until the usage UI defines its semantics.

Unknown methods stay in the collapsed raw JSON fallback. The fallback includes
`mcpServer/startupStatus/updated`, `thread/tokenUsage/updated`,
`thread/status/changed`, and `turn/completed`. That fallback is a diagnostic
surface, not the normal transcript representation.

## Test seam

Test the Session transcript boundary with a notification sequence. It must
render completed user, agent, and command items. It must retain a raw fallback
for an unrecognized method. A delta-only sequence must not create a visible
streaming message item.
