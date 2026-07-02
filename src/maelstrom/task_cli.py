"""Thin CLI for the task notebook: ``mael task ...``.

Each command builds a :class:`~maelstrom.task_store.GitFileStore`, calls a single
model function from :mod:`maelstrom.task`, and renders the result. All logic
lives in the model; this layer only parses arguments and prints.
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import click

from . import task as model  # noqa: F401  (module, used as `model.*`)
from . import task_actions
from . import session_discovery
from . import session_store
from .context import resolve_context
from .table import draw_table
from .task_store import GitFileStore
from .shell import run_cmd
from .worktree import (
    get_current_branch,
    list_worktrees,
    setup_worktree_for_branch,
)
from .worktree_launcher import (
    build_task_launch_line,
    launch_claude_in_worktree,
)


def _current_branch_or_none() -> str | None:
    """Detect the current git branch, or ``None`` when there's no preference.

    Returns ``None`` outside a git repo (any failure), and treats ``main`` or
    an empty result as "no branch preference" so the command degrades to global
    next-task behavior.
    """
    try:
        branch = get_current_branch(Path.cwd())
    except (subprocess.CalledProcessError, OSError):
        return None
    if not branch or branch == "main":
        return None
    return branch


def _store() -> GitFileStore:
    return GitFileStore()


def _active_session_for_task_worktree(
    project: str, task: "model.Task"
) -> "session_discovery.LiveSession | None":
    """The live Claude session running in ``task``'s worktree, or ``None``.

    Resolves the task to its branch, the branch to its checked-out worktree via
    the git worktree list, then asks :func:`session_discovery` whether a live
    ``claude`` process is running there. ``None`` when the task has no worktree
    checked out or that worktree has no live session. Used by the
    duplicate-launch guard and shares its liveness source with ``reconcile``.
    """
    ctx = resolve_context(project, require_project=True, arg_is_project=True)
    project_path = ctx.project_path
    if project_path is None or not project_path.exists():
        return None
    branch = task.branch or model.default_branch(task.id, task.parent)
    for wt in list_worktrees(project_path):
        if wt.branch == branch:
            return session_discovery.active_session_for_worktree(wt.path)
    return None


def _read_content_file(content_file: str | None) -> str:
    """Read the ``--content-file`` argument's contents.

    ``-`` reads from stdin (the Unix ``cat -`` idiom), so callers can pipe a
    brief without managing a temp file; any other value is a path that must
    exist.
    """
    if content_file is None:
        return ""
    if content_file == "-":
        return sys.stdin.read()
    path = Path(content_file)
    if not path.is_file():
        raise click.ClickException(f"Content file not found: {content_file}")
    return path.read_text()


def _run_task(
    store: GitFileStore, project: str, task: "model.Task", *, here: bool = False
) -> None:
    """Mark a task in-progress and launch its Claude session.

    The launchers may ``execvp`` (replacing the process), so every store write
    MUST complete before they are called. With ``here=True`` the session runs
    in the current shell — no worktree reconciliation, no new cmux workspace.
    """
    # Refuse a second parallel launch: the task's worktree already has a *live*
    # Claude session (one worktree, one PR — racing two sessions on it corrupts
    # both). Liveness is a running `claude` process whose cwd is that worktree
    # (see session_discovery); a *finished* session leaves nothing running, so a
    # finished task stays re-runnable and is deliberately NOT blocked.
    existing = _active_session_for_task_worktree(project, task)
    if existing is not None:
        raise click.ClickException(
            f"Task {task.id} already has a live Claude session "
            f"(pid {existing.pid}) in worktree {existing.cwd}. Close it before "
            f"relaunching, or run `mael task reconcile` to inspect."
        )

    # Skills running inside the session self-reference via these — e.g. to
    # `mael task done $MAEL_TASK_ID` and `--follow-end linear.<parent>`.
    session_env = {
        "MAEL_TASK_ID": task.id,
        # A parentless task self-parents: children it emits nest under it and
        # share its branch (one PR per chain), instead of each becoming a fresh
        # orphan. A real parent wins over the task.id fallback. A scheduled run is
        # an intended case of this: it is created parentless (its dot-id already
        # names it under its template), so `task.id` becomes MAEL_TASK_PARENT and
        # its follow-ups nest under the run, not the template. See docs/dev/tasks.md.
        "MAEL_TASK_PARENT": task.parent or task.id,
    }
    perm = model._permission_mode_for(task.mode)
    # Deterministic session id (same task → same id), passed to `claude
    # --session-id` so the registry can map the live session back to the task.
    session_id = model.session_id_for(project, task.id)
    # The prompt is produced lazily by `mael task prompt` inside the launch
    # pipeline, not passed here — keeps the launch command line short.

    if here:
        task_actions.move_with_actions(
            store, project, task.id, model.STATUS_IN_PROGRESS
        )  # write BEFORE launch; fires pre_action
        click.echo(f"Running {task.id} here (current shell)")
        run_cmd(
            build_task_launch_line(
                project, task.id, perm, env=session_env, session_id=session_id
            ),
            cwd=None,
            env=session_env,
            replace_process=True,
        )
        return

    ctx = resolve_context(project, require_project=True, arg_is_project=True)
    project_path = ctx.project_path
    if project_path is None or not project_path.exists():
        raise click.ClickException(
            f"Project '{project}' not found at {project_path}"
        )
    branch = task.branch or model.default_branch(task.id, task.parent)
    # The launcher owns install (shell pane on create, blocking in non-cmux).
    result = setup_worktree_for_branch(
        project_path, project, branch, run_install=False
    )
    task_actions.move_with_actions(
        store, project, task.id, model.STATUS_IN_PROGRESS
    )  # write BEFORE launch; fires pre_action
    click.echo(f"Running {task.id} on {branch}")
    click.echo(f"  → {project}/{result.name} ({result.action})")
    launch_claude_in_worktree(
        result.path,
        project=project,
        worktree=result.name,
        task_id=task.id,
        permission_mode=perm,
        env=session_env,
        session_id=session_id,
    )


def _resolve_project(project: str | None) -> str:
    """Return the project name, defaulting to the cwd's project."""
    if project:
        return project
    ctx = resolve_context(None, require_project=True)
    assert ctx.project is not None  # require_project guarantees this
    return ctx.project


def _resolve_task_id(id: str | None) -> str:
    """Return the task id from the arg, falling back to ``MAEL_TASK_ID``."""
    task_id = id or os.environ.get("MAEL_TASK_ID")
    if not task_id:
        raise click.ClickException(
            "No task id given and MAEL_TASK_ID is not set."
        )
    return task_id


def _default_parent(parent: str) -> str:
    """Default an unset ``--parent`` to the launching session's parent.

    A session launched by ``mael task run`` exports ``MAEL_TASK_PARENT`` — the
    launching task's parent, or the task's own id when it has none — so a
    parentless planning session still chains its children under one
    parent/branch (one PR per chain) instead of each becoming a fresh orphan.
    For a Linear-rooted task this is the ``linear.<ID>`` parent. Chain tasks a
    skill emits nest under it without spelling it out; an explicit ``parent``
    always wins.
    """
    return parent or os.environ.get("MAEL_TASK_PARENT", "")


@click.group("task")
def task() -> None:
    """Manage the git-backed task notebook."""


@task.command("add")
@click.argument("title", required=False, default=None)
@click.option(
    "-p", "--project", default=None, help="Project name (default: from cwd)."
)
@click.option(
    "-c", "--command", default="", help="Command to launch the session with."
)
@click.option(
    "-m",
    "--mode",
    default="",
    help="Session mode (default: plan; 'auto' for an unattended execute session, 'normal' for a non-planning session).",
)
@click.option(
    "--priority",
    type=click.Choice(model.PRIORITIES),
    default=None,
    help="Task priority (default: medium; affects list ordering and `task next`).",
)
@click.option(
    "-b", "--branch", default="", help="Branch for the task (default: task/<id>)."
)
@click.option(
    "-P", "--parent", default="", help="Parent task id (creates a child id)."
)
@click.option(
    "--pre-action",
    "pre_action",
    default="",
    help="Lifecycle action fired when the task starts (e.g. linear.in-progress).",
)
@click.option(
    "--post-action",
    "post_action",
    default="",
    help="Lifecycle action fired when the task finishes (e.g. linear.done).",
)
@click.option(
    "--follow",
    "follows",
    multiple=True,
    help="Id this task follows (repeatable).",
)
@click.option(
    "--follow-end",
    "follow_ends",
    multiple=True,
    help="Follow the end leaves of the given id's follows-chain (repeatable).",
)
@click.option(
    "--content-file",
    default=None,
    help="File whose contents become the task's Content section ('-' reads stdin).",
)
@click.option(
    "--from",
    "from_id",
    default=None,
    help="Seed the new task by duplicating this task's recipe; other flags override.",
)
@click.option(
    "--template",
    "is_template",
    is_flag=True,
    help="Park the new task in 'template' status (a reusable, non-actionable recipe).",
)
@click.option(
    "--schedule",
    default=None,
    help="Cron expression (acted on only for template tasks); e.g. '0 9 * * 1-5'.",
)
@click.option(
    "-e",
    "--edit",
    "edit",
    is_flag=True,
    help="Open the new task in $EDITOR after creating it.",
)
@click.option(
    "-r", "--run", is_flag=True, help="Launch the task as a session immediately."
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_add(
    title: str | None,
    project: str | None,
    command: str,
    mode: str,
    priority: str | None,
    branch: str,
    parent: str,
    pre_action: str,
    post_action: str,
    follows: tuple[str, ...],
    follow_ends: tuple[str, ...],
    content_file: str | None,
    from_id: str | None,
    is_template: bool,
    schedule: str | None,
    edit: bool,
    run: bool,
    here: bool,
) -> None:
    """Add a new task and print its id."""
    content = _read_content_file(content_file) if content_file is not None else None
    add_task(
        title=title,
        project=project,
        command=command,
        mode=mode,
        priority=priority,
        branch=branch,
        parent=parent,
        pre_action=pre_action,
        post_action=post_action,
        follows=follows,
        follow_ends=follow_ends,
        content=content,
        from_id=from_id,
        is_template=is_template,
        schedule=schedule,
        edit=edit,
        run=run,
        here=here,
    )


def add_task(
    *,
    title: str | None = None,
    project: str | None,
    command: str = "",
    mode: str = "",
    priority: str | None = None,
    branch: str = "",
    parent: str = "",
    pre_action: str = "",
    post_action: str = "",
    follows: tuple[str, ...] = (),
    follow_ends: tuple[str, ...] = (),
    content: str | None = None,
    from_id: str | None = None,
    is_template: bool = False,
    schedule: str | None = None,
    edit: bool = False,
    run: bool = False,
    here: bool = False,
) -> "model.Task":
    """Create a task (and optionally launch it), echoing its id.

    The single create-and-launch path shared by ``mael task add`` and any other
    command that creates a task (e.g. ``mael linear plan``), so there is exactly
    one place that resolves follows, creates the task, and runs it.

    With ``from_id`` the task is seeded by :func:`model.duplicate` from that
    source; the remaining flags override the copied recipe. ``is_template`` parks
    the new task in ``template/`` status and ``schedule`` sets its cron metadata.
    ``content`` of ``None`` means "unspecified" (so a duplicate keeps the
    source's content); pass ``""`` to deliberately blank it.
    """
    proj = _resolve_project(project)
    store = _store()
    parent = _default_parent(parent)

    if from_id is None and not (title and title.strip()):
        raise click.ClickException("A title is required (or pass --from <task-id>).")

    follow_list = list(follows)
    for end_id in follow_ends:
        follow_list.extend(model._resolve_follow_end(store, proj, end_id, parent))
    # De-dupe while preserving first-seen order.
    deduped = list(dict.fromkeys(follow_list))

    status = model.STATUS_TEMPLATE if is_template else model.STATUS_TODO

    if from_id is not None:
        try:
            new = model.duplicate(
                store,
                proj,
                from_id,
                title=title,
                command=command or None,
                mode=mode or None,
                priority=priority,
                content=content,
                pre_action=pre_action or None,
                post_action=post_action or None,
                branch=branch,
                parent=parent,
                follows=deduped,
                schedule=schedule or "",
                status=status,
            )
        except KeyError:
            raise click.ClickException(f"Task not found: {from_id}")
    else:
        new = model.create(
            store,
            project=proj,
            title=title or "",
            command=command,
            mode=mode,
            priority=priority or model.DEFAULT_PRIORITY,
            branch=branch,
            parent=parent,
            pre_action=pre_action,
            post_action=post_action,
            follows=deduped,
            content=content or "",
            schedule=schedule or "",
            status=status,
        )
    click.echo(new.id)
    if edit:
        try:
            model.edit_in_editor(store, proj, new.id)
        except KeyError:
            raise click.ClickException(f"Task not found: {new.id}")
        except RuntimeError as e:
            raise click.ClickException(str(e))
    if run:
        _run_task(store, proj, new, here=here)
    return new


@task.command("load-many")
@click.argument("file")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--run",
    is_flag=True,
    help="Launch the head task (first created) into a session.",
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_load_many(file: str, project: str | None, run: bool, here: bool) -> None:
    """Create one or more tasks from a marked plan file ('-' reads stdin)."""
    text = _read_content_file(file)
    try:
        blocks, warnings = model.parse_task_blocks(text)
    except ValueError as e:
        raise click.ClickException(str(e))
    for w in warnings:
        click.echo(f"warning: {w}", err=True)
    proj = _resolve_project(project)
    created = model.load_many(
        _store(), project=proj, blocks=blocks, default_parent=_default_parent("")
    )
    for t in created:
        click.echo(f"{t.id}\t{t.title}")
    if run and created:
        head = created[0]
        # Print BEFORE _run_task — the non-cmux launcher execvp's, so anything
        # after the call never reaches the terminal.
        click.echo(
            f"{head.id} started in a separate claude session "
            "— do *not* work on it yourself."
        )
        _run_task(_store(), proj, head, here=here)


def _scheduled_projects(project: str | None, all_projects: bool) -> list[str]:
    """Resolve the project set ``add-scheduled`` should scan.

    ``--all-projects`` enumerates every maelstrom-managed project (the launchd
    entry point); otherwise it's the single ``-p`` project or the cwd's.
    """
    if all_projects:
        from .context import load_global_config
        from .worktree import find_all_projects

        projects = find_all_projects(load_global_config().projects_dir)
        return [p.name for p in projects]
    return [_resolve_project(project)]


def _fire_due_templates(
    store: GitFileStore, project: str, *, now: datetime, run: bool, here: bool
) -> list["model.Task"]:
    """Create (and optionally launch) one run per due template in ``project``.

    Each fired template, in its own transaction: duplicate it into a date-keyed
    run (skipped if that id already exists → idempotent across RunAtLoad+interval
    double-fires) and advance its ``last-run`` watermark to the boundary. Returns
    the run tasks that were created this call.

    The run's id (``<tmpl>.<date>``) names it as a dot-child of the template, but
    its ``parent`` is deliberately left **empty** so it roots its own chain: the
    launcher exports ``MAEL_TASK_PARENT = run.id`` (via ``task.parent or task.id``),
    making each firing's follow-ups grandchildren of the template rather than
    piling onto the template's own chain. See docs/dev/tasks.md.
    """
    from . import schedule as sched

    created: list[model.Task] = []
    for tmpl, date in sched.due_templates(store, project, now=now):
        run_id = model.allocate_run_id(tmpl.id, date)
        if model.find_key(store, project, run_id) is not None:
            continue  # already fired this boundary
        prev = sched.previous_fire(tmpl.schedule, now)
        assert prev is not None  # due_templates only yields when a boundary exists
        with store.transaction(message=f"task: scheduled run {run_id}"):
            new = model.duplicate(
                store,
                project,
                tmpl.id,
                parent="",  # parentless → run roots its own chain (see docstring)
                branch=tmpl.branch,
                id=run_id,
            )
            model.update(store, project, tmpl.id, last_run=prev.isoformat())
        created.append(new)
    if run:
        for t in created:
            _run_task(store, project, t, here=here)
    return created


@task.command("add-scheduled")
@click.option("-p", "--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--all-projects",
    "all_projects",
    is_flag=True,
    help="Scan every maelstrom project (the launchd entry point).",
)
@click.option(
    "--run", is_flag=True, help="Launch each due run into a session (cmux workspace)."
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_add_scheduled(
    project: str | None, all_projects: bool, run: bool, here: bool
) -> None:
    """Fire every due template: duplicate it into a dated run and advance its watermark.

    The scheduler entry point invoked by the launchd agent. Thin: it computes the
    due templates and reuses the canonical duplicate/launch path — it owns only
    the cron/last-run/catch-up logic, never creation or launch.
    """
    now = datetime.now().astimezone()
    # Stamp every run so schedule.log records when the agent fired, even when
    # nothing is due — the answer to "did the scheduler run?" at diagnosis time.
    click.echo(f"[{now.isoformat(timespec='seconds')}] add-scheduled")
    store = _store()
    total = 0
    for proj in _scheduled_projects(project, all_projects):
        for t in _fire_due_templates(store, proj, now=now, run=run, here=here):
            click.echo(f"{proj}/{t.id}\t{t.title}")
            total += 1
    if total == 0:
        click.echo("No scheduled tasks due.")


@task.command("list")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--status", default=None, help="Filter by status (folder).")
@click.option("--parent", default=None, help="Filter by parent id.")
@click.option(
    "--all-todo",
    "all_todo",
    is_flag=True,
    help="Also show blocked-but-todo tasks (incomplete deps); still hides done/cancelled.",
)
@click.option(
    "--all",
    "all_",
    is_flag=True,
    help="Show everything, including done and cancelled. Takes precedence over --all-todo.",
)
def task_list(
    project: str | None,
    status: str | None,
    parent: str | None,
    all_todo: bool,
    all_: bool,
) -> None:
    """List actionable tasks (those that can be started now).

    By default only actionable tasks are shown. ``--all-todo`` also includes
    blocked-but-todo tasks (incomplete ``follows`` deps or in ``blocked/``);
    ``--all`` additionally includes done and cancelled. ``--status`` still
    constrains the folder scanned, so e.g. ``--status done`` without ``--all``
    naturally shows nothing.
    """
    proj = _resolve_project(project)
    store = _store()
    tasks = model.list_tasks(store, project=proj, status=status, parent=parent)
    if not tasks:
        click.echo("No tasks.")
        return

    # An explicit ``--status template`` is a direct window into the template
    # folder: templates are never actionable (so the default view hides them),
    # but asking for the folder by name should list them.
    show_all_in_folder = status == model.STATUS_TEMPLATE

    # Display order is priority-first (id as the within-band tie-break); the
    # gatherer stays id-sorted for dependency resolution, so sort here.
    tasks.sort(key=lambda t: (model.priority_rank(t.priority), t.id))

    rows = []
    for t in tasks:
        actionable = model.is_actionable(t, store)
        terminal = model.is_terminal(t.status)
        blocked = not actionable and not terminal
        if all_ or show_all_in_folder:
            visible = True
        elif all_todo:
            visible = actionable or blocked
        else:
            visible = actionable
        if not visible:
            continue
        row = {"ID": t.id, "STATUS": t.status, "PRIORITY": t.priority}
        if all_ or all_todo:
            row["ACTIONABLE"] = "yes" if actionable else "no"
        if show_all_in_folder:
            row["SCHEDULE"] = t.schedule or ""
            row["NEXT-FIRE"] = _next_fire_display(t)
        row["BRANCH"] = t.branch or model.default_branch(t.id, t.parent)
        row["TITLE"] = t.title
        rows.append(row)

    if not rows:
        click.echo("No tasks.")
        return

    if show_all_in_folder:
        columns = ["ID", "STATUS", "PRIORITY", "SCHEDULE", "NEXT-FIRE", "BRANCH", "TITLE"]
    elif all_ or all_todo:
        columns = ["ID", "STATUS", "PRIORITY", "ACTIONABLE", "BRANCH", "TITLE"]
    else:
        columns = ["ID", "STATUS", "PRIORITY", "BRANCH", "TITLE"]
    draw_table(rows, columns)


def _next_fire_display(task: "model.Task") -> str:
    """Render a template's next scheduled fire for the listing, or ''."""
    if not task.schedule:
        return ""
    from . import schedule as sched

    try:
        nxt = sched.next_fire(task.schedule, datetime.now().astimezone())
    except ValueError:
        return "(invalid)"
    return nxt.isoformat(timespec="minutes") if nxt else ""


@task.command("next")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--parent", default=None, help="Restrict to children of this id.")
@click.option("--run", is_flag=True, help="Launch the next actionable task as a session.")
@click.option(
    "-b",
    "--branch",
    default=None,
    help="Restrict to this branch (no fallback to other branches).",
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_next(
    project: str | None,
    parent: str | None,
    run: bool,
    branch: str | None,
    here: bool,
) -> None:
    """Print the id of the next actionable task.

    By default, prefers a task on the current git branch and falls back to the
    global next task. With ``--branch``, restricts strictly to that branch (no
    fallback).
    """
    proj = _resolve_project(project)
    store = _store()
    if branch is not None:
        effective_branch, fallback = branch, False
    else:
        effective_branch, fallback = _current_branch_or_none(), True
    nxt = model.next_task(
        store, proj, parent=parent, branch=effective_branch, fallback=fallback
    )
    if nxt is None:
        raise click.ClickException("No actionable task.")
    if run:
        _run_task(store, proj, nxt, here=here)
    else:
        click.echo(nxt.id)


@task.command("run")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--here",
    is_flag=True,
    help="Launch in the current shell (no worktree, no new workspace).",
)
def task_run(id: str, project: str | None, here: bool) -> None:
    """Launch a task as a Claude session (ensures its worktree first)."""
    proj = _resolve_project(project)
    store = _store()
    try:
        t = model.load(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    _run_task(store, proj, t, here=here)


def _live_sessions_by_task(
    store: GitFileStore, project: str
) -> dict[str, "session_discovery.LiveSession"]:
    """Map ``task_id -> live LiveSession`` for every task in ``project``.

    Correlates live ``claude`` processes to the task notebook by worktree: a
    session's cwd resolves to a worktree, which resolves to a branch, and every
    in-progress task sharing that branch is attributed the session. Chain
    siblings share one branch (one PR per parent), so a single live worktree can
    map to several in-progress tasks — the right granularity for ``reconcile``,
    which only distinguishes in-progress from not. This is the same liveness
    source (:func:`session_discovery.all_live_sessions`) the run-guard uses, so
    the two always agree. Tasks whose worktree has no live session are omitted.
    """
    ctx = resolve_context(project, require_project=True, arg_is_project=True)
    project_path = ctx.project_path
    if project_path is None or not project_path.exists():
        return {}

    sessions = session_discovery.all_live_sessions()
    if not sessions:
        return {}

    # branch -> the live session running in its worktree (first match wins).
    # One shared worktree-list memo so `git worktree list` runs once, not per
    # branch resolved.
    cache: dict = {}
    branch_session: dict[str, session_discovery.LiveSession] = {}
    for wt in list_worktrees(project_path):
        if not wt.branch or wt.branch in branch_session:
            continue
        session = session_discovery.active_session_for_worktree(
            wt.path, sessions, cache
        )
        if session is not None:
            branch_session[wt.branch] = session

    mapping: dict[str, session_discovery.LiveSession] = {}
    for task in model.list_tasks(store, project=project):
        branch = task.branch or model.default_branch(task.id, task.parent)
        session = branch_session.get(branch)
        if session is not None:
            mapping[task.id] = session
    return mapping


@task.command("reconcile")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--fix",
    is_flag=True,
    help="Apply the suggested corrections (default: dry-run table only).",
)
def task_reconcile(project: str | None, fix: bool) -> None:
    """Reconcile in-progress tasks against live Claude sessions.

    Liveness comes from live ``claude`` processes by cwd (via
    ``session_discovery``), the same source ``mael task run``'s duplicate-launch
    guard uses, so the two always agree. Lists the full picture — healthy (OK)
    rows included — and flags two mismatch classes: a stale ``in-progress`` task
    with no live session (→ ``done``) and a live session whose task isn't
    ``in-progress`` (→ ``in-progress``). With ``--fix`` the suggested moves are
    applied; without it, nothing changes and a hint is printed if any fix is
    pending.

    Because correlation keys strictly off tasks that still exist, a live
    session whose task was *deleted* mid-run is no longer surfaced as an orphan;
    every existing task's session is still reconciled.
    """
    proj = _resolve_project(project)
    store = _store()
    session_task_ids = _live_sessions_by_task(store, proj)
    rows = model.reconcile(store, proj, session_task_ids=session_task_ids)

    if not rows:
        click.echo("No in-progress tasks or live task sessions.")
        return

    _STATE_LABEL = {
        model.RECONCILE_OK: "OK",
        model.RECONCILE_STALE: "NO SESSION",
        model.RECONCILE_ORPHAN: "NO TASK",
    }
    _FIX_LABEL = {
        model.STATUS_DONE: "→ done",
        model.STATUS_IN_PROGRESS: "→ in-progress",
    }

    table_rows = []
    for r in rows:
        sess = str(r.session.pid) if r.session is not None else ""
        table_rows.append({
            "STATE": _STATE_LABEL.get(r.state, r.state),
            "TASK": f"{r.task_id} ({r.task_status})",
            "SESSION/PID": sess,
            "SUGGESTED FIX": _FIX_LABEL.get(r.fix_status or "", ""),
        })
    draw_table(
        table_rows, ["STATE", "TASK", "SESSION/PID", "SUGGESTED FIX"]
    )

    fixable = [r for r in rows if r.fix_status is not None]
    if not fix:
        if fixable:
            click.echo(
                f"\n{len(fixable)} task(s) need correcting — re-run with --fix."
            )
        return

    if not fixable:
        click.echo("\nNothing to fix.")
        return

    for r in fixable:
        try:
            task_actions.move_with_actions(
                store, proj, r.task_id, r.fix_status
            )
        except KeyError:
            click.echo(f"  skipped {r.task_id}: task no longer exists", err=True)
            continue
        click.echo(f"  {r.task_id}: {r.task_status} -> {r.fix_status}")


@task.command("show")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_show(id: str, project: str | None) -> None:
    """Show a summary of a task."""
    proj = _resolve_project(project)
    store = _store()
    try:
        t = model.load(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(f"id:      {t.id}")
    click.echo(f"title:   {t.title}")
    click.echo(f"status:  {t.status}")
    click.echo(f"project: {t.project}")
    click.echo(f"command: {t.command}")
    click.echo(f"mode:    {t.mode}")
    click.echo(f"priority: {t.priority}")
    click.echo(f"branch:  {t.branch}")
    if t.parent:
        click.echo(f"parent:  {t.parent}")
    if t.follows:
        click.echo(f"follows: {', '.join(t.follows)}")
    if t.schedule:
        click.echo(f"schedule: {t.schedule}")
    if t.last_run:
        click.echo(f"last-run: {t.last_run}")
    click.echo(f"created: {t.created}")
    click.echo(f"updated: {t.updated}")
    click.echo(f"actionable: {'yes' if model.is_actionable(t, store) else 'no'}")
    if t.content:
        click.echo("\n## Content\n")
        click.echo(t.content)


@task.command("read")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_read(id: str, project: str | None) -> None:
    """Print the raw task file."""
    proj = _resolve_project(project)
    store = _store()
    key = model.find_key(store, proj, id)
    if key is None:
        raise click.ClickException(f"Task not found: {id}")
    text = store.read(key)
    if text is None:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(text, nl=False)


@task.command("prompt")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_prompt(id: str, project: str | None) -> None:
    """Print the initial Claude prompt for a task (for ``... | claude``)."""
    proj = _resolve_project(project)
    try:
        task = model.load(_store(), proj, id)  # raises if not found
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(model.build_prompt(task), nl=False)


@task.command("log")
@click.argument("id")
@click.argument("msg")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_log(id: str, msg: str, project: str | None) -> None:
    """Append a line to a task's log."""
    proj = _resolve_project(project)
    store = _store()
    try:
        model.append_log(store, proj, id, msg)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(f"Logged to {id}.")


@task.command("update")
@click.argument("id")
@click.argument("title", required=False)
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--id",
    "new_id",
    default=None,
    help="Re-key the task to this id, rewriting follows/parent references.",
)
@click.option("--branch", default=None, help="Set the task's branch.")
@click.option("--command", default=None, help="Set the task's command/skill the session launches with.")
@click.option("--mode", default=None, help="Set the task's mode (e.g. normal, plan).")
@click.option(
    "--priority",
    type=click.Choice(model.PRIORITIES),
    default=None,
    help="Set the task's priority (affects list ordering and `task next`).",
)
@click.option(
    "--pre-action",
    "pre_action",
    default=None,
    help="Set the start lifecycle action (pass '' to clear).",
)
@click.option(
    "--post-action",
    "post_action",
    default=None,
    help="Set the finish lifecycle action (pass '' to clear).",
)
@click.option(
    "--schedule",
    default=None,
    help="Set the cron schedule (acted on only for template tasks; '' clears).",
)
@click.option(
    "--content-file",
    default=None,
    help="File whose contents replace the Content section ('-' reads stdin).",
)
def task_update(
    id: str,
    title: str | None,
    project: str | None,
    new_id: str | None,
    branch: str | None,
    command: str | None,
    mode: str | None,
    priority: str | None,
    pre_action: str | None,
    post_action: str | None,
    schedule: str | None,
    content_file: str | None,
) -> None:
    """Update a task's fields (title, branch, command, mode, actions, schedule, content).

    With ``--id`` the task is re-keyed first (rewriting follows/parent references
    that point at it), then the remaining field updates apply to the new id.
    """
    proj = _resolve_project(project)
    store = _store()
    content = _read_content_file(content_file) if content_file is not None else None

    target = id
    renamed = False
    if new_id is not None and new_id != id:
        # Refuse re-keying a running task — its deterministic session_id and its
        # worktree/branch are tied to the old id, so renaming would orphan a live
        # Claude session.
        try:
            t = model.load(store, proj, id)
        except KeyError:
            raise click.ClickException(f"Task not found: {id}")
        if t.status == model.STATUS_IN_PROGRESS:
            raise click.ClickException(
                f"Cannot change the id of in-progress task {id}; move it back to todo first."
            )
        # Rename intentionally uses the registry check (any *registered*
        # session, not just a live one): re-keying a task out from under a
        # session that recorded the old id — even a stale entry — is unsafe,
        # whereas a relaunch (which uses the stricter is_live discovery) only
        # needs to avoid a genuinely racing process.
        if session_store.find_live_session_for_task(proj, id) is not None:
            raise click.ClickException(
                f"Task {id} has an open Claude session; close it before changing its id."
            )
        try:
            model.rename(store, proj, id, new_id)
        except KeyError:
            raise click.ClickException(f"Task not found: {id}")
        except ValueError as e:
            raise click.ClickException(str(e))
        target = new_id
        renamed = True

    try:
        model.update(
            store, proj, target, title=title, branch=branch, content=content,
            command=command, mode=mode, priority=priority,
            pre_action=pre_action, post_action=post_action,
            schedule=schedule,
        )
    except KeyError:
        raise click.ClickException(f"Task not found: {target}")
    except ValueError as e:
        raise click.ClickException(str(e))
    if renamed:
        click.echo(f"Renamed {id} -> {target}.")
    click.echo(f"Updated {target}.")


@task.command("edit")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_edit(id: str, project: str | None) -> None:
    """Open the task file in $EDITOR (vi); commit if changed."""
    proj = _resolve_project(project)
    store = _store()
    try:
        _task, changed = model.edit_in_editor(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    except RuntimeError as e:
        raise click.ClickException(str(e))
    click.echo(f"Updated {id}." if changed else f"No changes to {id}.")


@task.command("rm")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_rm(id: str, project: str | None) -> None:
    """Delete a task and strip it from any dependents' follows lists."""
    proj = _resolve_project(project)
    store = _store()
    try:
        model.delete(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(f"Deleted {id}.")


@task.group("status")
def task_status() -> None:
    """Move a task between lifecycle states."""


def _status_command(name: str, status: str, help_text: str):
    @task_status.command(name)
    @click.argument("id", required=False)
    @click.option("--project", default=None, help="Project name (default: from cwd).")
    def _cmd(id: str | None, project: str | None) -> None:
        task_id = _resolve_task_id(id)
        proj = _resolve_project(project)
        store = _store()
        try:
            task_actions.move_with_actions(store, proj, task_id, status)
        except KeyError:
            raise click.ClickException(f"Task not found: {task_id}")
        click.echo(f"{task_id} -> {status}")
        if status == model.STATUS_DONE:
            running = model.running_follower(store, proj, task_id)
            if running is not None:
                title = f" - {running.title}" if running.title else ""
                click.echo()
                click.echo(
                    f"The following task is already in-progress:\n  {running.id}{title}"
                )
            else:
                nxt = model.next_follower(store, proj, task_id)
                if nxt is not None:
                    title = f" - {nxt.title}" if nxt.title else ""
                    click.echo()
                    click.echo(
                        "mael task next --run will run the following task in a new session:"
                    )
                    click.echo(f"  {nxt.id}{title}")

    _cmd.__doc__ = help_text
    return _cmd


_status_command("todo", model.STATUS_TODO, "Move a task back to todo.")
_status_command("start", model.STATUS_IN_PROGRESS, "Move a task to in-progress.")
_status_command("done", model.STATUS_DONE, "Move a task to done.")
_status_command("cancel", model.STATUS_CANCELLED, "Move a task to cancelled.")
_status_command("block", model.STATUS_BLOCKED, "Move a task to blocked.")
_status_command(
    "template", model.STATUS_TEMPLATE, "Park a task as a reusable template."
)
