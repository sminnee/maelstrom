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
    list_worktrees,
    open_worktree,
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

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
