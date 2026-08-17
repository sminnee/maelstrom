"""``mael mv-project`` — rename a project and everything derived from its name.

The IO adapter for :mod:`maelstrom.mv_project`. It gathers the facts the pure
model needs, renders or applies the resulting :class:`~maelstrom.mv_project.MovePlan`,
and is the only layer here that touches the filesystem, git, or the terminal.

A plain ``mv`` of a project directory is not safe. Two failures are silent:
``mael doctor`` prunes port allocations whose worktree folders it can no longer
find, and every task's Claude session id is derived from the project name, so a
rename orphans existing sessions. This command handles the first and warns
loudly about the second.
"""

import json
import os
import subprocess
from pathlib import Path

import click

from . import task as task_model
from .claude_integration import read_json
from .context import get_maelstrom_dir, load_global_config, validate_project_name
from .env import (
    load_env_state,
    load_shared_state,
    stop_env,
    stop_sessions,
    stop_shared_services,
)
from .env_cli import make_store as make_env_store
from .mv_project import DirMove, MovePlan, build_move_plan, rekey_claude_json
from .ports import rename_project_allocations
from .session_discovery import LiveSession, all_live_sessions
from .session_store import read_session_file, sessions_dir
from .task_cli import open_index
from .task_store import GitFileStore
from .util import abbreviate_home, locked_file
from .worktree import (
    find_all_projects,
    list_worktrees,
    run_git,
    setup_claude_memory_symlink,
    update_claude_local_md,
)
from .worktree_model import extract_worktree_name_from_folder, has_claude_transcript


def _claude_json_path(home: Path) -> Path:
    """The path to Claude Code's global settings file."""
    return home / ".claude.json"


def _read_claude_json(home: Path) -> dict:
    """Read ``~/.claude.json``, or ``{}`` if it is missing or unreadable."""
    return read_json(_claude_json_path(home))


def _global_symlinks(home: Path) -> list[tuple[Path, Path]]:
    """Every ``(link, target)`` under ``~/.claude/skills`` and ``commands``.

    These are absolute symlinks into a project's ``shared/`` tree, so renaming
    the project that hosts them leaves them dangling. Targets are read without
    resolving, so a dangling link is still reported.
    """
    pairs: list[tuple[Path, Path]] = []
    for subdir in ("skills", "commands"):
        directory = home / ".claude" / subdir
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            if not entry.is_symlink():
                continue
            target = Path(entry.readlink())
            if not target.is_absolute():
                target = (entry.parent / target).resolve()
            pairs.append((entry, target))
    return pairs


def check_preconditions(
    project_path: Path, new_project_path: Path, *, force: bool
) -> tuple[list[str], list[LiveSession]]:
    """Refuse the rename unless the project is quiet. Changes nothing.

    Moving a directory out from under a running service or a live Claude session
    leaves it writing into a path that no longer exists. Without ``--force`` this
    aborts and lists what is running. With it, the caller passes the result to
    :func:`stop_running_state` — this function never stops anything itself, so
    ``--dry-run`` can run every check and still be inert.

    Returns:
        ``(running worktree names, live sessions)`` for the caller to stop.

    Raises:
        click.ClickException: If the target exists, the source is not a
            maelstrom project, or something is running and ``force`` is False.
    """
    if not project_path.is_dir():
        raise click.ClickException(f"Project not found: {project_path}")
    if not (project_path / ".mael").exists():
        raise click.ClickException(
            f"{project_path} is not a maelstrom project (no .mael marker)"
        )
    if new_project_path.exists():
        raise click.ClickException(
            f"Target already exists: {new_project_path}"
        )

    project = project_path.name
    env_store = make_env_store()

    running_worktrees = [
        name
        for name in _worktree_nato_names(project_path)
        if load_env_state(env_store, project, name) is not None
    ]
    shared_running = load_shared_state(env_store, project) is not None

    live = [
        s
        for s in all_live_sessions()
        if _is_under(s.cwd, project_path)
    ]

    if not force:
        blockers: list[str] = []
        if running_worktrees:
            blockers.append(
                f"running environment(s): {', '.join(sorted(running_worktrees))}"
            )
        if shared_running:
            blockers.append("running shared services")
        if live:
            pids = ", ".join(str(s.pid) for s in live)
            blockers.append(f"live Claude session(s): pid {pids}")
        if blockers:
            raise click.ClickException(
                "Refusing to rename while the project is in use:\n  "
                + "\n  ".join(blockers)
                + "\nStop them, or re-run with --force to stop them automatically."
            )
    return running_worktrees, live


def stop_running_state(
    project_path: Path, running_worktrees: list[str], live: list[LiveSession]
) -> list[str]:
    """Stop the environments and Claude sessions found by the precondition check.

    Split from :func:`check_preconditions` because stopping things is a
    mutation: ``--dry-run`` must be able to report what is running without
    killing it.
    """
    project = project_path.name
    env_store = make_env_store()
    messages: list[str] = []

    for name in sorted(running_worktrees):
        messages.extend(stop_env(env_store, project, name))

    # Re-read afterwards: unsubscribing the last per-worktree env stops the
    # shared services, but a subscriber whose env state went missing (crash,
    # manual cleanup) leaves them running with nothing left to unsubscribe them.
    if load_shared_state(env_store, project) is not None:
        messages.extend(stop_shared_services(env_store, project))

    if live:
        messages.extend(stop_sessions(live))
    return messages


def _is_under(path: Path, root: Path) -> bool:
    """Whether ``path`` is ``root`` or sits beneath it."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _worktree_nato_names(project_path: Path) -> list[str]:
    """NATO names of the project's worktrees, from git's own worktree list."""
    names: list[str] = []
    for wt in list_worktrees(project_path):
        if wt.path == project_path:
            continue
        name = extract_worktree_name_from_folder(project_path.name, wt.path.name)
        if name:
            names.append(name)
    return names


def _worktree_folders(project_path: Path) -> list[str]:
    """Folder names of every real worktree, ``_main`` included.

    Git's worktree list is the source of truth rather than a name pattern, so
    non-conventional worktrees are moved and repaired like any other.
    """
    return [
        wt.path.name
        for wt in list_worktrees(project_path)
        if wt.path != project_path
    ]


def gather_plan(old: str, new: str, projects_dir: Path, home: Path) -> MovePlan:
    """Collect the facts the model needs and build the plan.

    Every read here is inert: nothing is changed, so this is also what
    ``--dry-run`` runs.
    """
    project_path = projects_dir / old
    folders = _worktree_folders(project_path)

    store = GitFileStore()
    tasks = task_model.list_tasks(store, project=old, no_index=True)
    task_ids = [t.id for t in tasks]
    task_statuses = {t.id: t.status for t in tasks}

    # A task has a session worth warning about when its transcript exists in any
    # of the project's worktrees — that is where `mael task run` would resume.
    worktree_paths = [project_path / folder for folder in folders]
    ran: set[str] = set()
    for task in tasks:
        session_id = task_model.session_id_for(old, task.id)
        for wt_path in worktree_paths:
            if has_claude_transcript(wt_path, session_id, home=home):
                ran.add(task.id)
                break

    claude_data = _read_claude_json(home)
    claude_projects = list(claude_data.get("projects", {}))
    if isinstance(claude_data.get("githubRepoPaths"), list):
        claude_projects += [
            p for p in claude_data["githubRepoPaths"] if isinstance(p, str)
        ]

    try:
        return build_move_plan(
            old_name=old,
            new_name=new,
            projects_dir=projects_dir,
            worktree_folders=folders,
            task_ids=task_ids,
            ran_task_ids=ran,
            home=home,
            claude_json_projects=claude_projects,
            global_symlinks=_global_symlinks(home),
            task_statuses=task_statuses,
        )
    except ValueError as e:
        raise click.ClickException(str(e))


def apply_dir_moves(plan: MovePlan) -> None:
    """Move the worktree folders, then the project directory itself.

    Order matters: worktrees are renamed *inside* the old project directory, and
    the project directory moves last. Every intermediate state keeps the project
    findable under its old name.
    """
    done: list[DirMove] = []
    for move in plan.dir_moves:
        if not move.renamed:
            continue
        try:
            move.src.rename(move.dst)
        except OSError as e:
            # Say what already moved. Reporting only this one failure would read
            # as "nothing happened" while renamed worktrees sit with dangling
            # git pointers.
            detail = ""
            if done:
                renamed = ", ".join(f"{m.src.name} -> {m.dst.name}" for m in done)
                detail = (
                    f"\nAlready renamed inside {plan.old_project_path}: {renamed}. "
                    f"Rename them back, or re-run the move and then "
                    f"`mael doctor {plan.new_name}`."
                )
            raise click.ClickException(
                f"Could not move {move.src} -> {move.dst}: {e}{detail}"
            )
        done.append(move)


def repair_git_worktrees(plan: MovePlan) -> None:
    """Point git and its worktrees back at each other after the move.

    ``git worktree repair`` fixes both directions — each worktree's ``.git`` file
    and the matching ``.git/worktrees/<admin>/gitdir`` — and needs neither a
    clean tree nor a remove/re-add, which would destroy per-worktree index,
    reflogs and stashes. Admin directories keep their old names; they are opaque
    handles.
    """
    paths = [
        str(plan.new_project_path / move.dst.name)
        for move in plan.worktree_moves
    ]
    try:
        if paths:
            run_git(["worktree", "repair", *paths], cwd=plan.new_project_path)
        run_git(["worktree", "prune"], cwd=plan.new_project_path)
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"git worktree repair failed: {e}")


def migrate_port_allocations(plan: MovePlan) -> None:
    """Carry the project's port bases across to the new path key.

    Allocations are keyed by absolute project path. Skipping this is the
    destructive trap: the next ``mael doctor`` would find no worktrees under the
    old key and prune every base.
    """
    try:
        rename_project_allocations(plan.old_project_path, plan.new_project_path)
    except ValueError as e:
        raise click.ClickException(str(e))


def migrate_env_state(plan: MovePlan) -> None:
    """Move ``envs/OLD`` to ``envs/NEW``, rewriting the paths inside each file.

    Each state file embeds the project name, the worktree path and the log file
    path, so the directory move alone would leave stale values behind.
    """
    envs_dir = get_maelstrom_dir() / "envs"
    old_dir = envs_dir / plan.old_name
    new_dir = envs_dir / plan.new_name
    if not old_dir.is_dir():
        return
    if new_dir.exists():
        raise click.ClickException(f"Env state already exists at {new_dir}")

    old_dir.rename(new_dir)
    for path in sorted(new_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            # Leaving it silently would strand a state file still naming the old
            # project and old paths, which reads as a live env that cannot work.
            click.echo(f"Warning: could not rewrite {path}: {e}", err=True)
            continue
        if not isinstance(data, dict):
            click.echo(f"Warning: unexpected content in {path}; left as-is", err=True)
            continue
        data = _rewrite_env_paths(data, plan)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _rewrite_env_paths(data: dict, plan: MovePlan) -> dict:
    """Rewrite ``project`` and any embedded path in one env-state document."""
    result = dict(data)
    if result.get("project") == plan.old_name:
        result["project"] = plan.new_name
    for key in ("worktree_path", "log_file"):
        value = result.get(key)
        if isinstance(value, str):
            result[key] = _rewrite_path_string(value, plan)
    services = result.get("services")
    if isinstance(services, list):
        result["services"] = [
            _rewrite_env_paths(s, plan) if isinstance(s, dict) else s
            for s in services
        ]
    return result


def _rewrite_path_string(value: str, plan: MovePlan) -> str:
    """Rewrite a path string that lies under the old project or log directory."""
    old_project = str(plan.old_project_path)
    if value == old_project or value.startswith(old_project + "/"):
        remainder = value[len(old_project):]
        # The worktree folder itself is renamed, so map that component too.
        for move in plan.worktree_moves:
            prefix = f"/{move.src.name}"
            if remainder == prefix or remainder.startswith(prefix + "/"):
                return (
                    str(plan.new_project_path)
                    + f"/{move.dst.name}"
                    + remainder[len(prefix):]
                )
        return str(plan.new_project_path) + remainder

    old_logs = str(get_maelstrom_dir() / "logs" / plan.old_name)
    if value == old_logs or value.startswith(old_logs + "/"):
        new_logs = str(get_maelstrom_dir() / "logs" / plan.new_name)
        return new_logs + value[len(old_logs):]
    return value


def migrate_logs(plan: MovePlan) -> None:
    """Move ``logs/OLD`` to ``logs/NEW``."""
    logs_dir = get_maelstrom_dir() / "logs"
    old_dir = logs_dir / plan.old_name
    new_dir = logs_dir / plan.new_name
    if not old_dir.is_dir():
        return
    if new_dir.exists():
        raise click.ClickException(f"Logs already exist at {new_dir}")
    old_dir.rename(new_dir)


def migrate_tasks(plan: MovePlan) -> int:
    """Re-key every task under the project and rebuild the index.

    Tasks are keyed by project name and also carry it in their frontmatter, so
    each one is read, re-stamped and written under its new key inside a single
    store transaction. The index is a rebuildable cache; it is dropped and
    re-derived, which regenerates the ``session_id`` column for free.
    """
    store = GitFileStore()
    tasks = task_model.list_tasks(store, project=plan.old_name, no_index=True)
    if tasks:
        with store.transaction(
            message=f"mv-project: {plan.old_name} -> {plan.new_name}"
        ):
            for task in tasks:
                old_key = task_model.task_key(plan.old_name, task.status, task.id)
                task.project = plan.new_name
                new_key = task_model.task_key(plan.new_name, task.status, task.id)
                store.write(new_key, task.to_markdown(), message="mv-project")
                store.delete(old_key, message="mv-project")

    # The index lives beside the store; its root may not exist yet on a machine
    # that has never written a task, and SQLite will not create the directory.
    store.root.mkdir(parents=True, exist_ok=True)
    index = open_index(store)
    projects = [
        p.name for p in find_all_projects(load_global_config().projects_dir)
    ]
    task_model.reindex(store, index, projects=projects, head=store.head())
    return len(tasks)


def migrate_claude_dirs(plan: MovePlan) -> int:
    """Move Claude's per-project state directories to their new path slugs.

    Claude keys state off a lossy slug of the absolute path, so the new slug is
    always computed forwards from the new path — an old slug cannot be parsed
    back into a path. Memory symlinks are re-established afterwards so each
    worktree points at the project's central memory directory again.
    """
    moved = 0
    for old_dir, new_dir in plan.claude_dir_moves:
        if not old_dir.is_dir() or old_dir == new_dir:
            continue
        if new_dir.exists():
            continue
        try:
            old_dir.rename(new_dir)
            moved += 1
        except OSError:
            continue

    for move in plan.worktree_moves:
        setup_claude_memory_symlink(
            plan.new_project_path, plan.new_project_path / move.dst.name
        )
    return moved


def migrate_claude_json(plan: MovePlan, home: Path) -> None:
    """Re-key trust and permission entries in ``~/.claude.json``.

    Held under the same advisory lock the rest of maelstrom uses for shared
    dotfiles, so a concurrent writer cannot interleave.
    """
    if not plan.claude_json_rekeys:
        return
    path = _claude_json_path(home)
    if not path.exists():
        return
    try:
        with locked_file(path, create=False, mode=0o600) as txn:
            if not txn.text.strip():
                return
            data = json.loads(txn.text)
            if not isinstance(data, dict):
                return
            updated = rekey_claude_json(data, plan.claude_json_rekeys)
            txn.text = json.dumps(updated, indent=2) + "\n"
    except (OSError, json.JSONDecodeError, TimeoutError) as e:
        click.echo(f"Warning: could not update {path}: {e}", err=True)


def repoint_global_symlinks(plan: MovePlan) -> int:
    """Re-point ``~/.claude`` skill/command symlinks that aimed into the project.

    These are absolute links into the project's ``shared/`` tree, so renaming the
    project that hosts them would otherwise leave every global skill dangling.
    """
    count = 0
    for link, new_target in plan.symlink_repoints:
        # Build the replacement beside the link and move it into place, so a
        # failure can never leave the entry deleted rather than merely stale.
        temp = link.with_name(f".{link.name}.mael-tmp")
        try:
            if temp.is_symlink() or temp.exists():
                temp.unlink()
            temp.symlink_to(new_target)
            os.replace(temp, link)
            count += 1
        except OSError as e:
            click.echo(f"Warning: could not re-point {link}: {e}", err=True)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    return count


def prune_stale_sessions(plan: MovePlan) -> int:
    """Drop session registry entries whose ``cwd`` was under the old path.

    Those sessions were stopped by the precondition check (or were already
    dead); their recorded cwd no longer exists, so the entry is stale.
    """
    sdir = sessions_dir()
    if not sdir.is_dir():
        return 0
    count = 0
    for path in sorted(sdir.glob("*.json")):
        session = read_session_file(path)
        if not session:
            continue
        cwd = session.get("cwd")
        if not isinstance(cwd, str):
            continue
        if _is_under(Path(cwd), plan.old_project_path):
            try:
                path.unlink()
                count += 1
            except OSError:
                continue
    return count


def refresh_worktree_files(plan: MovePlan) -> None:
    """Regenerate each worktree's ``.claude/CLAUDE.local.md``.

    It records the worktree path and app URL, both of which the rename changed.
    """
    for move in plan.worktree_moves:
        worktree_path = plan.new_project_path / move.dst.name
        name = extract_worktree_name_from_folder(plan.new_name, move.dst.name)
        if name is None:
            continue
        try:
            update_claude_local_md(plan.new_project_path, worktree_path, name)
        except OSError:
            continue


def set_git_remote(plan: MovePlan, git_url: str) -> None:
    """Point ``origin`` at ``git_url``."""
    try:
        run_git(
            ["remote", "set-url", "origin", git_url], cwd=plan.new_project_path
        )
    except subprocess.CalledProcessError as e:
        raise click.ClickException(f"Could not set origin URL: {e}")


def _unfinished_message(plan: MovePlan, reason: str) -> str:
    """The error for a failure after the directory has already moved."""
    return (
        f"The project directory moved to {plan.new_project_path}, but the "
        f"migration did not finish: {reason}\n"
        f"Recover with: mael doctor {plan.new_name} && mael task reindex"
    )


def render_plan(plan: MovePlan, home: Path, *, git_url: str | None) -> None:
    """Print the full plan without changing anything."""
    click.echo(
        f"Plan: rename project '{plan.old_name}' -> '{plan.new_name}'"
    )
    click.echo("")

    click.echo(f"Directories ({len(plan.dir_moves)}):")
    width = max((len(abbreviate_home(m.src, home)) for m in plan.dir_moves), default=0)
    for move in plan.dir_moves:
        src = abbreviate_home(move.src, home)
        if move.renamed:
            click.echo(f"  {src:<{width}}  -> {abbreviate_home(move.dst, home)}")
        else:
            click.echo(f"  {src:<{width}}     (name unchanged)")
    click.echo("")

    worktree_count = len(plan.worktree_moves)
    click.echo(f"Git:              worktree repair, {worktree_count} worktrees")
    click.echo(
        f"Port allocations: re-key 1 project entry ({worktree_count} worktrees)"
    )
    click.echo(
        f"Env state:        envs/{plan.old_name} -> envs/{plan.new_name}"
    )
    click.echo(f"Tasks:            re-key {len(plan.task_rekeys)} tasks; rebuild index")
    click.echo(
        f"Claude projects:  {len(plan.claude_dir_moves)} dirs; "
        f"re-point {worktree_count} memory symlinks"
    )
    click.echo(
        f"Claude trust:     re-key {len(plan.claude_json_rekeys)} entries "
        f"in ~/.claude.json"
    )
    click.echo(
        f"Global symlinks:  re-point {len(plan.symlink_repoints)} skills/commands"
    )

    for warning in plan.warnings:
        click.echo("")
        click.echo(f"Warning: {warning}")

    if git_url is None:
        click.echo("")
        click.echo(
            "Not handled: remote.origin.url is left as-is (pass --git-url to "
            "change it)."
        )


@click.command("mv-project")
@click.argument("old")
@click.argument("new")
@click.option("--dry-run", is_flag=True, help="Show the plan without changing anything")
@click.option(
    "-f", "--force", is_flag=True,
    help="Stop running envs and sessions instead of refusing",
)
@click.option("--git-url", default=None, help="Also point origin at this URL")
def cmd_mv_project(
    old: str, new: str, dry_run: bool, force: bool, git_url: str | None
) -> None:
    """Rename a maelstrom project and everything derived from its name.

    OLD and NEW are project names under the configured projects directory. A
    project's name is load-bearing — worktree folders, task and env directories,
    port allocations and Claude Code's state all follow from it — so a plain
    ``mv`` breaks the project silently. This command moves the directory and
    updates each of those.

    Claude session ids are derived from the project name and are *not* migrated.
    Existing sessions are orphaned: ``mael task run`` starts a fresh session
    rather than resuming. The plan says how many are affected.
    """
    try:
        validate_project_name(new)
    except ValueError as e:
        raise click.ClickException(str(e))

    projects_dir = load_global_config().projects_dir
    home = Path.home()
    old_project_path = projects_dir / old
    new_project_path = projects_dir / new

    running_worktrees, live = check_preconditions(
        old_project_path, new_project_path, force=force
    )
    plan = gather_plan(old, new, projects_dir, home)

    # Nothing above this line mutates, so --dry-run is inert even with --force.
    if dry_run:
        render_plan(plan, home, git_url=git_url)
        return

    for message in stop_running_state(old_project_path, running_worktrees, live):
        click.echo(message)

    apply_dir_moves(plan)

    # Past this point the directory has moved, so a failure must say exactly what
    # state the project is in rather than reading as "nothing happened".
    try:
        migrate_port_allocations(plan)
        repair_git_worktrees(plan)
        migrate_env_state(plan)
        migrate_logs(plan)
        task_count = migrate_tasks(plan)
        claude_dirs = migrate_claude_dirs(plan)
        migrate_claude_json(plan, home)
        symlinks = repoint_global_symlinks(plan)
        pruned = prune_stale_sessions(plan)
        refresh_worktree_files(plan)
        if git_url:
            set_git_remote(plan, git_url)
    except click.ClickException as e:
        raise click.ClickException(_unfinished_message(plan, e.format_message()))
    except Exception as e:
        # Past the directory move every failure needs the same message, so this
        # catches broadly on purpose: a bare traceback here would leave the user
        # with a moved project and no idea what state it is in.
        raise click.ClickException(_unfinished_message(plan, str(e)))

    click.echo(f"Renamed '{plan.old_name}' -> '{plan.new_name}'")
    click.echo(f"  Project:         {plan.new_project_path}")
    click.echo(f"  Worktrees:       {len(plan.worktree_moves)} repaired")
    click.echo(f"  Tasks:           {task_count} re-keyed; index rebuilt")
    click.echo(f"  Claude projects: {claude_dirs} dirs moved")
    click.echo(f"  Global symlinks: {symlinks} re-pointed")
    if pruned:
        click.echo(f"  Sessions:        {pruned} stale entries pruned")

    for warning in plan.warnings:
        click.echo("")
        click.echo(f"Warning: {warning}")

    if not git_url:
        click.echo("")
        click.echo(
            "remote.origin.url is unchanged. Pass --git-url to point it "
            "somewhere else."
        )
    click.echo("")
    click.echo(f"Next: mael doctor {plan.new_name}")
