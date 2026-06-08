"""Thin CLI for the task notebook: ``mael task ...``.

Each command builds a :class:`~maelstrom.task_store.GitFileStore`, calls a single
model function from :mod:`maelstrom.task`, and renders the result. All logic
lives in the model; this layer only parses arguments and prints.
"""

import os
import sys
from pathlib import Path

import click

from . import task as model  # noqa: F401  (module, used as `model.*`)
from .context import resolve_context
from .table import draw_table
from .task_store import GitFileStore
from .worktree import setup_worktree_for_branch, start_claude_session


def _store() -> GitFileStore:
    return GitFileStore()


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


def _run_task(store: GitFileStore, project: str, task: "model.Task") -> None:
    """Ensure a worktree for the task, mark it in-progress, then launch Claude.

    ``start_claude_session`` may ``execvp`` (replacing the process), so every
    store write MUST complete before it is called.
    """
    ctx = resolve_context(project, require_project=True, arg_is_project=True)
    project_path = ctx.project_path
    if project_path is None or not project_path.exists():
        raise click.ClickException(
            f"Project '{project}' not found at {project_path}"
        )
    branch = task.branch or model.default_branch(task.id)
    result = setup_worktree_for_branch(project_path, project, branch)
    model.move(store, project, task.id, model.STATUS_IN_PROGRESS)  # write BEFORE launch
    click.echo(f"Running {task.id} on {branch}")
    click.echo(f"  → {project}/{result.name} ({result.action})")
    # Skills running inside the session self-reference via these — e.g. to
    # `mael task done $MAEL_TASK_ID` and `--follow-end linear.<parent>`.
    session_env = {"MAEL_TASK_ID": task.id}
    if task.parent:
        session_env["MAEL_TASK_PARENT"] = task.parent
    start_claude_session(
        result.path,
        project=project,
        worktree=result.name,
        initial_prompt=model.build_prompt(task),
        permission_mode=model._permission_mode_for(task.mode),
        env=session_env,
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


@click.group("task")
def task() -> None:
    """Manage the git-backed task notebook."""


@task.command("add")
@click.argument("title")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--command", default="", help="Command to launch the session with.")
@click.option(
    "--mode",
    default="",
    help="Session mode (default: per-command, usually normal; plan commands plan).",
)
@click.option(
    "--branch", default="", help="Branch for the task (default: task/<id>)."
)
@click.option("--parent", default="", help="Parent task id (creates a child id).")
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
@click.option("--run", is_flag=True, help="Launch the task as a session immediately.")
def task_add(
    title: str,
    project: str | None,
    command: str,
    mode: str,
    branch: str,
    parent: str,
    follows: tuple[str, ...],
    follow_ends: tuple[str, ...],
    content_file: str | None,
    run: bool,
) -> None:
    """Add a new task and print its id."""
    content = _read_content_file(content_file)
    add_task(
        title=title,
        project=project,
        command=command,
        mode=mode,
        branch=branch,
        parent=parent,
        follows=follows,
        follow_ends=follow_ends,
        content=content,
        run=run,
    )


def add_task(
    *,
    title: str,
    project: str | None,
    command: str = "",
    mode: str = "",
    branch: str = "",
    parent: str = "",
    follows: tuple[str, ...] = (),
    follow_ends: tuple[str, ...] = (),
    content: str = "",
    run: bool = False,
) -> "model.Task":
    """Create a task (and optionally launch it), echoing its id.

    The single create-and-launch path shared by ``mael task add`` and any other
    command that creates a task (e.g. ``mael linear plan``), so there is exactly
    one place that resolves follows, creates the task, and runs it.
    """
    proj = _resolve_project(project)
    store = _store()

    follow_list = list(follows)
    for end_id in follow_ends:
        follow_list.extend(model.follow_end_leaves(store, proj, end_id))
    # De-dupe while preserving first-seen order.
    deduped = list(dict.fromkeys(follow_list))

    new = model.create(
        store,
        project=proj,
        title=title,
        command=command,
        mode=mode,
        branch=branch,
        parent=parent,
        follows=deduped,
        content=content,
    )
    click.echo(new.id)
    if run:
        _run_task(store, proj, new)
    return new


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

    rows = []
    for t in tasks:
        actionable = model.is_actionable(t, store)
        terminal = model.is_terminal(t.status)
        blocked = not actionable and not terminal
        if all_:
            visible = True
        elif all_todo:
            visible = actionable or blocked
        else:
            visible = actionable
        if not visible:
            continue
        row = {"ID": t.id, "STATUS": t.status}
        if all_ or all_todo:
            row["ACTIONABLE"] = "yes" if actionable else "no"
        row["TITLE"] = t.title
        rows.append(row)

    if not rows:
        click.echo("No tasks.")
        return

    columns = (
        ["ID", "STATUS", "ACTIONABLE", "TITLE"]
        if (all_ or all_todo)
        else ["ID", "STATUS", "TITLE"]
    )
    draw_table(rows, columns)


@task.command("next")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--parent", default=None, help="Restrict to children of this id.")
@click.option("--run", is_flag=True, help="Launch the next actionable task as a session.")
def task_next(project: str | None, parent: str | None, run: bool) -> None:
    """Print the id of the next actionable task."""
    proj = _resolve_project(project)
    store = _store()
    nxt = model.next_task(store, proj, parent=parent)
    if nxt is None:
        raise click.ClickException("No actionable task.")
    if run:
        _run_task(store, proj, nxt)
    else:
        click.echo(nxt.id)


@task.command("run")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_run(id: str, project: str | None) -> None:
    """Launch a task as a Claude session (ensures its worktree first)."""
    proj = _resolve_project(project)
    store = _store()
    try:
        t = model.load(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    _run_task(store, proj, t)


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
    click.echo(f"branch:  {t.branch}")
    if t.parent:
        click.echo(f"parent:  {t.parent}")
    if t.follows:
        click.echo(f"follows: {', '.join(t.follows)}")
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
            model.move(store, proj, task_id, status)
        except KeyError:
            raise click.ClickException(f"Task not found: {task_id}")
        click.echo(f"{task_id} -> {status}")

    _cmd.__doc__ = help_text
    return _cmd


_status_command("start", model.STATUS_IN_PROGRESS, "Move a task to in-progress.")
_status_command("done", model.STATUS_DONE, "Move a task to done.")
_status_command("cancel", model.STATUS_CANCELLED, "Move a task to cancelled.")
_status_command("block", model.STATUS_BLOCKED, "Move a task to blocked.")
