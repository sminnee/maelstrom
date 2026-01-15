"""Command-line interface for maelstrom."""

import sys
from pathlib import Path

import click

from . import __version__
from .context import load_global_config, resolve_context
from .github import (
    create_pr,
    download_artifact,
    get_check_logs_truncated,
    get_full_check_log,
    get_pr_number_for_branch,
    get_worktree_code,
    read_pr,
)
from .linear import linear
from .sentry import sentry
from .claude_integration import install_claude_integration
from .claude_sessions import get_active_ide_sessions
from .worktree import (
    add_project,
    create_worktree,
    extract_project_name,
    extract_worktree_name_from_folder,
    find_all_projects,
    get_commits_ahead,
    get_worktree_folder_name,
    get_worktree_dirty_files,
    list_worktrees,
    open_worktree,
    remove_worktree_by_path,
    run_git,
    sync_worktree,
)


@click.group()
@click.version_option(version=__version__, prog_name="mael")
def cli():
    """Maelstrom - Parallel development environment manager."""
    pass


# --- Core worktree commands ---


@cli.command("install")
def cmd_install():
    """Install maelstrom's Claude Code skills and hooks."""
    messages = install_claude_integration()
    for msg in messages:
        click.echo(msg)


@cli.command("add-project")
@click.argument("git_url")
@click.option("--projects-dir", help="Base directory for projects (default from ~/.maelstrom.yaml or ~/Projects)")
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
@click.argument("branch")
@click.option("-p", "--project", default=None, help="Project name (default: detect from cwd)")
@click.option("--no-open", is_flag=True, help="Don't open the worktree after creation")
def cmd_add(branch, project, no_open):
    """Add a new worktree for a branch."""
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

    if not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")

    click.echo(f"Creating worktree for branch '{branch}'...")
    try:
        worktree_path = create_worktree(project_path, branch)
        click.echo(f"Worktree created at: {worktree_path}")

        # Check if .env was created
        env_file = worktree_path / ".env"
        if env_file.exists():
            click.echo(f"Environment file created: {env_file}")
            click.echo("Port assignments:")
            for line in env_file.read_text().strip().split("\n"):
                click.echo(f"  {line}")

        # Open the worktree unless --no-open was specified
        if not no_open:
            global_config = load_global_config()
            try:
                open_worktree(worktree_path, global_config.open_command)
            except RuntimeError as e:
                click.echo(f"Warning: Could not open worktree: {e}", err=True)
    except Exception as e:
        raise click.ClickException(f"Error creating worktree: {e}")


@cli.command("remove")
@click.argument("target")
@click.option("-f", "--force", is_flag=True, help="Skip confirmation prompt for modified/untracked files")
def cmd_remove(target, force):
    """Remove a worktree."""
    try:
        ctx = resolve_context(
            target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        raise click.ClickException(str(e))

    project_path = ctx.project_path
    worktree_name = ctx.worktree  # The NATO name (e.g., "alpha")

    if not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")

    folder_name = get_worktree_folder_name(ctx.project, worktree_name)
    worktree_path = project_path / folder_name
    if not worktree_path.exists():
        raise click.ClickException(f"Worktree '{worktree_name}' not found in project '{ctx.project}'")

    # Check for modified/untracked files (excluding maelstrom-managed files)
    dirty_files = get_worktree_dirty_files(worktree_path)
    if dirty_files and not force:
        click.echo("The following modified/untracked files will be lost:")
        for f in dirty_files:
            click.echo(f"  {f}")
        if not click.confirm("Continue?"):
            click.echo("Aborted.")
            raise SystemExit(1)

    click.echo(f"Removing worktree '{worktree_name}'...")
    try:
        remove_worktree_by_path(project_path, folder_name)
        click.echo("Worktree removed successfully.")
    except Exception as e:
        raise click.ClickException(f"Error removing worktree: {e}")


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

    if not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")

    worktrees = list_worktrees(project_path)

    # Filter out bare/detached worktrees (typically the project root)
    worktrees = [wt for wt in worktrees if wt.branch and wt.path != project_path]

    if not worktrees:
        click.echo("No worktrees found.")
        return

    # Get active IDE sessions
    active_sessions = get_active_ide_sessions()

    # Gather extended info for each worktree
    rows = []
    for wt in worktrees:
        dirty = "Y" if get_worktree_dirty_files(wt.path) else ""
        commits = get_commits_ahead(wt.path)
        pr_num = get_pr_number_for_branch(project_path, wt.branch)
        pr_display = f"#{pr_num}" if pr_num else ""
        agents = active_sessions.get(wt.path, 0)
        # Extract worktree name from folder for display (e.g., "myproject-alpha" -> "alpha")
        display_name = extract_worktree_name_from_folder(ctx.project, wt.path.name) or wt.path.name
        rows.append((display_name, wt.branch, dirty, commits, pr_display, agents))

    # Print header
    click.echo(f"{'WORKTREE':<12} {'BRANCH':<30} {'DIRTY':<6} {'AHEAD':<6} {'PR':<8} {'AGENTS':<6}")
    click.echo("-" * 76)

    for name, branch, dirty, commits, pr_display, agents in rows:
        commits_display = str(commits) if commits > 0 else ""
        agents_display = str(agents) if agents > 0 else ""
        click.echo(f"{name:<12} {branch:<30} {dirty:<6} {commits_display:<6} {pr_display:<8} {agents_display:<6}")


@cli.command("list-all")
def cmd_list_all():
    """List all worktrees across all projects."""
    global_config = load_global_config()
    projects_dir = global_config.projects_dir

    projects = find_all_projects(projects_dir)
    if not projects:
        click.echo("No projects found.")
        return

    # Get active IDE sessions once for all projects
    active_sessions = get_active_ide_sessions()

    # Collect all worktrees with project info
    rows = []
    for project_path in projects:
        project_name = project_path.name
        worktrees = list_worktrees(project_path)

        for wt in worktrees:
            # Skip bare/detached worktrees (typically the project root)
            if not wt.branch or wt.path == project_path:
                continue

            dirty = "Y" if get_worktree_dirty_files(wt.path) else ""
            commits = get_commits_ahead(wt.path)
            pr_num = get_pr_number_for_branch(project_path, wt.branch)
            pr_display = f"#{pr_num}" if pr_num else ""
            agents = active_sessions.get(wt.path, 0)
            rows.append((project_name, wt.path.name, wt.branch, dirty, commits, pr_display, agents))

    if not rows:
        click.echo("No worktrees found.")
        return

    # Print header with PROJECT column
    click.echo(f"{'PROJECT':<12} {'WORKTREE':<12} {'BRANCH':<30} {'DIRTY':<6} {'AHEAD':<6} {'PR':<8} {'AGENTS':<6}")
    click.echo("-" * 88)

    for project_name, name, branch, dirty, commits, pr_display, agents in rows:
        commits_display = str(commits) if commits > 0 else ""
        agents_display = str(agents) if agents > 0 else ""
        click.echo(f"{project_name:<12} {name:<12} {branch:<30} {dirty:<6} {commits_display:<6} {pr_display:<8} {agents_display:<6}")


@cli.command("open")
@click.argument("target", required=False, default=None)
def cmd_open(target):
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

    if not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    global_config = load_global_config()
    try:
        open_worktree(worktree_path, global_config.open_command)
    except RuntimeError as e:
        raise click.ClickException(str(e))


@cli.command("sync")
@click.argument("target", required=False, default=None)
def cmd_sync(target):
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

    if not worktree_path.exists():
        raise click.ClickException(f"Worktree not found at {worktree_path}")

    click.echo(f"Syncing {ctx.worktree} with origin/main...")
    result = sync_worktree(worktree_path)

    if result.success:
        click.echo(result.message)
        if result.push_message:
            click.echo(result.push_message)
        return

    # Handle conflicts
    if result.had_conflicts:
        click.echo("Rebase encountered conflicts.", err=True)
        click.echo()

        # Show commands with specific SHAs if available
        if result.merge_base and result.upstream_head:
            click.echo("To see what changed upstream:")
            click.echo(f"  git log {result.merge_base}..{result.upstream_head} --oneline")
            click.echo(f"  git diff {result.merge_base}...{result.upstream_head}")
        else:
            click.echo("To see what changed upstream:")
            click.echo("  git log HEAD..origin/main --oneline")
            click.echo("  git diff HEAD...origin/main")

        click.echo()
        click.echo("To resolve conflicts:")
        click.echo("  git status                  # see conflicted files")
        click.echo("  # edit files to resolve conflicts")
        click.echo("  git add <resolved-files>")
        click.echo("  git rebase --continue")
        click.echo()
        click.echo("To abort the rebase:")
        click.echo("  git rebase --abort")
        raise SystemExit(1)

    raise click.ClickException(result.message)


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

    if not project_path.exists():
        raise click.ClickException(f"Project '{ctx.project}' not found at {project_path}")

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

    click.echo(f"Syncing {len(worktrees)} worktree(s) with origin/main...")
    click.echo()

    for wt in worktrees:
        # Extract worktree name from folder for display (e.g., "myproject-alpha" -> "alpha")
        display_name = extract_worktree_name_from_folder(ctx.project, wt.path.name) or wt.path.name
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


# --- GitHub subcommand group ---


@cli.group("gh")
def gh():
    """GitHub-related commands."""
    pass


@gh.command("create-pr")
@click.argument("target", required=False, default=None)
@click.option("--draft", is_flag=True, help="Create as draft PR")
def gh_create_pr(target, draft):
    """Create a PR for the current worktree (or push if PR exists)."""
    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Determine working directory for PR creation
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        url, created = create_pr(cwd=cwd, draft=draft)
        if created:
            click.echo(f"PR created: {url}")
        else:
            click.echo(f"Pushed to existing PR: {url}")
    except Exception as e:
        raise click.ClickException(str(e))


def _format_size(size_bytes: int) -> str:
    """Format a byte size as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


@gh.command("read-pr")
@click.argument("target", required=False, default=None)
def gh_read_pr(target):
    """Read PR status, unresolved comments, and check results."""
    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Determine working directory
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        pr_info = read_pr(cwd=cwd)
    except RuntimeError as e:
        raise click.ClickException(str(e))

    # Print header
    click.echo(f"PR #{pr_info.number}: {pr_info.title}")
    click.echo(f"URL: {pr_info.url}")

    # If merged, show simple message and exit
    if pr_info.merged:
        click.echo()
        click.echo("PR has been merged - no further action necessary")
        return

    click.echo(f"Status: {pr_info.state}")

    # Unresolved comments
    if pr_info.review_threads:
        click.echo()
        click.echo(f"--- Unresolved Comments ({len(pr_info.review_threads)}) ---")
        for thread in pr_info.review_threads:
            line_info = f":{thread.line}" if thread.line else ""
            click.echo(f"  {thread.path}{line_info}")
            for comment in thread.comments:
                # Indent and truncate long comments
                body_preview = comment.body.replace("\n", " ")[:100]
                if len(comment.body) > 100:
                    body_preview += "..."
                click.echo(f"    @{comment.author}: {body_preview}")

    # Checks
    failed_checks = [c for c in pr_info.checks if c.state == "FAILURE"]
    passing_checks = [c for c in pr_info.checks if c.state == "SUCCESS"]
    pending_checks = [c for c in pr_info.checks if c.state not in ("FAILURE", "SUCCESS")]

    if failed_checks:
        click.echo()
        click.echo("--- Failed Checks ---")
        for check in failed_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            click.echo(f"  X {check.name}{run_id_info}")

            # Show truncated logs
            if check.run_id:
                logs = get_check_logs_truncated(cwd, check.run_id, max_lines=30)
                if logs:
                    click.echo()
                    for line in logs.split("\n"):
                        click.echo(f"    {line}")
                    click.echo()
                click.echo(f"    -> Full log: mael gh check-log [--failed-only] {check.run_id}")

                # Show artifacts for this run
                if check.run_id in pr_info.artifacts:
                    click.echo()
                    click.echo("    Artifacts:")
                    for artifact in pr_info.artifacts[check.run_id]:
                        size_str = _format_size(artifact.size)
                        click.echo(f"      - {artifact.name} ({size_str})")
                        click.echo(f"        -> Download: mael gh download-artifact {check.run_id} {artifact.name}")

    if pending_checks:
        click.echo()
        click.echo("--- Pending Checks ---")
        for check in pending_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            click.echo(f"  ... {check.name}{run_id_info}")

    if passing_checks:
        click.echo()
        click.echo("--- Passing Checks ---")
        for check in passing_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            click.echo(f"  + {check.name}{run_id_info}")


@gh.command("download-artifact")
@click.argument("run_id")
@click.argument("artifact_name")
@click.option("-o", "--output", default=None, help="Output directory (default: current directory)")
def gh_download_artifact(run_id, artifact_name, output):
    """Download an artifact from a PR's workflow run."""
    output_dir = Path(output).expanduser() if output else None

    try:
        artifact_path = download_artifact(
            cwd=Path.cwd(),
            run_id=run_id,
            artifact_name=artifact_name,
            output_dir=output_dir,
        )
        click.echo(f"Artifact downloaded to: {artifact_path}")
    except RuntimeError as e:
        raise click.ClickException(str(e))


@gh.command("check-log")
@click.argument("run_id")
@click.option("--failed-only", is_flag=True, help="Show only failed step logs")
def gh_check_log(run_id, failed_only):
    """Show full log output for a GitHub Actions run."""
    try:
        logs = get_full_check_log(
            cwd=Path.cwd(),
            run_id=run_id,
            failed_only=failed_only,
        )
        click.echo(logs)
    except RuntimeError as e:
        raise click.ClickException(str(e))


@gh.command("show-code")
@click.argument("target", required=False, default=None)
@click.option("--committed", is_flag=True, help="Show only committed changes")
@click.option("--uncommitted", is_flag=True, help="Show only uncommitted changes")
def gh_show_code(target, committed, uncommitted):
    """Show commits and uncommitted changes for a worktree."""
    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Determine working directory
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        commits_output, uncommitted_output = get_worktree_code(cwd)
    except RuntimeError as e:
        raise click.ClickException(str(e))

    # Determine what to show based on flags
    show_committed = not uncommitted
    show_uncommitted = not committed

    if show_committed and commits_output:
        click.echo("=== Commits ===")
        click.echo(commits_output)

    if show_uncommitted and uncommitted_output:
        if show_committed and commits_output:
            click.echo()  # Separator between sections
        click.echo("=== Uncommitted Changes ===")
        click.echo(uncommitted_output)

    if not commits_output and not uncommitted_output:
        click.echo("No commits or uncommitted changes found.")


# --- Linear and Sentry subcommand groups ---

cli.add_command(linear)
cli.add_command(sentry)


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
