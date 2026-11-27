# Tasks

The Tasks subdomain holds the work that maelstrom gives to agents: each task and its order, the
run of a task on an agent, the documents of a task, and the comms that a task feeds. The root
`CONTEXT.md` defines **Task** and **Task tracker**. `docs/README.md` maps the four subsystems.

## Task tracker

**Status**:
Where a task is in its life: `todo`, `in-progress`, `built`, `merged`, `published` or
`cancelled`. `built` means the work is pushed. `merged` means every **Registered PR** is merged.
The orchestrator server's landing sync moves a `built` task to `merged` when its **Landing** reaches
`merged`. It moves no task at another status.
`published` means the merge is in the `live` deploy, or the **Post-merge work** is finished. No
status means "waiting": see **Follows**. The code has one more value, `template`, which marks a
**Template**.
_Avoid_: State, folder

**Done**:
A derived word, not a stored status. A task with no **Post-merge work** is done at `merged` or
`published`. A task with post-merge work is done only at `published`. `mael task status done`
moves a task to the first of its done statuses. The task entity on the wire carries a `done` flag.
_Avoid_: Finished, complete, closed

**Build ended**:
A derived word, not a stored status: the task's Status is `built`, `merged` or `published`. It is
the end of the build that **Status action** names. A task whose build has ended frees the tasks
that follow it.
_Avoid_: Finished build, pushed

**Post-merge work**:
The work an agent does on a task after the merge, such as a check of the deploy. The
`post_merge` field holds its text. Empty means none. The field sets which statuses count as
**Done**: with post-merge work, a `merged` task is not done yet.
_Avoid_: Follow-up, after-work

**Priority**:
How urgent a task is: `critical`, `high`, `medium` or `low`. A new task is `medium`. Priority
orders the **Task list** and the choice of the next task. It never makes a task wait.
_Avoid_: Rank, severity

**Status action**:
What a task tells an outside service when its **Status** moves, written `<service>.<verb>`, such
as `linear.done`. A task has two: one for the move to `in-progress` and one for the end of the
build. The build ends at the move to `built`, or at the move to **Done** of a task that was never
`built`. The service's issue comes from the task's id or its **Parent**, such as
`linear.NORT-12`. A status action never stops a status move: a failure is a warning.
_Avoid_: Hook, lifecycle action, trigger (a **Trigger** fires a template)

**Task log**:
The dated lines that an agent appends to a task as it works. A line is added and never changed.
_Avoid_: History, journal, notes

**Parent**:
The grouping key that puts a task in a chain sharing one branch. The siblings share the branch,
not a PR. A follower starts when the build before it ends, so its commits can join that
sibling's open PR. A task with an
empty parent roots its own chain. A parent is often virtual, such
as `linear.NORT-123`, and is never checked against a real task.
_Avoid_: Epic, group

**Follows**:
The ordering relationship. A task becomes actionable only once the build of every id it follows
has ended: see **Build ended**. The next task of a **Chain** thus starts when the build before it
is pushed, not when it merges. Follows is the one way to make a task wait, and it is independent of parent.
`mael task follow` adds follows to a task that already exists. A task can follow a task in
another project by its **Qualified id**.
_Avoid_: Depends-on, blocked-by

**Chain**:
The sibling tasks that share one parent and its branch, ordered by follows. Each sibling links
its own PRs.
The end of a chain is each task of it that no other task of it follows.

**Recipe**:
What says how to do a task, apart from where it is in the order: its title, content, command,
mode, model, **Priority**, **Status actions** and **Post-merge work**. A copy of a task takes
its recipe. A **Task draft** and a **Template** hold a recipe.
_Avoid_: Spec, definition

**Splice**:
A promote that puts a **Task set** after one task in a chain. The first new task follows that
task, and every task that followed it now follows the last new task. `A → P → C` spliced after
`P` becomes `A → P → N1 → N2 → C`. A promote with no follow flag splices after `$MAEL_TASK_ID`,
and approving a task set splices after the planning task. A task can thus expand the middle of
its own chain without forking it.
_Avoid_: Insert, rewire

**Dotted id**:
Lineage expressed in a task's name. `PROJ-12.3` is a child of `PROJ-12`, and
`maintenance.2026-07-02` is a scheduled run of the `maintenance` template. Lineage and parent
are separable on purpose: a scheduled run is named under its template yet has an empty parent,
so each firing roots its own chain.

**Qualified id**:
A task id that names its project: `northwind/NORT-7`, against the bare `NORT-7`. A surface
shows the qualified id when it must tell two projects' tasks apart, and the bare id when
something else on screen already names the project, such as a node in its lane or a panel tab
under the panel sidebar.

**Actionable**:
A task maelstrom may launch now: not **Done**, not `cancelled`, not a template, and the build of
every followed id has ended. A `cancelled` or missing followed task keeps it waiting.
_Avoid_: Ready, unblocked

**Cross-project task**:
A plan-mode task that a session in one project creates and launches in another project. It has
an empty parent, so it roots its own chain there, and it follows no task. Its own plan mode plans
that project's code.
_Avoid_: Remote task, foreign task

**Template**:
A recipe that tasks are made from. It is a domain type of its own, not a kind of task: it never
launches, it is never **Actionable**, and it has no **Registered PR**. A `schedule`, a
**Trigger**, or both on a template make the scheduler create a task from it. The code holds a
template as a task row with the status `template`.
_Avoid_: Template task, recurring task

**Schedule zone**:
The time zone every template `schedule` on a host is read in. `schedule_timezone:` in
`~/.maelstrom/config.yaml` sets it. With no key, it is the host's own zone.
_Avoid_: Cron zone, local time

**Trigger**:
The event that fires a template, written `<kind>/<spec>`. `gh-action/nightly.yml` fires when a
run of that GitHub Actions workflow finishes with a listed conclusion. With a `schedule` too, a
cron boundary fires only when the newest completed run has a listed conclusion.
_Avoid_: Gate, condition

**Registered PR**:
A pull request recorded on a task as its own: a number and a URL. A task holds a list of them,
oldest first. `mael gh create-pr`, `mael gh link-pr` and the **PR link tag** add one to the
list. A PR the list holds already is not added again. `mael gh withdraw-pr` and the PR link tag
with a bare `withdrawn` attribute unlink one, while the agent does more work after the push. An
unlinked PR leaves the card and does not count toward `merged`. The next `mael gh create-pr`
push links it again. A task card shows its task's newest Registered PR only, never one its
**Chain** or its branch has. The worktree table shows the branch's PR.
_Avoid_: Linked PR, task PR, withdrawn PR

**PR link tag**:
The marker an agent writes in the text of an ordinary message to register a PR on its task:
`<link rel="gh-pr">118</link>`, with a PR number, `#118` or a GitHub PR URL. The server cuts it
from the message and adds the PR to the task's **Registered PRs**. With a bare `withdrawn`
attribute, `<link rel="gh-pr" withdrawn>118</link>`, it unlinks that PR instead. A **Free agent** registers nothing, and a subagent writes none. A `<link>` of any other `rel` stays as text. One of the
markers, with the **Document tag**, the **Image tag**, the **Note**, the **Milestone** and `<live-feedback>`.
_Avoid_: PR tag, link tag

**Phase**:
Which of four stages a task's work is in: shape, plan, build, land. A phase is named as the
imperative of the work, which is what keeps it apart from the agent's state — a task is in build
whether or not an agent runs on it now. Read from the task's `command`, and never stored: an
agent shows the phase of its task. A build task in plan mode reads plan until its agent leaves
plan mode, which plan approval does. A command nobody recognises has no phase, and neither does an
agent with no task.
_Avoid_: Stage, step, executing, finalising. **Shape** and **plan** are the phases' own names, so
they are used for those phases and not as loose synonyms: shaping creates the tasks that planning
then plans. The `plan-task` skill shapes: it drafts and promotes tasks. `plan` belongs to the
`plan-next-step` skill and to an agent's plan mode.

**Shape**:
Exploring a brief until a set of tasks is agreed and created. Ends at a user checkpoint. May be
skipped.

**Plan**:
Producing an agreed plan for one task. Ends at the plan-review checkpoint.

**Build**:
Building the code, running its own review, presenting it, opening the PR. Ends when the PR first
ships. In a `merge: local` project, it ends at the first `mael merge submit`. Not the **Queue
build**, the gate run of a Merge request.

**Land**:
Answering CI failures and review feedback on an open PR. Ends when it merges. In a `merge: local`
project, the **Chaperone** answers `build.*` events and review feedback on its **Merge request**.

**Brief**:
The free-text starting point for shaping.

**Planning level**:
How much planning a new task gets before it is built: high, regular or none. The level is a
reading over the task's `command` and `mode`, never stored as a field of its own — high is
`plan-task` under `normal`, regular is no command under `plan`, none is no command under `auto`.
High and none mirror `mode_for_command`; regular has no equivalent in Python, where an empty
command means `auto`. New work and the task editor both offer the three as radios and read them
back off the two fields, so editing either field re-derives the level and a pair no level names
reads as N/A.
_Avoid_: Planning depth, plan mode (that is a permission mode), autonomy

**Task metadata generator**:
The component that names a task's title, branch and command from its prose. It asks OpenAI's
`gpt-6-luna` when `OPENAI_API_KEY` resolves. With no key, every name is the deterministic slug
of the prose's first line.
_Avoid_: Branch namer, inference model

**Task list**:
The view that lists every task the server knows, with filters for status, project, branch and
text. The canvas shares the project, branch and text filters. The task list is where a task joins the desk, and one of the places it leaves it. A task
node's expanded card offers the same toggle for every task its task follows or is followed by.
The task list lists tasks only: a free agent has no row, and is taken **Off desk** from its node on the canvas.
_Avoid_: Table view, index

**Task editor**:
The form that edits one task's fields: title, status, content and branch, with the planning level
shown above Advanced, and command, mode, priority, model and follows folded away there.
Below the fields, a Comms section links and unlinks comms at once, not through Save. It
opens from the task list or a task's node on the desk, and writes through `task.update`, except
status, which writes through its own route. New work composes the same
field components, so both surfaces read and order fields the same way — except status, which new
work has no control for.
_Avoid_: Task modal, edit form, task detail

## Task runs

**Task run**:
One launch of a task on an agent, and what maelstrom observes of that agent against the task. A
task run owns the link from the task to its sessions. The Task does not know its sessions. The
code holds the session ids on the task row.
_Avoid_: Execution, job, attempt

**Task session id**:
The id of one of a task's agent sessions. A task stores a list of them, oldest first, and each
entry names its harness. For Claude, maelstrom makes the id at the first launch and passes it as
`--session-id`. Each `/clear` of the task's driven agent appends the id that Claude Code moves to. For
Codex, the id is the thread id that Codex returns. The newest entry for a
harness is the current one, and a launch on that harness resumes it. The task table finds a task
by any of its ids, and an agent row reports one. Use the task session id to answer "which task
is this?".
_Avoid_: Session id (for this concept)

**New session**:
A launch of a task on a new **Task session id**, in place of a resume of the current one. The
prompt names the earlier session ids, oldest first. A new session can reopen a done task. The
Resume menu of a task's agent offers it, and `mael task run --new-session` starts one.
_Avoid_: Fresh session, restart

**Parked task**:
An `in-progress` task that follows a task whose build has not ended. Its agent ended its session to wait, and the
task keeps its status and its session. Its node is in the queued zone, with the queued styling.
When the build of every followed task has ended, the node is a stopped node in the running zone again, and the
user resumes it. A parked task is not drift: `mael task reconcile --fix` leaves it.
_Avoid_: Blocked task, paused task

**Drift**:
A task status that disagrees with the agent observed on the task. Three kinds, the same three
`mael task reconcile` names: *finished*, *never-ran*, *orphan-session*. The agent wins when the
two disagree, because an agent state is observed from events and a task status is a file that
goes out of date. A closed task with an agent still on it is not drift — that is
**Finalising**. Drift is a bookkeeping note, never something waiting on the user: it is not
counted in attention.
_Avoid_: Stale (a stale prompt is a different thing), out of date, orphaned

**Finalising**:
A task that is `built` or **Done** while an agent still runs on it. That is the ordinary tail of
the task-completion flow: the PR is pushed, the task is closed, and `watch-pr` carries CI to green.
The node draws its own state and sits in the running zone, because the work is not settled until
CI is. It carries no drift mark. When the agent stops, the node of a **Done** task reads Done.
_Avoid_: Orphan session, lingering session, post-done

**Awaiting merge**:
A `built` task that no agent works on: no agent, an idle agent, or a cleanly stopped one. Its PR
is pushed and waits on the user to merge it, so the node sits in the built zone. An open
attention item or a faulted agent outranks it and keeps the node in the running zone. While
`watch-pr` still runs, the node is **Finalising** instead.
_Avoid_: Ready to merge (a PR state), finished, idle

**Reconcile**:
Reading every in-progress task against the sessions running now, and reporting each drift with
the status that would correct it. `mael task reconcile` lists the findings; `--fix` applies
them. Reconcile never guesses at a terminal task: a done task with a session still up is
reported, not moved.
_Avoid_: Repair, sync, heal

## Task documents

**Document**:
A versioned markdown artefact an agent puts in front of the user: a plan, a task draft, a PR
description, a review, a document bound for the repo. Every document but a plan is one file, and
its worktree-relative path is its identity within the task (or within the agent when it has no
task). Presenting the same path again makes the next version of the same document, whatever its
status. A document that stands at a checkpoint awaits review. A document the user was only asked
to read is a **draft**, and blocks nothing.
_Avoid_: Artefact, output

**Task document**:
A file one task owns, with every version kept. Its kind is `markdown` or `sarif`. Its name is
unique within that task, and it may hold `/`. A reference names it as
`<project>:<task-id>:<name>`, with an optional `@v<n>` for one version and `#<fragment>` for one
part. Without `@v<n>`, a reference reads the latest version. Each **Document** a task's agent
shows with a **Document tag** stores a version, and so does each plan it submits. A free agent
stores none. A version keeps its review status, its agent and its **Review group**. Each verdict
writes the status. The rows are in the **State database**, and the media are in the task's
**Bucket**. See `docs/adr/0001-task-documents.md` for archiving, plans and storage.
_Avoid_: Attached document, kept document, saved document, persisted document, report

**Document role**:
The kind of the **Document** that stored a **Task document**: `plan`, `tasks`, `pr`, `review`,
`verification` or `other`. The card shows `plan` under Plans, `verification` under
Verifications, and every other role under Other. `mael doc add --as` sets it for a hand-added
document. The **Task document**'s own `kind` is its format, `markdown` or `sarif`.
_Avoid_: Type, category, section

**Document link**:
A task's link to a **Task document** that another task owns, or to one **Fragment** of it. The
owner's link is implicit. A link is deleted, not archived.
_Avoid_: Reference (that is the text that names a document), relation

**Fragment**:
One part of a **Task document** that a reference names after `#`. In markdown it is a heading's
slug, made as GitHub makes it: `## Set up` is `set-up`, and a second `## Set up` is `set-up-1`. In
SARIF it is a **Finding key**.
_Avoid_: Anchor, section id

**Finding**:
One result in a SARIF **Task document**: a rule, a level, a location and the tool's prose. The
findings are parsed from the stored SARIF each time, so no table holds them.
_Avoid_: Issue, alert, vulnerability

**Finding key**:
The name of one **Finding** that stays the same across scans. It is the Claude Security finding
id, else a SARIF fingerprint, else a hash of the tool, the rule and the location. A URL loses its
host and port, because each worktree serves on its own port. A line number never counts. Two
findings with one key in one scan get `-2`, `-3` in SARIF order.
_Avoid_: Finding id, label (`F3` names a finding inside one scan only)

**Document tag**:
The marker an agent writes in the text of an ordinary message to put a document in front of the
user: `<doc-file>`, naming files in the agent's worktree, comma-separated, resolved against that
directory and nothing outside it. Each file becomes one document, and the files of one tag form a
**Review group** the tag's `title` names. The tag is cut out of the message the transcript shows.
A tag opens its documents at `draft`; `review="true"` opens them awaiting review instead, which is
what raises an attention item.
One of the markers, with the **Image tag**, the **Note**, the **Milestone**, the **PR link
tag** and `<live-feedback>`. The names carry
nothing maelstrom-specific, so another frontend may render them its own way.
_Avoid_: Directive, macro, shortcode

**Image tag**:
The marker an agent writes in the text of an ordinary message to show the user a picture:
`<image src="docs/shot.png" alt="The failing dialog">`. The tag becomes a picture where it was
written, so an image is read in the flow of the message and is not a document. `src` names a file
in the agent's worktree; `alt` describes the picture and defaults to the filename. A file the agent
may not show, or one that is not there, leaves prose saying so, never a broken picture. One of the
markers, with the **Document tag**, the **Note**, the **Milestone**, the **PR link tag** and `<live-feedback>`.
_Avoid_: Screenshot tag, figure, embed

**Review group**:
The documents one **Document tag** named, settled by one verdict. Approving or sending back any
member settles every current member, and raises or clears one attention item for the group. Each
member keeps its own version. A member the agent leaves out when it presents the tag again is
**superseded**, and drops off the card. A plan is a group of one.
_Avoid_: Bundle, batch, set (except **Task set**)

**Comment**:
Feedback anchored to a span of one document version. Requesting changes sends the unresolved
comments back to the agent. For feedback on a diff, see **Change comment**.

**Review dock**:
The band under a document holding whatever waits on the user there: an agent's own wait, or the
document's own review route. One dock, so a reader answers in one place whoever is asking. See
`orchestrator-ui/DESIGN.md`, "Review Dock".
_Avoid_: Action bar, footer

**Verification**:
A **Document** of kind `verification`. It holds the evidence that the work is complete: prose,
images and video. An agent writes it at `.drafts/verification.md` after the gates pass.
_Avoid_: Proof, evidence document, demo

**Task draft**:
A task file that is not yet a task, written by a planning session into the worktree's `.drafts/`
directory. A task draft is a kind of **Document**: the user reads it and approves it. It is inert,
invisible to listing, `next` and follow-end resolution, until the Task tracker loads it as a real
task and deletes the file. That gap is the approval gate. Two surfaces open it:
`mael task promote` in a session, and approving the **Task set**'s document in the orchestrator
UI, which calls the same step.
_Avoid_: Draft (alone: `draft` is also the state of a **Document** that awaits no review),
proposal, pending task, plan file

**Task set**:
The **Task drafts** of one chain, shown as one **Review group**. Its `<doc-file>` tag names every file in
chain order, and that order is the order approval promotes and chains them in. Each draft is its
own document.
_Avoid_: Batch, plan bundle

**PR draft**:
The file `.drafts/pr.md`, holding the body a PR will get. `mael gh create-pr` reads it, writes it
to the PR, then deletes it. A missing draft leaves an existing PR's body alone.
_Avoid_: PR description file, body file, draft PR (which is GitHub's own unready-for-review state)

**File registry**:
The ids that stand for the files agents named, and the only route to a file's bytes. A `<doc-file>`
or an `<image>` registers its file, and the URL carries the id and never a path, so a file nobody
registered cannot be reached. An id is not a secret — it is guessable, and what it cannot do is
name a file nobody registered. Not persisted, as a document is not.
_Avoid_: File table, allow-list, handle

**Attachment**:
Something the Task tracker stores for one task. There are two kinds. A media attachment is an
image, a video, or any other file the user attaches, in the **Attachments directory**, under
`<project>/images/<bucket>/`. A **Task document** is rows in the **State database**. Task content,
the body of a task document, and a message to an agent reference a media attachment by a
`{{MAEL_TASK_DIR}}` token, which expands to an absolute path when the agent gets it. An image is
embedded and any other file is linked; see "Attachments" in `docs/dev/orchestrator-server.md`. A
brief from Linear, a screenshot pasted into the orchestrator UI and a text note are all media
attachments.
_Avoid_: Upload, asset; "file" for an attachment in general (a `file` attachment is the non-image
kind)

**Attachments directory**:
The plain directory `<notebook root>/attachments/` that holds every media attachment. The
`{{MAEL_TASK_DIR}}` token expands to `<notebook root>/attachments/<project>`.
_Avoid_: Task repo, notebook repo, images folder

**Bucket**:
The directory that groups one piece of work's attachments. A task uses its notebook id, a
Linear issue its identifier, an agent tied to no task `agent-<id>`, and a task that does not
exist yet `draft-<random>`.
_Avoid_: Folder, group, album

## Comms

**Comm**:
Something to tell people outside the team when some work lands. It has an id (`c1`, `c2`, …,
one sequence across every project), a title, its content (the request as it came in) and its
recipients. A recipient is free text: a Slack channel, an email address or a note. A comm also
has a **Comm category** and a project: the project a task made from the comm goes to.
- A new comm's project defaults to the **Default project** of its category.
- A comm links to tasks, and task ↔ comm is many-to-many. The link is the task's `comms`
  field, so a task names the comms it feeds.
- A new task gets the comms of every task it is made from: its parent, its follows tasks, and
  the task whose session made it. A duplicate (`--from`) also gets the comms of the task it
  copies. A promoted chain gets the planning task's comms. A
  Reopen-branch task gets the comms of the tasks on its branch. An explicit list wins.
- New work opened under a Comm filter links the task to that comm.
- You close a comm when everyone is told. A closed comm leaves the open list.
_Avoid_: outcome, promise, communiqué, notification, announcement, ticket

**Comm codename**:
A one- or two-word name for a **Comm**, such as `Lantern` or `Night Owl`, unique across all
comms. The **Task metadata generator** makes it when the comm is made. The UI shows a comm as its codename, in
the one comm colour.
- Without an OpenAI key, or when the generator fails twice, the codename stays blank. The UI then
  shows the comm id in its place. The server tries again for each blank comm when it starts.
- You can change the codename. A codename another comm uses is refused.
_Avoid_: nickname, alias, label, tag

**Comm category**:
Free text that groups comms of one kind, such as `release` or `support`. You pick an existing
category or type a new one.
_Avoid_: tag, label, type, channel

**Comm update**:
What a **Comm** tells its recipients at one time, with the news of its linked tasks. Its id is `<comm id>.<n>`, such
as `c3.2`. It holds the **Landing** steps it reports, its content and its state: `draft` or
`sent`.
- A comm's queue is the landing steps of its linked tasks that no update of the comm holds. A
  step is held once it is in an update, so a step never queues twice for one comm.
- A task linked late queues the steps it reached before the link. A closed comm queues nothing.
- The content starts as one line per task, at the furthest step it queued.
- You edit the content while the update is a draft. Mark sent records that you sent it; maelstrom
  sends nothing itself. A sent update does not change.
_Avoid_: flush, message, notification, digest

**Draft updates**:
The action that writes a new **Comm update** for each open comm with a non-empty queue. An unsent
draft stays as it is: the action adds an update, it does not merge into the old one.
_Avoid_: flush, triage, send

**Default project**:
The project a new comm in a category gets: the most common non-blank project among the comms in
that category, closed comms included. A tie goes to the project of the newest comm. It is derived
from the comms each time, not stored, so it follows how the category is used. A comm with no
category has no default project. With no comm in
the category, the CLI uses the project of the current directory and the UI uses the filter bar's
project.
_Avoid_: category project, project mapping

**Comms**:
The view that lists the open **Comms** and edits them: a row per comm, with its recipients, its
linked task count and the highest **Landing** status among its tasks. The editor links and
unlinks tasks, and shows each linked task's landing strip. The **Draft updates** button shows
how many tasks the open comms queue. A row with an unsent **Comm update** says `draft`, and the
editor lists the comm's updates, newest first. Its New task button opens New work on
the comm's title, content and project, and the task it writes is linked to the comm. The task
count opens Tasks with the Comm filter set. A comm's chip shows its **Comm codename**; a click
on a chip on a task's node or card opens the comm's editor. On a kanban or list row the chip is
text, because the row is itself a link. The **Task editor** links comms too. Each picker
offers the five comms or tasks this browser viewed or linked most recently, before a search. A person works here; an agent uses
`mael comms`.
_Avoid_: Inbox, outbox, notifications tab

## Open questions

This is unresolved. Do not treat it as settled intent.

**Cancelled dependencies**: Follows gating tests for **Done** only, so a cancelled task stalls
everything that follows it permanently. Whether cancelling should release or block its
followers is undecided.
