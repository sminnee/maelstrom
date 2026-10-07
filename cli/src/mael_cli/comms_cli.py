"""``mael comms``: what each tracked task's landing has reached.

See ``CONTEXT.md``, "Landing". The orchestrator server writes the rows on its
worktree poll; this command only reads them.
"""

from datetime import datetime, timezone

import click

from mael_common.cli_async import AsyncGroup
from mael_domain.config import DEPLOY_STEPS
from mael_domain.context import load_global_config
from mael_domain.landing import Landings, project_config, tracked_tasks
from mael_domain.landing_store import SqlitePullRequestStore, SqliteTaskStepStore
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


@click.group(cls=AsyncGroup)
def comms() -> None:
    """Follow each task's landing: merged, then each deploy."""


@comms.command("list")
@click.option(
    "--since",
    default=None,
    help="List the steps recorded after this ISO time (UTC when it has no "
    "offset), instead of each task.",
)
async def cmd_list(since: str | None) -> None:
    """Print each tracked task's landing status and deploy states."""
    since = _utc(since)
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        try:
            await db.check()
        except StateDbError as exc:
            raise click.ClickException(str(exc)) from exc
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
            projects = [p.name for p in find_all_projects(projects_dir)]
            return await tracked_tasks(table, projects)

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
    finally:
        db.close()
