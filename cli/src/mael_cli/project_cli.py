"""CLI commands for maelstrom-aware projects."""

import json

import click

from mael_common.cli_async import AsyncGroup
from mael_common.util import abbreviate_home
from mael_domain.context import load_global_config, resolve_context
from mael_domain.worktree import list_projects

from .json_flag import wants_json
from .mv_project_cli import cmd_mv_project
from .table_cli import draw_table


@click.group("project", cls=AsyncGroup)
def project() -> None:
    """Add, create, rename, list and check maelstrom-aware projects."""


@project.command("list")
def project_list() -> None:
    """List maelstrom-aware projects under the configured projects directory."""
    output_json = wants_json()
    global_config = load_global_config()

    projects = list_projects(global_config.projects_dir)

    if output_json:
        click.echo(
            json.dumps(
                {
                    "projects": [
                        {
                            "name": p.name,
                            "path": str(p.path),
                            "worktree_count": p.worktree_count,
                        }
                        for p in projects
                    ]
                }
            )
        )
        return

    if not projects:
        click.echo("No projects found.")
        return

    rows = [
        {
            "PROJECT": p.name,
            "PATH": abbreviate_home(p.path),
            "WORKTREES": str(p.worktree_count),
        }
        for p in projects
    ]
    draw_table(rows, ["PROJECT", "PATH", "WORKTREES"])


@project.command("check")
@click.argument("project", required=False)
def cmd_project_check(project: str | None) -> None:
    """Check a project's health, and fix the safe issues."""
    from .project_check import CheckStatus, run_project_check

    try:
        ctx = resolve_context(
            project,
            require_project=True,
            require_worktree=False,
            arg_is_project=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    project_path = ctx.project_path
    if not project_path or not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found")

    click.echo(f"Checking project: {ctx.project}")
    result = run_project_check(project_path)

    status_icons = {
        CheckStatus.OK: click.style("✓", fg="green"),
        CheckStatus.FIXED: click.style("✗", fg="yellow"),
        CheckStatus.WARNING: click.style("⚠", fg="yellow"),
        CheckStatus.ERROR: click.style("✗", fg="red"),
    }

    for check in result.checks:
        icon = status_icons[check.status]
        suffix = ""
        if check.status == CheckStatus.FIXED:
            suffix = " → fixed"
        click.echo(f"  {icon} {check.message}{suffix}")

    click.echo()
    if result.issues_found == 0:
        click.echo("All checks passed.")
    else:
        parts = []
        if result.fixed_count:
            parts.append(f"{result.fixed_count} fixed")
        if result.attention_count:
            parts.append(f"{result.attention_count} require(s) attention")
        click.echo(f"{result.issues_found} issue(s) found: {', '.join(parts)}")

    if result.attention_count > 0:
        raise SystemExit(1)


project.add_command(cmd_mv_project)
