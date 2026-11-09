# Codex app-server contract

A Codex agent is a thread on the Codex app-server. The orchestrator reaches the app-server
through `codex app-server proxy`, in `codex_bridge.py`. `CodexDaemonClient`, in `codex_daemon.py`,
presents each thread as an agent behind the same `AsyncDaemonClient` boundary as the Claude
daemon. `normalise.py` turns the thread's notifications into the transcript items a Claude agent
makes.

A Codex agent takes the same commands, follows the same markers, and draws the same Session as a
Claude agent. "Not supported" lists the gaps.

The contract was checked against Codex CLI 0.154.0 and its schema
(`codex app-server generate-json-schema --out <dir>`). A real turn is recorded in
`lib/domain/fixtures/codex_events/codex-turn.jsonl`. Its golden is
`lib/domain/fixtures/normalised/codex-turn.json`.

## Routing

Every thread event names its thread by `threadId`, or by `thread.id` on a thread object. The
thread id is the agent id. The task stores it as a task session id, and the Agent record carries
it as `task_session_id`.
Do not key on a turn id: one thread has many turns.

The adapter keeps one ring per thread, of up to 200 events. Each event gets a seq and a time, as
the Claude daemon stamps its events. An attach replays the ring after its cursor and closes with a
backlog-end marker. A cursor from another epoch replays the whole ring. The adapter mints one epoch per life. When the ring has
rolled past the cursor, a truncated marker says how many events are gone.

Deltas go to watchers live and stay out of the ring, as the Claude daemon keeps no
`stream_event`. A reattach replays the completed item instead.

## Commands

| Command | Codex request | Notes |
|---|---|---|
| `start` | `thread/start`, then `turn/start` | See "Start" below. |
| `say` | `turn/start` | `text` is a text input. Each attachment is a `localImage` input. |
| `say` `/compact …` | `thread/compact/start` | Codex takes no compaction instructions. |
| `run` | `thread/shellCommand` | The `!` line. The command draws as a shell item. |
| `interrupt` | `turn/interrupt` | Declines open approvals first. Refused when no turn runs. |
| `approve`, `deny` | the approval's JSON-RPC reply | See "Approvals" below. |
| `set-mode` | none at once | The next `turn/start` carries the mode's policy. |
| `resume` | `thread/resume`, then `turn/start` with `text` | Brings a stopped thread back under its id. |
| `stop` | `thread/unsubscribe` | Each watch ends with an exit marker, code 0. |
| `list` | none | The adapter's own rows. |
| `answer`, `recover`, `show` | none | Refused: Codex has no equivalent. |

A bridge failure is the reply's `error`, never an exception. The server reports it as it reports
a Claude daemon refusal.

### Start

`thread/start` carries:

- `cwd` and `model`, from the launch.
- `sandbox`, from the mode, as `codex_thread_start_options` maps it.
- `developerInstructions`: the content of `system_prompt_file`. This is the Codex equivalent of
  `--append-system-prompt-file`, so the agent learns the markers.
- `config.shell_environment_policy.set`: the launch `env`. Every command the agent runs sees
  `MAEL_TASK_ID` and its siblings.

### Modes

Each `turn/start` carries `sandboxPolicy`, `approvalPolicy` and `approvalsReviewer` from
`codex_turn_policy`. Codex keeps a turn's policy for the turns after it, so every mode names its
approval policy and its reviewer. These values override the user's Codex config.

| Mode | Sandbox policy | Approval policy | Reviewer |
|---|---|---|---|
| `plan` | `readOnly` | `on-request` | `user` |
| `normal` | `workspaceWrite` | `untrusted` | `user` |
| `auto` | `workspaceWrite` | `on-request` | `auto_review` |

In `auto`, Codex's auto-reviewer answers each escalation, and no approval ask reaches Maelstrom.
Thread start and a `--cli` launch (`-c approvals_reviewer=...`) set the same reviewer.

A `set-mode` also records a `thread/settings/updated` frame with the new policy. The normaliser
reads the mode back from the policy with `codex_mode_of`: no two modes share one.

### Plan mode

Codex has no `ExitPlanMode` tool. A Codex plan-mode task prompt tells the agent to write
`.drafts/plan.md` and show it with `<doc-file kind="other" review="true">`. When the user
approves that document, the server sets the thread to `auto` mode, as an approved Claude plan does,
and tells the agent to build.
Only the approval of `.drafts/plan.md` does this: another document approved during planning leaves
the mode alone.

### Task prompts

Codex reads a leading `/plan-task` as literal text. A Codex task prompt names the skill in words:
"Load the `plan-task` skill, then: <title>".

## Approvals

The adapter records each approval request, and later its own reply, as `codex_raw` frames that
carry the JSON-RPC `id`. The normaliser turns the request into a permission ask, and the reply
into its answer. `mael_agent/codex_wire.py` maps a request to the ask, for the normaliser and for
the adapter's detail frame alike.

| Request | Tool |
|---|---|
| `item/commandExecution/requestApproval` | `Bash` |
| `item/fileChange/requestApproval` | `Edit` |

`approve` answers `decision: "accept"`. `deny` answers `decision: "decline"`. A request can list
its `availableDecisions` without `decline`. Then `deny` answers `cancel`, which also interrupts
the turn. Codex takes no reason with a refusal. The recorded reply carries it under
`mael_reason`, so the transcript still shows it.

`approve` with `remember: true` sends a lasting allow instead of `accept`. The adapter picks the
first match from `availableDecisions`:

| Offered | Sent | Label |
|---|---|---|
| `{"acceptWithExecpolicyAmendment": {...}}` with a prefix | That decision. Codex writes a rule for the command prefix. | ``Always allow `<prefix>` ``, without the `<shell> -lc` wrapper |
| `acceptForSession` | `acceptForSession` | `Allow for this session` |
| Neither | Nothing: `approve` fails with "offers no remember". | None, so no button |

The rule comes first because it outlasts the session. `codex_wire.approval_of` builds both the
decision and the label, so the button and the reply cannot disagree. The normaliser marks the
answer `remembered` when the decision is any allow except `accept`.

`approve` and `deny` with no `request` answer the single open request, as the Claude daemon does.
With more than one open, the command must name one.

The adapter answers every other server request with JSON-RPC error `-32601`, so no turn waits on
it. The request still draws as a raw event. This covers `item/tool/requestUserInput`,
`item/permissions/requestApproval`, `mcpServer/elicitation/request`, and `item/tool/call`.

## Notifications

`normalise.py` maps each notification as follows.

| Method | Becomes |
|---|---|
| `item/started`, `item/completed` | The item's transcript item; see "Items". |
| `item/agentMessage/delta` | The partial message; see "Agent-message deltas". |
| `turn/started` | Agent state `processing`. |
| `turn/completed` | A `turn_result` item, the end of every open wait, and the turn end. |
| `turn/plan/updated` | A `TodoWrite` tool call, with a todo per plan step. |
| `thread/settings/updated` | The agent's `permissionMode`. |
| `serverRequest/resolved` | The end of the wait on a request no reply answered. |
| `thread/tokenUsage/updated` | `totalTokens` from `total`, `contextTokens` from `last`. |
| `account/rateLimits/updated` | Nothing in the transcript. The adapter keeps it; see "Agent state and usage". |
| `warning`, `configWarning`, `guardianWarning` | An `error` item with the message. |
| `item/autoApprovalReview/completed`, `denied` or `timedOut` | An `error` item: the action, its risk level, and the rationale. |
| `item/autoApprovalReview/completed`, any other status | Nothing: an approved review only adds noise. |
| `error` with `willRetry` | An `error` item that says Codex retries. |
| `error` without `willRetry` | Nothing: `turn/completed` reports the failure. |
| lifecycle and diagnostics | Nothing. See the list below. |
| anything else | A collapsed `raw_event` item. |

These draw nothing: `thread/started`, `thread/status/changed`, `thread/name/updated`,
`thread/goal/updated`, `thread/goal/cleared`, `mcpServer/startupStatus/updated`,
`turn/diff/updated`, `hook/started`, `hook/completed`, `item/autoApprovalReview/started`,
`deprecationNotice`, `thread/compacted`, and every delta other than the agent-message delta. Codex 0.154 reports a compaction as a `contextCompaction` item,
and the item is what draws.

A failed turn adds an `error` item with `turn.error.message`. The `turn_result` subtype is
`success` for `completed`, `error` for `failed`, and the Codex status otherwise. Codex reports no
price, so `costUsd` is 0.

### The turn end

`turn/completed` sets `Normalised.turn_end`, as a Claude `result` does, so every marker works for a
Codex agent. See [orchestrator-server.md](orchestrator-server.md#the-normaliser-and-its-goldens).

### Items

| Item type | Started | Completed |
|---|---|---|
| `userMessage` | nothing | The user-text classifier Claude uses. |
| `agentMessage`, `plan` | nothing | The assistant-text path Claude uses. |
| `reasoning` | nothing | A low-attention assistant message, when it has text. |
| `commandExecution` | A running `Bash` call | The call settles in place. |
| `commandExecution` from `userShell` | A running shell item | The shell item settles in place. |
| `fileChange` | A running `Edit` or `Write` per file | Each call settles in place. |
| `mcpToolCall` | A running `mcp__<server>__<tool>` call | The call settles in place. |
| `webSearch` | A running `WebSearch` call | The call settles in place. |
| `collabAgentToolCall` | A running `Agent` call | The call settles in place. |
| `dynamicToolCall` | A running `<namespace>__<tool>` call | The call settles in place. |
| `imageView` | A running `Read` call | The call settles in place. |
| `contextCompaction` | nothing | A `compact` item, and the compaction result the server waits on. |
| `subAgentActivity`, `hookPrompt`, `sleep`, `functionCallOutput`, `imageGeneration`, `enteredReviewMode`, `exitedReviewMode` | nothing | nothing |
| a type Codex 0.154 does not define | nothing | A `raw_event` item. |

A user message goes through the same classifier as a Claude user turn. So an External event, a
jig turn, or a skill body draws as it does for Claude.

An agent message goes through the same assistant-text path as a Claude text block. That path
parses every marker: documents, `<note>`, `<milestone>`, `<link rel="gh-pr">`, `<live-feedback>`,
`<compact-session>` and `<end-session>`.

A command's input shows the command the agent wrote. Codex wraps it in `/bin/zsh -lc '…'`; the
single `commandActions` entry holds the unwrapped command. A non-zero `exitCode`, or a `failed`
status, makes the call an error. A `declined` status makes it `denied`.

A file change carries a unified diff. The Edit card diffs two strings, so the normaliser splits
the diff into its old and new sides. An added file becomes a `Write` with the new text.

A sub-agent call is an `Agent` call. Its `description` is the collab tool, such as `spawnAgent` or
`wait`, and its output is the message of each target agent. A dynamic tool call without a
namespace keeps the bare tool name. Its output is the text of its content items, and
`success: false` makes it an error. An image generation draws nothing, because no card can show its
base64 result.

### Agent-message deltas

`item/agentMessage/delta` carries `itemId` and `delta`. The deltas grow the same partial message
that Claude chunks grow, keyed by `itemId`. A tag that is still open is held back, as for Claude.
The `item/completed` for that item replaces the partial message in place, under the same item id.

## Agent state and usage

The adapter's `list` row follows the stream:

- `state` and `turn_id` from `turn/started` and `turn/completed`, and from approvals.
- `tokens` and `context_tokens` from `thread/tokenUsage/updated`.
- `last_message` and `last_message_at` from completed agent messages and sent turns.
- `mode` from the start and from `set-mode`.
- `pid`: the proxy's pid. Every thread runs behind that one process.

`description` is empty, as for a top-level Claude agent. `cost` stays empty.

The `list` reply's `usage` is the Codex account's usage windows, in the Claude daemon's shape:
`five_hour` and `seven_day`, each `{utilization, resets_at}`, and `at`. Codex sends
`account/rateLimits/updated` after each turn. The notification names no thread, so the adapter
handles it before the thread lookup and keeps the newest reading in memory.

| Codex field | Usage field |
|---|---|
| a window with `windowDurationMins` 300 | `five_hour` |
| a window with `windowDurationMins` 10080 | `seven_day` |
| `primary` or `secondary` with no duration | `five_hour` or `seven_day` |
| `usedPercent` (0–100) | `utilization` (0–1) |
| `resetsAt` (unix seconds) | `resets_at` |

Only the `codex` bucket counts: `limitId` is `"codex"` or null. A window of another length is
ignored. The update is sparse, so a window it leaves out keeps its last value, and an update with
no window keeps the last reading's `at`. `usage` is null until the first Codex turn, and after an
orchestrator restart. The adapter does not call `account/rateLimits/read` at start, because that
call starts a proxy only to show a chip.

## Restore and reconnect

After an orchestrator restart, the router hands the adapter every Agent record. The adapter
resumes each live Codex thread with `thread/resume`, and skips ended records. It rebuilds the
ring from the returned `turns[].items`: each item replays as `item/completed`, and each finished
turn as `turn/completed`. A turn still `inProgress` sets the row to `processing`.

An approval that was open at the restart is lost. Codex sends no new request for it, so the turn
waits until the user interrupts it.

When the proxy dies, the bridge drops the process, and the next request starts a new one. The
adapter ends every open watch without an exit marker. The server attaches again on its next poll.
Before that attach, the adapter resumes each thread with `excludeTurns: true` to subscribe the new
connection. A thread that does not resume is tried again on the next request. Events from the gap
are lost, and so are the open approvals: the old connection's requests died with it. The resumed
status says whether a turn still runs.

An approval leaves the adapter's open requests when it is answered, when `serverRequest/resolved`
names it, or when its turn completes. The normaliser ends the Session's wait on the same events.

A task relaunch resumes the task's current Codex thread, in the launch's worktree. See
[orchestrator-server.md](orchestrator-server.md#launch).

## Not supported

- The `mael agent` CLI talks to the Claude daemon socket only. Codex threads live in the
  orchestrator. See [cli-over-api.md](cli-over-api.md).
- The CLI's daemon launcher refuses non-Claude models.
- Codex subagent threads are not shown.
- Codex reports no price and no account usage.
