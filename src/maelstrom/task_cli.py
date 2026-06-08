"""Thin CLI for the task notebook: ``mael task ...``.

Each command builds a :class:`~maelstrom.task_store.GitFileStore`, calls a single
model function from :mod:`maelstrom.task`, and renders the result. All logic
lives in the model; this layer only parses arguments and prints.
"""

from pathlib import Path

import click

from . import task as model  # noqa: F401  (module, used as `model.*`)
from .context import resolve_context
from .table import draw_table
from .task_store import GitFileStore


def _store() -> GitFileStore:
    return GitFileStore()


def _resolve_project(project: str | None) -> str:
    """Return the project name, defaulting to the cwd's project."""
    if project:
        return project
    ctx = resolve_context(None, require_project=True)
    assert ctx.project is not None  # require_project guarantees this
    return ctx.project


@click.group("task")
def task() -> None:
    """Manage the git-backed task notebook."""


@task.command("add")
@click.argument("title")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--command", default="", help="Command to launch the session with.")
@click.option("--mode", default="normal", help="Session mode (default: normal).")
@click.option(
    "--branch", default="", help="Branch for the task (default: the task id)."
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
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="File whose contents become the task's Content section.",
)
def task_add(
    title: str,
    project: str | None,
    command: str,
    mode: str,
    branch: str,
    parent: str,
    follows: tuple[str, ...],
    follow_ends: tuple[str, ...],
    content_file: Path | None,
) -> None:
    """Add a new task and print its id."""
    proj = _resolve_project(project)
    store = _store()

    follow_list = list(follows)
    for end_id in follow_ends:
        follow_list.extend(model.follow_end_leaves(store, proj, end_id))
    # De-dupe while preserving first-seen order.
    deduped = list(dict.fromkeys(follow_list))

    content = content_file.read_text() if content_file else ""

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


@task.command("list")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--status", default=None, help="Filter by status (folder).")
@click.option("--parent", default=None, help="Filter by parent id.")
def task_list(project: str | None, status: str | None, parent: str | None) -> None:
    """List tasks in a table."""
    proj = _resolve_project(project)
    store = _store()
    tasks = model.list_tasks(store, project=proj, status=status, parent=parent)
    if not tasks:
        click.echo("No tasks.")
        return
    rows = []
    for t in tasks:
        rows.append({
            "ID": t.id,
            "STATUS": t.status,
            "ACTIONABLE": "yes" if model.is_actionable(t, store) else "no",
            "TITLE": t.title,
        })
    draw_table(rows, ["ID", "STATUS", "ACTIONABLE", "TITLE"])


@task.command("next")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--parent", default=None, help="Restrict to children of this id.")
def task_next(project: str | None, parent: str | None) -> None:
    """Print the id of the next actionable task."""
    proj = _resolve_project(project)
    store = _store()
    nxt = model.next_task(store, proj, parent=parent)
    if nxt is None:
        raise click.ClickException("No actionable task.")
    click.echo(nxt.id)


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


def _move_command(name: str, status: str, help_text: str):
    @task.command(name)
    @click.argument("id")
    @click.option("--project", default=None, help="Project name (default: from cwd).")
    def _cmd(id: str, project: str | None) -> None:
        proj = _resolve_project(project)
        store = _store()
        try:
            model.move(store, proj, id, status)
        except KeyError:
            raise click.ClickException(f"Task not found: {id}")
        click.echo(f"{id} -> {status}")

    _cmd.__doc__ = help_text
    return _cmd


_move_command("start", model.STATUS_IN_PROGRESS, "Move a task to in-progress.")
_move_command("done", model.STATUS_DONE, "Move a task to done.")
_move_command("cancel", model.STATUS_CANCELLED, "Move a task to cancelled.")
_move_command("block", model.STATUS_BLOCKED, "Move a task to blocked.")
