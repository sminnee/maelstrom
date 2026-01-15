"""Command-line interface for maelstrom."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .context import load_global_config, resolve_context
from .worktree import (
    add_project,
    create_pr,
    create_worktree,
    download_artifact,
    get_check_logs_truncated,
    get_full_check_log,
    get_worktree_dirty_files,
    list_worktrees,
    open_worktree,
    read_pr,
    remove_worktree_by_path,
)


def cmd_add_project(args: argparse.Namespace) -> int:
    """Clone a git repository for use with maelstrom."""
    git_url = args.git_url

    # Use explicit --projects-dir or fall back to global config
    if args.projects_dir:
        projects_dir = Path(args.projects_dir).expanduser()
    else:
        global_config = load_global_config()
        projects_dir = global_config.projects_dir

    print(f"Cloning {git_url}...")
    try:
        project_path = add_project(git_url, projects_dir)
        print(f"Project created at: {project_path}")
        print(f"Alpha worktree at: {project_path / 'alpha'}")
        return 0
    except Exception as e:
        print(f"Error adding project: {e}", file=sys.stderr)
        return 1


def cmd_create_pr(args: argparse.Namespace) -> int:
    """Create a PR for the current worktree, or push if PR exists."""
    draft = getattr(args, "draft", False)
    target = getattr(args, "target", None)

    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Determine working directory for PR creation
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        url, created = create_pr(cwd=cwd, draft=draft)
        if created:
            print(f"PR created: {url}")
        else:
            print(f"Pushed to existing PR: {url}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_add_worktree(args: argparse.Namespace) -> int:
    """Add a new worktree."""
    branch = args.branch
    no_open = getattr(args, "no_open", False)

    # Get project context (branch is separate, not from context)
    try:
        ctx = resolve_context(
            args.project,
            require_project=True,
            require_worktree=False,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_path = ctx.project_path

    if not project_path.exists():
        print(f"Error: Project '{ctx.project}' not found at {project_path}", file=sys.stderr)
        return 1

    print(f"Creating worktree for branch '{branch}'...")
    try:
        worktree_path = create_worktree(project_path, branch)
        print(f"Worktree created at: {worktree_path}")

        # Check if .env was created
        env_file = worktree_path / ".env"
        if env_file.exists():
            print(f"Environment file created: {env_file}")
            print("Port assignments:")
            for line in env_file.read_text().strip().split("\n"):
                print(f"  {line}")

        # Open the worktree unless --no-open was specified
        if not no_open:
            global_config = load_global_config()
            try:
                open_worktree(worktree_path, global_config.open_command)
            except RuntimeError as e:
                print(f"Warning: Could not open worktree: {e}", file=sys.stderr)

        return 0
    except Exception as e:
        print(f"Error creating worktree: {e}", file=sys.stderr)
        return 1


def cmd_rm_worktree(args: argparse.Namespace) -> int:
    """Remove a worktree."""
    try:
        ctx = resolve_context(
            args.target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_path = ctx.project_path
    worktree_name = ctx.worktree  # This is the directory name (already sanitized)

    if not project_path.exists():
        print(f"Error: Project '{ctx.project}' not found at {project_path}", file=sys.stderr)
        return 1

    worktree_path = project_path / worktree_name
    if not worktree_path.exists():
        print(f"Error: Worktree '{worktree_name}' not found in project '{ctx.project}'", file=sys.stderr)
        return 1

    # Check for modified/untracked files (excluding maelstrom-managed files)
    dirty_files = get_worktree_dirty_files(worktree_path)
    if dirty_files and not args.force:
        print("The following modified/untracked files will be lost:")
        for f in dirty_files:
            print(f"  {f}")
        try:
            response = input("Continue? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if response != "y":
            print("Aborted.")
            return 1

    print(f"Removing worktree '{worktree_name}'...")
    try:
        remove_worktree_by_path(project_path, worktree_name)
        print("Worktree removed successfully.")
        return 0
    except Exception as e:
        print(f"Error removing worktree: {e}", file=sys.stderr)
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List all worktrees."""
    try:
        ctx = resolve_context(
            args.project,
            require_project=True,
            require_worktree=False,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    project_path = ctx.project_path

    if not project_path.exists():
        print(f"Error: Project '{ctx.project}' not found at {project_path}", file=sys.stderr)
        return 1

    worktrees = list_worktrees(project_path)

    if not worktrees:
        print("No worktrees found.")
        return 0

    print(f"Worktrees in {ctx.project}:")
    for wt in worktrees:
        branch_display = wt.branch or "(detached)"
        print(f"  {wt.path.name:30} {branch_display:30} {wt.commit[:8]}")

    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Open a worktree in the configured editor."""
    try:
        ctx = resolve_context(
            args.target,
            require_project=True,
            require_worktree=True,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    worktree_path = ctx.worktree_path

    if not worktree_path.exists():
        print(f"Error: Worktree not found at {worktree_path}", file=sys.stderr)
        return 1

    global_config = load_global_config()
    try:
        open_worktree(worktree_path, global_config.open_command)
        return 0
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _format_size(size_bytes: int) -> str:
    """Format a byte size as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def cmd_read_pr(args: argparse.Namespace) -> int:
    """Read PR status, unresolved comments, and check results."""
    target = getattr(args, "target", None)

    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Determine working directory
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        pr_info = read_pr(cwd=cwd)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Print header
    print(f"PR #{pr_info.number}: {pr_info.title}")
    print(f"URL: {pr_info.url}")

    # If merged, show simple message and exit
    if pr_info.merged:
        print()
        print("PR has been merged - no further action necessary")
        return 0

    print(f"Status: {pr_info.state}")

    # Unresolved comments
    if pr_info.review_threads:
        print()
        print(f"--- Unresolved Comments ({len(pr_info.review_threads)}) ---")
        for thread in pr_info.review_threads:
            line_info = f":{thread.line}" if thread.line else ""
            print(f"  {thread.path}{line_info}")
            for comment in thread.comments:
                # Indent and truncate long comments
                body_preview = comment.body.replace("\n", " ")[:100]
                if len(comment.body) > 100:
                    body_preview += "..."
                print(f"    @{comment.author}: {body_preview}")

    # Checks
    failed_checks = [c for c in pr_info.checks if c.state == "FAILURE"]
    passing_checks = [c for c in pr_info.checks if c.state == "SUCCESS"]
    pending_checks = [c for c in pr_info.checks if c.state not in ("FAILURE", "SUCCESS")]

    if failed_checks:
        print()
        print("--- Failed Checks ---")
        for check in failed_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            print(f"  X {check.name}{run_id_info}")

            # Show truncated logs
            if check.run_id:
                logs = get_check_logs_truncated(cwd, check.run_id, max_lines=30)
                if logs:
                    print()
                    for line in logs.split("\n"):
                        print(f"    {line}")
                    print()
                print(f"    -> Full log: mael check-log [--failed-only] {check.run_id}")

                # Show artifacts for this run
                if check.run_id in pr_info.artifacts:
                    print()
                    print("    Artifacts:")
                    for artifact in pr_info.artifacts[check.run_id]:
                        size_str = _format_size(artifact.size)
                        print(f"      - {artifact.name} ({size_str})")
                        print(f"        -> Download: mael download-artifact {check.run_id} {artifact.name}")

    if pending_checks:
        print()
        print("--- Pending Checks ---")
        for check in pending_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            print(f"  ... {check.name}{run_id_info}")

    if passing_checks:
        print()
        print("--- Passing Checks ---")
        for check in passing_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            print(f"  + {check.name}{run_id_info}")

    return 0


def cmd_download_artifact(args: argparse.Namespace) -> int:
    """Download an artifact from a PR's workflow run."""
    run_id = args.run_id
    artifact_name = args.artifact_name
    output_dir = Path(args.output).expanduser() if args.output else None

    try:
        artifact_path = download_artifact(
            cwd=Path.cwd(),
            run_id=run_id,
            artifact_name=artifact_name,
            output_dir=output_dir,
        )
        print(f"Artifact downloaded to: {artifact_path}")
        return 0
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_check_log(args: argparse.Namespace) -> int:
    """Show full log output for a GitHub Actions run."""
    run_id = args.run_id
    failed_only = getattr(args, "failed_only", False)

    try:
        logs = get_full_check_log(
            cwd=Path.cwd(),
            run_id=run_id,
            failed_only=failed_only,
        )
        print(logs)
        return 0
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="mael",
        description="Maelstrom - Parallel development environment manager",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # add-project command
    add_parser = subparsers.add_parser(
        "add-project",
        help="Clone a git repository for use with maelstrom",
    )
    add_parser.add_argument(
        "git_url",
        help="Git URL to clone (e.g., git@github.com:user/repo.git)",
    )
    add_parser.add_argument(
        "--projects-dir",
        dest="projects_dir",
        help="Base directory for projects (default from ~/.maelstrom.yaml or ~/Projects)",
    )
    add_parser.set_defaults(func=cmd_add_project)

    # create-pr command
    pr_parser = subparsers.add_parser(
        "create-pr",
        help="Create a PR for the current worktree (or push if PR exists)",
    )
    pr_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target worktree as [project.]worktree (default: detect from cwd)",
    )
    pr_parser.add_argument(
        "--draft",
        action="store_true",
        help="Create as draft PR",
    )
    pr_parser.set_defaults(func=cmd_create_pr)

    # add-worktree command (with "add" alias)
    add_wt_parser = subparsers.add_parser(
        "add-worktree",
        aliases=["add"],
        help="Add a new worktree for a branch",
    )
    add_wt_parser.add_argument(
        "branch",
        help="Branch name to create worktree for",
    )
    add_wt_parser.add_argument(
        "-p", "--project",
        dest="project",
        default=None,
        help="Project name (default: detect from cwd)",
    )
    add_wt_parser.add_argument(
        "--no-open",
        dest="no_open",
        action="store_true",
        help="Don't open the worktree after creation",
    )
    add_wt_parser.set_defaults(func=cmd_add_worktree)

    # rm-worktree command (with "rm" alias)
    rm_parser = subparsers.add_parser(
        "rm-worktree",
        aliases=["rm"],
        help="Remove a worktree",
    )
    rm_parser.add_argument(
        "target",
        help="Worktree to remove as [project.]worktree-dir (project defaults from cwd)",
    )
    rm_parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Skip confirmation prompt for modified/untracked files",
    )
    rm_parser.set_defaults(func=cmd_rm_worktree)

    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List all worktrees",
    )
    list_parser.add_argument(
        "project",
        nargs="?",
        default=None,
        help="Project name (default: detect from cwd)",
    )
    list_parser.set_defaults(func=cmd_list)

    # open command
    open_parser = subparsers.add_parser(
        "open",
        help="Open a worktree in the configured editor",
    )
    open_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Worktree to open as [project.]worktree (default: detect from cwd)",
    )
    open_parser.set_defaults(func=cmd_open)

    # read-pr command
    read_pr_parser = subparsers.add_parser(
        "read-pr",
        help="Read PR status, unresolved comments, and check results",
    )
    read_pr_parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target worktree as [project.]worktree (default: detect from cwd)",
    )
    read_pr_parser.set_defaults(func=cmd_read_pr)

    # download-artifact command
    download_parser = subparsers.add_parser(
        "download-artifact",
        help="Download an artifact from a PR's workflow run",
    )
    download_parser.add_argument(
        "run_id",
        help="GitHub Actions run ID",
    )
    download_parser.add_argument(
        "artifact_name",
        help="Name of the artifact to download",
    )
    download_parser.add_argument(
        "-o", "--output",
        dest="output",
        default=None,
        help="Output directory (default: current directory)",
    )
    download_parser.set_defaults(func=cmd_download_artifact)

    # check-log command
    check_log_parser = subparsers.add_parser(
        "check-log",
        help="Show full log output for a GitHub Actions run",
    )
    check_log_parser.add_argument(
        "run_id",
        help="GitHub Actions run ID",
    )
    check_log_parser.add_argument(
        "--failed-only",
        dest="failed_only",
        action="store_true",
        help="Show only failed step logs",
    )
    check_log_parser.set_defaults(func=cmd_check_log)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
