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
`tests/fixtures/agent_events/`. `tests/test_agent_model.py` replays those transcripts through
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

### Interrupting

An interrupt is the one message the host sends that is not a reply. It is a `control_request` of
its own, so it carries its own `request_id`:

```json
{"type": "control_request", "request_id": "b2724f71-…",
 "request": {"subtype": "interrupt"}}
```

The child answers it, then closes the turn:

```json
{"type": "control_response", "response": {"subtype": "success",
 "request_id": "b2724f71-…", "response": {"still_queued": []}}}
{"type": "user", "message": {"role": "user",
 "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}
{"type": "result", "subtype": "error_during_execution", "is_error": true, …}
```

The turn ends `idle`, and the agent is still there to take the next message. Recorded in
`tests/fixtures/agent_events/interrupt.jsonl`.

An interrupt does not answer a request the child is blocked on. So the daemon denies a pending
wait first, with the reason `Interrupted by user`, and the child returns that denial as the tool
result before the interrupt lands. Recorded in
`tests/fixtures/agent_events/interrupt-while-waiting.jsonl`.

### Sending a message

A follow-up message is a plain user turn on stdin. The opening prompt uses the same shape:

```json
{"type": "user", "message": {"role": "user",
 "content": [{"type": "text", "text": "also update the README"}]}}
```

## The layers

`src/maelstrom/` follows the three layers in
[architecture-patterns.md](architecture-patterns.md):

- `agent_model.py` — the pure model. The `apply_event` reducer, the `build_agent_row` and
  `build_agent_detail` renderers, the argv, and the reply builders. No I/O, no clock, no
  subprocess.
- `agent_transport.py` — the transport trio, mirroring `cmux/client.py`: a `DaemonClient`
  Protocol, the real `SocketDaemonClient`, and the `RecordingDaemonClient` fake. Auto-start lives
  here too, because every command funnels through one connect.
- `agent_server.py` — the daemon. Child processes, the control socket, and `AgentDaemon.handle`.
- `agent_cli.py` — the thin CLI. It parses flags, sends one command, and prints the reply.

`agent_model.py` holds no I/O at all, so replaying a transcript through `apply_event` gives the
same state every time, with no subprocess and no socket — the way `session_view.build_session_row`
works. `tests/test_agent_model.py` does exactly that against the recorded fixtures.

The state comes from observed events, not from hook inference. So the daemon needs no equivalent
of `session_view.STALE_PROCESSING_SECS`, and an interrupt is visible rather than leaving a
session stuck in `processing`.

## When an agent dies

The event stream ending is the only notice the daemon gets that a child has gone. The agent then
moves to `exited`, every attached client gets the `mael_agent_exited` marker, and
`mael agent list` shows the exit code:

```
id        state      waiting_on  last_message               cwd
0efb3469  exited(1)              Running the test suite now  /private/tmp
```

Every command that writes to the agent refuses against an exited agent. Writing to a closed stdin
succeeds silently, so without that refusal `mael agent answer` would report success against a dead
process. `stop`, `show`, `tail` and `attach` send the agent nothing, so they still work — and
`show` is how you find out why it died.

`resume` is the one command that wants an exited agent. It refuses a running one: two children on
one session id would fight over one transcript.

## Running it

The daemon is one process per machine, and the first command that needs it starts it. A daemon
started by accident holds no agents, so it costs nothing.

```bash
mael agent start ~/Projects/maelstrom/maelstrom-alpha --prompt "run the tests"
mael agent start . --session-id <uuid>                # pin the session to resume later
mael agent resume a1b2c3d4                            # start an exited agent again
mael agent list
mael agent show a1b2c3d4
mael agent answer a1b2c3d4 "Green"
mael agent approve a1b2c3d4
mael agent deny a1b2c3d4 --reason "not on a public network"
mael agent say a1b2c3d4 "also update the README"
mael agent interrupt a1b2c3d4                         # abandon the turn, keep the agent
mael agent tail a1b2c3d4
mael agent attach a1b2c3d4
mael agent stop a1b2c3d4
mael agent daemon                                     # run it in the foreground instead
```

An auto-started daemon writes its output to `~/.maelstrom/agent-daemon.log`, and runs in its own
process group. So Ctrl-C on the command that started it does not kill the daemon holding every
agent. A daemon that fails to start is reported with what it wrote to that log.

Auto-start waits 5 seconds for the daemon to bind, and reports a child that dies sooner as soon as
it dies. `MAEL_AGENT_NO_AUTOSTART=1` turns auto-start off, and every spawned daemon inherits it, so
a daemon can never spawn a daemon.

`MAEL_AGENT_SOCKET` overrides the socket path, `MAEL_AGENT_LOG` the log path, and
`MAEL_AGENT_SPEC_DIR` the spawn-record directory.
`mael agent list --json` emits the rows as JSON.

A second daemon on the same socket refuses to start. Unlinking a live socket would leave the
first daemon listening on a path no client can reach, holding agents nothing can stop. A stale
socket file left by a killed daemon refuses connections, so it reads as free and is replaced.

`mael agent list` names what each waiting agent waits on, which is the point of the whole
mechanism:

```
id        state                 waiting_on                   last_message              cwd
0c35d123  idle                                               Both tests pass now.      ~/…/alpha
1761dcf6  awaiting-question     Which colour do you prefer?  I need one decision …     /private/tmp
0b2f5f5b  awaiting-plan-review  ExitPlanMode                 **Context:** Create a …   /private/tmp
```

`last_message` is what the agent last said, cut to one line. Without it two working agents look
identical, because a state and a wait kind say only that both are busy.

### Showing one agent

`mael agent show <id>` prints one agent in full: what it last said, every option of a question with
its description, the plan text of a plan review, and the command that answers the wait.

```
id:       1761dcf6
state:    awaiting-question
session:  ce84c0ca-c8e1-41ed-a05f-f7fd3e5c6ec5
cwd:      /private/tmp
model:    claude-opus-5

I'll ask about your colour preference.

Colour: Which colour do you prefer?
  Red — Warm, bold, high-energy.
  Green — Natural, calm, fresh.
  Blue — Cool, calm, classic.

Answer with:  mael agent answer 1761dcf6 Red
```

**`ExitPlanMode` carries the plan in its own `input`**, under `plan`, with `planFilePath` naming
the file the agent wrote it to:

```json
{"type": "control_request", "request_id": "18a1c0f2-…",
 "request": {"subtype": "can_use_tool", "tool_name": "ExitPlanMode",
             "input": {"plan": "# Create hello.txt\n\n## Context…",
                       "planFilePath": "~/.claude/plans/plan-a-hello-txt-….md"},
             "requires_user_interaction": true}}
```

An agent that **cannot write its plan file** sends an empty `input` instead, and puts the plan in
an ordinary message. `plan-review.jsonl` records exactly that: a sandbox refused the write with
`EPERM`. So `show` reads the request first and falls back to the last retained message, which is
the only case where the message buffer stands in for the plan.

`show` works on an exited agent. Reading why an agent died is the main reason to run it, and
`show` sends the agent nothing.

### Tailing

`mael agent tail <id>` renders an agent's event stream without driving it. It prints the buffered
history and stops. `mael agent tail -f <id>` keeps streaming. Nothing typed reaches the agent
either way, so a tail is read-only by construction rather than by redirecting stdin.

The daemon writes a `{"type": "mael_backlog_end"}` marker after the replayed history, which is how
a tail without `-f` knows where to stop. An idle timeout would race a slow agent and flake. A
30-second read timeout backstops a daemon that never sends the marker, so a tail errors rather
than hangs — it is not what ends a normal tail.

### Teleport

`mael agent attach <id>` is teleport. A headless agent has no TTY, so there is no pane to attach
to. Attach is another client of the same socket, and it renders that socket's stream as a
terminal UI.

The screen has three parts. A transcript shows the agent's messages, its tool calls with the
first few lines of each result, and how each turn ended. A console at the bottom sends what you
type as a user message, and the transcript shows it. A line above the console says the agent is
working while it owes a reply. A footer names the working directory, the model, the tokens
consumed, the git branch and the agent's state.

A wait is answered in place. A permission ask, a question and a plan review each open a prompt
over the transcript, so you never leave the terminal to run `mael agent approve` in another one.
The prompt closes by itself when the wait ends some other way — another client answered it, the
turn ended, or the agent died.

Two keys work everywhere, including inside a prompt:

| Key | What it does |
|---|---|
| Esc | Interrupts the running turn. Nothing happens when the agent is idle. |
| Ctrl-C or Ctrl-D | Detaches. The agent keeps running; `mael agent stop` is what ends one. |

Esc inside a prompt is not "close this prompt". The daemon denies the pending request and then
interrupts the turn, which is what Esc at a permission ask does in Claude Code itself.

Attach needs a terminal, and refuses without one rather than rendering into a pipe. Use
`mael agent tail -f <id>` to follow an agent from a script or a redirect. That command is the raw
read-only view, and nothing you type reaches the agent through it.

## The control socket protocol

The daemon's socket is the interface the orchestrator server drives agents through, and the one
`mael agent` uses. This section is the reference for that interface: the orchestrator server ↔
agent host protocol. A host on another machine later means the same messages over TCP.

The transport is NDJSON on a Unix domain socket at `MAEL_AGENT_SOCKET`. Every command is one line
in and one line out, except `attach`, which holds the connection open and streams.

### Commands

Every request carries `cmd`. Every reply is either an ok reply or `{"error": "<message>"}`.

| `cmd` | Request fields | Ok reply |
|---|---|---|
| `start` | `cwd`; optional `prompt`, `mode`, `model`, `session`, `env`, `resume` | `{"ok": true, "id": "<agent id>"}` |
| `list` | — | `{"agents": [<row>, …]}`, each row as `mael agent list --json` prints |
| `show` | `id` | `{"agent": <detail>}`, as `mael agent show --json` prints |
| `say` | `id`, `text` | `{"ok": true}` |
| `approve` | `id` | `{"ok": true}` |
| `deny` | `id`; optional `reason` | `{"ok": true}` |
| `answer` | `id`; `answers` (a map keyed by question text) or `choice` | `{"ok": true}` |
| `interrupt` | `id` | `{"ok": true}` |
| `stop` | `id` | `{"ok": true}` |
| `resume` | `id`; optional `text` | `{"ok": true, "id": "<agent id>"}` |
| `attach` | `id` | A stream; see below |

`start` merges `env` over the daemon's own environment for that child, with no allowlist: a
client of the socket can set any variable. The socket's file permissions are the trust boundary.
A task launch passes `MAEL_TASK_ID`, `MAEL_TASK_PARENT` and `MAEL_TASK_SESSION_ID` this way.

`answer` with `answers` files each answer under its question. `choice` applies one answer to
every question. An empty `answers` map is refused: the agent reads an empty map as no answer at
all.

`start` with `resume: true` continues the session `session` names instead of claiming it. A task
that has run before already owns its session id, and claiming it again is refused, so the
orchestrator sets this from the transcript on disk.

`interrupt` abandons the turn the agent is running and leaves the agent alive.

`stop` removes the agent from the daemon and deletes its spawn record. A later `list` does not
name it, and no later daemon start brings it back.

`resume` starts an exited agent again under its own id, and sends it one turn: `text`, or the
default nudge. See "The resume rules".

`start` and `resume` both report the spawn, not the run. A child that dies straight after
spawning — a bad `--model`, an expired login, a `--resume` Claude will not accept — is reported
`ok`, and the exit shows in the next `list`. `mael agent show` says why.

### The guards

| Refusal | When |
|---|---|
| `no such agent: <id>` | No agent has that id |
| `agent <id> has exited` | Any command except `show`, `stop` and `resume` against an exited agent |
| `agent <id> is running` | `resume` against an agent that has not exited |
| `agent <id> has no spawn record` | `resume` when a `stop` deleted the record first |
| `agent <id> is not waiting` | `approve`, `deny` or `answer` with no pending request |
| `agent <id> is not waiting on a question — use approve or deny` | `answer` against a permission or plan review |
| `no answers given` | `answer` with an empty `answers` map |
| `could not reach agent <id>` | The child's stdin would not take the message: it is dying |
| `could not start claude: …` | `start` when the child could not be spawned |
| `unknown command: <cmd>` | Anything else |

### The attach stream

`{"cmd": "attach", "id": "<agent id>"}` turns the connection into a stream of the agent's raw
stream-json events, one per line:

1. The retained backlog: up to 200 events, oldest first.
2. `{"type": "mael_backlog_end"}`.
3. Live events, as the agent emits them.
4. `{"type": "mael_agent_exited", "exit_code": N}`, then the stream ends.

An attach to an agent that has already exited sends 1, 2 and 4. An unknown id gets one
`{"error": "no such agent: <id>"}` line, and the stream ends.

The two `mael_*` markers are the daemon's own, not the agent's. Neither reaches
`apply_event`.

Replies the daemon writes to the agent appear in the stream too, and in the backlog: the child
does not repeat a `control_response`, so without this a client that did not send the reply would
go on showing a wait that has already been answered. Every attached client therefore sees a wait
resolve, whoever resolved it, and the orchestrator server needs no copy of its own.

A `user` turn from `say` is the exception. The child replays every user turn on its own stdout,
marked `isReplay`, so the daemon does not record one — doing so would put a single turn on the
stream twice, and the orchestrator's normaliser mints a fresh item id per copy.


### Retention

| What | Kept |
|---|---|
| Raw events per agent | The last 200 |
| Agent messages | The last 5, each up to 8000 characters |
| Events queued for one slow attached client | 1000; the oldest is dropped silently past that |
| Agent state | Spawn record on disk; events and live state in memory |

## What is persisted

**Claude keeps the conversation. The daemon keeps how to start it again.**

A driven agent writes a normal session transcript to
`~/.claude/projects/<slug>/<session-id>.jsonl`, the same file an interactive session writes. The
transcript holds every `user` and `assistant` entry, in the same `message` shape the stream
carries. It does not hold `control_request`, `control_response`, `result` or `system/init` — so a
pending permission is not in it, and neither is a wait.

Each entry is stamped `"entrypoint": "sdk-cli"`. The `claude --resume` picker and the VS Code
session list both hide that entrypoint, so a driven session does not appear in either. An explicit
`claude -p --resume <id>` ignores the filter and works.

The transcript is what makes a resume possible, so the daemon protects it. Every child is spawned
without `CLAUDECODE` and `CLAUDE_CODE_CHILD_SESSION`, which an inherited marker can use to
suppress the write, and with `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE=1`. See
`agent_model.build_agent_env`.

### The spawn record

Claude stores none of the cwd, permission mode, model or environment a spawn needs. So the daemon
writes one record per agent to `~/.maelstrom/agents/<agent-id>.json`, holding exactly that:

| Field | Why |
|---|---|
| `agent_id`, `cwd` | The id the orchestrator and the user already know; where to spawn |
| `session_id` | Always set — the daemon mints one when the caller gives none. A child that dies before its `system/init` stays resumable |
| `permission_mode`, `model`, `env` | The argv and environment to rebuild. `env` is the caller's own extra vars only |
| `prompt` | A child that died before its first turn is started again with the prompt it never got |
| `status` | `running` or `exited`. A `stop` deletes the record |
| `exit_code` | So `list` still reports the exit after a daemon restart |

`MAEL_AGENT_SPEC_DIR` overrides the directory. A test daemon on its own socket wants its own
records, so it cannot resume the real daemon's agents.

Records are written owner-only (`0600`, in a `0700` directory). `env` holds whatever a client
passed to `start`, and that has no allowlist, so a record can hold a secret. `mael doctor`
tightens a record it finds loose.

### The resume rules

- **A crashed child does not restart itself.** The agent shows `exited(N)`, and
  `mael agent resume <id>` brings it back. The id is kept, which is what makes a resume invisible
  to the orchestrator and to the user.
- **A daemon start resumes every record still marked `running`.** A record marked `exited` is
  loaded as an exited agent instead, so `list`, `show` and `resume` all answer for it, but nothing
  respawns it. That is also the loop guard: a resumed child that dies again is recorded `exited`,
  so the next daemon start leaves it alone.
- **A daemon shutdown stops every child but leaves the records `running`.** So the next daemon
  start resumes them. Restarting the daemon to pick up new code costs nothing.
- **A resumed agent gets a turn saying why it came back.** A print-mode session sits idle until a
  user turn arrives, and a permission it was blocked on did not survive. `mael agent resume --text`
  replaces the default nudge in `agent_model.DEFAULT_RESUME_PROMPT`.
- **A daemon shutdown does not record an exit.** Stopping a child ends its stream, which would
  otherwise mark the record `exited` and stop the next daemon resuming it.

The retained event buffer is still the only history the daemon itself reads. Reading the
transcript back through the normaliser is a follow-up, not built. `show` renders the last 3
messages, and each is kept whole up to 8000 characters.

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
