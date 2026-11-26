# Maelstrom

Maelstrom orchestrates several Claude Code agents working on one repository at the same time.
This file defines the domain terms that two or more subdomains use. Use these words, in these
meanings, in code and in prose. `docs/README.md` maps the subdomains and their subsystems. A
subdomain with a glossary of its own has it at `docs/specs/<subdomain>/CONTEXT.md`.

## Project and code isolation

**Project**:
One repository maelstrom manages, held as a bare clone at `~/Projects/<name>/.git` and marked
by a `.mael` file. The project name is load-bearing: worktree folders, port allocations, task
ids and session ids all derive from it. The `mael project` group holds the commands that add, create,
rename (`mv`) and list projects.
_Avoid_: Repo, codebase

**Project setup**:
The `setup-project` skill's run on a project. First it makes the dev env start. Then it audits
the project and turns the agreed gaps into a chain of tasks. The **Set up** button in the
Projects tab launches it as a task. See `docs/guide/planning.md`.
_Avoid_: Hygiene audit, onboarding.

**Worktree**:
A git worktree under a project. Twenty-six are named from the NATO phonetic alphabet — alpha
to zulu — and sit at `<project>/<project>-<nato>`. The twenty-seventh is `_main`. A worktree is
a durable slot that outlives the branch it currently holds, which is why it is never named
after a branch.
_Avoid_: Checkout, workdir, workspace

**Worktree config**:
The files maelstrom writes into a worktree. Some are generated and gitignored:
`.claude/CLAUDE.local.md`, `AGENTS.md` and the `.claude/settings.local.json` symlink. The rest
are edits to tracked files: ignore entries in `.gitignore`, the import in `CLAUDE.md`, and the
draft write rules in `.claude/settings.json`. On a feature branch, the tracked edits are committed
as `chore: maelstrom config`.
_Avoid_: Maelstrom files, setup files

**`_main`**:
The one worktree that holds the main branch, so that every NATO worktree stays free for work.
`_main` is the unclosable worktree, and it takes no project prefix in its folder name.
_Avoid_: Reference checkout, main worktree

**Unclosable worktree**:
A worktree that cannot be closed, recycled or removed. `_main` is the only one: it holds the
project's main checkout, and losing it would leave the project with none.

**Recycle**:
Reuse a closed worktree's folder, name and port base for new work. Recycling is why worktree
names and ports stay stable over time.

## Worktree lifecycle

The verbs differ in what they preserve. Close preserves and remove deletes. Trash is a close
that sets unmerged work aside.

**Open**:
Put a branch into a worktree ready to work in: create the worktree, recycle a closed one, or
reuse the one that already holds the branch, then rebase the branch onto `main` before the
session starts. A reused worktree keeps the setup it has, except its `.env`, and its rebase
does not push. A recycled or reused worktree gets its `.env` rebuilt from the project root's
`.env`.
_Avoid_: Set up (a worktree), provision. "Set up" names **Project setup**.

**Close**:
Return a worktree to an empty slot: detach to `origin/main`, free the port allocation, keep the
folder and keep the branch. `close --force` commits outstanding work as `wip: uncommitted
changes` rather than discarding it. `close --discard` removes dirty files but keeps branch
commits and ignored files.

**Remove**:
Delete the worktree folder and free its port allocation. The branch survives. Remove runs the
same teardown as close first — stop the environment, the agents and the sessions — so a removed
worktree leaves nothing of its own running.

**Trash**:
A close that sets unmerged work aside without deleting it: `mael close --trash`. The branch
becomes `trash/<branch>` locally and on origin, and its open PR closes. Outstanding work is
committed as `wip: uncommitted changes` first.
_Avoid_: Archive, abandon, discard

**Shelve**:
A close that goes past the refusals and keeps the work: `mael close --force`, and Shelve in the
orchestrator UI. Outstanding work is committed as `wip: uncommitted changes`, and the branch and
its PR stay. A shelve that went over unmerged work adds a task to reopen the branch.
_Avoid_: Force close (in the UI), archive, park

**Closed**:
The state that makes a worktree available for recycling: detached HEAD, no dirty files, and no
commits ahead of `origin/main`.

**Step**:
One named unit of a worktree mutation, wrapping a model function and returning its lines. Close,
remove and the server's operations are each a list of steps rather than a function spelled out
per caller, so a step cannot go missing from one of them. See `docs/dev/worktree-steps.md`.
_Avoid_: Stage, phase, action

**Step scope**:
What a step must hold alone while it runs: the repo, or one worktree. The repo scope covers the
project's shared `.git`, which a fetch writes; the worktree scope covers one checkout's index and
`HEAD`. Held with a cross-process lock, because a user running `mael sync` in a terminal is a peer
writer. A step needing both takes repo first, always, so no two steps deadlock. Distinct from the
squash **Scope** below, which is an extent rather than a lock.
_Avoid_: Lock, mutex, critical section

**Freshness stamp**:
The mtime of the project's `.git/FETCH_HEAD`: when the last full fetch of origin ended.
`ensure_fetched` skips its fetch when the stamp is younger than the caller's maximum age. A fetch
of named refs runs with `--no-write-fetch-head`, so it does not write the stamp. Distinct from a
cached row's `fetched_at`. See `docs/dev/worktree-steps.md`.
_Avoid_: Last fetched, fetch time

**Dirty file**:
A file `git status` reports as changed in a worktree: staged, unstaged or untracked. Each file in
an untracked directory counts. `.env` is excluded, because maelstrom generates it — a changed
`.env` is not the agent's work.
_Avoid_: Modified file, uncommitted change

**Local commit**:
A commit that exists only on this machine, measured against `origin/<branch>`. A local commit
is work that would be lost with the disk. A branch that is pushed has no local commits, however
far ahead of main it is.
_Avoid_: Unpushed commit, commit ahead

**PR match**:
Whether the worktree's local `HEAD` is the head commit of its open PR: `match` or `differ`.
The PR head is the one GitHub last reported. A branch off the desk keeps the head of the last
read that asked about it, so its verdict can be old. **Dirty files** do not count. A merge is refused while the worktree differs, because the merge would land something
other than the local work.
_Avoid_: In sync, up to date

## Tasks

The Tasks subdomain has its own glossary, `docs/specs/tasks/CONTEXT.md`: Status, Follows, Chain,
Task draft, Task document, Comm and the other terms of its four subsystems. The terms below are
the ones that each subdomain uses.

**Task tracker**:
The subsystem that holds every task maelstrom knows: each current, future and past task, its
status and its order. The tasks are rows in the **State database**. A task's media attachments are
in the **Attachments directory**. The set of all tasks has no name of its own: say "the tasks of
the Task tracker", or "the tasks of a project".
_Avoid_: Task notebook, notebook, backlog, task board. The code still says `notebook`, as in
`MAEL_NOTEBOOK_ROOT` and `notebookId`.

**Task**:
One unit of agent work, stored as one row in the **State database**. A task carries a plan in
its body and launches exactly one Claude session. The row carries the prose, so a task is read
and written whole.
_Avoid_: Ticket, issue, job

**Failover id**:
The rule that fills an id slot when no task holds it. An agent with no task shows its own
agent id where a task would show its id, in the same slot and the same register. The slot is
one field, so a free agent's node and tab name themselves as plainly as a task's do, and
nothing marks a free agent as lacking a task.
_Avoid_: Fallback id, placeholder id

## Sessions

**Session**:
One Claude Code conversation, or one Codex thread. A session maelstrom launches is tied to exactly
one task.

**Live session**:
A session whose `claude` process is currently running, established from the running processes
themselves rather than from any file. Only a live session, or a live agent the orchestrator holds
on the task session id, stops a task from being re-run. A Codex thread runs in no such process.

**Stopped session**:
A session the daemon started whose `claude` process has ended, and which `mael agent resume` or
the node card's Resume can bring back. The Resume menu's **Resume and compact** compacts the
session as its first turn. A stopped session keeps its spawn record, which is what a resume reads, and its
transcript, which says what it was doing. A session started by hand has no record and is not a
stopped session. `mael agent list --stopped` lists them.
_Avoid_: Closed session (a closed worktree is a different thing), ended session, dead session

**Session id**:
The id of the conversation running now, reported by Claude Code as `CLAUDE_CODE_SESSION_ID`. A
`/clear` starts a new conversation and moves the session id, so it cannot key a task. For a driven
agent, the spawn record follows it, and the orchestrator appends it to the task at its next agent
poll. Use the session id to answer "which conversation am I in now?".

**Workspace**:
A cmux workspace named `<project>-<worktree>`. The orchestrator makes it when you open a
worktree's terminal, and it holds one terminal in the worktree. Closing the worktree closes it.
An agent runs in no workspace: you watch it in the orchestrator UI.
_Avoid_: Window, pane group

## Agents

**Model reference**:
A model name in the form `<harness>:<alias>`, such as `claude:opus`,
`codex:terra`, or `opencode:kimi`. A bare value is Claude-compatible. A blank
value resolves to `claude:opus`.

**Execute model**:
The **Model reference** a session switches to when its plan is approved. A session plans on its
model and builds on its execute model. An unset execute model means no switch, which is what
every session did before the field existed. It must name the `claude` harness: the switch is a
`/model` command, and that cannot change which binary is running.
_Avoid_: Child model, next model, build model

**Harness transport**:
The path that starts an agent. `cli` starts the CLI selected by the **Model
reference**. `daemon` starts a driven Claude agent. Daemon agents export
`MAEL_HARNESS_TYPE=daemon` to their children.

**Agent record**:
The canonical row for an agent maelstrom started: its harness, mode, model, and task, stored so
it survives an orchestrator restart. `DaemonRouter` writes one at start, and reads every record
back to restore Codex threads and rebuild `list`. A live subagent has no record of its own; it
rides through on its parent's. An agent live on a daemon with no record is **adopted** by the next
`list`, which writes one for it; `mael agent register` writes one by hand when that has not
happened.
_Avoid_: Agent session, binding.

**Adopted**:
A live top-level agent with no Agent record, given one by the next `list`. `mael add` and
`mael agent start` reach the daemon socket without the router, so their agents arrive with no
record; adoption is what makes them visible to every reader. A subagent is never adopted.
_Avoid_: Claimed, imported, registered.

**Swept**:
Retired because several consecutive `list` calls did not name the agent, rather than because a
`stop` ended it. The record says which, because a `list` revives only a swept one.
_Avoid_: Reaped, culled, timed out.

**Revived**:
An Agent record that read `ended` and is set back to `running`, because its agent turned out to be
alive. Keeps the task and the start the agent really had, which a fresh adoption would lose. A
`list` revives only a **swept** record; a stop is settled until the user resumes the agent.
_Avoid_: Restored, resurrected, reopened.

**Driven agent**:
A `claude` process the agent daemon holds on a stream-json pipe. A `--daemon` launch and every
orchestrator launch make one. A `--cli` launch does not. `mael agent attach <id>` opens a client of
it in a terminal. The daemon owns the pipe, not the terminal: the agent runs whether a client
watches it or not.

**Subagent**:
A driven agent's child, spawned by its `Agent` tool and held by the agent daemon as a stream of
its own under a dotted id: agent `X` has subagents `X.1` and `X.2`, and `X.1.1` is a subagent of
`X.1`. A subagent is read, never driven: it has no process of its own, so its asks are answered
through the parent. The wait itself is the subagent's — its row says what it waits on, and the
parent reports every ask beneath it. `mael agent list` shows it under its parent, `show` and
`tail` take its id, and the orchestrator UI opens it from the parent's session tab.
_Avoid_: Sidechain, child session, sub-agent

**Delegating**:
The state of a driven agent whose turn ended while at least one of its **subagents** still runs.
No turn is open and nobody waits for the user, but the work goes on. A **background shell** does
not count: it gives the state `background`, which delegating outranks.
_Avoid_: Idle, waiting

**Background shell**:
A `Bash` call a driven agent ran with `run_in_background`. It runs on after the call returns, and
often after the turn ends. It has no stream of its own, so nothing opens it. An agent whose turn
ended while one runs, and no **subagent** does, is in the state `background`: the UI reads
"Background tasks working". The shells die with the agent's process.
_Avoid_: job. Claude Code calls it a background task, a word that covers subagents too; only the
UI label "Background tasks working" keeps it.

**Usage window**:
One rolling budget an agent account spends against: the five-hour window and the seven-day
window. Each carries a utilisation and the time it rolls over. The Claude account and the Codex
account each have their own two windows. The reading arrives on a driven agent's stream. So one
account spans every agent of its harness on the machine, and the freshest reading is the
machine's. It only arrives while an agent takes a turn, so a quiet desk holds an ageing one —
a reader that shows it must say how old it is.
_Avoid_: Rate limit, quota, allowance. **Rate limit** in this codebase means GitHub's, which
refuses a poll and is a different thing entirely.

**Budget quotient**:
How a usage window is doing: the fraction of the window still to run, over the fraction of the
budget still unspent. Below one the window resets before the budget runs out. Above one the
budget goes first, and the figure says by how much. It is what colours the top bar's usage
chips.

The first term is wall clock for the five-hour window, which is shorter than the working day it
sits inside. The seven-day window measures it against the **working week** instead: it spans
nights and weekends, and on the clock those hours give the reading back time nobody could have
spent.
The quotient reads the latest reading's spend against the time now, whatever the reading's age.
No driven agent has spent since that reading, or a newer one would have arrived (see **Usage
window**). Other spend on the account, such as claude.ai or another machine, shows only at the
next reading. A reading whose window has reset says nothing about the current window.
_Avoid_: Burn rate, pace percentage. Not **utilisation**, which is the raw spend the source
reports; the quotient is what that spend means against the time the window actually offers.

**Budget schedule**:
The share of a window's budget that its time allows for at each instant, from 0 at the window
start to 1 at the reset. The seven-day schedule follows the **working week**, so it is flat at
night and rises at half slope at the weekend. The **budget quotient** compares the spend with
this schedule at the time now. The Budget chart, opened from a seven-day usage chip, draws it.
_Avoid_: Burn-down, pace line.

**Working week**:
The weighting the seven-day **budget quotient** measures elapsed time against: 8am to 6pm at full
rate on weekdays, the same hours at half rate at the weekend, and nothing overnight. Sixty
weighted hours to the week. The hours are read against a named zone — `Pacific/Auckland` — rather
than the browser's, because a reset is a zone-free instant but "8am" is a wall clock, which exists
only in a zone.
_Avoid_: Business hours, office hours. Both imply a policy about when work is permitted; this is
only a weighting for a reading.

**Context occupancy**:
How full a driven agent's prompt is now, read off the newest `assistant` event on its stream, or
off a **compact boundary**. A level, not a total: each reading replaces the last, so it advances
mid-turn and falls when the agent compacts. It is the number that answers whether to compact, and
the session header and the TUI footer both report it. The **session total** is a different thing:
it sums every turn, re-counting the cached prompt each time, so it runs past any window and says
how much work the session has done.
_Avoid_: Token count, context window usage. Do not call the session total a context size — the
two numbers differ by an order of magnitude on a long session.

**Subagent tokens**:
What a driven agent's subagents consumed, summed, and held on the parent. Disjoint from the
**session total**, which covers the agent's own requests alone: a subagent emits no `result`, so
the host never counted it there. The two sum to the tree's spend, and the parent holds the figure
because an evicted subagent's tokens were still spent.
_Avoid_: Child tokens, nested usage. Not the **session total**, which is the parent's own.

**Compact boundary**:
Where a driven agent's context was compacted, and the only event that says a compact finished. It
carries the occupancy either side of the fall and whether a person asked for it. A compact the
agent refuses — too short a conversation — ends its turn like any other, so nothing else tells a
refusal from a success. The agent may never speak again after compacting, so the boundary is also
where the **context occupancy** falls. The session transcript holds one per compaction, and the
orchestrator UI draws a rule there.
_Avoid_: Compaction event, summary point. A `/clear` is a different thing: it drops the context
and reports nothing.

**Agent daemon**:
The process that holds driven agents and serves the control socket `mael agent` talks to. A
driven agent's live state dies with the daemon, but its spawn record does not, so a later daemon
starts the agent again, however the earlier daemon died.

One daemon per daemon root, and one root per machine by default — so normally one daemon holds
every driven agent. An environment can declare its own on its own root, which is how a worktree
tests a change to the agent protocol without driving the agents its `_main` holds.
`mael-agent-daemon status` names the daemon answering: its root, its process id, its start time,
and the worktree its code came from.

**Daemon root**:
The one directory a daemon owns: its socket, its lock, its pid file, its log, its `audit.jsonl`
and its `agents/` spawn records. `MAEL_AGENT_ROOT` names it, and nothing else does — there is no default. One
daemon per root, enforced by the lock, so a session belongs to exactly one daemon. One owner per
root too. The OS supervisor runs the production daemon as a **Host service** on
`~/.maelstrom/state/daemon`, `mael env start` runs a worktree's, and nothing else starts one.
_Avoid_: Socket directory, spec dir, daemon home, everyday daemon (say production daemon)

**Audit record**:
One JSON line that says who changed what. The **Orchestrator server** writes one for each
state-changing `/api/` request, and one when an **Operation** learns the agent it started. The
**Agent daemon** writes one for each socket command that changes an agent, with the calling
process and its ancestry. They go to `<notebook root>/audit.jsonl` and `<daemon root>/audit.jsonl`.
_Avoid_: Access log, event log

**Wire contract**:
What an agent daemon client sends and reads back: the request payloads, the replies to an ask,
the stream markers, and the shapes of a row and a detail. `agent_wire.py` holds all of it. A
client imports the wire contract and never the daemon's model, so the daemon can change how it
holds agents without changing a client.
_Avoid_: Protocol module, shared types

**Client surface**:
The code a client of the agent daemon imports: the **Wire contract**, the transport that speaks
it, and the harness model. `mael_agent` holds it, so the `mael` CLI and the orchestrator server
reach the daemon without importing `mael_daemon`.
_Avoid_: Client library, SDK

**Orchestrator client**:
The code a `mael` command uses to call the **Orchestrator server**: `orchestrator_client.py`. A
command that uses it never opens the **State database**, so the server is the one writer. The
orchestrator's counterpart of the **Client surface**.
_Avoid_: API wrapper, REST client

**Stray**:
A driven agent's `claude` process that outlived the daemon that held it. Left by a daemon that
died uncleanly; found by the next daemon start or by `mael-agent-daemon gc` through the pid and
the argv session id in its spawn record, killed with its process group, and its record resumed
once. Not an orphan: that
word belongs to the Free agent's `_Avoid_` list and to `mael task reconcile`.
_Avoid_: Orphan, zombie, leftover

**Duplicate**:
A second driven `claude` on a session id a spawn record already owns. Two children on one session
write to one transcript, and each is told the other's turn ended unexpectedly. Killed by the gc;
the record's own child, or the record's resume, is the one that stays.
_Avoid_: Clone, double, second copy

**Permission mode**:
How much a driven agent may do without asking: `plan`, `normal` or `auto`. A task launches under
one mode, and a running agent can be moved between them — by `mael agent set-mode`, by shift+tab
in teleport, by the mode chip in the orchestrator UI, or by the daemon when a plan review is
approved. An approved plan moves the agent to `auto` and clears its context: the plan is settled,
so carrying it out does not need approving edit by edit, and the handover is the whole brief.
A Codex agent has no plan review: the approval of its plan document moves it to `auto` and keeps
its context. On both harnesses, `auto` hands each ask to a classifier (Claude's auto mode or
Codex's auto-reviewer), and the user is not asked.
The three words are the same ones a task carries, so one word means one thing.
Claude spells `normal` as `default` on the pipe; nothing outside `agent_wire.py` uses that word.
The mode is read off the agent's own event stream, never from what was asked for, so no surface
can show a mode the agent refused.
_Avoid_: Permission level, autonomy, trust level

**Handover**:
The user turn a driven agent gets after its plan is approved and its context is cleared. It names
the plan file rather than carrying the plan, and it says the conversation is fresh, so the agent
re-reads instead of assuming it remembers. The plan file is the canonical copy and has no size
limit; a plan review that names no file is denied, because there is no handover to make.
_Avoid_: Handoff, brief, kickoff

**Wait kind**:
Which of three things a driven agent is blocked on: `awaiting-question`, `awaiting-plan-review`,
or `awaiting-permission`. All three arrive as the same `can_use_tool` event, so the wait kind
comes from the tool name. An agent can hold several waits at once, its own and its subagents',
and each is answered on its own; its state names the oldest. The wait kind is what makes an answer possible — it says which of
`answer`, `approve` or `deny` applies. A question takes `answer` or `deny`. A plan review and a
permission take `approve` or `deny`.
_Avoid_: Blocked, stuck

**Decline**:
The UI's word for a `deny` of a question. The agent gets the refusal as the tool result and
continues its turn. **Decline & stop** is an **Interrupt** instead: it denies every open ask and
ends the turn. Either way the question is declined, which is a settled state, not a stale one.
_Avoid_: Skip, dismiss, cancel

**Ask**:
The UI's word for a `deny` of a plan review whose reason is a question. The UI wraps the question
so the agent answers in chat and stays in plan mode. Beside it, the plan review calls its plain `deny` **Change**.
_Avoid_: Query, comment

**Stale prompt**:
A prompt whose wait ended, and which nobody answered through the orchestrator. The tool was
approved in the cmux pane, by `mael agent approve`, or by auto-accept; the host resolves the
request and sends no `control_response`. The user can also interrupt the wait, or the agent can
stop, before any answer arrives. A wait also ends when the agent host stops reporting it. The
normaliser marks the item stale when the wait ends. A stale prompt shows what was asked. It
never offers a decision. A declined question is not stale — see "Decline". A stale plan review takes its plan document to the `stale` status, so
the document's review bar stops offering one too. Stale means
the outcome is unknown, not that the answer was no: a tool approved in the cmux pane went ahead,
and the orchestrator only knows it never saw the answer.
_Avoid_: Abandoned, orphaned, expired

**Interrupt**:
Abandoning the turn an agent is running, and leaving the agent alive to take the next message.
Four surfaces offer it to the user: `mael agent interrupt`, Esc in teleport, the session tab's
**Stop** button, and a question prompt's **Decline & stop**. An interrupt of a waiting agent denies the open ask first, with the
reason `Interrupted by user`; answer or deny the ask instead. This is not **stop**, which ends
the agent's process group and is terminal — the node card labels that act **Terminate**. The
wire says what the daemon says; the UI says what the user means.
_Avoid_: Cancel, abort, kill

**Silent agent**:
A working agent that has said nothing for ten minutes. The node card colours the age of its last
message, because past that point the age is the signal and the message is not. An idle agent is
never silent in this sense — its turn has ended, and its age is not alarming. An idle agent that
left a message is an **Unanswered agent**.
_Avoid_: Stale (a stale prompt is a different thing), stalled, hung

**Unanswered agent**:
An idle agent on a task that is not **Done** or `cancelled`, where the agent has a last message.
The agent ended its turn on unfinished work, so the message is usually for the user. The node
draws as a node with an **Attention item** does, in yellow in place of amber, and the attention
chip counts it apart. Its expanded card takes a reply. The UI derives this state; no attention item exists for it. A free agent, and an agent
that has said nothing, stay idle. An idle agent whose worktree has a **Gate job** is working, not
unanswered: it ended its turn to wait on the gate.
_Avoid_: Comment, Waiting, Blocked

**Agent message**:
One thing a driven agent said, in its own words. Text blocks only — a `thinking` block is
reasoning the agent did not choose to say, and a `tool_use` block is an action. The daemon keeps
only the last message, so `mael agent list` and `mael agent show` answer without reading a file.

**Partial message**:
The text of an Agent message so far, while the agent writes it. The Claude daemon sends it as
chunks, one `stream_event` line each. A Codex thread sends `item/agentMessage/delta`
notifications. Only the session tab draws one, and no tag in it has an effect
until the message is complete.
_Avoid_: Delta, streamed message, draft

**Loaded skill**:
The whole skill file, injected as a user turn when an agent loads a skill. It is not something
the user said, so the transcript folds it under the skill's name rather than showing the file.
The turn opens with `Base directory for this skill:`, which is the only mark the daemon stream
carries.
_Avoid_: Skill message, skill prompt

**External event**:
A user turn the orchestrator server writes to an agent from outside its conversation, in the
`<external-event source=… type=…>` envelope. The source says where it came from: `orchestrator-ui`,
`jig`, `github`, `gate`, `build`, `merge` or `orchestrator`. The type says what happened, such as `document.approved` or
`pr.checks_failed`. The attributes are its facts, and the body is what the agent is to do. The
transcript draws it apart from the user's words, as it does a **Loaded skill** and a **Task
notification**. See `docs/dev/orchestrator-server.md`, "External events".
_Avoid_: notification (that is **Task notification**), nudge, ping, alert, event (alone)

**Shell command**:
A command the agent host runs on the user's behalf, asked for with a `!` line in teleport or in
the orchestrator UI. The host runs it in the agent's working directory, then gives the agent the
command and its output. Maelstrom asks the agent for nothing: a shell command is context, never a
request.
_Avoid_: Bash command (that is the agent's own tool call), local command, bang command

**Spawn record**:
What one driven agent takes to start again: its working directory, its session id, its permission
mode, its model, and the environment it was given. Claude keeps the conversation itself, so the
spawn record holds only the things Claude does not. Its session id follows the agent across a
`/clear`, so a resume continues the conversation the agent is in.
_Avoid_: Agent state, checkpoint, snapshot

**Teleport**:
`mael agent attach <id>` — driving one agent from a terminal UI: a transcript of what it does, a
console to answer it in, a prompt for each wait, and a key that interrupts the running turn.
Teleport is a client of the control socket, not a pane attach: a driven agent has no pane to
attach to. Contrast a tail, which renders the same stream but sends nothing back.

**Tail**:
`mael agent tail <id>` — rendering one driven agent's event stream without driving it. A tail
is read-only by construction: it has no channel back to the agent at all.

**Agent host**:
The agent daemon as the orchestrator server sees it: the thing agents run in and are answered
through. The server sees it only through the `AsyncDaemonClient` boundary: the local control
socket or the in-process Codex adapter. The name says the server has a client's view of it.
_Avoid_: Daemon (in UI-facing prose)

**Remote host**:
A second orchestrator server, with its own `host_name:`, that a **Parent server** names in `hosts:`. It
is a normal server with its own UI, tasks and agents. The parent delegates launches to it and
merges its world. See `docs/dev/remote-hosts.md`.
_Avoid_: Node, worker, slave, runner, child machine (in UI-facing prose)

**Delegated agent**:
An agent that a **Remote host** runs for a **Parent server**, whose task stays on the parent. Its Agent
record keeps the parent's URL and the task id, and its env has `MAEL_PARENT_URL`. Its task work
and its `mael` task commands go to the parent.
_Avoid_: Remote agent (for the direct agents of a remote host), runner agent

**Parent server**:
The orchestrator server that delegated a launch and holds the agent's task. It is a fact about
one agent, not about a server: one remote host can run agents for several parents and its own
direct agents. Not the task **Parent**, which groups a chain.
_Avoid_: Master, primary

**Child mode**:
The planned mode of a server with `parent:` in its config. A child will hold no task, comm or
document state, serve no UI, and send every state call to its **Parent server**'s API. See
`docs/adr/0005-child-mode.md`.
_Avoid_: Worker mode, headless host

**Task home**:
Where the task work of one agent or worktree goes: this server's Task tracker, or the **Parent
server**'s Task tracker for a **Delegated agent**. A worktree's task home is that of its newest
delegated agent, else this server's Task tracker. A Shelve writes its reopen task there.
_Avoid_: Task server, task target

**Id prefix**:
A server's `host_name:` and `:` at the head of each agent, worktree, gate job and operation id it
makes: `cobalt:<agent id>`, `cobalt:<project>-<nato>`, `cobalt:op12`. A server with no `host_name:` makes bare ids. A
parent finds the owner of each merged id from its prefix. A desk id such as `agent:cobalt:x` is
not read for one.
_Avoid_: Host id, namespace

**Remote client**:
A browser on a **Remote host** of the server whose UI it shows. Its `cmux` control opens a terminal
in the remote host's own cmux: an SSH shell for a worktree another host owns. See
`docs/dev/cmux.md`.
_Avoid_: Remote browser, remote user

**SSH target**:
A worktree's `user@host:/path`: the user the server runs as, the server's `dev_host:`, and the
worktree path. A **Remote client**'s terminal opens an SSH shell on it. `""` when the server has
no `dev_host:`.
_Avoid_: SSH URL, SSH address

**Orchestrator server**:
The process that builds the world from the Task tracker, `list-all` and the agent host, and serves
it to the orchestrator UI over HTTP: resources by REST, change notices on one stream, one socket
per open agent transcript. `mael-orchestrator serve` runs it.
_Avoid_: Backend, API server

**Passkey**:
A WebAuthn credential on one device that signs a browser in to one orchestrator server. A
server has one implicit user, and each passkey is one device of that user. It is pending or
verified.
_Avoid_: Account, login, user

**Pending passkey**:
A **Passkey** that has signed up and has no access yet. It waits for `mael auth verify <code>`
on the host, which makes it verified. Only the **Host key** can verify, so only a person with a
shell on the host admits a device.
_Avoid_: Unapproved device, guest

**Verify code**:
The short code, such as `K7QF-2M9D`, that the sign-up screen shows for a **Pending passkey**.
`mael auth verify` names it, so the host admits that passkey and no other.
_Avoid_: PIN, OTP, token

**Sign-in**:
A browser's access to an orchestrator server: a cookie that names one **Passkey**, kept 30 days.
A revoked passkey ends its sign-ins.
_Avoid_: Session, login session (a **Session** is a Claude conversation)

**API key**:
A secret a non-browser client carries to the orchestrator server, as a bearer token. The server
keeps only its SHA-256. It is a **Host key** or a **Delegate key**.
_Avoid_: Orchestrator token, server token (both name the GitHub token), API token

**Host key**:
The **API key** of scope `host`. It opens each route. The server mints it at start, into
`<notebook root>/auth/host.key`. The CLI carries it. A parent names each
**Remote host**'s host key as `token:` in `hosts:`.
_Avoid_: Admin key, root key

**Delegate key**:
An **API key** of scope `delegate`, minted by a parent for one delegated launch. It opens only
`/api/tasks`, `/api/task-documents` and the operation reads. The **Delegated agent** carries it as `MAEL_PARENT_KEY`.
Ending the agent's worktree revokes it.
_Avoid_: Parent token, launch token

**UI origin**:
An origin that may use a **Sign-in**: the server's own, and each `--ui-origin`. A request that
carries the cookie and writes, or opens a socket, must come from one.
_Avoid_: Allowed host, CORS origin

**Change notice**:
One message on the orchestrator server's event stream saying which entities of one kind changed,
by id, and nothing else. The UI refetches what it shows and finds each id present or gone.
_Avoid_: Event, update, push, delta

**Epoch**:
A name for one life of the thing that mints it, so a cursor from before it is refused. The
agent daemon mints one per `start` and per `resume`, and carries it on the backlog marker. The
orchestrator server mints one at start, and carries it on the change stream's `reset`. The two
are unrelated names on unrelated streams.
_Avoid_: Generation, run id, session

**Operation**:
One slow change to the world that the orchestrator server runs in the background. Its kinds are
the worktree changes (close, shelve, trash, delete, sync, merge, environment), the agent changes
(**Dismiss**, terminate, resume), and the launch kinds (launch a task, start a free agent, create
a task, plan a Linear issue). It is a list of **Steps**, each with a state and log lines. It ends
`done`, **Refused** or `failed`. The state database keeps it, so a reload loses nothing.
_Avoid_: Action (a **Step** avoids it), job (a **Task** avoids it), activity, background task

**Refused**:
How an **Operation** ends when a **Step** said no on purpose, such as a close over unmerged
commits. A refusal is expected, and the user chooses the next act. Distinct from `failed`, which
is a fault: a step raised an error.
_Avoid_: Blocked (the step's own outcome field), rejected, error

**Unseen**:
An **Operation** that ended **Refused** or `failed`, and that the user has not yet looked at. It
stays in the **Console** strip, and marks the node and worktree it acts on, until the user
selects it in the **Console** or opens the card of that node or worktree. The record stays in
the history.
_Avoid_: Unread, new, unacknowledged

**Console**:
The drawer at the foot of the orchestrator UI that lists **Operations**: a one-line strip of the
running and **Unseen** ones when closed, and the history with each log by step when open.
_Avoid_: Log panel, activity feed, drawer (alone), toast

**Welcome dialog**:
The orchestrator UI dialog that shows where this install keeps its projects, skills and config,
whether **minnstack** is installed, its version, the last **Update job**, and whether the agent
host answers. On the narrow layout it also holds the usage and gates readings. It opens by itself
on a world with no **Project**, once per page load, and the mark opens it at any time. Its actions start Add project and an **Update job**.
_Avoid_: Onboarding, splash, about box

**Project dialog**:
The orchestrator UI dialog for one **Project**. Its Overview tab shows where `_main` and the
worktrees are on disk. Its Worktrees tab holds the worktree table, the one surface that lists a
closed worktree. Add project opens it when a clone ends.
_Avoid_: Project detail, project page, Worktrees view

## Dev environments

**Environment**:
The running services for one worktree. An environment exists only while its services run — at
most one per worktree, and none once every process is dead. This is the only meaning of "env"
as a noun; a set of shell variables is a *service environment*.
_Avoid_: Env (as a standalone noun for the running services)

**Service**:
One process maelstrom spawns for a worktree, declared in `.maelstrom.yaml`. A service that
declares an `engine` is a container service; every other service is a command service.

**Service version**:
The hash of the **SHA bucket** named after the service, recorded when the service starts. It
covers the committed files the service runs, so it moves only when a commit changes them. A
service with no SHA bucket of its name has no version.
_Avoid_: service hash, build id

**SHA bucket**:
A tangier tag in `pipeline.toml` with `sha = true`. Its hash covers its own paths and the paths of
the tags it `depends` on. Distinct from a **Bucket**, which holds a task's attachments.
_Avoid_: bucket (alone)

**Shared service**:
A service marked `shared: true`, started once for the whole project rather than once per
worktree. A database is the usual case. The project owns shared services; worktrees subscribe
to them, and they stop when the last subscriber leaves. A shared service is a project-level
singleton, so it counts as running when it runs for any worktree. A start brings up the
shared services that are not running, whenever they were declared.

**Optional service**:
A service marked `optional: true`. `mael env start` skips an optional service; `mael env start
<name>` starts that one service and leaves the rest of the environment alone. An optional
service still owns its declared ports, so marking a service optional never renumbers the
services after it. A service cannot be both optional and shared.

**Env state**:
The condition of a worktree's environment, taken over its core services: every declared service
that is not optional, shared ones included. `running` when every core service is alive,
`stopped` when none is, `partial` otherwise. An optional service never changes the env state.
_Avoid_: App running

**Web-facing service**:
A service with a port whose name holds `APP` or `FRONTEND` as a `_`-separated segment. A running
web-facing service gets a dev env link in the orchestrator.

**Dev host**:
The host name in every dev environment URL that maelstrom reports. The global `dev_host:` key
sets it for the machine, and it is `localhost` without the key. Each worktree's `.env` carries
it as `DEV_HOST`. Port probes do not use it: they stay on `127.0.0.1`.

**Dev certificate**:
The TLS certificate for the dev host that Tailscale issues, kept in `~/.maelstrom/certs`. The
global `dev_https:` key turns it on, and it needs a dev host. `mael env start` renews it, and
each worktree's `.env` names its files as `DEV_TLS_CERT` and `DEV_TLS_KEY`. Each app ends TLS
itself; maelstrom runs no proxy.

**Subscriber**:
A worktree currently using a project's shared services. Shared services stop when the
subscriber list empties.

**Port base**:
The number a worktree owns. Each service port is `port_base * 10 + index`, so two worktrees
never collide. The pseudo-worktree `_shared` holds the project's shared port base.

**Floating base**:
A port base the allocator picks, from the pool of 3-digit numbers 300 to 999. Every NATO
worktree has one, and a recycled worktree keeps the base it had.

**Reserved base**:
The port base `_main` owns, declared by the project and outside the floating pool. A reserved
base returns the same ports every time, and no NATO worktree can be given it.

**Fixed environment**:
The environment of `_main`, on a reserved base — the one instance that is always at the same
address, whatever a NATO worktree happens to be running. A project opts in by declaring the
reserved base; a project that declares none gives `_main` no ports and no environment.

## Quality checks

**Gate**:
An automated check that blocks work when it fails — the project's tests, lint and type check.
A gate that cannot fail is not a gate. A point where a human approves something is a
*checkpoint*, not a gate.
_Avoid_: Gate (for a human approval step)

**Pre-push check**:
The project's `pre_push_cmd`: a gate that runs after the last rebase and before a push, so it
sees the commit the push publishes. Maelstrom only runs it; what it verifies is the project's.
_Avoid_: pre-push hook (git's own client-side hook), pre-push gates

**Pre-PR gate**:
Maelstrom's own `tangier gate run lint test web jig --fail-fast`, run by hand on the committed tree
before a PR. It is not a pre-push check: no command runs it for you. When maelstrom moves to
`merge: local`, it becomes `tangier gate run @pre-merge --direct`, the light **Gate set**, run
before the **Steer review** and before submit.
_Avoid_: pre-push check (for this command)

**Gate set**:
A named list of gates in `pipeline.toml`, run as `tangier gate run @<name>`. Each gate in it can
have its own `accept` rule. `@pre-merge` is the light set the agent runs. `@merge` is the full
set the **Build queue** runs.
_Avoid_: gate group (a group is one gate with members), gate list, profile

**Gate job**:
One `tangier gate run` invocation that runs one or more gates on one worktree. A gate job is
queued or running: tangier runs four at a time on one machine, and the rest wait. The Gates chip
counts them.
_Avoid_: gate run (for the job)

**Gate lozenge**:
The pill on a node whose worktree has a **Gate job**. It names the gate that runs now, or the
job's place in the queue: `queued 2`. A click opens the Gate jobs dialog on that job.
_Avoid_: gate badge, gate chip (the Gates chip is the count of gate jobs)

**Gate watch**:
The orchestrator's one `tangier gate status --watch` process. It keeps the world's gate jobs, and
tells each agent in a gate job's worktree when the job ends, with a `gate` **External event**.
_Avoid_: gate poll

**Build queue**:
The orchestrator server's service that runs a **Queue build** for each submit to a **Merge
request**, in a **Build worktree**, in a `merge: local` project. It gates each submit, and
merges a `passed` Merge request when the user clicks Merge. It never rebases or commits on a
branch. It replaces CI, not the gates. See `docs/dev/build-queue.md`.
_Avoid_: merge queue (it does not order merges), CI, pipeline

**Merge request**:
A task branch's request to merge into `main`, as a PR is, in a `merge: local` project. It holds
the head SHA and base of the last `mael merge submit`, and links the newest **Queue build**. Its
states are `open`, `withdrawn` and `merged`. A submit updates an `open` or `withdrawn` one and
reopens it; a submit after `merged` opens a new one.
Its description is the `.drafts/pr.md` of a submit, which the Merge confirm shows.
Its status comes from it and its linked Queue build: `queued`, `building`, `passed`, `stale`,
`failed`, `withdrawn` or `merged`. A `passed` one turns `stale` when `main` moves. A `passed`
Merge request draws the Merge button. The Merge pushes its SHA to `main` and makes it `merged`.
_Avoid_: Candidate, Submission, PR (in a `merge: local` project)

**Queue build**:
One gate run of a **Merge request**'s SHA in a **Build worktree**. Its states are `queued`,
`running`, `passed`, `failed`, `stale` and `cancelled`. It ends `stale` when `main` moved since
the submit, and no gate runs. A Merge request has many; only its newest counts. Called `Build`
in the code: the glossary's **Build** is the phase.
_Avoid_: Build (the phase), job (tangier's), Candidate

**Build worktree**:
A worktree in the **Build queue**'s pool, `_build1` … `_buildN`, that checks out a **Queue
build**'s SHA, detached, to gate it. Like `_main`, it never closes. It has its own `PORT_BASE`
and `.env`. It is not in the world: the running Queue build's **Gate lozenge** shows on the
Merge request's own worktree node. `build_slots:` sets N.
_Avoid_: Build slot (the count), runner, CI worktree

**Chaperone**:
The agent of a **Merge request**'s task. It gets every `build.*` and `merge.*` **External event**
for the Merge request, and does all git work on its branch. The orchestrator resumes the task's
last agent when an event is due and the chaperone is not live.
_Avoid_: Owner, watcher

**Machine metrics**:
The machine's CPU, memory and CPU temperature, with the usage of each running **Gate job**. The
orchestrator server samples them every 15 s and keeps the last 15 minutes in memory. The
**Welcome dialog** charts them.
_Avoid_: system stats, host metrics (the agent host is a separate thing)

**Checkpoint**:
A point where the user approves, answers or decides something before the work continues: a plan
review, a question, a permission, a document review. A checkpoint is where a human steps in; a
gate is automated.
_Avoid_: Gate, approval step

**Steer review**:
A **Checkpoint** in a `merge: local` project, right after the `built` milestone. The agent
presents the branch for a rough pass over structure and behaviour, and waits. It comes before
`/code-review` and `/present`, so a redirect wastes few tokens.
_Avoid_: Draft review, early review, pre-review

**Security requirements**:
A project's `docs/security/requirements.md`: its assets, roles, authentication, trust
boundaries, ranked top risks, and accepted risks. `/security-test` writes it with the user, and
ranks its triage by the top risks.
_Avoid_: threat model (the `claude-security` plugin's in-memory model), security policy

**DAST scope**:
A project's `docs/security/dast-scope.md`: the rules of engagement for a dynamic application
security test (DAST). It names the target services, the test account of each role, and the paths
that must not be scanned actively. The target is always the worktree's own dev environment.
_Avoid_: scan config (the ZAP plan is generated from it)

**Owning suite**:
The one test suite that tests a contract in full: the suite of the package that holds the rule.
`docs/dev/testing.md` lists which suite owns which contract.
_Avoid_: canonical tests

**Contract rule**:
One clause of the contract of a command or a query: what it requires of its caller, what it
ensures, or what always holds for its **Domain type**. It is a `Rule` in a **Feature file** and
has a stable id. A plan lists the contract rules
that its work adds, changes, or withdraws, under **Contract changes**. A test is worth having
when it proves one.
_Avoid_: obligation, promise, guarantee, requirement

**Domain type**:
A type that a glossary names: an entity, a value object, or an aggregate. It has invariants,
commands and queries. A **Subsystem** holds one or more.
_Avoid_: state owner, model class

**Primary seam**:
The one interface in the code where the contract of a command or a query holds in full: most
often a method of the **Domain type**. The tests of each **Contract rule** call it. An outer
seam, such as a handler, a CLI command or a page, reaches it from outside and has no contract of
its own.
_Avoid_: owning layer, driving port, entry point

**Feature file**:
A Gherkin file in `docs/specs/<subdomain>/<subsystem>/`. It holds the contract of one command or query, or
the invariants of one **Domain type**, as **Contract rules** with examples.
_Avoid_: spec file, scenario file

**Subdomain**:
A part of what maelstrom does, with one language: Tasks, Code, Delivery, Agents, Desk, Method or
Platform. It groups **Subsystems** and has one glossary, `docs/specs/<subdomain>/CONTEXT.md`.
`docs/README.md` lists them. Maelstrom as a whole is the domain.
_Avoid_: domain (for a part), area, bounded context

**Subsystem**:
A slice of behaviour in one **Subdomain**, with its own **Domain types** and **Feature files**.
It owns its code at each layer: model, CLI, routes, UI and skills. A subsystem of the Platform
subdomain is infrastructure: the frame of one layer, or a service that each other subsystem uses.
_Avoid_: bounded context, component, module

**Suite outline**:
The test names of a module's **Owning suite**, without the bodies. A plan reads it to find the
tests of the current **Contract rules**.
_Avoid_: test list, test inventory

**Outer layer**:
A layer that reaches a contract through another layer: a CLI command, a server route, or a
command that goes over the API. `docs/dev/testing.md` says what an outer layer tests.
_Avoid_: wrapper tests

## Pull requests

**Landing**:
A task's steps after the build: `done → merged → uat → live`. Each of these is a **Landing
step**. The first step, `done`, is reached when the task is `built`; it is not **Done**. The
landing is derived, never stored on the task. It joins the task's **Registered PRs** to GitHub's
data about each PR, which the orchestrator server reads on its worktree poll. `merged` needs
every PR merged, and a deploy step needs every PR landed there.
- `merged` comes from the PR's merge commit. `uat` and `live` come from the deploys to the
  GitHub environment that `deploy.environments` names for each step.
- An environment's state is `landed`, `not_yet` or `unknown`. A failed read is `unknown`, and
  `unknown` never counts as reached.
- In a `merge: local` project, `merged` comes from the **Build queue**'s record of the
  **Merge request**'s merge, not from a PR.
- A project with no `deploy:` block stops at `merged`.
- The server records each landing step the first time a task reaches it, with the time it
  recorded it.
- When a `built` task's landing reaches `merged`, the server moves the task to Status `merged`.
- A landing belongs to one task. There is no chain landing.
_Avoid_: Follow-through (it clashes with Follows), ladder, pipeline, stage, phase, milestone

**Orchestrator token**:
The GitHub token the orchestrator server uses for UI actions that write to GitHub. One token
serves every project. Set as `github.orchestrator_token`, read from `config.yaml` only.
_Avoid_: Merge token, server token

**Agent token**:
The `GITHUB_TOKEN` a project's `.env` gives every service and agent. It may lack merge rights,
and may reach only its own project.
_Avoid_: Narrow token, inherited credentials

**Working history**:
The ref `refs/mael/history/<branch>/<stamp>`, holding the chronological commits that
`mael git squash-branch` or `mael git uncommit-branch` collapsed. The working history is the order
the work happened in, kept so `git log` can still show the journey and so the collapse can be
undone. One ref per run, stamped in UTC, so a second run keeps the first run's chronology. Review
and present each write one.
_Avoid_: Backup branch, history branch, archive ref

**Squash**:
To collapse a branch's commits into one, leaving it committed. `mael git squash-branch` saves the
working history, collapses the commits, then rebases onto `origin/main`. Review runs it to put the
branch's final state in one commit a reviewer reads whole.
_Avoid_: Fold, flatten, uncommit (which resets the commit into the working tree)

**Scope**:
How much of a branch a squash or an uncommit takes in. Distinct from a **Step scope**, which is a
lock rather than an extent. The whole branch (`--remote`, the default),
or only the commits that were never pushed (`--local`). A re-review of a branch whose PR is open
uses `--local`, so it reads the new work alone and the already-reviewed commits keep their own
subjects.
_Avoid_: Range, extent, depth

**Uncommit**:
To return a branch to unstaged changes at its fork point from `main`. `mael git uncommit-branch` squashes the
branch, then resets that commit into the working tree. Nothing is discarded — every change is in
the working tree, ready to be committed again. Present runs it to re-cut a reviewed tree.
_Avoid_: Reset, unwind

**Story commit**:
One commit per design decision, holding the decision's rationale in its body. A reviewer reads
story commits in order on the PR's Commits tab. They replace the chronological commits of the
build, so a story commit says *why*, never *when*.
_Avoid_: Logical commit, atomic commit, curated commit

**Present**:
To squash a branch's commits into a working history, then re-cut the same final diff into story
commits. Present runs once per task, after the branch's own review, and never during land. The
tree is the invariant: a wrong partition gives a wrong story, never a wrong tree.
_Avoid_: Curate, reorganise, tidy

## Orchestrator UI

**Held text**:
What the user typed into a surface of the orchestrator UI and has not submitted, kept in the
browser so that closing a dialog, switching a tab or reloading the page does not lose it. Held
text is browser-local and per surface. It is cleared when the work is submitted, and where a
surface offers a control to clear it — never when the surface closes. View state — the open tabs,
the filters, the expanded node — is not held.
_Avoid_: Draft (a task file in `.drafts/`), autosave, cache, unsaved changes

**Note**:
What an agent says it is doing now, written as `<note>Rebasing onto main</note>` in the text of an
ordinary message. A field rather than a document: it is cut from the message, and the latest one
replaces the one before it. The expanded card shows it in place of the agent's last message, which
is whatever prose happened to end a turn. A message carrying no note leaves the standing one
alone, and a subagent writes none. The card still dates its block from the last message, because
silence means the agent said nothing, and a note is not speech. One of the markers, with the
**Document tag**, the **Image tag**, the **Milestone**, the **PR link tag** and `<live-feedback>`.
_Avoid_: Status, Progress (that is a node's state), Activity

**Milestone**:
A stage of the work an agent marks as reached, written as `<milestone>built</milestone>` in the
text of an ordinary message. The vocabulary is the task-completion flow: `planned`, `built`,
`reviewed` and `presented`. A name outside it is recorded as the agent wrote it
and flagged, never dropped. The marker is cut from the message and writes a **milestone
snapshot**; the latest one in a message wins, as a note does. `planned` is Maelstrom's own: it is
written when the user approves a plan, because the approval clears the agent before it could
write one. One of the markers, with the
**Document tag**, the **Image tag**, the **Note**, the **PR link tag** and `<live-feedback>`. A subagent writes none.
_Avoid_: Checkpoint, phase, stage marker. A **phase** is a task's own, and a checkpoint is where
a document awaits review.

**Compaction request**:
An agent's request to compact its own session, written as `<compact-session min-tokens="200k">`
at the end of an ordinary message. The agent cannot compact itself, but it knows where the break
points are. When the turn ends, the orchestrator compacts the session if its **context occupancy**
is at least `min-tokens`. A body becomes the compact's focus instructions. The orchestrator always
replies, so an unattended agent continues. See `docs/dev/orchestrator-server.md`, "A compaction
request".
_Avoid_: Auto-compact (that is the harness's own), self-compact

**Agent queue**:
The orchestrator's ordered steps for one agent: `say`, `compact`, `resume` and `stop`. A
**Compaction request** and an **End-session request** add a step that waits for the turn to end.
See `docs/dev/orchestrator-server.md`, "The agent queue".
_Avoid_: Chain (a task chain), Build queue (the `merge: local` queue), hold

**Compaction hold**:
A `compact` step at the head of the **Agent queue**. The turns the orchestrator tells the agent
while it compacts, **External events** and jig instructions, wait behind it. It comes from a
**Compaction request**, a `/compact` the user sends, or Resume and compact. When the compact
settles, the queued turns go to the agent in order. See `docs/dev/orchestrator-server.md`, "The
compaction hold".
_Avoid_: Event buffer, mute

**End-session request**:
An agent's request to end its own session, written as `<end-session>` at the end of an ordinary
message. When the turn ends, a `stop` step on the **Agent queue** runs a **Terminate** on the
agent. The session stays resumable, and its task stays open. See
`docs/dev/orchestrator-server.md`, "An end-session request".
_Avoid_: session end (the removed command)

**Conditional end**:
An **End-session request** that waits for an **External event**, written as
`<end-session on="pr.merged">`. The agent writes it after it pushes its PR, when it waits only for
the merge. The orchestrator stores it per agent. When the PR watch tells `pr.merged` to that agent,
the orchestrator ends the session at the end of that turn. A stop, an exit, or a user message
clears it, so a session the user resumes or talks to does not end on the merge. See
`docs/dev/orchestrator-server.md`, "An end-session request".
_Avoid_: end gate, deferred end

**Milestone snapshot**:
What a driven agent had spent when it reached a milestone: its own tokens, its subagents' tokens,
and the host's dollar figure, with the delta since the milestone before it. The deltas sum to the
total, which is what makes the record answer which stage the spend went to. Written to the
`agent_milestones` table, so it outlives the agent. Read by `mael agent cost` and by the
orchestrator UI, which draws it in the session panel and on the expanded node card.
_Avoid_: Checkpoint, usage record. Not a **usage window**, which is the account's budget rather
than one agent's spend.

**Closing row**:
The milestone snapshot Maelstrom writes as an agent exits, named `<final>`, holding what the
agent spent after its last stage — `/present`, the PR push, the CI watch. Without it that spend
is unaccounted and the deltas no longer sum to the total. Maelstrom's own row, so it is never
flagged as an unknown name, and the brackets are syntax no marker can carry, so an agent cannot
write one. An agent that spent nothing since its last stage gets none.
_Avoid_: Final milestone, shipped. A **milestone** is a stage the agent declares; this one closes
the ledger instead.

**User attention**:
The rank an agent sets with `<user-attention high>` or `<user-attention low>` in an ordinary
message. High prose is for the user to read. Low prose is working detail. The renderer draws low
prose at the low rank. The tag stays in the message until the renderer reads it, so it is not
one of the markers. Low prose holds markdown, including a literal or a link. Nothing on the
server parses it, except that node summaries remove the tag.
_Avoid_: Callout, Highlight, Summary, Important, Attention tag

**Task notification**:
What the harness injects to say background work finished. A subagent's arrives as a `system` event
and ends the subagent, drawing no transcript line of its own. A background command's arrives as a
user turn, which is that command's only trace, so the transcript folds it to one line carrying its
status and summary and drops the ids and the output path it also carries — addresses of files on
the agent's host, which the reader cannot open.
_Avoid_: Task update, background result, completion message

**Change comment**:
Feedback on a range of lines in the **Changes tab**. A change comment names its rev, its file and
its lines, and it quotes the lines as the user saw them. The comments of one worktree are **Held
text** until the user posts them. One post sends them all, as one message, to every top-level
agent in the worktree that has not exited.
_Avoid_: Review comment, line note, inline comment, feedback (alone)

**Jig**:
A dev-only overlay that a Vite plugin can put on every page of a worktree's dev server: the app
and each Ladle story. It is a **Live feedback** client with one **Live feedback param**, `jig`. It
shows only while that param is on: an agent of the worktree turns it on, or the user turns on its
switch in the **Dev env tab**. Hidden, it puts nothing on the page. It carries changes into the page and **Feedback** out to the agent: a
**Monkeypatch**, an **Instruction**, a request for a **Control set** for the **Pick**, or the values of
the **Controls**. Each goes with a screenshot of the viewport when the capture works.
_Avoid_: Tuner, tuning jig, inspector, devtools panel

**Live feedback**:
The two-way channel between the agents of a worktree and a client in one of its dev servers, such
as the **Jig**. The client declares **Live feedback params**, and either side sets them. The
client sends **Feedback** to the agents. The jig is one client, so the channel, its socket, its
API and the world name nothing of the jig.
_Avoid_: Jig protocol, jig channel, feedback channel

**Live feedback provider**:
The service that a client connects to for **Live feedback**, over one socket at
`MAEL_FEEDBACK_URL`. That URL names the client's worktree and dev server, so the client dials it
as it is and sends no identity of its own. The provider keeps each dev server's params, and takes
**Feedback** to the agents. The orchestrator that `orchestrator_url:` names is the one provider so
far. An MCP server could be another.
_Avoid_: Jig provider, jig server, jig backend

**Live feedback param**:
A named value that a **Live feedback** client declares, such as the **Jig**'s `jig`. Each dev
server keeps its own value, and the value outlives the client's socket. `boolean` is the one type.
The **Dev env tab** draws a switch for each, named by its label. Every change needs an agent in
the worktree, and the agents hear of each change the user makes. When the last agent leaves,
every param turns off. An agent sets one with `<live-feedback jig="on">`, or on one dev server
with `<live-feedback service="web" jig="off">`. That marker is one of the markers with the
**Document tag**, the **Image tag**, the **Note**, the **Milestone** and the **PR link tag**.
_Avoid_: Capability, flag, toggle

**Feedback**:
A typed message from a **Live feedback** client, such as the **Jig**, to the agents of a worktree. One
send delivers it, as one message, to every top-level agent in the worktree that has not exited.
Its `type` names what it carries: `monkeypatch` for a **Monkeypatch**, `instruction` for an
**Instruction**, `pick` for a request for a **Control set** for the **Pick**, and `controls` for the
values the user set on each **Control**. When the user sets a **Live feedback param**, the agents get a message that
says so in the same way.
_Avoid_: Tuning, report, event

**Monkeypatch**:
The CSS that the user writes in the **Jig**. It is kept in `.drafts/monkeypatch.css` in the
worktree, not in the browser, so every page of the worktree applies the same text. Send delivers
it as **Feedback**. The agent applies it in source and deletes the file. The agent also writes
the wiring of each **Control** there.
_Avoid_: Tuning CSS, overrides file, tweaks

**Pick**:
The element the user clicks in the **Jig**'s pick mode, and the act of clicking it. The click goes
to the jig, not the app. The jig describes the element by a selector, its React components and
its computed styles. The pick stays until the user clears it or picks again.
_Avoid_: Inspect, select

**Instruction**:
Free text that the user writes to the agents in the **Jig**'s prompt box. It goes as
**Feedback** of type `instruction`, with the **Pick**'s description when there is one.
_Avoid_: Prompt, command, chat message

**Control set**:
The **Controls** an agent writes to `.drafts/jig-controls.json` when a **Pick** asks for them.
The **Jig** draws them on its Controls tab. The jig only reads the file.
_Avoid_: Tuning panel, knobs

**Control**:
One CSS variable that the user sets in the **Jig**, with a slider, a number, a colour, a choice or
text. The agent wires the variable in the **Monkeypatch**, so one control can drive several
properties. The values are kept in `.drafts/jig-values.json`, so every page shares them.

**Attention item**:
One thing waiting on the user: a wait kind, a document awaiting review, an exited agent. Raised
and cleared by the backend, never inferred by the UI. An **Unanswered agent** is not one.

**Desk**:
The sticky record of what the canvas draws: tasks, and free agents. The server adds an entry for
every agent it sees start, and the entry outlives the agent, so stopped work stays on the
canvas until the user takes it **Off desk**. A restart rebuilds the agents, so an entry naming an agent
that is gone is dropped as the desk loads. Each entry names its kind — `task:<project>/<notebook id>`
or `agent:<agent id>`. The desk is tracked apart from the Task tracker and is not a status, so a
task stays on the desk whatever its status. A task joins the desk by **On desk**, and a node leaves it by **Off desk**. There is
one desk today, and one per user later.
_Avoid_: Workspace, board, pinned

**On desk**:
Putting a task on the desk. A button says "On desk"; a label that joins it to another act uses
the verb form, "put on desk". The task list row, its bulk bar and the node card's follows rows
offer it. In a task list row and a follows row it is a toggle: it always says "On desk", and a
square on the desk icon shows the task is on the desk. A created task is on the desk already:
every task the orchestrator server writes joins it, whether the UI, `mael task add`, `promote`
or `load-many` asked for it.
_Avoid_: Add to desk, pin

**Off desk**:
Taking a node off the desk. Off desk never stops an agent; **stop** does, and the node card
offers both in one control, behind its **Dismiss** button — see `docs/dev/orchestrator-ui.md`. A
menu item or a list button says "Off desk"; a label that joins it to another act uses the verb
form, "take off desk", as in "…and take off desk" after Terminate.
_Avoid_: Remove from desk, Hide, archive

**Dismiss**:
The usual end of work on a node: **stop** its agent, take it **off desk**, and close its
worktree when it can close and the node is its last **Holder**. It is the button of the
node card's end-of-work control. The server runs it as one `dismiss` **Operation**. Its chain is
`terminate` (stop the agent, then off desk) or a worktree end — `close`, `shelve`, `trash` or
`delete` — then off desk. A close refuses a dirty tree or unmerged commits, so Dismiss is
**Refused** on a branch whose PR is not merged yet, and the node stays on the desk. A Dismiss
from the UI closes the node's session and document tabs, and on a worktree end every tab of the
**Worktree group**.
_Avoid_: Done, Finish, Remove

**Holder**:
Work that still needs a worktree, so a **Dismiss** of another node keeps the worktree open: a live
top-level agent in the worktree, an open task on it (`todo` or `in-progress`), or a desk
node whose work is in it, whatever its status. A task is on the worktree where its agent ran last,
else, or when that worktree is closed, on the open worktree of its branch. A bulk Dismiss does not
count the holders in its **Selection**.
_Avoid_: Owner, user, lock

**Selection**:
The nodes of the Desk canvas the user has Shift-clicked, for one **Bulk bar** action. Task and
free-agent nodes join it; an **Opening node** and a worktree box do not. A plain click, Esc, a
click on the canvas or Clear empties it. It is view state, lost on a reload, and holds drawn nodes
only: a node a filter hides leaves it.
_Avoid_: Grid, multi-select

**Bulk bar**:
The bar that applies one action to many items: the ticked task list rows, or the canvas
**Selection** of two or more nodes. Each action runs on the items it fits and skips the rest. A
refused item stays picked.
_Avoid_: Ribbon, batch bar

**Opening node**:
A node that stands for work an **Operation** is starting before its agent or task exists: a
free agent's start, or a Linear plan. It draws in its project's lane, hatched, with the
operation's words and step meter. The node of the agent or task the operation makes takes its
place.
_Avoid_: Placeholder node, pending node, ghost node

**Active branch**:
A branch with a desk entry at it — a task through the Task tracker, an agent through its worktree.
The worktree poll asks GitHub about active branches only, because GraphQL is charged by node
count against a budget that refills hourly. A branch off the desk keeps the pull request the last
read saw, rather than reading as having none.
_Avoid_: Watched branch, live branch

**Free agent**:
An agent with no task. A launch pins a task session id on the agent, so an agent that carries
none matches no task. A free agent is started by hand in a worktree, or from the orchestrator
UI: from the new-work form, or from a **Worktree card**, which opens that form on the worktree's
branch. A free agent takes its name, branch and lane from the worktree it runs in;
an agent whose worktree the world has not read yet falls back to its own project and a generic
name. A free agent has no task list row, so its node is the only place to take it **Off desk**.
The **Foreman** is the one free agent with no node: the **Foreman pane** shows it.
_Avoid_: Orphan agent, loose agent, unlinked agent

**Investigation**:
A **Free agent** told not to change code. It runs on the project's main branch unless the user
names another, in `auto` or `normal` mode only: a plan hands over to an execute session, which
changes code. It can write files under `.drafts/` and show them as documents.
`shared/investigation-prompt.md` holds its rules. Only that prompt stops it changing code: no
permission rule backs it.
_Avoid_: Research agent, read-only agent

**Foreman**:
The one long-lived **Free agent** that manages work for the user. It reads and changes tasks,
agents, comms and projects through the `mael` CLI, and changes no code: it makes a task for code
work. It runs in `auto` mode in `~/.maelstrom/foreman`, a directory in
no project, and the server knows it by that cwd. `shared/foreman-prompt.md` holds its rules, and,
as for an investigation, no permission rule backs them. It never joins the desk. One host has one
foreman.
_Avoid_: Head agent, concierge, assistant

**Foreman pane**:
The top bar's pane that holds the **Foreman**'s conversation and message box. With no foreman,
it holds a Start button. With a foreman that stopped, it holds a Resume button over the
conversation, so the conversation goes on.

**Canvas**:
The desk in its graph **Desk mode**: one lane per project, with a **Worktree box** round the nodes of
each worktree. It draws one node per task and one per free agent. A node is drawn when it is on
the desk, or it has a live agent, so running work is always visible.
_Avoid_: board

**Zone**:
One of the five stages of progress the desk lays out left to right: queued, next, running,
built, done. New work is on the left. Queued work waits on other work: it follows a task whose build has not ended. A
**Parked task** is queued too, though an agent ran on it. Next work is **Actionable** and waits on the operator.
Built work is **Awaiting merge**: no agent works on it, and its PR waits on the user. A node's zone comes from
its state, not from its task status: the running zone means an agent has been launched and the
task is not built yet, so an agent that ran and stopped is still running work rather than
history. Zone boundaries line up across every lane, so the canvas reads as five vertical
stripes. A zone is a stage of progress; a lane is one project's strip.
_Avoid_: Band, column group, stage, phase

**Worktree box**:
A dashed outline round the nodes of one worktree, inside a project lane. A box is one rectangle,
from the first column to the last column of its nodes. No other node takes a cell of that
rectangle, so a box holds those nodes and no other. Two boxes with no column in common can sit
side by side. A node with no worktree sits in no box. An open worktree that holds no node draws
as an empty box, in a strip below the last row of the lane. `_main` gets a box only when a node
is in it. The box's label names the worktree and its branch, and is a control: it opens the
**Worktree card**.
_Avoid_: Worktree frame, sub-lane

**Worktree card**:
The card a **Worktree box** label opens on the canvas. It holds the worktree's controls, the
same ones the node card ends with, and it starts a free agent in the worktree. The canvas shows
one card at a time, a node's or a worktree's.
_Avoid_: Worktree popover, box card, worktree panel

**Deck list**:
The desk in its list **Desk mode**: one row per node, tabbed by zone and opening on running. It
draws what the canvas draws — a node per task and per free agent — laid out down the screen
instead of across it. When the drawn nodes come from two or more projects, a subheading names
each project above its rows in the open tab. The narrow layout has no room for a board, so it
draws the desk only as the deck list.
_Avoid_: Mobile canvas, card list, feed

**Desk mode**:
How the desk draws its nodes: graph (the **Canvas**), kanban, or list (the **Deck list**). Each
mode has its own path, such as `/desk/kanban`, and all three use the same zones. Kanban draws
one column of cards per zone, with no boxes or follows wires. When the drawn nodes come from two
or more projects, one lane per project crosses the five columns. A drag of a card to another
column sets the task status that puts the card in that column: Next sets `todo`, Built sets `built`, Done moves
the task to its **Done** status. Only follows make a task wait, so the Queued column takes no drop. A drop never launches,
so the Running column takes no drop. A drop
stays in the card's own lane: it cannot move a task to another project.
_Avoid_: Desk view, layout (a layout is narrow, medium or wide)

**Swipe action**:
A deck row's left swipe. A row in the Next zone swipes to Launch. A row in the done,
cancelled, stopped or exited state swipes to **Dismiss**, which runs the same chain as the node
card's Dismiss. Other rows have no swipe action. `orchestrator-ui/DESIGN.md`, "The Swipe Reveal
Rule", gives the threshold.
_Avoid_: Swipe to delete, quick action

**Fake mode**:
The orchestrator UI on a fake server, with no orchestrator server and no agent daemon. The
`web-fake` service serves it. It is the production app, so a layout seen there is the layout the
app draws. A build does not include it.
_Avoid_: Fake backend, demo mode, mock mode

**Scenario**:
A named world that the fake mode opens on: the desk, each node state, a detail, a set of asks.
Each value of a protocol set appears in some scenario, and a test fails when one does not.
_Avoid_: Fixture world, preset, sample data

**Control height**:
The one height of each button, link, field and tab of the orchestrator UI: `--control`. It is
32px, and 48px on the narrow layout, where it is the floor for anything a finger presses. A hue
chip is not a control and has its own height. A split chip is a reading, and a control only where
it takes a click, as the agents chip does on the Desk. It takes this height either way, so it lines
up with the top bar.
_Avoid_: Touch size, button size

**Expanded node**:
A node grown in place into a card that shows its status, the decision it waits on, and links
into the panel. One node is expanded at a time. The narrow layout has no canvas to grow on, so
the same reading fills the screen as a pushed screen instead.
_Avoid_: Popup, detail panel, summary tab, dialog

**Pushed screen**:
What the narrow layout puts over the deck list: a node's detail, a session, or a document. One
screen shows at a time and back pops one level, because a phone has no room for the panel's
tabs. The wide layout pushes nothing — it expands a node and opens tabs.
_Avoid_: Route, page, modal, drill-down

**Decision**:
The block an expanded node or a document shows when an agent waits on the user: the last
messages before the wait, then the prompt (question, permission or plan review).
_Avoid_: Checkpoint UI, prompt card

**Remembered approval**:
An approve that also tells the agent not to ask again. The agent offers it with the ask, and the
**Decision** shows it as a button beside Approve, labelled with what it remembers. The agent
keeps the rule; Maelstrom keeps none.
_Avoid_: Always allow (as a term), auto-approve, trust

**Panel**:
The tabs, drawn in one slot. A tab holds a session, a document, a worktree's changes (see
**Changes tab**), or a running web service of a worktree (see **Dev env tab**). The panel sidebar
on its left edge lists the worktree groups, and the tab strip shows the tabs of the group in view.
The body shows the active tab, and the group's **Split tab** beside it when the group has one. The top bar labels the panel `Tabs`, and its anchor starts on
the right. In the wide layout a click on `Tabs` closes the panel's slot, and a panel link shows the
panel again. The narrow layout has no panel.
_Avoid_: Sidebar (for the panel as a whole), drawer, detail pane

**Slot**:
One of the two places in the body, left and right, that draws a top bar item: the desk, the task
list, the Comms view, the Projects view, the **Foreman pane** or the panel. The wide layout draws both slots. A click on a showing item
closes its slot, and the other slot takes the full width. The body is never empty: closing the
last open slot reopens the left slot on its most recent item. The code calls a top bar item a
pane.
_Avoid_: Column, region, split (a split inside the panel is a **Split tab**)

**Anchor**:
The side, left or right, that a top bar item shows on. The desk, the task list, the Comms view and
the Projects view start anchored left. The **Foreman pane** and the panel start anchored right. A shift-click on the item moves its anchor to the other
side. The top bar draws each item in the group of its anchor.
_Avoid_: Dock, pin

**Medium layout**:
The layout from 840px to 1599px, except a short touch screen, such as a phone in landscape,
which is narrow. A main view and the panel do not both fit, so the body has one
slot and the top bar has one menu of six items. A click shows the item in place of the one in
front. Above it is the wide layout, with two slots; below it is the narrow layout.
_Avoid_: Tablet layout, laptop layout

**Screen strip**:
The one row of chrome on a pushed screen of the narrow layout: Back, the screen's title, the
screen's actions, and More, which opens the **Side sheet**. See `orchestrator-ui/DESIGN.md`, "The
One Strip Rule".
_Avoid_: Header, title bar, back row

**Side sheet**:
The narrow layout's overlay from the right edge. More opens it on a pushed screen, for that
screen's secondary content. Filters opens it on a main view, for that view's filters. Its head
row starts with a bare × that closes it.
_Avoid_: Drawer, panel

**Worktree group**:
The open panel tabs of one worktree, shown as one row of the panel sidebar. A worktree group is
derived from the open tabs and holds no state of its own: it shows while one of its tabs is open,
and closing it closes all its tabs. Tabs that no worktree holds form a "no worktree" group for
their project.

**Tab picker**:
The `+` at the end of the panel's tab strip, and its menu of each tab the worktree in view can
show: its **Changes tab**, and each session and document that a node card on the desk would open
in this worktree. A tab already open stays in the menu, marked as open.
_Avoid_: Tab menu, add-tab button

**Changes tab**:
A panel tab that shows one worktree's changes: its **Dirty files**, each commit its branch has
over `main`, or the whole branch as one diff. The expanded node's `Changes` link and the
**Tab picker** open it.
The user answers the changes there with a **Change comment**.
_Avoid_: Diff view, diff browser, code tab

**Diff group**:
A named class of files in the **Changes tab**, such as tests or docs. A project names its diff
groups in `diff_groups:` in `.maelstrom.yaml`, as an ordered list, each with its globs. A file goes to the
first group that matches it, else to the last group, Other. The tab draws each group under a head
that folds it. A group can start folded.
_Avoid_: Category, file class, section

**Card**:
One file in the **Changes tab**'s flow, from one source: the new side of the rev in view. A card
draws an ordered list of **Spans**. A diff card holds the file's hunks, and any **Pin** on its
path merges into it. A pinned card holds pinned lines of a file the rev does not change. A path
has one card at most.
_Avoid_: File block, panel, pane

**Span**:
Consecutive lines of one file in a **Card**. A hunk is one kind of span; pinned lines are the
other.
_Avoid_: Chunk, block, region

**Pin**:
Extra context that the reviewer brings into the **Changes tab**: a whole file, or a range of
lines. A file picked from the file list is pinned whole. Following a name to a definition the
flow does not draw also makes a pin. A pin lives per rev, in **Held text**. Unpinning removes it;
the diff stays.
_Avoid_: Addition, added file, bookmark, attachment

**Snippet**:
A **Pin** of the lines of one function or class, or of a top-level TypeScript `const`, reached
by following a symbol. When none holds the target, the pin is a plain range **Pin** around it.
_Avoid_: Fragment (a part of a **Task document**), excerpt (the wire read), slice

**Reference menu**:
The menu that a click on a definition in the **Changes tab** opens: each use of the name in the
rev. A pick pins the **Snippet** that holds the use.
_Avoid_: Dropdown, references popup

**Symbol card**:
A card for a name in the **Changes tab**: the symbol's type, from the worktree's **Language
server**. Planned work.
_Avoid_: Tooltip, hover card, popover

**Language server**:
A process that answers questions about the code of one worktree, such as where a name is
defined. Maelstrom hardcodes the list.
_Avoid_: LSP, language service, analyser

**Rev overlay**:
The files a **Language server** holds open at the rev's text, because they differ from the
working tree. Without it, a definition would come back at its line on disk, not in the rev.
_Avoid_: Shadow tree, virtual files

**Rendered view**:
A dialog over the **Changes tab** that draws one whole file of the rev in view, by its **Renderer
plugin**. The **View** button on a file's head or its row in the file list opens it; a binary file has none, unless it is an image. It has three
modes: **Final** draws the new file, **Whole file** draws the whole file with the changes marked,
and **Changes only** draws the changed parts alone. It opens on Changes only. Final is disabled
for a deleted file. A plugin can also offer displays, such as the Gherkin plugin's Formatted and
Per scenario; every such plugin also has Raw, the text plugin's lines.
_Avoid_: Preview, document, file viewer

**Renderer plugin**:
One way the **Rendered view** draws a file, chosen by the file's path. The text plugin draws
lines in syntax colour, and draws any file no other plugin claims. The Gherkin plugin draws a
`.feature` file as a markdown document of its rules and scenarios, and falls back to text when a
side does not parse. The Markdown plugin draws a `.md` file as a document, with each changed block
marked. The Image plugin draws an image's two sides side by side.
_Avoid_: Viewer, formatter, previewer

**Dev env tab**:
A panel tab that frames one running web service of a worktree, such as its app or Ladle. Its
toolbar has a reload button, an **External link** to the same URL and a switch for each
**Live feedback param** the service exposes, such as the **Jig**'s. A stopped service shows the worktree's env control and no frame. The frame stays loaded while another tab is in view.
_Avoid_: Preview tab, browser tab

**Split tab**:
The tab a worktree group shows in the right half of the panel body, beside its active tab. A
shift-click on a tab in the strip sets it, and a second shift-click clears it. Each group keeps
its own split tab, so the split comes back with the group. The active tab is never the split tab.
_Avoid_: Split view, side-by-side, pinned tab

**Panel link**:
A link that opens a session, a document, a worktree's changes or its dev env as a tab in the
panel. It carries the open-in-panel icon.
_Avoid_: Open button

**Location**:
Where the user is in the orchestrator UI, as its URL says: the main view, the open card, the
active panel tab, the filters, the deck's zone, and whether the task editor or the new-work form
is open. A copied URL opens the same location, and Back returns to the one before. The open tabs,
the slots and the panel width are not part of it; the store keeps them.
_Avoid_: Route, page, nav state

**Offer**:
The list a combobox shows next to its field: the options that match what is typed, which the user
may take or ignore. It narrows as the user types and closes when nothing matches, because the
field keeps free text either way.
_Avoid_: Dropdown, autocomplete list, suggestions, menu

**External link**:
A link that leaves the app in a new browser tab — a worktree's pull request on GitHub, or its
dev environment on the narrow layout and in a **Dev env tab**'s toolbar. It carries the external-link icon, so a reader tells it from a panel link
before clicking.
_Avoid_: Outbound link, web link

**PR state**:
How close a worktree's pull request is to merging, as one of seven values. The server decides it
from the merge, the head commit's check rollup and GitHub's mergeability, so every reader shows
one reading.

| Value | Means |
|---|---|
| `merged` | The pull request merged |
| `ci-failed` | A check failed |
| `ci-running` | A check is pending or running |
| `checks-unreadable` | Nothing may read this commit's checks; waiting will not help |
| `conflict` | Checks pass, and the branch does not merge cleanly |
| `unknown` | GitHub has not answered mergeability yet |
| `ready` | Checks pass, and the branch merges cleanly |

The order above is the order the rule reads them, and it is the order a user asks in. A red
build is the thing to fix before a conflict, and `unknown` settles within seconds of a push —
never a quiet `ready`. A draft pull request reads as a draft instead of its state.

`checks-unreadable` and `unknown` part on whether waiting helps, so the first reads quiet rather
than as a spinner nothing will stop. See **Check rollup**.
_Avoid_: PR status, merge state, CI state

**Auto-merge**:
GitHub's own request to merge a pull request when its **PR state** reaches `ready`. Maelstrom
pins it to the head commit the user saw, so a later push is not merged. The repository must
allow it.
_Avoid_: Merge when ready, auto-land, queued merge

**Stale PR state**:
A worktree's **PR state** as the last successful read left it, because the latest read failed,
timed out or stood off for a GitHub rate limit. The wire marks it `prStale`, and `mael list`
ends the cell with `(stale)`. The local columns are never stale this way: only the pull request
read leaves the machine.
_Avoid_: Stale (alone: a **Stale prompt** is a different thing), cached PR, old PR

**Check rollup**:
GitHub's one-word verdict on a commit's checks, read through GraphQL. The rollup needs the
`checks=read` token permission, which GitHub no longer offers, so some repositories refuse it and
answer `null` — the same answer a repository running no CI gives. The refusal arrives beside the
data as a per-field error, so only the payload says it happened.

A refused rollup falls back to the **Actions run** for the same commit. There each workflow
answers with its newest run on the head commit, and a cancelled run reads `ci-failed`. A
repository that refuses both reads `checks-unreadable`.
_Avoid_: Check status, CI rollup, status check

**Actions run**:
One workflow run, read from the Actions REST API. It carries a status, a conclusion and the
commit it ran against, and it is how a pull request gets a state when the **check rollup** is
refused. Runs are read one page per repository rather than one read per branch, and matched on
the pull request's head commit: a run against an earlier push says nothing about this one.
_Avoid_: Workflow, job, build

**Tone**:
The reading a colour stands for, as one of six: `good`, `bad`, `busy`, `neutral`, `quiet`,
`special`. A tone names how a thing reads, never what it is — `bad`, never `ci-failed` — so one
chip serves a pull request and anything else that reports a state, and each domain keeps its own
map from its values onto the six. Two states may share a tone when they make the same demand on
the reader; the icon parts them.
_Avoid_: Colour, status colour, variant

## Data patterns

How a piece of state reaches the orchestrator. The state database and its machinery exist, and
the desk is on them. Tasks, worktrees and pull requests are not yet: the terms below say where
each is going. See [`docs/dev/data-architecture.md`](docs/dev/data-architecture.md).

The entries above describe the system as it stands today. Where the two disagree — a task is a
markdown file above and a canonical row here — the disagreement is the work outstanding.

**Canonical**:
State maelstrom itself authors, held in the state database. The write is the authoritative act,
so the table is backed up, migrated, and never rebuilt from empty. A row carries its own prose
rather than pointing at a file. The desk is canonical today; tasks are to follow.
_Avoid_: Source of truth, primary, master

**Cached**:
State another system authors, held in the state database as the last answer seen. A reader
takes what is there whatever its age, and a refresher keeps it current. Losing the table costs
one slow read, never data. Worktrees and pull requests are cached.
_Avoid_: Mirror, copy, snapshot

**Refresher**:
What keeps one cached table current. It owns its cadence, its budget and what it does when the
upstream refuses. A reader never triggers one. No module implements it yet: see
`docs/dev/data-architecture.md`.
_Avoid_: Poller, syncer, updater

**Pass-through**:
State read from its source on each request and stored nowhere, because one route needs it
rather than the whole world. The Linear issues route and attachment bytes are pass-through.
_Avoid_: Uncached, direct, live

**Pushed**:
State whose owner streams it, so the state database holds nothing live. Agents are pushed: the
agent host owns them.
_Avoid_: Streamed, realtime, subscribed

**Progressive**:
A write too slow to answer on completion. It replies at once with an entity in a preparing
state, then reports each step as a change. Worktree setup, worktree close and task inference
are progressive.
_Avoid_: Async, background, deferred

**State database**:
The SQLite database at `<notebook root>/state.db` holding every canonical and cached table. One
file, so one transaction and one revision counter cover them all. It holds the desk, the tasks
and the **Wiki**. The install script creates and upgrades it; every other open refuses a schema it
cannot read.
_Avoid_: Cache, store, db

**Revision**:
One monotonic counter in the state database, bumped once per write transaction, and stamped on
every row that transaction wrote. A reader asks what changed since a revision it holds, rather
than re-reading a table. One counter across every table, because that is what lets one number
name an atomic write of several. A transaction that moves no row consumes none, so an idle poll
leaves it where it was. It is not an **Epoch**: an epoch names one life of a stream, and a
revision orders writes within one database.
_Avoid_: Version, sequence, generation, epoch

## Wiki and archive

**Wiki**:
Curated markdown pages for design patterns that apply to more than one project, one row each in
the `wiki_pages` table of the **State database**. The wiki fills the gap that per-project memory
and a repo's own `docs/` both leave open.

**State archive**:
The zip that `mael self export` writes and `mael self import` reads. It holds the **State
database** and the **Attachments directory**, and a manifest of their schema versions.
_Avoid_: Backup, dump, snapshot

## Code layout

**Workspace member**:
One package in the uv workspace, with its own `pyproject.toml`, `src/` and `tests/`. The members
are `cli/`, `lib/common/`, `lib/agent/`, `lib/domain/`, `agent-daemon/` and `orchestrator-api/`.
The repository root is not a member: it builds nothing, and `uv sync` reads it to install every
member into one environment.
_Avoid_: Subproject, module, crate

## Install

How a host runs maelstrom apart from any checkout. See
[`docs/adr/0001-maelstrom-installs-as-a-release-bundle.md`](docs/adr/0001-maelstrom-installs-as-a-release-bundle.md),
[`docs/adr/0002-one-installer-and-host-services.md`](docs/adr/0002-one-installer-and-host-services.md)
and [`docs/dev/host-services.md`](docs/dev/host-services.md).

**Release bundle**:
The tarball a release publishes, `maelstrom.tar.gz`: a manifest and one directory per
**Component**. The installer, `install.sh`, copies each component into `~/.maelstrom/install/`
and moves the `current` link to the release.
_Avoid_: package, distribution

**Bundle kind**:
What a **Release bundle** installs. It names the bundle's set of **Components**. Today the one
kind is `host`.
_Avoid_: flavour, profile

**Component**:
One part of a **Release bundle** with its own version: the hash of the **SHA bucket** of its
name. A host installs each version of a component once. A component can be one wheel, the
built UI, or the pinned third-party requirements.
_Avoid_: module, package

**Host service**:
A process of the install that the OS supervisor runs and restarts: `orchestrator` or
`agent-daemon`. It is a launchd agent on macOS and a systemd user unit on Linux, and it runs the
`current` release. `mael self setup` installs it, and `mael self restart` restarts it. It is not a
**Service**, which is a process from `.maelstrom.yaml` for one worktree.
_Avoid_: Service, daemon (for the orchestrator), self-env

**Update job**:
A one-shot unit of the OS supervisor that runs `mael self update` apart from the **Host
services**, so it outlives their restart. `mael self update --detach` and the Welcome dialog
start it. It writes its record to `~/.maelstrom/install/update.json`. It is not an **Operation**:
an Operation runs in the orchestrator, and the job restarts the orchestrator.
_Avoid_: update operation, background update

**Host check**:
The check of the machine outside maelstrom's own install: the host tools, the sign-ins, the git
author identity, Docker, the browser tool, Tailscale and the secret files under `~/.maelstrom`.
It also runs each **Project host check**.
`mael self check-host` runs it, and installs or signs in only after a yes. Each install runs it
with `--quiet`, which skips an optional item the user said no to. It is not
`mael self setup`, which applies maelstrom's own install, nor `mael project check`,
which checks one project.
_Avoid_: doctor, host doctor

**Project host check**:
The project's `check_host_cmd`. It checks that the host has what the project needs, such as a
tool that the **Host check** does not cover. Maelstrom only runs it, in `_main`, and shows its
output.
_Avoid_: host check (that is `mael self check-host`)

**Core plugin**:
The `maelstrom` plugin in `shared/plugins/`: the skills the orchestrator launches by name and the
`mael` workflow needs. Each maelstrom launch loads the core plugin of the code that launches it,
so it needs no global install.
_Avoid_: core skills, maelstrom skills (for the plugin)

**minnstack**:
The optional plugin in `shared/plugins/` that holds the development practices, such as
`design-build-test`, `code-review` and `writing-for-humans`. A user installs it globally. A
**Core plugin** skill that names a minnstack skill works without it.
_Avoid_: optional skills
