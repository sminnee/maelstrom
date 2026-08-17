"""CLI commands for inspecting maelstrom-aware projects."""

import json

import click

from .context import load_global_config
from .table import draw_table
from .util import abbreviate_home
from .worktree import list_projects


@click.group("project")
def project() -> None:
    """Inspect maelstrom-aware projects."""


@project.command("list")
def project_list() -> None:
    """List maelstrom-aware projects under the configured projects directory."""
    output_json = click.get_current_context().obj.get("json", False)
    global_config = load_global_config()

    projects = list_projects(global_config.projects_dir)

    if output_json:
        click.echo(json.dumps({
            "projects": [
                {
                    "name": p.name,
                    "path": str(p.path),
                    "worktree_count": p.worktree_count,
                }
                for p in projects
            ]
        }))
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
