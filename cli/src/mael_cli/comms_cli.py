"""``mael comms``: change comms, and read each tracked task's landing.

See ``CONTEXT.md``, "Comm" and "Landing". The orchestrator server writes the
landing rows on its worktree poll; ``comms landings`` only reads them.
"""

from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import click

from mael_common.cli_async import AsyncGroup
from mael_domain import comms as comms_model
from mael_domain.comm_store import SqliteCommStore
from mael_domain.config import DEPLOY_STEPS
from mael_domain.context import (
    load_global_config,
    resolve_context,
    resolve_project,
)
from mael_domain.landing import Landings, project_config, tracked_tasks
from mael_domain.landing_store import SqlitePullRequestStore, SqliteTaskStepStore
from mael_domain.state_db.db import StateDb
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path
from mael_domain.state_db.types import StateDbError
from mael_domain.task_table import SqliteTaskTable
from mael_domain.worktree import find_all_projects

from .table_cli import draw_table

_ENV_COLUMNS = [step.upper() for step in DEPLOY_STEPS]


def _utc(value: str | None) -> str | None:
    """``value`` as the UTC ISO string the server records, so the compare is exact.

    A time with no offset is read as UTC.
    """
    if value is None:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter(f"not an ISO time: {value}") from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


@asynccontextmanager
async def _db() -> AsyncIterator[StateDb]:
    """The state database, checked against this build's schema."""
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        try:
            await db.check()
        except StateDbError as exc:
            raise click.ClickException(str(exc)) from exc
        yield db
    finally:
        db.close()


def _projects() -> list[str]:
    return [p.name for p in find_all_projects(load_global_config().projects_dir)]


def _check_project(project: str | None) -> None:
    if project and project not in _projects():
        raise click.ClickException(f"Unknown project: {project}")


def _cwd_project() -> str:
    """The project of the current directory, or blank outside every project."""
    return resolve_context(None).project or ""


async def _require(store: SqliteCommStore, id: str) -> None:
    if await store.read(id) is None:
        raise click.ClickException(f"Comm not found: {id}")


@click.group(cls=AsyncGroup)
def comms() -> None:
    """Change comms, and follow each task's landing."""


@comms.command("new")
@click.argument("title")
@click.option("--content", default="", help="The request, as it came in.")
@click.option(
    "--to",
    "recipients",
    multiple=True,
    help="A recipient: a channel, an address, or a note (repeatable).",
)
@click.option("--category", default="", help="The comm's category: free text.")
@click.option(
    "--project",
    default=None,
    help="The project a task made from the comm goes to. Default: the category's "
    "default project, else the project of the current directory.",
)
async def cmd_new(
    title: str,
    content: str,
    recipients: tuple[str, ...],
    category: str,
    project: str | None,
) -> None:
    """Create a comm and print its id."""
    _check_project(project)
    async with _db() as db:
        store = SqliteCommStore(db)
        if project is None:
            usual = comms_model.default_project(await store.list(), category.strip())
            project = usual or _cwd_project()
        try:
            comm = await comms_model.new(
                store,
                title,
                content,
                list(recipients),
                category=category,
                project=project,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    click.echo(comm.id)


@comms.command("list")
@click.option("--all", "all_", is_flag=True, help="Show closed comms too.")
@click.option("--category", default=None, help="Show only this category.")
async def cmd_list(all_: bool, category: str | None) -> None:
    """Print the open comms, with the count of tasks linked to each."""
    async with _db() as db:
        table = SqliteTaskTable(db)
        # One scan of the tasks, counted per comm.
        linked: Counter[str] = Counter()
        for project in _projects():
            for task in await table.list(project):
                linked.update(task.comms)
        rows = []
        for comm in await SqliteCommStore(db).list():
            if comm.closed_at and not all_:
                continue
            if category is not None and comm.category != category.strip():
                continue
            rows.append(
                {
                    "ID": comm.id,
                    "TITLE": comm.title,
                    "CATEGORY": comm.category,
                    "PROJECT": comm.project,
                    "RECIPIENTS": ", ".join(comm.recipients),
                    "TASKS": str(linked[comm.id]),
                }
            )
    if not rows:
        click.echo("No comms." if all_ else "No open comms.")
        return
    draw_table(rows, ["ID", "TITLE", "CATEGORY", "PROJECT", "RECIPIENTS", "TASKS"])


@comms.command("close")
@click.argument("comm")
async def cmd_close(comm: str) -> None:
    """Close a comm, so it leaves the open list."""
    async with _db() as db:
        store = SqliteCommStore(db)
        await _require(store, comm)
        await comms_model.close(store, comm)


@comms.command("link")
@click.argument("comm")
@click.argument("tasks", nargs=-1, required=True)
@click.option("--unlink", is_flag=True, help="Remove the link instead.")
@click.option("--project", default=None, help="Project name (default: from cwd).")
async def cmd_link(
    comm: str, tasks: tuple[str, ...], unlink: bool, project: str | None
) -> None:
    """Link tasks to a comm, or unlink them. All or nothing."""
    try:
        proj = resolve_project(project)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    async with _db() as db:
        await _require(SqliteCommStore(db), comm)
        relink = comms_model.unlink if unlink else comms_model.link
        try:
            await relink(SqliteTaskTable(db), comm, proj, list(tasks))
        except KeyError as exc:
            raise click.ClickException(str(exc.args[0])) from exc


@comms.command("edit")
@click.argument("comm")
@click.option("--title", default=None, help="Set the title.")
@click.option("--content", default=None, help="Set the content.")
@click.option(
    "--to", "recipients", multiple=True, help="Set the recipients (repeatable)."
)
@click.option("--clear-to", is_flag=True, help="Remove every recipient.")
@click.option("--category", default=None, help="Set the category ('' clears it).")
@click.option("--project", default=None, help="Set the project ('' clears it).")
async def cmd_edit(
    comm: str,
    title: str | None,
    content: str | None,
    recipients: tuple[str, ...],
    clear_to: bool,
    category: str | None,
    project: str | None,
) -> None:
    """Change a comm. With no option, edit its content in $EDITOR."""
    _check_project(project)
    async with _db() as db:
        store = SqliteCommStore(db)
        current = await store.read(comm)
        if current is None:
            raise click.ClickException(f"Comm not found: {comm}")
        given = (title, content, category, project)
        if all(v is None for v in given) and not recipients and not clear_to:
            # click.edit returns None when the editor closes without a save.
            content = click.edit(current.content)
            if content is None:
                return
        try:
            await comms_model.edit(
                store,
                comm,
                title=title,
                content=content,
                recipients=[] if clear_to else (list(recipients) or None),
                category=category,
                project=project,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc


@comms.command("landings")
@click.option(
    "--since",
    default=None,
    help="List the steps recorded after this ISO time (UTC when it has no "
    "offset), instead of each task.",
)
async def cmd_landings(since: str | None) -> None:
    """Print each tracked task's landing status and deploy states."""
    since = _utc(since)
    async with _db() as db:
        if since is not None:
            draw_table(
                [
                    {
                        "RECORDED": e.recorded_at,
                        "PROJECT": e.project,
                        "TASK": e.task_id,
                        "PR": f"#{e.pr_number}" if e.pr_number else "-",
                        "STEP": e.step,
                        "AT": e.at,
                    }
                    for e in await SqliteTaskStepStore(db).list(since)
                ],
                ["RECORDED", "PROJECT", "TASK", "PR", "STEP", "AT"],
            )
            return
        projects_dir = load_global_config().projects_dir
        table = SqliteTaskTable(db)

        async def tracked():
            return await tracked_tasks(table, _projects())

        landings = Landings(
            prs=SqlitePullRequestStore(db),
            tracked=tracked,
            config_for=lambda project: project_config(projects_dir, project),
        )
        rows = []
        for row in await landings.report():
            envs = row.pr.envs if row.pr is not None else {}
            rows.append(
                {
                    "PROJECT": row.task.project,
                    "TASK": row.task.task_id,
                    "PR": f"#{row.task.pr_number}",
                    "STATUS": row.status or "-",
                    **{
                        step.upper(): envs[step].state if step in envs else "-"
                        for step in DEPLOY_STEPS
                    },
                }
            )
        draw_table(rows, ["PROJECT", "TASK", "PR", "STATUS", *_ENV_COLUMNS])
