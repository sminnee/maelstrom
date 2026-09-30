"""CLI commands for maelstrom-aware projects."""

import json

import click

from mael_common.cli_async import AsyncGroup
from mael_common.util import abbreviate_home
from mael_domain.context import load_global_config
from mael_domain.worktree import list_projects

from .json_flag import wants_json
from .mv_project_cli import cmd_mv_project
from .table_cli import draw_table


@click.group("project", cls=AsyncGroup)
def project() -> None:
    """Add, create, rename and list maelstrom-aware projects."""


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


project.add_command(cmd_mv_project)
