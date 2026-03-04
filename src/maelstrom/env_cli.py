"""CLI commands for managing dev environments."""

import sys
import time
from pathlib import Path

import click

from .cmux import close_surface, is_cmux_mode, open_browser_pane
from .context import resolve_context
from .env import (
    EnvState,
    format_uptime,
    get_env_status,
    get_log_files,
    get_shared_status,
    list_all_envs,
    list_project_envs,
    load_env_state,
    read_service_logs,
    save_env_state,
    start_env,
    stop_all_envs,
    stop_env,
)
from .ports import get_app_url
from .table import draw_table


def _env_service_columns(state: EnvState) -> tuple[str, str]:
    """Return (running_services, stopped_services) as comma-separated names."""
    statuses = list(get_env_status(state.project, state.worktree) or [])

    # Include shared services
    shared_statuses = get_shared_status(state.project)
    if shared_statuses:
        statuses.extend(shared_statuses)

    running = [s.name for s in statuses if s.alive]
    stopped = [s.name for s in statuses if not s.alive]
    return ", ".join(running), ", ".join(stopped)


def _get_app_display(project_path: Path, worktree: str) -> str:
    """Return the APP display string for a worktree (URL or *port)."""
    app_info = get_app_url(project_path, worktree)
    if app_info:
        url, is_running = app_info
        port = url.split(":")[-1]
        return url if is_running else f"*{port}"
    return ""


@click.group("env")
def env():
    """Manage dev environments (start/stop/list services)."""
    pass


def _print_service_status(
    project: str, worktree: str, project_path: Path | None = None,
) -> None:
    """Print a SERVICE/PID/STATUS/LOG table for an environment."""
    state = load_env_state(project, worktree)
    if not state:
        click.echo(f"No environment state for {project}/{worktree}.")
        return

    statuses = get_env_status(project, worktree)

    header_parts = []
    if project_path:
        app_display = _get_app_display(project_path, worktree)
        if app_display:
            header_parts.append(f"APP RUNNING AT: {app_display}")
    header_parts.append(f"UPTIME: {format_uptime(state.started_at)}")
    click.echo(" • ".join(header_parts))
    click.echo()

    rows = []
    for s in statuses or []:
        rows.append({
            "SERVICE": s.name,
            "PID": str(s.pid),
            "STATUS": "running" if s.alive else "dead",
            "LOG": s.log_file,
        })

    # Add shared services
    shared_statuses = get_shared_status(project)
    if shared_statuses:
        for s in shared_statuses:
            rows.append({
                "SERVICE": f"{s.name} (shared)",
                "PID": str(s.pid),
                "STATUS": "running" if s.alive else "dead",
                "LOG": s.log_file,
            })

    draw_table(rows, ["SERVICE", "PID", "STATUS", "LOG"])


@env.command("start")
@click.argument("target", required=False, default=None)
@click.option("--skip-install", is_flag=True, help="Skip the install step before starting")
def env_start(target, skip_install):
    """Start services for a worktree environment."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    worktree_path = ctx.worktree_path
    if not worktree_path or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    try:
        state = start_env(
            ctx.project,
            ctx.worktree,
            worktree_path,
            skip_install=skip_install,
        )
    except RuntimeError as e:
        raise click.ClickException(str(e))

    # Open browser pane in cmux if available
    if is_cmux_mode():
        app_info = get_app_url(ctx.project_path, ctx.worktree)
        if app_info:
            url, _is_running = app_info
            surface_ref = open_browser_pane(url)
            if surface_ref:
                state.cmux_browser_surface = surface_ref
                save_env_state(state)

    _print_service_status(ctx.project, ctx.worktree, ctx.project_path)


@env.command("status")
@click.argument("target", required=False, default=None)
def env_status(target):
    """Show status of services for a worktree environment."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    _print_service_status(ctx.project, ctx.worktree, ctx.project_path)


@env.command("stop")
@click.argument("target", required=False, default=None)
def env_stop(target):
    """Stop services for a worktree environment."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    # Close cmux browser pane if one was opened
    if is_cmux_mode():
        state = load_env_state(ctx.project, ctx.worktree)
        if state and state.cmux_browser_surface:
            close_surface(state.cmux_browser_surface)

    messages = stop_env(ctx.project, ctx.worktree)
    for msg in messages:
        click.echo(msg)
    click.echo(f"Environment stopped for {ctx.project}/{ctx.worktree}.")


@env.command("list")
@click.argument("project", required=False, default=None)
def env_list(project):
    """List running environments for a project."""
    try:
        ctx = resolve_context(
            project,
            require_project=True,
            require_worktree=False,
            arg_is_project=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    envs = list_project_envs(ctx.project)
    if not envs:
        click.echo(f"No running environments for {ctx.project}.")
        return

    rows = []
    for state in envs:
        running, stopped = _env_service_columns(state)
        uptime = format_uptime(state.started_at)
        app_display = _get_app_display(ctx.project_path, state.worktree)
        rows.append({
            "WORKTREE": state.worktree,
            "APP": app_display,
            "RUNNING SERVICES": running,
            "STOPPED SERVICES": stopped,
            "UPTIME": uptime,
        })
    draw_table(rows, ["WORKTREE", "APP", "RUNNING SERVICES", "STOPPED SERVICES", "UPTIME"])


@env.command("list-all")
def env_list_all():
    """List all running environments across all projects."""
    envs = list_all_envs()
    if not envs:
        click.echo("No running environments.")
        return

    rows = []
    for state in envs:
        running, stopped = _env_service_columns(state)
        uptime = format_uptime(state.started_at)
        project_path = Path(state.worktree_path).parent
        app_display = _get_app_display(project_path, state.worktree)
        rows.append({
            "PROJECT": state.project,
            "WORKTREE": state.worktree,
            "APP": app_display,
            "RUNNING SERVICES": running,
            "STOPPED SERVICES": stopped,
            "UPTIME": uptime,
        })
    draw_table(rows, ["PROJECT", "WORKTREE", "APP", "RUNNING SERVICES", "STOPPED SERVICES", "UPTIME"])


@env.command("stop-all")
def env_stop_all():
    """Stop all running environments across all projects."""
    results = stop_all_envs()
    if not results:
        click.echo("No running environments.")
        return
    for project, worktree, messages in results:
        click.echo(f"{project}/{worktree}:")
        for msg in messages:
            click.echo(f"  {msg}")
    click.echo(f"Stopped {len(results)} environment(s).")


def _follow_logs(
    project: str, worktree: str, service: str | None, multi: bool,
) -> None:
    """Poll log files and print new lines as they appear.

    Polls every 0.5s using file size tracking. Handles file truncation
    (resets position). Catches KeyboardInterrupt for clean Ctrl+C exit.
    """
    log_files = get_log_files(project, worktree)
    if not log_files:
        return

    targets = {service: log_files[service]} if service else log_files
    positions: dict[str, int] = {}
    for name, path in targets.items():
        try:
            positions[name] = path.stat().st_size
        except OSError:
            positions[name] = 0

    try:
        while True:
            time.sleep(0.5)
            for name, path in targets.items():
                try:
                    size = path.stat().st_size
                except OSError:
                    continue

                pos = positions.get(name, 0)
                if size < pos:
                    # File was truncated (restarted)
                    pos = 0

                if size > pos:
                    with open(path) as f:
                        f.seek(pos)
                        new_data = f.read()
                    for line in new_data.splitlines():
                        if multi:
                            click.echo(f"[{name}] {line}")
                        else:
                            click.echo(line)
                    positions[name] = size
    except KeyboardInterrupt:
        pass


@env.command("logs")
@click.argument("target", required=False, default=None)
@click.argument("service", required=False, default=None)
@click.option("-n", "num_lines", default=100, type=int, help="Number of lines to show")
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
def env_logs(target, service, num_lines, follow):
    """Show logs for an environment's services."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    try:
        lines = read_service_logs(ctx.project, ctx.worktree, service, num_lines)
    except ValueError as e:
        raise click.ClickException(str(e))

    multi = service is None and len(get_log_files(ctx.project, ctx.worktree)) > 1
    for svc_name, line in lines:
        if multi:
            click.echo(f"[{svc_name}] {line}")
        else:
            click.echo(line)

    if follow:
        _follow_logs(ctx.project, ctx.worktree, service, multi)
