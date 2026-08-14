"""Command-line interface for maelstrom."""

import subprocess
import sys
from pathlib import Path

import click

from . import __version__
from .context import load_global_config, resolve_context, validate_project_name
from .ports import get_app_url
from .github import (
    create_project_repo,
    get_pr_number_and_commits,
    wait_for_merge,
)
from .cmux import mael_layout
from .cmux.client import ensure_cmux_running, resolve_socket_path
from .session_cli import session as session_cli, session_channel as session_channel_cmd
from .task_cli import task as task_cli
from .task_cli import add_task
from . import task as task_model
from .task_index import StaleTaskIndexError
from .task_store import GitFileStore
from .wiki_cli import wiki as wiki_cli
from .schedule_launchd import schedule_group
from .env import get_env_status, regenerate_and_restart_if_running, stop_env, stop_sessions
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
from .integrations.slack import slack
from .integrations.uptimerobot import uptimerobot
from .mv_project_cli import cmd_mv_project
from .status_cli import status as status_cli
from .admin_cli import cmd_install, cmd_self_update
from . import session_discovery
from .table import draw_table
from .worktree import (
    SyncResult,
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
    sync_worktree_with_autorepair,
    tidy_branches,
    update_claude_local_md,
)
from .worktree_launcher import (
    launch_claude_in_worktree,
    open_worktree,
)
from .worktree_model import (
    MAIN_BRANCH,
    REPAIRED_MESSAGE,
    extract_project_name,
    extract_worktree_name_from_folder,
    get_worktree_folder_name,
    has_claude_transcript,
)


# The branch `mael create-project` opens its first worktree on, so a new project
# starts on a feature branch rather than on main.
START_BRANCH = "feat/start-project"


def _launch_claude_or_raise(
    worktree_path: Path, project: str | None, worktree: str | None
) -> None:
    """Launch a plain Claude session inside cmux, or raise if placement fails.

    ``mael`` always places Claude in cmux by driving the socket — starting the
    app if it's down. There is no local fallback: if cmux can't be reached we
    error clearly rather than silently dropping a ``claude`` into the current
    shell. ``mael task run --here`` is the only path that runs Claude locally.
    """
    if not launch_claude_in_worktree(worktree_path, project=project, worktree=worktree):
        raise click.ClickException(
            "cmux is not running and could not be started; start cmux and retry"
        )


def _report_open_sync(sync: SyncResult | None) -> None:
    """Echo the result of the sync that ran when a worktree was opened.

    ``None`` means no sync ran (the worktree was reused), so there is nothing to
    say. A failure exits non-zero: the caller must not launch a session onto
    code that was never rebased.
    """
    if sync is None:
        return

    if not sync.success:
        if sync.had_conflicts:
            click.echo(sync.message, err=True)
            click.echo(
                "Resolve them by running `mael sync --autorepair` in the worktree, "
                "or rebase by hand.",
                err=True,
            )
            raise SystemExit(1)
        raise click.ClickException(f"Sync failed: {sync.message}")

    click.echo(sync.message)
    if sync.repaired:
        click.echo(REPAIRED_MESSAGE)
    if sync.push_message:
        # A rejected push still leaves a usable worktree, so the launch goes
        # ahead — but the branch and its remote have diverged, which is a
        # warning, not progress.
        click.echo(sync.push_message, err=not sync.pushed)


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


@cli.command("create-project")
@click.argument("name")
@click.option("--public", is_flag=True, help="Create a public repository (default: private)")
@click.option("--description", default=None, help="Repository description")
@click.option("--projects-dir", help="Base directory for projects (default from ~/.maelstrom/config.yaml or ~/Projects)")
@click.pass_context
def cmd_create_project(ctx, name, public, description, projects_dir):
    """Create a GitHub repository and check it out for use with maelstrom.

    NAME is the repository name, optionally as ``owner/name``. The new
    repository holds a seed commit with `.gitignore`, `.maelstrom.yaml`,
    `README.md` and `CLAUDE.md`. It is then checked out as a maelstrom project
    and a worktree opens on `feat/start-project`.
    """
    configured_dir = load_global_config().projects_dir
    if projects_dir:
        projects_dir_path = Path(projects_dir).expanduser()
    else:
        projects_dir_path = configured_dir

    local_name = name.split("/")[-1]
    try:
        validate_project_name(local_name)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Check before any remote work, so a name clash never leaves an orphan repo.
    expected_project_path = projects_dir_path / local_name
    if expected_project_path.exists():
        raise click.ClickException(
            f"Project directory already exists: {expected_project_path}"
        )

    click.echo(f"Creating GitHub repository {name}...")
    try:
        git_url = create_project_repo(name, private=not public, description=description)
    except RuntimeError as e:
        raise click.ClickException(str(e))
    click.echo(f"Repository created: {git_url}")

    click.echo(f"Cloning {git_url}...")
    try:
        project_path = add_project(git_url, projects_dir_path)
    except Exception as e:
        # The repository exists remotely; do not delete it. Point at the
        # canonical recovery so the work so far is not lost. A failed git call
        # keeps its reason in stderr, so `str(e)` alone would say only that the
        # command exited non-zero.
        reason = str(e)
        if isinstance(e, subprocess.CalledProcessError) and e.stderr:
            reason = e.stderr.strip()
        raise click.ClickException(
            f"Repository created at {git_url}, but checkout failed: {reason}\n"
            f"Retry the checkout with: mael add-project {git_url}"
        )

    # Name the project after the directory add_project actually made. It derives
    # that from the clone URL, which need not match the requested name.
    project_name = project_path.name
    alpha_folder = get_worktree_folder_name(project_name, "alpha")
    click.echo(f"Project created at: {project_path}")
    click.echo(f"Alpha worktree at: {project_path / alpha_folder}")

    # Open a worktree to start work in, the same as `mael add feat/start-project`
    # run inside the project. `mael add` resolves the project through the global
    # projects_dir, so it cannot find a project put somewhere else with
    # --projects-dir; say so rather than failing there.
    if projects_dir_path.resolve() != configured_dir.resolve():
        click.echo(
            f"No worktree opened: {projects_dir_path} is not the configured "
            f"projects directory, so `mael add` cannot find this project. "
            f"Set projects_dir in ~/.maelstrom/config.yaml to use it."
        )
        return

    # The repository and the checkout are both done by now, so a failure here
    # must not read as "nothing happened": say what exists and how to retry.
    try:
        ctx.invoke(cmd_add, branch=START_BRANCH, project=project_name)
    except Exception as e:
        raise click.ClickException(
            f"Project is checked out at {project_path}, but opening a worktree "
            f"failed: {e}\n"
            f"Retry with: mael add {START_BRANCH} -p {project_name}"
        )


@cli.command("add")
@click.argument("branch", required=False, default=None)
@click.option("-p", "--project", default=None, help="Project name (default: detect from cwd)")
@click.option("--open", is_flag=True, help="Open in configured editor instead of Claude CLI")
@click.option("--no-recycle", is_flag=True, help="Don't recycle closed worktrees, always create new")
def cmd_add(branch, project, open, no_recycle):
    """Add a new worktree for a branch.

    If BRANCH is provided:
      - Tries to recycle a closed worktree (a detached, clean one) if available
      - Otherwise creates a new worktree

    If BRANCH is omitted:
      - Creates a new worktree detached at origin/main
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
            _launch_claude_or_raise(worktree_path, ctx.project, wt_name)
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

    # Opening a worktree rebases its branch onto origin/main first. A failed sync
    # blocks the launch: a session must never start on unrebased code. Reported
    # after the env regen above so a retry (which reuses the worktree, and so
    # never syncs) doesn't skip the regen.
    _report_open_sync(result.sync)

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
        _launch_claude_or_raise(worktree_path, ctx.project, wt_name)


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


def _branch_session_ids(project_name):
    """Map ``branch -> [session_id, ...]`` for every task in ``project_name``.

    Several tasks can share a branch/worktree (one PR per parent), so each branch
    maps to the deterministic session ids of *all* its tasks. Used to detect a
    stopped-but-not-live session for a worktree: any of a branch's task sessions
    having an on-disk transcript means that worktree "ran before". Returns an empty
    map when the task notebook is absent or unreadable — the SESSION column then
    simply shows no stopped marker; this cosmetic feature must never break ``list``.
    """
    try:
        store = GitFileStore()
        result: dict[str, list[str]] = {}
        for t in task_model.list_tasks(store, project=project_name, no_index=True):
            branch = t.branch or task_model.default_branch(t.id, t.parent)
            result.setdefault(branch, []).append(
                task_model.session_id_for(project_name, t.id)
            )
        return result
    except (OSError, ValueError, KeyError):
        # An absent/unreadable notebook or a malformed task must degrade to "no
        # marker", not crash `list`. Kept narrow: a logic bug (AttributeError etc.)
        # still surfaces rather than being silently swallowed.
        return {}


def _session_display(count, worktree_path, branch, branch_sessions):
    """Render the SESSION cell: live count wins, else a stopped marker, else blank.

    ``count`` is the live-session count for the worktree. When there is a live
    session we show it unchanged. When there is none, we show ``"— stopped"`` if
    any task on ``branch`` left an on-disk transcript in ``worktree_path`` (ran and
    stopped), distinguishing it from a never-run worktree, which stays blank.
    """
    if count:
        return str(count)
    if branch:
        for session_id in branch_sessions.get(branch, []):
            if has_claude_transcript(worktree_path, session_id):
                return "— stopped"
    return ""


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

    # One live-session sweep shared across every row (a `pgrep`+`lsof` pair),
    # rather than a system-wide scan per worktree. The instance also memoises the
    # per-session worktree-list lookup so `git worktree list` runs once, not
    # once per (worktree row × session).
    live_sessions = session_discovery.LiveSessionSet()
    # Branch → task session ids, built once, so the SESSION column can show a
    # stopped marker (transcript exists, no live session) vs blank (never run).
    branch_sessions = _branch_session_ids(project_name)

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

        # Live Claude session count, or a stopped marker when a transcript exists.
        session_count = live_sessions.count_for(wt.path)
        session_display = _session_display(
            session_count, wt.path, wt.branch, branch_sessions
        )

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
            "SESSION": session_display,
        })

    draw_table(rows, ["WORKTREE", "BRANCH", "DIRTY FILES", "LOCAL COMMITS", "PR (COMMITS)", "APP", "SESSION"])

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

    # One live-session sweep shared across every project/worktree row, plus a
    # memo so the per-session worktree-list lookup runs once, not per row.
    live_sessions = session_discovery.LiveSessionSet()

    # Collect structured data for all worktrees
    projects_data = []
    rows = []
    closed_by_project: dict[str, list[str]] = {}
    for project_path in projects:
        project_name = project_path.name
        worktrees = list_worktrees(project_path)
        worktree_data = []
        # Branch → task session ids for this project (stopped-marker detection).
        branch_sessions = _branch_session_ids(project_name)

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
                    "session_count": 0,
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

            # Live Claude session count, or a stopped marker when a transcript exists.
            session_count = live_sessions.count_for(wt.path)
            session_display = _session_display(
                session_count, wt.path, wt.branch, branch_sessions
            )

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
                "session_count": session_count,
            })

            rows.append({
                "PROJECT": project_name,
                "WORKTREE": wt.path.name,
                "BRANCH": branch_display,
                "DIRTY FILES": dirty_display,
                "LOCAL COMMITS": local_display,
                "PR (COMMITS)": pr_display,
                "APP": app_display,
                "SESSION": session_display,
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

    draw_table(rows, ["PROJECT", "WORKTREE", "BRANCH", "DIRTY FILES", "LOCAL COMMITS", "PR (COMMITS)", "APP", "SESSION"])

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

    _launch_claude_or_raise(worktree_path, ctx.project, ctx.worktree)


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

    _launch_claude_or_raise(worktree_path, ctx.project, ctx.worktree)


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
@click.option(
    "--autorepair",
    is_flag=True,
    help="On rebase conflict, run a headless Claude session "
    "(/resolve-rebase-conflicts) to resolve and continue",
)
def cmd_sync(target, squash, abort, close, autorepair):
    """Rebase worktree against origin/main.

    With --autorepair, a rebase conflict starts a headless Claude session that
    resolves it and continues the rebase. This supersedes --abort: an autorepair
    failure aborts and restores the worktree, except where the session finished
    the rebase on another branch and there is nothing to abort.
    """
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
    if autorepair:
        result = sync_worktree_with_autorepair(
            worktree_path, squash=squash, close_if_empty=close, announce=click.echo,
        )
    else:
        result = sync_worktree(
            worktree_path, squash=squash, abort_on_conflict=abort, close_if_empty=close,
        )

    if result.success:
        if result.closed:
            click.echo(result.message)
            return
        click.echo(result.message)
        if result.repaired:
            click.echo(REPAIRED_MESSAGE)
        if result.push_message:
            click.echo(result.push_message)
        return

    # Handle conflicts. An aborted rebase is restored, so the manual-resolution
    # help would name a rebase that is no longer there — that covers most
    # autorepair failures. A repair that failed without aborting, by landing on
    # the wrong branch, still needs the help.
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

        # Gracefully stop any live Claude sessions in this worktree before tearing
        # it down, so close doesn't orphan them. Best-effort: SIGINT (cancel any
        # in-flight turn), then SIGTERM survivors, then proceed regardless.
        worktree_sessions = session_discovery.LiveSessionSet().all_for(worktree_path)
        if worktree_sessions:
            click.echo(f"Stopping {len(worktree_sessions)} Claude session(s) in '{ctx.worktree}'...")
            for msg in stop_sessions(worktree_sessions):
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
@click.option(
    "--autorepair",
    is_flag=True,
    help="On rebase conflict, run a headless Claude session "
    "(/resolve-rebase-conflicts) to resolve it and continue",
)
def cmd_sync_all(project, autorepair):
    """Sync all worktrees in a project against origin/main.

    With --autorepair, a rebase conflict starts a headless Claude session that
    resolves it and continues the rebase. One session runs per conflicting
    worktree, in turn.
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
        if autorepair:
            result = sync_worktree_with_autorepair(
                wt.path, skip_fetch=True, announce=click.echo,
            )
        else:
            result = sync_worktree(wt.path, skip_fetch=True)

        if result.success:
            click.echo(f"  {result.message}")
            if result.repaired:
                click.echo(f"  {REPAIRED_MESSAGE}")
            if result.push_message:
                click.echo(f"  {result.push_message}")
            click.echo()
            continue

        # An aborted rebase is restored, so the manual-resolution steps would
        # name a rebase that is no longer there. A repair that failed without
        # aborting still leaves the worktree needing hands-on work.
        if result.aborted:
            click.echo(f"  {result.message}", err=True)
            raise SystemExit(1)

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

@cli.group("cmux")
def cmux_cli() -> None:
    """Inspect the cmux integration."""


@cmux_cli.command("status")
def cmd_cmux_status() -> None:
    """Report whether mael can place a Claude session into cmux.

    Runs the same ``ensure_cmux_running`` probe the launcher uses — starting the
    app if it's down — and reports the outcome. Exits non-zero when cmux can't be
    reached, so it doubles as a health check for scheduled runs.
    """
    # Report the path the probe actually uses, which defaults when the env var
    # is unset — so the diagnostic never claims "unset" for a socket it did try.
    socket_path = resolve_socket_path()
    if ensure_cmux_running():
        click.echo(f"cmux OK (socket: {socket_path})")
        return
    raise click.ClickException(
        "cmux is not reachable and could not be started "
        f"(socket: {socket_path})"
    )


cli.add_command(cmux_cli)
cli.add_command(env_cli)
cli.add_command(git_cli)
cli.add_command(gh_cli)
cli.add_command(linear)
cli.add_command(sentry)
cli.add_command(slack)
cli.add_command(uptimerobot)
cli.add_command(session_cli)
cli.add_command(session_channel_cmd)
cli.add_command(task_cli)
cli.add_command(wiki_cli)
cli.add_command(schedule_group)
cli.add_command(status_cli)
cli.add_command(cmd_mv_project)
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
    except StaleTaskIndexError as e:
        # Handled here rather than per-command: any task command can be the one
        # that first touches a pre-schema-change index.db, and the remedy
        # (`mael task reindex`) is the same for all of them.
        click.echo(f"Error: {e}", err=True)
        return 1
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0


if __name__ == "__main__":
    sys.exit(main())
