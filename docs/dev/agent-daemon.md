# The agent daemon

How maelstrom drives a Claude Code agent from outside the terminal it runs in, and answers it
while it waits.

A session in a cmux pane has no way to receive an answer except through that pane. The agent
daemon adds that missing half. It runs each agent as a `claude` child on a bidirectional NDJSON
pipe, reads what the agent is doing from its event stream, and writes answers back on the child's
stdin. `mael agent` is the client.

This path is now the launch path. `mael add`, `mael open`, `mael task run` and
`mael task next --run` all start a driven agent, and the workspace's pane 0 runs
`mael agent attach <id>` as a client of it. See
[the harness table](../reference/cli.md#sessions-and-workspaces) for the flags and the legacy
runners.

The daemon owns the pipe, not the pane. Ctrl-C in the pane detaches the client and leaves the
agent running. `mael agent start` makes an agent with no workspace at all, which is what the
orchestrator's own launches do.

## The mechanism

Each agent is a normal `claude` process with different I/O plumbing:

```
claude -p --input-format stream-json --output-format stream-json --verbose \
       --permission-prompt-tool stdio --forward-subagent-text --replay-user-messages \
       [--permission-mode auto]
```

Because it is the same binary, skills, `CLAUDE.md`, settings, sub-agents, MCP servers, hooks and
`--permission-mode auto` all behave as they do today.

Six flags matter, and two of them are easy to miss:

| Flag | Why it is needed |
|---|---|
| `-p` | Headless. There is no TUI in this process, and none is wanted. |
| `--input-format stream-json` | Makes stdin a live message channel, not a one-shot prompt. |
| `--output-format stream-json --verbose` | Emits the event stream. `--verbose` is required with this output format. |
| `--permission-prompt-tool stdio` | **Load-bearing.** Tells the CLI that permission prompts reach the host over the pipe. |
| `--forward-subagent-text` | Puts a subagent's text and thinking blocks on the stream beside its tool calls. Without it a subagent's stream shows what it did and never what it said. |
| `--replay-user-messages` | **Load-bearing.** Makes the child echo every `user` turn it reads from stdin back on stdout, marked `isReplay`. Without it a `say` never reaches the transcript. Confirmed against v2.1.261. |

Without `--permission-prompt-tool stdio` a headless agent has nobody to ask. Every "ask" decision
resolves itself, the agent never pauses, and no wait is ever observable. The flag does not appear
in `claude --help` on v2.1.252, but it works.

`--replay-user-messages` is easy to miss for the opposite reason: v2.1.252 echoed stdin user turns
without it, so a fixture recorded then shows the echo with no flag. v2.1.261 echoes nothing
without it.

The daemon holds the child's stdin open for the life of the agent. A closed stdin ends the session
after one turn, which is what a bare `claude -p` does.

## The event vocabulary

Every shape below was recorded from a live agent on v2.1.252 and saved under
`tests/fixtures/agent_events/`. `tests/test_agent_model.py` replays those transcripts through
the state machine, so nothing here is designed from an assumed shape.

`mael agent tail --raw ID` records one. The rendered form of `tail` prints only what it has a
line for, which leaves out every `system` event, so a recording uses `--raw`. The daemon's own
`mael_*` markers and its `mael_seq`/`mael_ts` stamp do not appear in the output.

A recording of a parent holds none of its subagents' events, because the daemon routes a
parented event to that subagent's ring. Tail each dotted id as well to capture those.

A recording is the stream, not the child's own output. So a `control_response` the daemon wrote
appears twice: once because the daemon records what it writes, and once because the child echoes
it back. `apply_event` ignores the second, whose `request_id` is no longer the pending one.

### A turn

A turn opens with `system`/`init`, which carries the `session_id`, the model and the permission
mode. Assistant output arrives as `assistant` events. The turn closes with a `result` event
carrying `total_cost_usd`, `subtype` and a `usage` block.

The two numbers on that event mean different things. `total_cost_usd` is the session's, so the
row replaces it. `usage` is the turn's, so the row adds it: `tokens` on the agent row is the
running sum, and `agent_model.tokens_of` is the one reader of the block. It counts cache reads
and cache writes as well as input and output. Those are billed: a total without them
under-reports a long session by most of its weight.

That sum says how much work the session has done, and it is not how full the context is. A turn
re-reads its whole prompt from cache on each request, so the same context is counted again every
time and the total runs past any window. `context_tokens` is the occupancy figure, and it comes
off `assistant` events instead, where `usage` sits under `message` and describes the one request
that event answers. `agent_model.context_of` reads it, summing `input_tokens`,
`cache_read_input_tokens` and `cache_creation_input_tokens` — `output_tokens` is out, being what
the model wrote rather than what the prompt held. A `result`'s `usage` cannot answer this: it
sums the turn's requests, so its cache counts exceed the context the agent holds. Each reading
replaces the last, so the number advances mid-turn and falls when the agent compacts.

The daemon does the summing, so the total is its own count since it took the agent. A daemon
restart rebuilds `AgentState` and the total starts again at 0, where `total_cost_usd` comes back
from the host's own report on the next `result`. An attach does not double it: the backlog it
replays is already in the row the total was seeded from, so only a live turn adds.

The orchestrator re-reads all three numbers off the row on every poll, not only when it adopts
the agent — otherwise an agent adopted before its first turn, off a synthesised launch row that
carries none of them, reads 0 until a `result` the server itself saw. Spend and size are taken as
the larger of the row and what the world holds, because they only climb within one agent: a poll
can carry a row the host stamped before a turn the stream has already delivered, and taking it
flat would walk them backwards between polls. Occupancy is taken as it stands, because it is a
level and a compact is meant to drop it.

### The account's budget

`rate_limit_event` carries how much of the account's budget is spent. It arrives on its own
cadence as a turn runs, not once per turn:

```json
{"type": "rate_limit_event",
 "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour",
                     "unifiedWindows": {
                       "five_hour": {"utilization": 0.07, "resetsAt": 1788241800},
                       "seven_day": {"utilization": 0.24, "resetsAt": 1788480000}}}}
```

Two things the shape does not say. `utilization` is quantised to whole percent by the source,
so a reader shows what it was given and never a finer figure. And the event fires throughout a
window, not only near its end — the recorded fixtures carry readings from 5% up. Claude Code's
own UI shows usage only from 75%, but that is a rule in that client, not a gate on the event.

`apply_event` reads it onto `AgentState.usage` and leaves `status` alone: it is a fact about
the account, not about what this agent is doing. One account spans every agent on the machine,
so `list` reports the freshest reading across them once rather than per row, and the
orchestrator publishes it on the one `Host` entity.

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

### Changing the permission mode

A mode change is the second message the host sends that is not a reply, and the only one whose
answer the daemon reads:

```json
{"type": "control_request", "request_id": "spm-1",
 "request": {"subtype": "set_permission_mode", "mode": "acceptEdits"}}
```

The child answers, then announces the mode it is now in:

```json
{"type": "control_response", "response": {"subtype": "success",
 "request_id": "spm-1", "response": {"mode": "acceptEdits"}}}
{"type": "system", "subtype": "status", "status": null,
 "permissionMode": "acceptEdits", …}
```

A mode the child does not know comes back with `subtype: "error"` instead. So the daemon waits
for the answer: reporting a refusal as a change would leave the spawn record, the list row and
the teleport footer all naming a mode the agent is not in. The daemon waits 10 seconds, then
fails the command.

`system`/`status` is the only thing any surface reads the mode from. The child also sends one
when it changes mode by itself — approving an `ExitPlanMode` leaves plan mode with nobody asking.
Recorded in `tests/fixtures/agent_events/plan-review.jsonl`.

maelstrom's three modes are `plan`, `normal` and `auto`. `WIRE_MODE` in `agent_model.py` maps
them to claude's words, and nothing else spells `default`.

### Sending a message

A follow-up message is a plain user turn on stdin. The opening prompt uses the same shape:

```json
{"type": "user", "message": {"role": "user",
 "content": [{"type": "text", "text": "also update the README"}]}}
```

A message can carry images. Each one is a base64 block ahead of the text:

```json
{"type": "user", "message": {"role": "user", "content": [
  {"type": "image",
   "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0…"}},
  {"type": "text", "text": "what is wrong with this screen?"}]}}
```

The model sees the image on the turn it arrives, rather than after choosing to read a file. A
very small image is refused by the API as unprocessable -- a 2 by 2 PNG was, a 200 by 200 one was
not -- so probe this shape with a realistic one rather than the smallest file you can build.

An image with no words is a message in its own right, so the text block is dropped when the text
is empty.

A task cannot use this path. `build_prompt` returns a string, so a task's images travel as
`{{MAEL_TASK_DIR}}` refs in its content, and the agent reads them with `Read`. See
[the orchestrator server](orchestrator-server.md) for where the bytes are stored.

### Running a shell command

A shell command is the user's, not the agent's: the host runs it and gives the agent the output
as context. `mael agent run`, a `!` line in teleport, and a `!` line in the orchestrator UI all
reach the same `run` command.

The daemon runs it, because the daemon is where the agent's working directory is. A surface that
ran the command itself would need to learn that directory and keep it fresh, and the orchestrator
server would run it on the wrong machine once the agent host moves.

The result reaches the agent as **two user turns, in the tags Claude Code's own `!` writes**:

```json
{"type": "user", "message": {"role": "user",
 "content": "<bash-input>git status</bash-input>"}}
{"type": "user", "message": {"role": "user",
 "content": "<bash-stdout>On branch main</bash-stdout><bash-stderr></bash-stderr>"}}
```

The content is a plain string, not a block list. Both output tags are always present, and empty
when unused.

Three consequences worth knowing:

- **Nothing is asked of the agent.** A shell command carries no request, so no surface shows
  the agent working and the console does not wait on it. The agent usually remarks on what it
  read anyway, as it does in the terminal. Maelstrom neither suppresses that nor counts on it.
- **There is no exit code on the wire.** The CLI declares a `bash-exit-code` tag and emits it in
  none of the recorded transcripts, so neither does the daemon. A failing command reaches the
  agent as `<bash-stderr>` text, which is what it already reads.
- **Neither turn is recorded**, for the same reason a `say` is not: the child echoes every stdin
  user turn itself. That echo needs `--replay-user-messages` — see the flag table above.

Output is capped per stream at the same bound a retained message keeps, and the command is
killed after 30 seconds — the process group, so a pipeline dies whole. A timeout reaches the
agent as `<bash-stderr>` text, and the caller still sees `{"ok": true}`: there is no exit code
on this format to say otherwise. One command runs per agent at a time, and a second is refused
rather than queued, so no client can starve the loop every agent shares.

The command runs with the daemon's own privileges, and there is no allowlist. That is
deliberate: the feature is "run what the user typed", and a filter would break it while only
looking like safety. So the socket's mode is the whole boundary, and the daemon sets it —
`0600` on the socket, `0700` on the root. Resting it on `~/.maelstrom` happening to be `0700`
would leave the boundary one `mkdir` from gone. Every command also reaches `shell.py`'s logger,
so what ran stays answerable afterwards. This is not a sandbox.

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
same state every time, with no subprocess and no socket. `tests/test_agent_model.py` does exactly
that against the recorded fixtures.

The state comes from observed events, not from hook inference. So an interrupt is visible, rather
than leaving a session stuck in `processing` until a timeout decides it is stale.

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

### Ending an agent

Every child runs in its own session, so it leads a process group of its own. Ending an agent is
an escalation against that group: `stop` closes the child's stdin first, which a driven `claude`
is meant to notice; a child still there after 5 seconds gets SIGTERM to its group, and one still
there 3 seconds after that gets SIGKILL to its group. A pump that fails unexpectedly skips the
stdin step and goes straight to the signals. Signalling the group rather than the pid is what
takes the child's hooks, MCP servers and tool shells with it: a `proc.kill()` reached the child
alone and left the rest running on a dead pipe.

The daemon's own shutdown, whether from `mael env stop`'s SIGTERM or a foreground `serve`'s
Ctrl-C, stops every child this way. Because the children are outside the
daemon's group, a signal to the daemon never reaches them directly; the daemon's handler is the
one path, and it leaves every record `running` so the next daemon resumes them.

## Running it

One daemon serves one *daemon root*: the directory holding its socket, its lock, its pid file,
its log and its `agents/` spawn records. Every environment names its own root in
`MAEL_AGENT_ROOT`, so one environment's agents never mix with another's.

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
mael agent run a1b2c3d4 "git log --oneline -3"   # give it a command's output
mael agent interrupt a1b2c3d4                         # abandon the turn, keep the agent
mael agent tail a1b2c3d4
mael agent attach a1b2c3d4
mael agent stop a1b2c3d4

mael agent daemon status                              # which daemon is answering, and whose code
mael agent daemon list                                # every record: pid, alive, held, mismatch
mael agent daemon reconcile                           # what gc would do
mael agent daemon gc                                  # kill strays and duplicates, write off crashes
```

`mael agent list --json` emits the rows as JSON.

### Starting a daemon

Two commands start one, and nothing else does:

```bash
mael self-env start                  # the everyday daemon, on ~/.maelstrom/daemons/_main
mael env start                       # this worktree's daemon
mael self-env restart agent-daemon   # replace one holding stale code
```

Both run `mael agent daemon serve` as a service, so a daemon's lifetime is its environment's, and
`mael env stop` takes the daemon and its agents with it. The service writes to
`<root>/agent-daemon.log`.

No `mael agent` command, session launch or orchestrator poll starts a daemon. Each reports the
root it looked for and both of these commands:

```
No agent daemon on /Users/you/.maelstrom/daemons/_main. Run `mael self-env start`
(everyday daemon) or `mael env start` (this worktree's).
```

### The daemon root

Everything a daemon owns lives under one directory:

| Path | What |
|---|---|
| `<root>/agent-daemon.sock` | The control socket |
| `<root>/agent-daemon.lock` | The exclusive lock, held for the daemon's life |
| `<root>/agent-daemon.pid` | The serving daemon's pid, written after the lock is taken and removed at shutdown |
| `<root>/agent-daemon.log` | Where a detached daemon writes |
| `<root>/agents/` | One spawn record per agent |

`MAEL_AGENT_ROOT` names the root, and nothing else does. There is no default and no `--root`
flag, so a daemon cannot be started on a root its environment does not own. The socket, the log
and the records used to be three independent variables,
so a daemon could be pointed at one directory's records over another directory's socket, and two
daemons could share the records that make them spawn. One root, with one daemon per root, is
what makes a session belong to exactly one daemon.

A second daemon on the same root refuses to start. An exclusive `flock` on the lock file, held
for the daemon's life, is what makes that true: a liveness probe is a check-then-act, so two
daemons can both find the socket free, and the loser then binds a path no client can reach while
it still holds its children. A stale socket file left by a killed daemon refuses connections, so
it reads as free and is replaced; the kernel releases that daemon's lock when the process dies.

### A sandbox can deny the socket

A sandbox allows a Unix domain socket only if the socket is on its allowlist, and the daemon
socket usually is not. The connect then fails with `EPERM`, and the agent daemon is alive the
whole time.

Two error messages tell the two cases apart:

| The connect fails with | The reply says | It means |
| --- | --- | --- |
| `ENOENT`, `ECONNREFUSED` | `No agent daemon on <root>` | No daemon holds this root |
| `EPERM`, `EACCES` | `Cannot connect to the agent daemon socket at <path>: permission denied` | A daemon may hold this root, and something refused the connect |

The split matters beyond the wording. `mael agent daemon gc` reads the `No agent daemon on`
marker as "no daemon holds these agents", then falls back to the spawn records and kills the
strays it finds. Under a denial that reasoning is wrong: the daemon still holds those agents. So
a denial never carries the marker.

`mael admin install` adds `mael task:*` to `sandbox.excludedCommands` in
`~/.claude/settings.json`, so the launch verbs run outside the sandbox. Run it on a machine
that still shows the denial.

### Which daemon is answering

A daemon holds the modules it imported at start, for days. So a command from a worktree is
served by whatever code the daemon started with, which is usually `_main`'s. That has produced a
bug that looked like the feature under development: a daemon running older code deleted a spawn
record.

`mael agent daemon status` answers it:

```
root:     /Users/sminnee/.maelstrom
socket:   /Users/sminnee/.maelstrom/agent-daemon.sock
pid:      59360
version:  0.1.2
source:   /Users/sminnee/Projects/maelstrom/_main
specs:    /Users/sminnee/.maelstrom/agents
started:  2026-09-05 14:47 (3h ago)
agents:   5
```

`source` is the field that matters: it names the worktree the serving code came from. A daemon
holds the modules it imported at start, for days, so a command from a newer tree is served by it
silently. That has produced a bug that looked like the feature under development.
`mael self-env restart agent-daemon` replaces one holding stale code.

A daemon too old to answer `ping` has no identity to print, so `status` fails with that same
advice rather than reporting the daemon's own confusion. A daemon that does not know `ping`
predates it by construction.

### A daemon per environment

Every environment runs a daemon of its own, on its own root. A worktree that runs
orchestrator/web is testing changed code; if that change touches the agent protocol, driving the
daemon another environment holds is the bug rather than the accident.

maelstrom's own `.maelstrom.yaml` declares it as an ordinary service:

```yaml
services:
  agent-daemon:
    command: uv run mael agent daemon serve
```

The root comes from the project root's `.env`, which each worktree's `.env` is substituted from:

```bash
MAEL_AGENT_ROOT=~/.maelstrom/daemons/${WORKTREE}
```

`serve` reads the root from the environment and takes no `--root` flag, so a daemon cannot be
started on a root its environment does not own.

```bash
mael env start                     # this worktree's services, the daemon among them
mael env stop                      # takes the daemon and its agents with it
```

`_main` is a worktree name like any other, so the same line gives it
`~/.maelstrom/daemons/_main`. That is the everyday daemon, and `mael self-env start` runs it.

Each root holds its own spawn records, so one environment's daemon cannot restore the records
another holds and start a second `claude` on each of its sessions.

`mael agent list` names what each waiting agent waits on, which is the point of the whole
mechanism:

```
id        state                 waiting_on                   last_message              cwd
0c35d123  idle                                               Both tests pass now.      ~/…/alpha
1761dcf6  awaiting-question     Which colour do you prefer?  I need one decision …     /private/tmp
0b2f5f5b  awaiting-plan-review  ExitPlanMode                 **Context:** Create a …   /private/tmp
```

`last_message` is what the agent last said, cut to one line. Without it two working agents look
identical, because a state and a wait kind say only that both are busy. The row also carries
`last_message_at`, which says when — not a column here, but read by `--json` and the
orchestrator UI.

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

`show` on a parent ends with a `Subagents:` table, one row per subagent with its dotted id, its
state, its description and its last message. `show` on a dotted id prints that subagent. A wait a
subagent raised prints as `Waiting on: WebFetch (from a1b2c3d4.1)`, and the answer hint names the
parent, because the parent is what takes the answer. See "Subagents" below.

### Tailing

`mael agent tail <id>` renders an agent's event stream without driving it. It prints the buffered
history and stops. `mael agent tail -f <id>` keeps streaming. Nothing typed reaches the agent
either way, so a tail is read-only by construction rather than by redirecting stdin. A tail of a
parent prints the parent alone; `mael agent tail a1b2c3d4.1` prints one subagent's stream.

The daemon writes a `{"type": "mael_backlog_end"}` marker after the replayed history, which is how
a tail without `-f` knows where to stop. An idle timeout would race a slow agent and flake. A
30-second read timeout backstops a daemon that never sends the marker, so a tail errors rather
than hangs — it is not what ends a normal tail. A `mael_truncated` marker prints as "— N earlier
events dropped".

### Teleport

`mael agent attach <id>` is teleport. A headless agent has no TTY, so there is no pane to attach
to. Attach is another client of the same socket, and it renders that socket's stream as a
terminal UI.

The screen has three parts. A transcript shows the agent's messages, its tool calls with the
first few lines of each result, and how each turn ended. A console at the bottom sends what you
type as a user message, and the transcript shows it. A line above the console says the agent is
working while it owes a reply. A footer names the working directory, the model, the tokens
consumed, the git branch, the agent's state and its permission mode.

A `!` line runs a shell command instead of saying something. `!git status` runs the command in
the agent's working directory, shows it in the transcript, and gives the agent the output. The
line asks the agent for nothing, so the working line stays off. See "Running a shell command"
above.

A wait is answered in place. A permission ask, a question and a plan review each open a prompt
over the transcript, so you never leave the terminal to run `mael agent approve` in another one.
The prompt closes by itself when the wait ends some other way — another client answered it, the
turn ended, or the agent died.

Three keys work everywhere, including inside a prompt:

| Key | What it does |
|---|---|
| Esc | Interrupts the running turn. Nothing happens when the agent is idle. |
| Shift-Tab | Moves the agent to the next permission mode: plan, then auto, then normal. |
| Ctrl-C or Ctrl-D | Detaches. The agent keeps running; `mael agent stop` is what ends one. |

Shift-Tab works inside a prompt on purpose. A permission ask is exactly when the mode you want
is a different one.

Esc inside a prompt is not "close this prompt". The daemon denies the pending request and then
interrupts the turn, which is what Esc at a permission ask does in Claude Code itself.

Attach needs a terminal, and refuses without one rather than rendering into a pipe. Use
`mael agent tail -f <id>` to follow an agent from a script or a redirect. That command is the raw
read-only view, and nothing you type reaches the agent through it.

## The control socket protocol

The daemon's socket is the interface the orchestrator server drives agents through, and the one
`mael agent` uses. This section is the reference for that interface: the orchestrator server ↔
agent host protocol. A host on another machine later means the same messages over TCP.

The transport is NDJSON on a Unix domain socket in the daemon root. Every command is one line
in and one line out, except `attach`, which holds the connection open and streams.

### Commands

Every request carries `cmd`. Every reply is either an ok reply or `{"error": "<message>"}`.

| `cmd` | Request fields | Ok reply |
|---|---|---|
| `start` | `cwd`; optional `prompt`, `mode`, `model`, `session`, `env`, `resume` | `{"ok": true, "id": "<agent id>"}` |
| `list` | optional `scope` (`running`, `stopped` or `all`; default `running`), optional `cwd` | `{"agents": [<row>, …]}`, each row as `mael agent list --json` prints |
| `show` | `id` | `{"agent": <detail>}`, as `mael agent show --json` prints |
| `say` | `id`, `text`; optional `attachments` | `{"ok": true}` |
| `run` | `id`, `command` | `{"ok": true}` |
| `approve` | `id`; optional `request` | `{"ok": true}`, plus `"mode": "auto"` or `"warning": "<why not>"` for a plan review |
| `deny` | `id`; optional `request`, `reason` | `{"ok": true}` |
| `answer` | `id`; optional `request`; `answers` (a map keyed by question text) or `choice` | `{"ok": true}` |
| `interrupt` | `id` | `{"ok": true}` |
| `set-mode` | `id`, `mode` (`plan`, `normal` or `auto`) | `{"ok": true, "mode": "<mode>"}` |
| `stop` | `id` | `{"ok": true}` |
| `resume` | `id`; optional `text` | `{"ok": true, "id": "<agent id>"}` |
| `attach` | `id`; optional `from`, `epoch` | A stream; see below |
| `ping` | none | `{"daemon": {…}}`: `pid`, `version`, `executable`, `source_tree`, `root`, `socket_path`, `spec_dir`, `started_at`, `agents` |
| `shutdown` | none | `{"ok": true}`, then the daemon stops |
| `reconcile` | none | `{"verdicts": [<verdict>, …]}`: what `gc` would do, doing nothing. See "Strays and gc" |
| `gc` | none | `{"verdicts": […], "killed": [<pgid>, …]}`: the verdicts, acted on. Never resumes |

`start` merges `env` over the daemon's own environment for that child, with no allowlist: a
client of the socket can set any variable. The socket's file permissions are the trust boundary.
A task launch passes `MAEL_TASK_ID`, `MAEL_TASK_PARENT` and `MAEL_TASK_SESSION_ID` this way.

`say` takes `attachments` as `[{"path": "/…/shot.png"}]`. The daemon reads each file and sends
it as an image block. Paths rather than base64 keep the socket line small, and the daemon runs on
the same machine as the files. A path that will not read is refused: a turn that silently lost
its picture is worse than a refusal.

`answer` with `answers` files each answer under its question. `choice` applies one answer to
every question. An empty `answers` map is refused: the agent reads an empty map as no answer at
all.

`start` with `resume: true` continues the session `session` names instead of claiming it. A task
that has run before already owns its session id, and claiming it again is refused, so the
orchestrator sets this from the transcript on disk.

`approve` on a plan review also moves the agent to `auto`. The mode request follows the allow, because
the child is waiting on that reply. A child that refuses the mode does not undo the approval: the
reply still says `ok`, and names the refusal under `warning`. `mael agent` prints a warning and
still exits 0, because the command did what was asked.

`interrupt` abandons the turn the agent is running and leaves the agent alive.

`ping` names the daemon rather than an agent, so it carries no id and answers on a daemon holding
nothing. `source_tree` is the worktree the serving code was imported from.

`shutdown` replies before it stops, so a caller learns the daemon heard it instead of reading a
closed connection. The daemon then stops every child and leaves each record `running`, so the
next daemon start resumes them.

`set-mode` changes the permission mode of a running agent, and takes effect on the turn the agent
is running. It is the one command that reads the child's answer, so a mode the child refuses is
reported as a refusal. On success the daemon rewrites the spawn record, so a resume or a daemon
restart keeps the new mode.

`stop` removes the agent from the daemon and marks its spawn record `stopped`. It ends the child's
whole process group — see "Ending an agent". A default `list`
does not name it, and no later daemon start brings it back. The record itself is kept, so
`mael agent resume` still has the model, permission mode and environment the agent ran with.

`list` with `scope: "stopped"` returns the sessions that can be resumed instead. A row is built
from two sources: the spawn record, which `resume` reads to start the agent again, and Claude's
session transcript, which says what the session was doing. A session with no record is left out,
because nothing could resume it. A session still running is left out too. `cwd` narrows the read
to one working directory, which is one transcript directory rather than all of them. The CLI
resolves a worktree or a project to that path — the daemon knows nothing about either.

The default scope is unchanged on purpose. The orchestrator server infers an agent's exit from its
id being absent from `list` (see [orchestrator-server.md](orchestrator-server.md)), so a stopped
agent appearing there would sit on the canvas for ever.

`resume` starts an exited agent again under its own id, and sends it one turn: `text`, or the
default nudge. See "The resume rules".

`start` and `resume` both report the spawn, not the run. A child that dies straight after
spawning — a bad `--model`, an expired login, a `--resume` Claude will not accept — is reported
`ok`, and the exit shows in the next `list`. `mael agent show` says why.

### The guards

| Refusal | When |
|---|---|
| `no such agent: <id>` | No agent has that id, or a dotted id names a subagent the daemon has not seen |
| `<id>.N is a subagent of <id>; drive <id>` | Any command except `show` and `attach` against a dotted id |
| `agent <id> has exited` | Any command except `show`, `stop` and `resume` against an exited agent |
| `agent <id> is running` | `resume` against an agent that has not exited |
| `agent <id> has no spawn record` | `resume` against an agent whose record is missing |
| `unknown mode: <mode> — one of plan, normal, auto` | `set-mode` with a mode maelstrom does not have |
| `agent <id> refused <mode>: …` | `set-mode` the child would not accept |
| `agent <id> did not answer` | `set-mode` when the child stays quiet for 10 seconds |
| `agent <id> is not waiting` | `approve`, `deny` or `answer` with no pending request |
| `agent <id> is not waiting on a question — use approve or deny` | `answer` against a permission or plan review |
| `no answers given` | `answer` with an empty `answers` map |
| `no command given` | `run` with an empty command |
| `could not reach agent <id>` | The child's stdin would not take the message: it is dying |
| `could not start claude: …` | `start` when the child could not be spawned |
| `unknown command: <cmd>` | Anything else |

### The attach stream

`{"cmd": "attach", "id": "<agent id>"}` turns the connection into a stream of the agent's raw
stream-json events, one per line:

1. `{"type": "mael_agent_detail", "agent": {…}}`: `build_agent_detail` for the agent.
2. `{"type": "mael_truncated", "dropped": N}`, only when events the client should see are gone.
3. The retained backlog: up to 200 events, oldest first.
4. `{"type": "mael_backlog_end", "epoch": "9b2e7c41", "seq": 417}`.
5. Live events, as the agent emits them.
6. `{"type": "mael_agent_exited", "exit_code": N}`, then the stream ends.

An attach to an agent that has already exited sends 1 to 4 and 6. An unknown id gets one
`{"error": "no such agent: <id>"}` line, and the stream ends.

Every event the daemon records carries two extra keys. `mael_seq` is its position in the agent's
stream, from 1, per life of the agent. `mael_ts` is when the event happened. A consumer
dispatches on `type` and ignores both, so the TUI and `tail` need no parsing change.

`mael_ts` is the event's own `timestamp` where it has one, and the daemon's clock otherwise. A
resumed agent's replayed turns carry Claude's timestamp, so they keep their real times instead of
claiming they just happened.

The backlog marker carries the agent's `epoch` — a name for this
life, minted per `start` and per `resume` — and the `seq` the replay reached.

A client that comes back sends both: `{"cmd": "attach", "id": "…", "from": 350, "epoch":
"9b2e7c41"}`. The daemon replays only the events after `from`, so nothing shows twice. A wrong or
missing epoch means the cursor is from another life, and the replay starts from the beginning of
this one. When the ring has rolled past the cursor, the `mael_truncated` marker says how many
events are gone. The same marker lands mid-stream when a slow client's queue overflowed, so a gap
is never silent.

The opening detail frame says what the agent is waiting on, so a client does not infer it from
the replayed events. Its `request_id` is what makes the wait answerable: a `list` row carries no
request id, so a row alone never is.

The four `mael_*` markers are the daemon's own, not the agent's. None reaches `apply_event`.

### Subagents

Claude Code stamps every event a subagent produces with `parent_tool_use_id`, the id of the
`Agent` call that spawned it. The daemon keeps those events apart from the parent's. Each
subagent is a stream of its own under a dotted id: agent `a1b2c3d4` has subagents `a1b2c3d4.1`,
`a1b2c3d4.2`, and a subagent of `a1b2c3d4.1` is `a1b2c3d4.1.1`. The parent's ring, seq, last
message, status and pending request never see a subagent's event.

The level comes from the ring that holds the spawning call, never from the tool name: a call in
the parent's ring opens `X.n`, and a call in `X.1`'s ring opens `X.1.n`. The ordinal is one past
the highest handed out at that level, and an ordinal is never reused.

A subagent opens on `system`/`task_started` with `task_type: local_agent`, or on the first
parented event for an id the daemon has not seen. A background `Bash` inside a subagent raises
`task_started` too, keyed by the `Bash` call and typed `local_bash`, so the type is the gate. A
subagent ends on `system`/`task_notification`, which carries its `status` (`completed`, `failed`
or `stopped`) and a `summary`. The parent's own `tool_result` for the `Agent` call ends nothing:
a backgrounded subagent gets that result at launch and runs on. A subagent that speaks after its
notification is running again.

#### A subagent's permission ask

A `control_request` carries no `parent_tool_use_id`, so a subagent's ask arrives on the parent's
stream. It carries `request.agent_id`, which is the `task_id` of that subagent's `task_started`.
That is the join: `subagent_tasks` maps it to a dotted id, and the ask is filed on the subagent
that raised it.

**Claude Code does not serialise the asks.** Two subagents can block at once, and so can two
parallel calls an agent makes itself.
`tests/fixtures/agent_events/subagent-permission-concurrent.jsonl` records two open together,
each with its own `request_id` and `agent_id`. So `pending` is a map, on the agent and on each
subagent, and one answer retires one ask.

The reply always goes to the parent's pipe: a subagent has no process of its own. But the wait is
the subagent's, so its row says what it waits on rather than reading `processing` while it is
stuck. The parent reports every ask beneath it, because the parent is where a reply is sent.

`request` names which wait to answer. Omit it and the daemon answers the only open one, refusing
when there is more than one. An id the agent does not hold is refused rather than falling back to
whichever wait is current: the caller answered something it could see, and that wait is over.

Two consequences:

- An `interrupt` denies every open ask. An undenied one leaves its caller blocked on a reply
  that never comes.
- A `task_notification` clears that subagent's asks. One that has gone holds nothing a reply
  could reach.

`task_started` gives a `spawn_depth` but never a parent, so it cannot say where a nested subagent
sits. Placing one at its level still needs the ring scan, which is why `_ring_holding_call`
survives for that one caller.

On the socket a dotted id works where a read does:

| `cmd` | On a dotted id |
|---|---|
| `list` | Every subagent follows its parent's row, in the same shape: `parent` names the parent, `description` is what the parent asked for, `state` is `processing` while it runs, `exited(0)` once completed, `exited(1)` once failed or stopped. `session`, `cwd`, `model` and `mode` are the parent's. `waiting_on` and `cost` are empty, and `tokens` and `context_tokens` are 0: a subagent has no session of its own, so its spend and its size count in the parent's totals, and its context is the parent's prompt. `last_message` is the summary once ended, else the last text, and `last_message_at` says when. A top-level row carries `parent: ""` |
| `show` | The subagent's row plus `message` in full. `show` on a parent adds `subagents`, the child rows, and `waiting_subagent` |
| `attach` | The subagent's stream: its own `mael_agent_detail`, its ring under its own `mael_seq`, `mael_backlog_end` with its seq, live events, then `mael_agent_exited` with `0` for completed and `1` otherwise, or the parent's code when the parent's process goes. `from` and `epoch` work against the subagent's seq and the parent's epoch |
| anything else | Refused: `<id>.1 is a subagent of <id>; drive <id>` |

An `attach` to a dotted id the daemon has not opened is `no such agent`. A `list` is how a client
learns which exist. "Retention" below gives the subagent limit.

### The daemon echoes what it writes

Every message the daemon writes to a child also goes on that child's event stream: a `user` turn
from `say`, and the `control_response` from `approve`, `deny` and `answer`. So an attached client
learns of a reply it did not make itself, and needs no local guessing.

This is what lets a client hold no opinion about how a wait is answered — the orchestrator server
relies on it. It also keeps the derived state fresh: `apply_event` clears the pending request on
the `control_response`, so `list` and `show` stop advertising a wait the moment it is answered
rather than at the child's next event.

A CLI that echoed its own `control_response` would be harmless: `apply_event` ignores a response
whose `request_id` is not the pending one.

Replies the daemon writes to the agent appear in the stream too, and in the backlog: the child
does not repeat a `control_response`, so without this a client that did not send the reply would
go on showing a wait that has already been answered. Every attached client therefore sees a wait
resolve, whoever resolved it, and the orchestrator server needs no copy of its own.

A `user` turn from `say` is the exception. The child replays every user turn on its own stdout,
marked `isReplay` — that is what `--replay-user-messages` buys — so the daemon does not record
one. Doing so would put a single turn on the stream twice, and the orchestrator's normaliser
mints a fresh item id per copy.


### Retention

| What | Kept |
|---|---|
| Raw events per agent | The last 200, each with its `mael_seq` and `mael_ts` |
| Raw events per subagent | The last 200, under the subagent's own `mael_seq`, each with its `mael_ts` |
| Subagents per agent | 50; past that the oldest that is not running goes, and its dotted id stays reserved |
| What the agent last said | One message, up to 8000 characters, and when it said it |
| Events queued for one slow attached client | 1000; past that the oldest is dropped and the client gets a `mael_truncated` marker saying how many |
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

Each child also gets `CMUX_CLAUDE_HOOKS_DISABLED=1`. Inside a cmux terminal a shell script named
`claude` shadows the real binary on `PATH`, and that script adds a `--settings` block of hooks
which call back into the cmux integrated development environment (IDE). A driven agent is not an
IDE session, so the variable tells the script to run the real binary with the argv untouched.

So a child's environment is the daemon's own, with these changes:

| Variable | What the daemon does | Why |
|---|---|---|
| `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION` | Removed | An inherited marker can suppress the transcript write |
| `CLAUDE_CODE_FORCE_SESSION_PERSISTENCE` | Set to `1` | Asks for the transcript outright, rather than relying on the removals |
| `CMUX_CLAUDE_HOOKS_DISABLED` | Set to `1` | Runs the real `claude`, not cmux's shim |
| `MAEL_AGENT_ROOT` | Set to the daemon's own root | A `mael agent …` command inside the session reaches the daemon that runs it, not whichever root the daemon's own shell named |

The request's `env` is applied last, so a caller that names any of these wins. That is the
no-allowlist contract above.

### The spawn record

Claude stores none of the cwd, permission mode, model or environment a spawn needs. So the daemon
writes one record per agent to `~/.maelstrom/agents/<agent-id>.json`, holding exactly that:

| Field | Why |
|---|---|
| `agent_id`, `cwd` | The id the orchestrator and the user already know; where to spawn |
| `session_id` | Always set — the daemon mints one when the caller gives none. A child that dies before its `system/init` stays resumable |
| `permission_mode`, `model`, `env` | The argv and environment to rebuild. `env` is the caller's own extra vars only. `set-mode` rewrites `permission_mode`, so a resume keeps the mode the agent was moved to |
| `prompt` | A child that died before its first turn is started again with the prompt it never got |
| `status` | `running`, `exited` or `stopped`. Only a `stopped` record is invisible to a default `list` |
| `exit_code` | So `list` still reports the exit after a daemon restart |
| `pid` | The child, while the record is `running`; `None` once it is known to be gone, so a dead record never names a pid the system has reused. How the next daemon tells a live child from a dead one |
| `started_at` | When the child was spawned. Orders two `running` records on one session, so the newer wins |
| `last_status` | What the agent is doing. Written whenever the status changes, so it survives a daemon that never shuts down. Decides which nudge the resume sends. Reset to `""` on every spawn |

The directory is the root's `agents/`, so a daemon on its own root has its own records and
cannot resume the real daemon's agents.

Records are never deleted, so a machine that runs agents for months accumulates one small JSON
file per agent.

Records are written owner-only (`0600`, in a `0700` directory). `env` holds whatever a client
passed to `start`, and that has no allowlist, so a record can hold a secret. `mael doctor`
tightens a record it finds loose.

### The resume rules

- **A crashed child does not restart itself.** The agent shows `exited(N)`, and
  `mael agent resume <id>` brings it back. The id is kept, which is what makes a resume invisible
  to the orchestrator and to the user.
- **A daemon start runs the gc first, then resumes each session once.** The gc kills every stray
  and duplicate on a record's session, writes off a record whose child is gone with no shutdown
  recorded, and retires the older of two `running` records on one session. Only then is each
  surviving `running` record resumed, one per session. A record marked `exited` — including one
  the gc just wrote — is loaded as an exited agent instead, so `list`, `show` and `resume` all
  answer for it, but nothing respawns it. That is also the loop guard: a resumed child that dies
  again is recorded `exited`, so the next daemon start leaves it alone. Where the process table
  cannot be read, the gc is skipped and every `running` record is resumed as before.
- **A `stopped` record is neither respawned nor loaded.** A stop is deliberate, so the agent stays
  out of `list` entirely. `mael agent list --stopped` finds it through its record, and reads its
  transcript for what it was doing. `mael agent resume` reads the record.
- **A daemon shutdown stops every child but leaves the records `running`.** So the next daemon
  start resumes them. Restarting the daemon to pick up new code costs nothing.
- **What a resumed agent is told follows what it was doing.** A print-mode session sits idle
  until a user turn arrives, so a resumed agent needs a turn of its own. Which turn depends on
  the record's `last_status`:

  | `last_status` | The turn the resume sends |
  | --- | --- |
  | `idle` | Nothing. A restart to pick up new code must not spend a turn on every agent the daemon held |
  | `awaiting-permission`, `awaiting-question`, `awaiting-plan-review` | `agent_model.LOST_ASK_RESUME_PROMPT`. The ask is gone, and a silent agent would wait for ever on a reply nobody can give |
  | Anything else, or a record too old to carry a status | `agent_model.DEFAULT_RESUME_PROMPT` |

  `mael agent resume --text` replaces the nudge. The nudge is sent, not recorded: the record keeps
  the opening prompt, so a child that never wrote a transcript can still be started fresh with it
  after any number of resumes.
- **The record learns the status as the agent runs, not at shutdown.** A daemon that is SIGKILLed
  or crashes never shuts down. A status written only at shutdown comes back empty, and the resume
  then nudges every agent — including the idle ones, which pick up work nobody asked for. Whether
  a daemon stopped the child is a separate flag, `stopped_at_shutdown`: the status says what the
  agent was doing, never how its child ended.
- **A daemon shutdown does not record an exit.** Stopping a child ends its stream, which would
  otherwise mark the record `exited` and stop the next daemon resuming it.

### Strays and gc

A **stray** is a driven agent's `claude` that outlived the daemon that held it. A **duplicate** is
a second driven `claude` on a session id a record already owns. Both come from the same gap: a
daemon that dies uncleanly — SIGKILL, an OOM kill, a crash — leaves its children running on a
dead pipe, and a daemon that then starts against the same records used to spawn each of them
again. One evening that made twenty-one duplicates.

The reconcile (`agent_reconcile.py`, pure) matches every `running` record against the process
table. Identity is the session id in the process's argv, nothing else: a pid the record names that
is a driven `claude` naming the record's session is the child; a pid that is anything else has
been reused and reads as the child having died. Read with `ps -ww`, because a child launched from
cmux carries a long `--settings {…}` JSON before its `--session-id`.

| Verdict | When | What happens |
|---|---|---|
| `owned` | The record's pid is its child, and this daemon holds it | Nothing |
| `stray` | The record's pid is its child, and no daemon holds it | Killed. Resumed on a daemon start; left `running` by `gc` |
| `duplicate` | Another driven `claude` names a running record's session | Killed |
| `resumable` | The child is gone and the record says why: `stopped_at_shutdown` is set, or no pid was ever recorded | Resumed on a daemon start |
| `crashed` | The record's pid is dead or reused, and no shutdown was recorded | Rewritten `exited` |
| `superseded` | The older of two `running` records on one session | Rewritten `stopped`; its child, if alive, killed |
| `unknown` | A driven `claude` no running record here names | Reported. Killed only under `--all-roots`, when no root names it |

A daemon start runs the gc before it resumes anything, so the resume finds no live child on any
session it brings back. `mael agent daemon gc` runs it by hand: through the daemon when one
answers, which knows what it holds; otherwise the CLI reads the records and the table itself,
which is the case it exists for — a daemon that died and left its children. `reconcile` prints
the verdicts and touches nothing. `list` puts the same verdicts beside every record, so a
mismatch is read off one table.

A session belongs to one root. `unknown` is never killed from one root because the same driven
`claude` may be another root's `owned`: a task keeps its session id across roots, so a task
relaunched under one environment's daemon runs on a session another environment's records also
name. `--all-roots` reconciles every root under `~/.maelstrom` — the directory itself, where the
everyday daemon used to live, and each `~/.maelstrom/daemons/*` — and kills only what none of
them claims.

A kill goes to the process group and escalates, SIGTERM then SIGKILL after 3 seconds, as "Ending
an agent" describes. The `ps` snapshot is stale by milliseconds; a process that leaves in between
is skipped, not an error. Where the process table cannot be read — inside an agent sandbox
`pgrep` exits 3 and `ps` will not run — the daemon skips the gc and resumes every `running`
record as it always did, and the commands report the failure instead of a verdict. Read as an
empty table, the gc would have written every record off.

What a child does on its own when its daemon dies has not been measured yet (the plan's step 0).
Expected: an idle child exits when its stdin closes, a child mid-turn exits at the end of the
turn, and one blocked on a permission request may wait for ever. That last case is what the gc and
the group kill exist for.

The retained event buffer is still the only history the daemon itself reads. Reading the
transcript back through the normaliser is a follow-up, not built. The daemon keeps only the last
thing the agent said, up to 8000 characters: a row shows one line of it, `show` shows it whole,
and a plan review with no plan in its input falls back to it. The conversation itself is the
session transcript on disk.

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
