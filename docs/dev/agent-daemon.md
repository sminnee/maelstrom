# The agent daemon

How maelstrom drives a Claude Code agent from outside the terminal it runs in, and answers it
while it waits.

A session in a cmux pane has no way to receive an answer except through that pane. The agent
daemon adds that missing half. It runs each agent as a `claude` child on a bidirectional NDJSON
pipe, reads what the agent is doing from its event stream, and writes answers back on the child's
stdin. `mael agent` is the client.

This path is additive. It runs beside the cmux launch path and does not replace it. An agent the
daemon drives has no cmux pane and no TTY. A session `mael task run` launches keeps its pane and
its hooks. Whether the two converge is a later decision.

## The mechanism

Each agent is a normal `claude` process with different I/O plumbing:

```
claude -p --input-format stream-json --output-format stream-json --verbose \
       --permission-prompt-tool stdio [--permission-mode auto]
```

Because it is the same binary, skills, `CLAUDE.md`, settings, sub-agents, MCP servers, hooks and
`--permission-mode auto` all behave as they do today.

Four flags matter, and one of them is easy to miss:

| Flag | Why it is needed |
|---|---|
| `-p` | Headless. There is no TUI in this process, and none is wanted. |
| `--input-format stream-json` | Makes stdin a live message channel, not a one-shot prompt. |
| `--output-format stream-json --verbose` | Emits the event stream. `--verbose` is required with this output format. |
| `--permission-prompt-tool stdio` | **Load-bearing.** Tells the CLI that permission prompts reach the host over the pipe. |

Without `--permission-prompt-tool stdio` a headless agent has nobody to ask. Every "ask" decision
resolves itself, the agent never pauses, and no wait is ever observable. The flag does not appear
in `claude --help` on v2.1.252, but it works.

The daemon holds the child's stdin open for the life of the agent. A closed stdin ends the session
after one turn, which is what a bare `claude -p` does.

## The event vocabulary

Every shape below was recorded from a live agent on v2.1.252 and saved under
`tests/fixtures/agent_events/`. `tests/test_agent_daemon.py` replays those transcripts through
the state machine, so nothing here is designed from an assumed shape.

### A turn

A turn opens with `system`/`init`, which carries the `session_id` and the model. Assistant output
arrives as `assistant` events. The turn closes with a `result` event carrying `total_cost_usd`
and `subtype`.

### A wait

Every wait — a permission ask, a question, a plan review — arrives as one event shape:

```json
{"type": "control_request", "request_id": "bf4483ca-…",
 "request": {"subtype": "can_use_tool", "tool_name": "WebFetch",
             "input": {"url": "https://example.com"},
             "description": "https://example.com",
             "tool_use_id": "toolu_01X1…"}}
```

The agent blocks until a `control_response` echoing that `request_id` arrives. The tool name says
which kind of wait it is:

| `tool_name` | State | Answer with |
|---|---|---|
| `AskUserQuestion` | `awaiting-question` | `mael agent answer <id> <choice>` |
| `ExitPlanMode` | `awaiting-plan-review` | `mael agent approve <id>` |
| anything else | `awaiting-permission` | `mael agent approve` / `deny <id>` |

`answer` works only on `awaiting-question`. A non-question wait carries no question text, so an
answer would go out as an empty map, which the agent reads as no answer at all. The daemon
refuses instead of resolving the wait wrongly.

A question and a plan review also carry `requires_user_interaction: true`. A plain permission ask
does not.

`AskUserQuestion` and `ExitPlanMode` are only in the agent's toolset when the child inherits no
parent Claude session. A `claude` spawned from inside another Claude session gets a reduced tool
list, so probe these shapes from a plain shell.

### The replies

Three replies share one envelope. The `request_id` must match the request, and `updatedInput` is
not optional — the CLI runs the tool with whatever it carries.

Approve a call as proposed by echoing its input back:

```json
{"type": "control_response",
 "response": {"subtype": "success", "request_id": "bf4483ca-…",
              "response": {"behavior": "allow",
                           "updatedInput": {"url": "https://example.com"}}}}
```

Deny it with a reason, which reaches the agent verbatim as the tool result:

```json
{"response": {"response": {"behavior": "deny", "message": "no network access"}}}
```

Answer a question by adding an `answers` map to `updatedInput`, keyed by each question's own
text:

```json
{"response": {"response": {"behavior": "allow",
   "updatedInput": {"questions": [...],
                    "answers": {"Which colour do you prefer?": "Green"}}}}}
```

The `answers` key is the part that is not guessable. Allowing an `AskUserQuestion` without it is
not a neutral approval — the agent reads it as "the user did not answer the questions" and moves
on.

### Sending a message

A follow-up message is a plain user turn on stdin. The opening prompt uses the same shape:

```json
{"type": "user", "message": {"role": "user",
 "content": [{"type": "text", "text": "also update the README"}]}}
```

## The layers

`src/maelstrom/` follows the three layers in
[architecture-patterns.md](architecture-patterns.md):

- `agent_daemon.py` — model and transport. The `apply_event` reducer, the `build_agent_row`
  renderer, the reply builders, and the transport trio (`DaemonClient` Protocol,
  `SocketDaemonClient`, `RecordingDaemonClient`), mirroring `cmux/client.py`.
- `agent_cli.py` — the thin CLI. It parses flags, sends one command, and prints the reply.

`apply_event` and `build_agent_row` are pure. Replaying a transcript through `apply_event` gives
the same state every time, with no subprocess and no socket, the way
`session_view.build_session_row` works.

The state comes from observed events, not from hook inference. So the daemon needs no equivalent
of `session_view.STALE_PROCESSING_SECS`, and an interrupt is visible rather than leaving a
session stuck in `processing`.

## When an agent dies

The event stream ending is the only notice the daemon gets that a child has gone. The agent then
moves to `exited`, and `mael agent list` shows the exit code:

```
id        state      waiting_on  cwd           model
0efb3469  exited(1)              /private/tmp  claude-opus-5
```

Every command except `stop` refuses against an exited agent. Writing to a closed stdin succeeds
silently, so without that refusal `mael agent answer` would report success against a dead
process.

## Running it

The daemon is one process per machine and does not start on its own. An agent dies with the
daemon holding it, so starting one by accident is worse than a clear error.

```bash
mael agent daemon &                                   # listens on ~/.maelstrom/agent-daemon.sock
mael agent start ~/Projects/maelstrom/maelstrom-alpha --prompt "run the tests"
mael agent list
mael agent answer a1b2c3d4 "Green"
mael agent approve a1b2c3d4
mael agent deny a1b2c3d4 --reason "not on a public network"
mael agent say a1b2c3d4 "also update the README"
mael agent attach a1b2c3d4
mael agent stop a1b2c3d4
```

`MAEL_AGENT_SOCKET` overrides the socket path. `mael agent list --json` emits the rows as JSON.

A second daemon on the same socket refuses to start. Unlinking a live socket would leave the
first daemon listening on a path no client can reach, holding agents nothing can stop. A stale
socket file left by a killed daemon refuses connections, so it reads as free and is replaced.

`mael agent list` names what each waiting agent waits on, which is the point of the whole
mechanism:

```
id        state                 waiting_on                   cwd            model
0c35d123  idle                                               ~/…/alpha      claude-opus-5
1761dcf6  awaiting-question     Which colour do you prefer?  /private/tmp   claude-opus-5
0b2f5f5b  awaiting-plan-review  ExitPlanMode                 /private/tmp   claude-opus-5
```

### Teleport

`mael agent attach <id>` is teleport. A headless agent has no TTY, so there is no pane to attach
to and no transcript to resume. Attach is another client of the same socket: it replays the
agent's buffered recent events, streams new ones, and forwards each line you type as a user
message.

The event stream decides when attach ends, not stdin. Closed stdin is normal, so
`mael agent attach <id> < /dev/null` is a read-only view of a live agent.

## Rejected alternatives

**The Claude Agent SDK** (commit `a6ce365`, `agent-cli`, removed in `c240316`) is a client library
over this same pipe. It adds a dependency and a billing-policy risk for no capability gain. Its
NDJSON protocol design is still worth reading: `git show a6ce365:agent-cli/src/protocol.ts`.

**The IDE integration protocol** — a WebSocket MCP server advertised through
`~/.claude/ide/<port>.lock` and `CLAUDE_CODE_SSE_PORT` — solves the inverse problem. It exposes
editor tools to the agent. Its inbound channel carries notifications like `at_mentioned` and
`selection_changed`, and nothing documented delivers a prompt or an answer.

**cmux `send` into a live pane** synthesises keystrokes against whatever widget is on screen. It
is fragile exactly where it matters.
