"""CLI commands for managing dev environments."""

import time
from pathlib import Path

import click

from .cmux import mael_layout
from .config import load_config_or_default
from .context import ResolvedContext, resolve_context
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
    regenerate_and_restart_if_running,
    save_env_state,
    start_env,
    stop_all_envs,
    stop_env,
)
from .env_store import JsonEnvStore
from .ports import get_app_url, wait_for_port
from .table import draw_table
from .worktree import (
    copy_back_new_env_vars,
    update_claude_local_md,
)
from .worktree_model import (
    CopyBackResult,
    get_worktree_folder_name,
)


def make_store() -> JsonEnvStore:
    """Build the persistent env store. The single factory for the CLI layer."""
    return JsonEnvStore()


def _declared_service_names(ctx: ResolvedContext) -> list[str]:
    """Names of the services the worktree's `.maelstrom.yaml` declares, in order."""
    worktree_path = ctx.worktree_path
    if worktree_path is None:
        return []
    return [svc.name for svc in load_config_or_default(worktree_path).services]


def _reject_worktree_target(service: str) -> None:
    """Reject a dotted name, which can only be the worktree target.

    Raises:
        ValueError: If the name holds a dot.
    """
    if "." in service:
        raise ValueError(
            f"{service!r} looks like a worktree target, not a service. "
            f"Use --worktree {service} instead."
        )


def resolve_service(
    service: str | None,
    worktree_opt: str | None,
) -> tuple[ResolvedContext, str | None]:
    """Resolve `env start/stop/restart/logs`'s positional service and --worktree.

    The positional names a service. The worktree comes from ``--worktree``, or
    from the current directory. A dotted name is rejected, because it can only
    be a worktree target and ``--worktree`` is where one goes.

    Returns (context, service name) — the service name is ``None`` for the whole
    environment.

    Raises:
        ValueError: If the worktree declares services and this is not one of
            them, or the context cannot be resolved.
    """
    if service is not None:
        _reject_worktree_target(service)

    ctx = resolve_context(worktree_opt, require_project=True, require_worktree=True)
    if service is None:
        return ctx, None

    # A project that declares nothing has no list to check against, so
    # get_services raises the Procfile diagnosis instead.
    declared = _declared_service_names(ctx)
    if declared and service not in declared:
        raise ValueError(
            f"Unknown service: {service}. Declared services: {', '.join(declared)}"
        )
    return ctx, service


def _report_stop(
    project: str,
    worktree: str,
    messages: list[str],
    service: str | None,
) -> None:
    """Print a stop's per-service messages, then its closing line.

    A partial stop must not claim the environment stopped, because the other
    services keep running.
    """
    for msg in messages:
        click.echo(msg)
    if service is None:
        click.echo(f"Environment stopped for {project}/{worktree}.")
    else:
        click.echo(f"Service stopped for {project}/{worktree}: {service}.")


def print_copy_back_result(result: CopyBackResult, project_path: Path) -> None:
    """Print copy-back results: added keys, then a single conflict warning.

    Conflicts are reported as one warning listing every differing key, with a
    synthetic diff of the worktree value being overwritten (``-``) vs the
    resolved parent value a reset applies (``+``). Prints nothing when there is
    nothing to report.
    """
    parent_env = project_path / ".env"
    if result.added:
        n = len(result.added)
        click.echo(f"Copied {n} new var(s) back to {parent_env}:")
        for key, value in result.added.items():
            click.echo(f"  +{key}={value}")
    if result.conflicts:
        keys = ", ".join(c.key for c in result.conflicts)
        click.echo(
            f"Warning: {keys} differ between worktree and {parent_env}; "
            "parent unchanged, the worktree overwritten:",
            err=True,
        )
        for c in result.conflicts:
            click.echo(f"  -{c.key}={c.worktree_value}", err=True)
            click.echo(f"  +{c.key}={c.resolved_parent_value}", err=True)


def ensure_cmux_browser(
    state: EnvState,
    project_path: Path,
    worktree: str,
    service: str | None = None,
) -> None:
    """Ensure a cmux browser pane exists for this env's app URL.

    ``service`` restricts the search to one service's own ports, so a named
    start waits on that service's port and opens a browser only when the service
    is web-facing.
    """
    app_info = get_app_url(project_path, worktree, service=service)
    if not app_info:
        return
    url, _ = app_info
    port = int(url.rsplit(":", 1)[1])
    wait_for_port(port)
    ref = mael_layout.show_app_browser(state.project, worktree, url)
    if ref:
        state.cmux_browser_surface = ref
        save_env_state(make_store(), state)


def _env_service_columns(state: EnvState) -> tuple[str, str]:
    """Return (running_services, stopped_services) as comma-separated names."""
    store = make_store()
    statuses = list(get_env_status(store, state.project, state.worktree) or [])

    # Include shared services
    shared_statuses = get_shared_status(store, state.project)
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


def print_service_status(
    project: str,
    worktree: str,
    project_path: Path | None = None,
) -> None:
    """Print a SERVICE/PID/STATUS/LOG table for an environment."""
    store = make_store()
    state = load_env_state(store, project, worktree)
    if not state:
        click.echo(f"No environment state for {project}/{worktree}.")
        return

    statuses = get_env_status(store, project, worktree)

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
        rows.append(
            {
                "SERVICE": s.name,
                "PID": str(s.pid),
                "STATUS": "running" if s.alive else "dead",
                "LOG": s.log_file,
            }
        )

    # Add shared services
    shared_statuses = get_shared_status(store, project)
    if shared_statuses:
        for s in shared_statuses:
            rows.append(
                {
                    "SERVICE": f"{s.name} (shared)",
                    "PID": str(s.pid),
                    "STATUS": "running" if s.alive else "dead",
                    "LOG": s.log_file,
                }
            )

    # Declared services the state has never held read "stopped", not "dead".
    if project_path is not None:
        known = {s.name for s in statuses or []} | {
            s.name for s in shared_statuses or []
        }
        folder_name = get_worktree_folder_name(project, worktree)
        config = load_config_or_default(project_path / folder_name)
        for svc in config.services:
            if svc.name in known:
                continue
            label = f"{svc.name} (optional)" if svc.optional else svc.name
            rows.append(
                {
                    "SERVICE": label,
                    "PID": "-",
                    "STATUS": "stopped",
                    "LOG": "-",
                }
            )

    draw_table(rows, ["SERVICE", "PID", "STATUS", "LOG"])


@env.command("open")
@click.argument("target", required=False, default=None)
def env_open(target):
    """Open the browser pane for a running environment."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    assert ctx.project is not None
    assert ctx.worktree is not None
    assert ctx.project_path is not None

    state = load_env_state(make_store(), ctx.project, ctx.worktree)
    if not state:
        raise click.ClickException(
            f"No running environment for {ctx.project}/{ctx.worktree}."
        )

    ensure_cmux_browser(state, ctx.project_path, ctx.worktree)


@env.command("start")
@click.argument("service", required=False, default=None)
@click.option(
    "--skip-install", is_flag=True, help="Skip the install step before starting"
)
@click.option(
    "-w",
    "--worktree",
    "worktree_opt",
    default=None,
    help="Worktree target (project.worktree). Default: the current directory",
)
def env_start(service, skip_install, worktree_opt):
    """Start services for a worktree environment.

    SERVICE names one declared service. Without it, every non-optional service
    starts.
    """
    try:
        ctx, service_name = resolve_service(service, worktree_opt)
    except ValueError as e:
        raise click.ClickException(str(e))

    assert ctx.project is not None
    assert ctx.worktree is not None
    assert ctx.project_path is not None

    worktree_path = ctx.worktree_path
    if not worktree_path or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    try:
        state = start_env(
            make_store(),
            ctx.project,
            ctx.worktree,
            worktree_path,
            skip_install=skip_install or service_name is not None,
            services=[service_name] if service_name else None,
        )
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e))

    ensure_cmux_browser(state, ctx.project_path, ctx.worktree, service=service_name)

    print_service_status(ctx.project, ctx.worktree, ctx.project_path)


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

    assert ctx.project is not None
    assert ctx.worktree is not None
    assert ctx.project_path is not None

    print_service_status(ctx.project, ctx.worktree, ctx.project_path)


@env.command("stop")
@click.argument("service", required=False, default=None)
@click.option(
    "-w",
    "--worktree",
    "worktree_opt",
    default=None,
    help="Worktree target (project.worktree). Default: the current directory",
)
def env_stop(service, worktree_opt):
    """Stop services for a worktree environment.

    SERVICE names one declared service. Without it, the whole environment stops.
    """
    try:
        ctx, service_name = resolve_service(service, worktree_opt)
    except ValueError as e:
        raise click.ClickException(str(e))

    assert ctx.project is not None
    assert ctx.worktree is not None
    assert ctx.project_path is not None

    # Close the cmux browser pane, but only on a full stop — a named stop must
    # leave the pane showing the main app alone.
    if service_name is None:
        app_info = get_app_url(ctx.project_path, ctx.worktree)
        if app_info:
            mael_layout.hide_app_browser(ctx.project, ctx.worktree, app_info[0])

    try:
        messages = stop_env(
            make_store(),
            ctx.project,
            ctx.worktree,
            services=[service_name] if service_name else None,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    _report_stop(ctx.project, ctx.worktree, messages, service_name)


@env.command("restart")
@click.argument("service", required=False, default=None)
@click.option("--install", is_flag=True, help="Run the install step before starting")
@click.option(
    "-w",
    "--worktree",
    "worktree_opt",
    default=None,
    help="Worktree target (project.worktree). Default: the current directory",
)
def env_restart(service, install, worktree_opt):
    """Restart services for a worktree environment.

    SERVICE names one declared service. Without it, the whole environment
    restarts.
    """
    try:
        ctx, service_name = resolve_service(service, worktree_opt)
    except ValueError as e:
        raise click.ClickException(str(e))

    assert ctx.project is not None
    assert ctx.worktree is not None
    assert ctx.project_path is not None

    worktree_path = ctx.worktree_path
    if not worktree_path or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    store = make_store()
    state = load_env_state(store, ctx.project, ctx.worktree)
    if state:
        try:
            messages = stop_env(
                store,
                ctx.project,
                ctx.worktree,
                services=[service_name] if service_name else None,
            )
        except ValueError as e:
            raise click.ClickException(str(e))
        _report_stop(ctx.project, ctx.worktree, messages, service_name)

    try:
        state = start_env(
            store,
            ctx.project,
            ctx.worktree,
            worktree_path,
            skip_install=not install,
            services=[service_name] if service_name else None,
        )
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e))

    ensure_cmux_browser(state, ctx.project_path, ctx.worktree, service=service_name)

    print_service_status(ctx.project, ctx.worktree, ctx.project_path)


@env.command("reset")
@click.argument("target", required=False, default=None)
def env_reset(target):
    """Regenerate .env file (e.g., after updating .maelstrom.yaml ports)."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    assert ctx.project is not None
    assert ctx.worktree is not None
    assert ctx.project_path is not None

    worktree_path = ctx.worktree_path
    if not worktree_path or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    # Rescue any new worktree vars into the parent before regenerating, so the
    # regenerate is a clean recreate from the parent template.
    copy_back = copy_back_new_env_vars(ctx.project_path, worktree_path)
    print_copy_back_result(copy_back, ctx.project_path)

    try:
        stop_messages, new_state = regenerate_and_restart_if_running(
            make_store(),
            ctx.project,
            ctx.worktree,
            ctx.project_path,
            worktree_path,
        )
    except RuntimeError as e:
        raise click.ClickException(str(e))

    if stop_messages:
        for msg in stop_messages:
            click.echo(msg)
        click.echo(f"Environment stopped for {ctx.project}/{ctx.worktree}.")

    click.echo(f"Regenerated .env for {ctx.project}/{ctx.worktree}.")

    update_claude_local_md(ctx.project_path, worktree_path, ctx.worktree)

    if new_state is not None:
        ensure_cmux_browser(new_state, ctx.project_path, ctx.worktree)
        print_service_status(ctx.project, ctx.worktree, ctx.project_path)


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

    assert ctx.project is not None
    assert ctx.project_path is not None

    envs = list_project_envs(make_store(), ctx.project)
    if not envs:
        click.echo(f"No running environments for {ctx.project}.")
        return

    rows = []
    for state in envs:
        running, stopped = _env_service_columns(state)
        uptime = format_uptime(state.started_at)
        app_display = _get_app_display(ctx.project_path, state.worktree)
        rows.append(
            {
                "WORKTREE": state.worktree,
                "APP": app_display,
                "RUNNING SERVICES": running,
                "STOPPED SERVICES": stopped,
                "UPTIME": uptime,
            }
        )
    draw_table(
        rows, ["WORKTREE", "APP", "RUNNING SERVICES", "STOPPED SERVICES", "UPTIME"]
    )


@env.command("list-all")
def env_list_all():
    """List all running environments across all projects."""
    envs = list_all_envs(make_store())
    if not envs:
        click.echo("No running environments.")
        return

    rows = []
    for state in envs:
        running, stopped = _env_service_columns(state)
        uptime = format_uptime(state.started_at)
        project_path = Path(state.worktree_path).parent
        app_display = _get_app_display(project_path, state.worktree)
        rows.append(
            {
                "PROJECT": state.project,
                "WORKTREE": state.worktree,
                "APP": app_display,
                "RUNNING SERVICES": running,
                "STOPPED SERVICES": stopped,
                "UPTIME": uptime,
            }
        )
    draw_table(
        rows,
        [
            "PROJECT",
            "WORKTREE",
            "APP",
            "RUNNING SERVICES",
            "STOPPED SERVICES",
            "UPTIME",
        ],
    )


@env.command("stop-all")
def env_stop_all():
    """Stop all running environments across all projects."""
    results = stop_all_envs(make_store())
    if not results:
        click.echo("No running environments.")
        return
    for project, worktree, messages in results:
        click.echo(f"{project}/{worktree}:")
        for msg in messages:
            click.echo(f"  {msg}")
    click.echo(f"Stopped {len(results)} environment(s).")


def _follow_logs(
    store: JsonEnvStore,
    project: str,
    worktree: str,
    service: str | None,
    multi: bool,
) -> None:
    """Poll log files and print new lines as they appear.

    Polls every 0.5s using file size tracking. Handles file truncation
    (resets position). Catches KeyboardInterrupt for clean Ctrl+C exit.
    """
    log_files = get_log_files(store, project, worktree)
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
@click.argument("service", required=False, default=None)
@click.option(
    "-w",
    "--worktree",
    "worktree_opt",
    default=None,
    help="Worktree target (project.worktree). Default: the current directory",
)
@click.option("-n", "num_lines", default=100, type=int, help="Number of lines to show")
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
def env_logs(service, worktree_opt, num_lines, follow):
    """Show logs for an environment's services.

    SERVICE names one service. Without it, every service's log shows.
    """
    # No resolve_service call: logs come from files, so a Procfile project's
    # logs read too.
    try:
        if service is not None:
            _reject_worktree_target(service)
        ctx = resolve_context(
            worktree_opt,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    assert ctx.project is not None
    assert ctx.worktree is not None

    store = make_store()
    try:
        lines = read_service_logs(store, ctx.project, ctx.worktree, service, num_lines)
    except ValueError as e:
        raise click.ClickException(str(e))

    multi = service is None and len(get_log_files(store, ctx.project, ctx.worktree)) > 1
    for svc_name, line in lines:
        if multi:
            click.echo(f"[{svc_name}] {line}")
        else:
            click.echo(line)

    if follow:
        _follow_logs(store, ctx.project, ctx.worktree, service, multi)
