"""Command-line interface for maelstrom."""

import sys
from pathlib import Path

import click

from . import __version__
from .context import load_global_config, resolve_context
from .ports import get_app_url
from .github import (
    get_pr_number_and_commits,
    wait_for_merge,
)
from .cmux import mael_layout
from .review_prepare import cmd_review_prepare
from .session_cli import session as session_cli, session_channel as session_channel_cmd
from .task_cli import task as task_cli
from .task_cli import add_task
from .schedule_launchd import schedule_group
from .env import get_env_status, regenerate_and_restart_if_running, stop_env
from .env_cli import (
    ensure_cmux_browser,
    env as env_cli,
    make_store,
    print_copy_back_result,
    print_service_status,
)
from .git_cli import git as git_cli
from .git_cli import print_rebase_conflict_help
from .github_cli import gh as gh_cli
from .integrations.linear import linear
from .integrations.sentry import sentry
from .integrations.uptimerobot import uptimerobot
from .status_cli import status as status_cli
from .admin_cli import cmd_install, cmd_self_update
from .claude_sessions import get_active_ide_sessions
from .table import draw_table
from .worktree import (
    add_project,
    close_worktree,
    copy_back_new_env_vars,
    create_worktree,
    find_all_projects,
    get_local_only_commits,
    get_pushed_commit_count,
    get_worktree_dirty_files,
    is_worktree_closed,
    list_worktrees,
    remove_worktree_by_path,
    run_git,
    run_install_cmd,
    setup_worktree_for_branch,
    sync_worktree,
    tidy_branches,
    update_claude_local_md,
)
from .worktree_launcher import (
    launch_claude_in_worktree,
    open_worktree,
)
from .worktree_model import (
    MAIN_BRANCH,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
)


@click.group()
@click.version_option(version=__version__, prog_name="mael")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.pass_context
def cli(ctx, output_json):
    """Maelstrom - Parallel development environment manager."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = output_json


# --- Core worktree commands ---


@cli.command("add-project")
@click.argument("git_url")
@click.option("--projects-dir", help="Base directory for projects (default from ~/.maelstrom/config.yaml or ~/Projects)")
def cmd_add_project(git_url, projects_dir):
    """Clone a git repository for use with maelstrom."""
    # Use explicit --projects-dir or fall back to global config
    if projects_dir:
        projects_dir_path = Path(projects_dir).expanduser()
    else:
        global_config = load_global_config()
        projects_dir_path = global_config.projects_dir

    click.echo(f"Cloning {git_url}...")
    try:
        project_path = add_project(git_url, projects_dir_path)
        project_name = extract_project_name(git_url)
        alpha_folder = get_worktree_folder_name(project_name, "alpha")
        click.echo(f"Project created at: {project_path}")
        click.echo(f"Alpha worktree at: {project_path / alpha_folder}")
    except Exception as e:
        raise click.ClickException(str(e))


@cli.command("add")
@click.argument("branch", required=False, default=None)
@click.option("-p", "--project", default=None, help="Project name (default: detect from cwd)")
@click.option("--open", is_flag=True, help="Open in configured editor instead of Claude CLI")
@click.option("--no-recycle", is_flag=True, help="Don't recycle closed worktrees, always create new")
def cmd_add(branch, project, open, no_recycle):
    """Add a new worktree for a branch.

    If BRANCH is provided:
      - Tries to recycle a closed worktree (one on main branch) if available
      - Otherwise creates a new worktree

    If BRANCH is omitted:
      - Creates a new worktree on the main branch
      - Does NOT recycle (for when you just want a fresh workspace)

    Use --no-recycle to always create a new worktree even when closed ones exist.
    """
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

    if project_path is None or not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")
    assert ctx.project is not None

    # No branch specified: create a fresh detached worktree at origin/main.
    # This path never recycles and stays inline (the core fn requires a branch).
    if branch is None:
        click.echo(f"Creating fresh worktree at origin/{MAIN_BRANCH}...")
        try:
            worktree_path = create_worktree(project_path, MAIN_BRANCH, detached=True)
        except Exception as e:
            raise click.ClickException(f"Error creating worktree: {e}")
        click.echo(f"Worktree created at: {worktree_path}")
        wt_name = extract_worktree_name_from_folder(ctx.project, worktree_path.name)
        if wt_name and update_claude_local_md(project_path, worktree_path, wt_name):
            click.echo(
                ".claude/CLAUDE.local.md generated with maelstrom workflow instructions"
            )
        app_info = get_app_url(project_path, wt_name) if wt_name else None
        if app_info:
            url, _ = app_info
            click.echo(f"App: {url}")
        run_install_cmd(worktree_path)
        if open:
            global_config = load_global_config()
            try:
                open_worktree(worktree_path, global_config.open_command)
            except RuntimeError as e:
                click.echo(f"Warning: Could not open worktree: {e}", err=True)
        else:
            launch_claude_in_worktree(worktree_path, project=ctx.project, worktree=wt_name)
        return

    click.echo(f"Creating worktree for branch '{branch}'...")

    # Ensure a fully set-up worktree exists for the branch (shared with `task run`).
    # The shared launcher owns install for the create path (it runs it in the new
    # workspace's shell pane), and reuses a live workspace as a new Claude tab — so
    # skip install here and let the launcher place the session.
    try:
        result = setup_worktree_for_branch(
            project_path, ctx.project, branch,
            no_recycle=no_recycle, run_install=False,
        )
    except RuntimeError as e:
        raise click.ClickException(str(e))
    worktree_path, wt_name = result.path, result.name

    if result.action == "recycled":
        click.echo(f"Worktree recycled at: {worktree_path}")
        # Rescue any stale worktree-only vars into the parent before the recreate.
        copy_back = copy_back_new_env_vars(project_path, worktree_path)
        print_copy_back_result(copy_back, project_path)
        try:
            stop_messages, new_state = regenerate_and_restart_if_running(
                make_store(), ctx.project, wt_name, project_path, worktree_path,
            )
        except RuntimeError as e:
            raise click.ClickException(str(e))

        if stop_messages:
            for msg in stop_messages:
                click.echo(msg)
            click.echo(f"Environment stopped for {ctx.project}/{wt_name}.")

        click.echo(f"Regenerated .env for {ctx.project}/{wt_name}.")

        if new_state is not None:
            ensure_cmux_browser(new_state, project_path, wt_name)
            print_service_status(ctx.project, wt_name, project_path)
    elif result.action == "created":
        click.echo(f"Worktree created at: {worktree_path}")
        click.echo(f"  → {ctx.project}/{wt_name} (created)")

    app_info = get_app_url(project_path, wt_name)
    if app_info:
        url, _ = app_info
        click.echo(f"App: {url}")

    # Open in editor or start a Claude session. Install was deferred
    # (run_install=False above): the launcher owns it for the Claude path (shell
    # pane on create, blocking in non-cmux), but the editor path has no launcher,
    # so run it blocking here.
    if open:
        run_install_cmd(worktree_path)
        global_config = load_global_config()
        try:
            open_worktree(worktree_path, global_config.open_command)
        except RuntimeError as e:
            click.echo(f"Warning: Could not open worktree: {e}", err=True)
    else:
        launch_claude_in_worktree(worktree_path, project=ctx.project, worktree=wt_name)


@cli.command("remove")
@click.argument("targets", nargs=-1, required=True)
@click.option("-f", "--force", is_flag=True, help="Skip confirmation prompt for modified/untracked files")
def cmd_remove(targets, force):
    """Remove one or more worktrees."""
    errors = []
    for target in targets:
        try:
            ctx = resolve_context(
                target,
                require_project=True,
                require_worktree=True,
            )
        except ValueError as e:
            click.echo(f"Error ({target}): {e}", err=True)
            errors.append(target)
            continue

        project_path = ctx.project_path
        worktree_name = ctx.worktree  # The NATO name (e.g., "alpha")

        if project_path is None or not project_path.exists():
            click.echo(f"Error: Project '{ctx.project}' not found at {project_path}", err=True)
            errors.append(target)
            continue
        assert ctx.project is not None
        assert worktree_name is not None

        folder_name = get_worktree_folder_name(ctx.project, worktree_name)
        worktree_path = project_path / folder_name
        if not worktree_path.exists():
            click.echo(f"Error: Worktree '{worktree_name}' not found in project '{ctx.project}'", err=True)
            errors.append(target)
            continue

        # Check for modified/untracked files (excluding maelstrom-managed files)
        dirty_files = get_worktree_dirty_files(worktree_path)
        if dirty_files and not force:
            click.echo(f"The following modified/untracked files in '{worktree_name}' will be lost:")
            for f in dirty_files:
                click.echo(f"  {f}")
            if not click.confirm("Continue?"):
                click.echo("Aborted.")
                errors.append(target)
                continue

        # Stop running environment if any
        project_name = ctx.project
        assert project_name is not None
        env_store = make_store()
        env_status = get_env_status(env_store, project_name, worktree_name)
        if env_status and any(s.alive for s in env_status):
            click.echo(f"Stopping environment for '{worktree_name}'...")
            for msg in stop_env(env_store, project_name, worktree_name):
                click.echo(f"  {msg}")

        click.echo(f"Removing worktree '{worktree_name}'...")
        try:
            remove_worktree_by_path(project_path, folder_name)
            click.echo("Worktree removed successfully.")
        except Exception as e:
            click.echo(f"Error removing worktree '{worktree_name}': {e}", err=True)
            errors.append(target)

    if errors:
        raise SystemExit(1)


# Register alias for remove
cli.add_command(cmd_remove, name="rm")


@cli.command("list")
@click.argument("project", required=False, default=None)
def cmd_list(project):
    """List all worktrees with status information."""
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

    if project_path is None or not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")
    project_name = ctx.project
    assert project_name is not None

    worktrees = list_worktrees(project_path)

    # Filter out the project root (bare repo), but keep detached worktrees
    worktrees = [wt for wt in worktrees if wt.path != project_path]

    if not worktrees:
        click.echo("No worktrees found.")
        return

    # Partition worktrees into open and closed
    closed_names = []
    open_worktrees = []
    for wt in worktrees:
        display_name = extract_worktree_name_from_folder(project_name, wt.path.name) or wt.path.name
        if is_worktree_closed(wt):
            closed_names.append(display_name)
        else:
            open_worktrees.append((wt, display_name))

    if not open_worktrees:
        if closed_names:
            click.echo(f"Closed environments: {', '.join(closed_names)}")
        else:
            click.echo("No worktrees found.")
        return

    # Get active IDE sessions
    active_sessions = get_active_ide_sessions()

    # Gather extended info for each open worktree
    rows = []
    for wt, display_name in open_worktrees:
        branch_display = wt.branch or "(detached)"

        # Dirty files count
        dirty_files = get_worktree_dirty_files(wt.path)
        dirty_display = str(len(dirty_files)) if dirty_files else ""

        # Local unpushed commits
        local_commits = get_local_only_commits(wt.path, wt.branch)
        local_display = str(local_commits) if local_commits > 0 else ""

        # PR info (number and commit count)
        pr_num, pr_commits = get_pr_number_and_commits(project_path, wt.branch) if wt.branch else (None, None)
        if pr_num:
            pr_display = f"#{pr_num} ({pr_commits})"
        elif wt.branch:
            # Check for pushed commits without PR
            pushed_commits = get_pushed_commit_count(wt.path, wt.branch)
            pr_display = f"({pushed_commits})" if pushed_commits else ""
        else:
            pr_display = ""

        # IDE session indicator
        ide_display = "Y" if active_sessions.get(wt.path, 0) > 0 else ""

        # App URL with running status
        app_display = ""
        app_info = get_app_url(project_path, display_name)
        if app_info:
            url, is_running = app_info
            port = url.split(":")[-1]
            app_display = url if is_running else f"*{port}"

        rows.append({
            "WORKTREE": display_name,
            "BRANCH": branch_display,
            "DIRTY FILES": dirty_display,
            "LOCAL COMMITS": local_display,
            "PR (COMMITS)": pr_display,
            "APP": app_display,
            "IDE": ide_display,
        })

    draw_table(rows, ["WORKTREE", "BRANCH", "DIRTY FILES", "LOCAL COMMITS", "PR (COMMITS)", "APP", "IDE"])

    if closed_names:
        click.echo(f"\nClosed environments: {', '.join(closed_names)}")


@cli.command("list-all")
def cmd_list_all():
    """List all worktrees across all projects."""
    output_json = click.get_current_context().obj.get("json", False)
    global_config = load_global_config()
    projects_dir = global_config.projects_dir

    projects = find_all_projects(projects_dir)
    if not projects:
        if output_json:
            click.echo('{"projects": []}')
        else:
            click.echo("No projects found.")
        return

    # Get active IDE sessions once for all projects
    active_sessions = get_active_ide_sessions()

    # Collect structured data for all worktrees
    projects_data = []
    rows = []
    closed_by_project: dict[str, list[str]] = {}
    for project_path in projects:
        project_name = project_path.name
        worktrees = list_worktrees(project_path)
        worktree_data = []

        for wt in worktrees:
            # Skip the project root (bare repo)
            if wt.path == project_path:
                continue

            display_name = extract_worktree_name_from_folder(project_name, wt.path.name) or wt.path.name

            # Check if worktree is closed (detached at origin/main)
            closed = is_worktree_closed(wt)

            if closed:
                closed_by_project.setdefault(project_name, []).append(display_name)
                # Still include in JSON data but skip table row
                worktree_data.append({
                    "name": display_name,
                    "folder": wt.path.name,
                    "path": str(wt.path),
                    "branch": wt.branch or None,
                    "is_closed": True,
                    "dirty_files": 0,
                    "local_commits": 0,
                    "pr_number": None,
                    "pr_commits": None,
                    "pushed_commits": None,
                    "app_url": None,
                    "app_running": False,
                    "ide_active": False,
                })
                continue

            branch_display = wt.branch or "(detached)"

            # Dirty files count
            dirty_files = get_worktree_dirty_files(wt.path)
            dirty_count = len(dirty_files)
            dirty_display = str(dirty_count) if dirty_files else ""

            # Local unpushed commits
            local_commits = get_local_only_commits(wt.path, wt.branch)
            local_display = str(local_commits) if local_commits > 0 else ""

            # PR info (number and commit count)
            pr_num, pr_commits = get_pr_number_and_commits(project_path, wt.branch) if wt.branch else (None, None)
            pushed_commits = None
            if pr_num:
                pr_display = f"#{pr_num} ({pr_commits})"
            elif wt.branch:
                # Check for pushed commits without PR
                pushed_commits = get_pushed_commit_count(wt.path, wt.branch)
                pr_display = f"({pushed_commits})" if pushed_commits else ""
            else:
                pr_display = ""

            # IDE session indicator
            ide_active = active_sessions.get(wt.path, 0) > 0
            ide_display = "Y" if ide_active else ""

            # App URL with running status
            app_display = ""
            app_url = None
            app_running = False
            app_info = get_app_url(project_path, display_name)
            if app_info:
                url, is_running = app_info
                app_url = url
                app_running = is_running
                port = url.split(":")[-1]
                app_display = url if is_running else f"*{port}"

            worktree_data.append({
                "name": display_name,
                "folder": wt.path.name,
                "path": str(wt.path),
                "branch": wt.branch or None,
                "is_closed": False,
                "dirty_files": dirty_count,
                "local_commits": local_commits,
                "pr_number": pr_num,
                "pr_commits": pr_commits,
                "pushed_commits": pushed_commits,
                "app_url": app_url,
                "app_running": app_running,
                "ide_active": ide_active,
            })

            rows.append({
                "PROJECT": project_name,
                "WORKTREE": wt.path.name,
                "BRANCH": branch_display,
                "DIRTY FILES": dirty_display,
                "LOCAL COMMITS": local_display,
                "PR (COMMITS)": pr_display,
                "APP": app_display,
                "IDE": ide_display,
            })

        projects_data.append({
            "name": project_name,
            "path": str(project_path),
            "worktrees": worktree_data,
        })

    if output_json:
        import json as json_mod
        click.echo(json_mod.dumps({"projects": projects_data}))
        return

    if not rows:
        if closed_by_project:
            click.echo("Closed environments:")
            for proj, names in closed_by_project.items():
                click.echo(f" - {proj}: {', '.join(names)}")
        else:
            click.echo("No worktrees found.")
        return

    draw_table(rows, ["PROJECT", "WORKTREE", "BRANCH", "DIRTY FILES", "LOCAL COMMITS", "PR (COMMITS)", "APP", "IDE"])

    if closed_by_project:
        click.echo("\nClosed environments:")
        for proj, names in closed_by_project.items():
            click.echo(f" - {proj}: {', '.join(names)}")


@cli.command("open")
@click.argument("target", required=False, default=None)
def cmd_open(target):
    """Start a Claude Code CLI session in a worktree."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    worktree_path = ctx.worktree_path

    if worktree_path is None or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    launch_claude_in_worktree(worktree_path, project=ctx.project, worktree=ctx.worktree)


@cli.command("ide")
@click.argument("target", required=False, default=None)
def cmd_ide(target):
    """Open a worktree in the configured editor."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    worktree_path = ctx.worktree_path

    if worktree_path is None or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    global_config = load_global_config()
    try:
        open_worktree(worktree_path, global_config.open_command)
    except RuntimeError as e:
        raise click.ClickException(str(e))


@cli.command("claude")
@click.argument("target", required=False, default=None)
def cmd_claude(target):
    """Start a Claude Code CLI session in a worktree."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    worktree_path = ctx.worktree_path

    if worktree_path is None or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    launch_claude_in_worktree(worktree_path, project=ctx.project, worktree=ctx.worktree)


@cli.command("sync")
@click.argument("target", required=False, default=None)
@click.option("--squash", is_flag=True, help="Autosquash fixup! commits while rebasing onto origin/main")
@click.option(
    "--abort",
    "abort",
    is_flag=True,
    help="On conflict, abort the rebase and restore the worktree instead of leaving it in progress",
)
@click.option(
    "--close",
    "close",
    is_flag=True,
    help="If the branch is empty after rebasing, delete it (local + remote) and close the worktree",
)
def cmd_sync(target, squash, abort, close):
    """Rebase worktree against origin/main."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    worktree_path = ctx.worktree_path

    if worktree_path is None or not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    if squash:
        click.echo(f"Syncing {ctx.worktree} with origin/main (autosquashing fixup! commits)...")
    else:
        click.echo(f"Syncing {ctx.worktree} with origin/main...")
    result = sync_worktree(worktree_path, squash=squash, abort_on_conflict=abort, close_if_empty=close)

    if result.success:
        if result.closed:
            click.echo(result.message)
            return
        click.echo(result.message)
        if result.push_message:
            click.echo(result.push_message)
        return

    # Handle conflicts
    if result.had_conflicts:
        if result.aborted:
            click.echo(result.message, err=True)
            raise SystemExit(1)
        print_rebase_conflict_help(result)
        raise SystemExit(1)

    raise click.ClickException(result.message)


@cli.command("close")
@click.argument("targets", nargs=-1)
@click.option("--wait", is_flag=True, help="Wait for the PR to merge before closing")
@click.option("--timeout", default=3600, help="Max seconds to wait for merge (default: 3600)")
@click.option("--interval", default=30, help="Poll interval in seconds (default: 30)")
@click.option(
    "--force",
    is_flag=True,
    help="Close even with unmerged/unresolved work; aborts an in-progress sync and "
    "creates a 'reopen the branch' task.",
)
def cmd_close(targets, wait, timeout, interval, force):
    """Close one or more worktrees (sync, verify clean, checkout main).

    Closes a worktree by:
    1. Syncing against origin/main (rebase)
    2. Verifying no uncommitted changes
    3. Verifying no unmerged commits
    4. Checking out the main branch

    The worktree folder, NATO name, and .env file are preserved.
    The worktree can later be recycled with 'mael add <branch>'.

    With --wait, monitors the worktree's PR and only attempts the close once it
    has merged; if the PR is closed without merging or its CI fails, an error is
    raised instead. Waiting is bounded by --timeout (default 1 hour).

    With --force, closes incomplete work too: a conflicting sync is aborted (not
    left mid-rebase), and the worktree is freed even with unmerged commits or a
    dirty tree. Nothing is discarded — uncommitted changes are committed onto the
    branch as 'wip: uncommitted changes' first. The branch and its PR are never
    deleted, and a 'Reopen <branch>' task is created so the work isn't forgotten.
    """
    # If no targets given, use cwd detection (original behavior)
    if not targets:
        targets = (None,)

    errors = []
    for target in targets:
        try:
            ctx = resolve_context(
                target,
                require_project=True,
                require_worktree=True,
            )
        except ValueError as e:
            click.echo(f"Error ({target}): {e}", err=True)
            errors.append(target)
            continue

        worktree_path = ctx.worktree_path

        if worktree_path is None or not worktree_path.exists():
            click.echo(f"Error: Worktree not found at {worktree_path}", err=True)
            errors.append(target)
            continue
        assert ctx.project is not None
        assert ctx.worktree is not None

        # Wait for the PR to merge before closing. Done before stopping the env so
        # a still-running dev environment stays alive while we wait.
        if wait:
            click.echo(f"Waiting for PR to merge before closing '{ctx.worktree}'...")
            try:
                pr = wait_for_merge(worktree_path, timeout=timeout, poll_interval=interval)
                click.echo(f"PR #{pr.number} merged.")
            except TimeoutError as e:
                click.echo(str(e), err=True)
                errors.append(target)
                continue
            except RuntimeError as e:
                click.echo(f"Error: {e}", err=True)
                errors.append(target)
                continue

        # Stop running environment if any
        env_store = make_store()
        env_status = get_env_status(env_store, ctx.project, ctx.worktree)
        if env_status and any(s.alive for s in env_status):
            click.echo(f"Stopping environment for '{ctx.worktree}'...")
            for msg in stop_env(env_store, ctx.project, ctx.worktree):
                click.echo(f"  {msg}")

        # Rescue any vars added to this worktree's .env back to the parent before
        # closing. Warnings never fail the close.
        if ctx.project_path is not None:
            copy_back = copy_back_new_env_vars(ctx.project_path, worktree_path)
            print_copy_back_result(copy_back, ctx.project_path)

        click.echo(f"Closing worktree '{ctx.worktree}'...")
        result = close_worktree(worktree_path, force=force)

        if result.success:
            click.echo(result.message)
            # On a forced close that preserved unmerged work, create a "reopen the
            # branch" task so the branch + PR aren't forgotten. Done before closing
            # the cmux workspace. A real branch only (already-detached → "HEAD").
            if force and result.had_unmerged_work and result.branch and result.branch != "HEAD":
                try:
                    add_task(
                        project=ctx.project,
                        title=f"Reopen {result.branch}",
                        command="reopen-branch",
                        branch=result.branch,
                        content=(
                            f"`{result.branch}` was force-closed with unmerged work (any "
                            f"uncommitted changes were saved as a `wip: uncommitted changes` "
                            f"commit). Reopening restores the worktree; review the PR and env "
                            f"to decide what's left, and unwind the wip commit if there was one."
                        ),
                        run=False,
                    )
                except click.ClickException as e:
                    # The worktree is already closed; a task-store hiccup must not fail
                    # the close. Warn and move on.
                    click.echo(
                        f"Warning: could not create reopen task for '{result.branch}': {e}",
                        err=True,
                    )
            # Close cmux workspace after successful worktree close
            if mael_layout.close_workspace(ctx.project, ctx.worktree):
                ws_name = mael_layout.workspace_name(ctx.project, ctx.worktree)
                click.echo(f"Closed cmux workspace '{ws_name}'.")
            continue

        # Handle specific failure cases
        if result.had_dirty_files:
            click.echo(f"Error: Worktree '{ctx.worktree}' has uncommitted changes.", err=True)
            click.echo()
            click.echo("Please commit or stash your changes before closing:")
            click.echo("  git status          # See uncommitted changes")
            click.echo("  git add . && git commit -m 'message'")
            click.echo("  # OR")
            click.echo("  git stash           # Temporarily stash changes")
            errors.append(target)
            continue

        if result.had_unpushed_commits:
            click.echo(f"Error: Worktree '{ctx.worktree}' has commits not merged to main.", err=True)
            click.echo()
            click.echo("Please push your changes and merge the PR before closing:")
            click.echo("  git push origin <branch>")
            click.echo("  # Then create/merge a PR")
            errors.append(target)
            continue

        click.echo(f"Error closing '{ctx.worktree}': {result.message}", err=True)
        errors.append(target)

    if errors:
        raise SystemExit(1)


@cli.command("sync-all")
@click.argument("project", required=False, default=None)
def cmd_sync_all(project):
    """Sync all worktrees in a project against origin/main."""
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

    if project_path is None or not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")
    project_name = ctx.project
    assert project_name is not None

    worktrees = list_worktrees(project_path)

    # Filter out bare/detached worktrees (the project root)
    worktrees = [wt for wt in worktrees if wt.branch and wt.path != project_path]

    if not worktrees:
        click.echo("No worktrees found to sync.")
        return

    # Fetch once for all worktrees (they share the same repo)
    click.echo("Fetching from origin...")
    try:
        run_git(["fetch", "origin"], cwd=project_path)
    except Exception as e:
        raise click.ClickException(f"Failed to fetch from origin: {e}")

    # Fast-forward local main to match origin/main
    from .worktree import update_local_main
    main_result = update_local_main(project_path)
    if main_result.status == "updated":
        click.echo(f"  {main_result.message}")
    elif main_result.status == "warning":
        click.echo(f"  Warning: {main_result.message}", err=True)

    click.echo(f"Syncing {len(worktrees)} worktree(s) with origin/main...")
    click.echo()

    for wt in worktrees:
        # Extract worktree name from folder for display (e.g., "myproject-alpha" -> "alpha")
        display_name = extract_worktree_name_from_folder(project_name, wt.path.name) or wt.path.name
        click.echo(f"Syncing {display_name} ({wt.branch})...")
        result = sync_worktree(wt.path, skip_fetch=True)

        if result.success:
            click.echo(f"  {result.message}")
            if result.push_message:
                click.echo(f"  {result.push_message}")
            click.echo()
            continue

        # Handle failure - stop immediately
        if result.had_conflicts:
            click.echo(f"  Rebase encountered conflicts in {display_name}.", err=True)
            click.echo()
            if result.merge_base and result.upstream_head:
                click.echo("To see what changed upstream:")
                click.echo(f"  cd {wt.path}")
                click.echo(f"  git log {result.merge_base}..{result.upstream_head} --oneline")
            click.echo()
            click.echo("To resolve conflicts:")
            click.echo(f"  cd {wt.path}")
            click.echo("  git status")
            click.echo("  # edit files to resolve conflicts")
            click.echo("  git add <resolved-files>")
            click.echo("  git rebase --continue")
            click.echo()
            click.echo("To abort the rebase:")
            click.echo("  git rebase --abort")
        else:
            click.echo(f"  Failed: {result.message}", err=True)

        raise SystemExit(1)

    click.echo("All worktrees synced successfully.")


@cli.command("tidy-branches")
@click.argument("project", required=False, default=None)
def cmd_tidy_branches(project):
    """Clean up feature branches by rebasing and removing merged ones.

    For each feature branch (not main):

    \b
    - If checked out in a worktree: skip
    - Pull remote changes if branch exists on origin
    - Attempt rebase against origin/main
    - If conflicts: abort and skip
    - If merged (same as main): delete local and remote branch
    - If not merged: force push to origin (if remote exists)
    """
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

    click.echo(f"Tidying branches for {ctx.project}...")
    click.echo()

    results = tidy_branches(project_path)

    if not results:
        click.echo("No feature branches to tidy.")
        return

    # Categorize results
    deleted = [r for r in results if r.action == "deleted"]
    pushed = [r for r in results if r.action == "pushed"]
    rebased = [r for r in results if r.action == "rebased"]
    conflicts = [r for r in results if r.action == "skipped_conflicts"]
    checked_out = [r for r in results if r.action == "skipped_checked_out"]
    errors = [r for r in results if r.action == "skipped_error"]

    click.echo("Results:")
    click.echo()

    if deleted:
        click.echo(f"  Deleted ({len(deleted)}):")
        for r in deleted:
            remote_info = " (local + remote)" if r.deleted_remote else " (local only)"
            click.echo(f"    - {r.branch}{remote_info}")

    if pushed:
        click.echo(f"  Rebased & pushed ({len(pushed)}):")
        for r in pushed:
            click.echo(f"    - {r.branch}")

    if rebased:
        click.echo(f"  Rebased (local only) ({len(rebased)}):")
        for r in rebased:
            click.echo(f"    - {r.branch}")

    if conflicts:
        click.echo(f"  Skipped (conflicts) ({len(conflicts)}):")
        for r in conflicts:
            click.echo(f"    - {r.branch}")

    if checked_out:
        click.echo(f"  Skipped (checked out) ({len(checked_out)}):")
        for r in checked_out:
            click.echo(f"    - {r.branch}")

    if errors:
        click.echo(f"  Errors ({len(errors)}):", err=True)
        for r in errors:
            click.echo(f"    - {r.branch}: {r.message}", err=True)


@cli.command("doctor")
@click.argument("project", required=False)
def cmd_doctor(project):
    """Check project health and auto-fix issues."""
    from .doctor import CheckStatus, run_doctor

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
    result = run_doctor(project_path)

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


# --- Subcommand groups ---

cli.add_command(cmd_review_prepare)
cli.add_command(env_cli)
cli.add_command(git_cli)
cli.add_command(gh_cli)
cli.add_command(linear)
cli.add_command(sentry)
cli.add_command(uptimerobot)
cli.add_command(session_cli)
cli.add_command(session_channel_cmd)
cli.add_command(task_cli)
cli.add_command(schedule_group)
cli.add_command(status_cli)
cli.add_command(cmd_install)
cli.add_command(cmd_self_update)


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI."""
    try:
        cli(args=argv, standalone_mode=False)
        return 0
    except click.ClickException as e:
        e.show()
        return 1
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
