"""Thin CLI for the task notebook: ``mael task ...``.

Each command builds a :class:`~maelstrom.task_store.GitFileStore`, calls a single
model function from :mod:`maelstrom.task`, and renders the result. All logic
lives in the model; this layer only parses arguments and prints.
"""

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import click

from . import task as model  # noqa: F401  (module, used as `model.*`)
# Second binding of the same module, for the few functions that take a `model`
# *parameter* (the `--model` flag / task field) and would otherwise shadow the
# alias above. Same module object — not a re-export.
from . import task as task_model
from . import task_actions
from . import session_discovery
from . import session_store
from .context import resolve_context
from .table import draw_table
from .util import read_content_file
from .task_index import SqliteTaskIndex
from .task_store import GitFileStore
from .cmux.client import ensure_cmux_running
from .shell import run_cmd
from .worktree import (
    get_current_branch,
    list_worktrees,
    setup_worktree_for_branch,
)
from .worktree_launcher import (
    build_task_launch_line,
    launch_claude_in_worktree,
)
from .worktree_model import has_claude_transcript


def _current_branch_or_none() -> str | None:
    """Detect the current git branch, or ``None`` when there's no preference.

    Returns ``None`` outside a git repo (any failure), and treats ``main`` or
    an empty result as "no branch preference" so the command degrades to global
    next-task behavior.
    """
    try:
        branch = get_current_branch(Path.cwd())
    except (subprocess.CalledProcessError, OSError):
        return None
    if not branch or branch == "main":
        return None
    return branch


def _store() -> GitFileStore:
    return GitFileStore()


def open_index(store: GitFileStore) -> "SqliteTaskIndex":
    """Return the SQLite metadata index living alongside ``store`` in its root.

    Ensures the store excludes ``index.db*`` first (idempotent, and a no-op on a
    not-yet-initialised repo), so even a task store that predates the index never
    surfaces the cache as an untracked/staged change. It's a rebuildable cache of
    the ``.md`` tree, never part of the notebook history.

    Public so other CLIs (e.g. ``session list``) open the *same* index the task
    CLI keeps current, without duplicating the ``store.root / "index.db"`` path.
    """
    store.ensure_excludes()
    return SqliteTaskIndex(store.root / "index.db")


def _read_index(store: GitFileStore) -> "tuple[SqliteTaskIndex, str | None]":
    """Return ``(index, head)`` for a read command's model-call fast path.

    ``head`` is the store's current git HEAD; the model serves from the index only
    when the index's own HEAD stamp matches it (else it falls back to a scan), so a
    never-built or stale index degrades safely to the old behaviour.
    """
    return open_index(store), store.head()


def _mutate_index(store: GitFileStore) -> "tuple[SqliteTaskIndex, bool]":
    """Return ``(index, was_fresh)`` for a mutating command.

    Capture whether the index is complete at the current HEAD *before* the
    mutation runs, so :func:`_restamp` can decide afterwards whether advancing the
    stamp is sound (see its docstring).
    """
    index = open_index(store)
    return index, index.head() == store.head()


def _restamp(store: GitFileStore, index: "SqliteTaskIndex", *, was_fresh: bool) -> None:
    """Advance the index HEAD stamp after a mutation — only if it was complete.

    A mutating command upserts/removes the single affected row(s) beside the store
    write, then calls this. We re-stamp to the post-commit HEAD *only* when the
    index was already fresh (complete at the pre-mutation HEAD): an incremental
    single-row update preserves completeness, so the cache stays trustworthy.
    If the index was stale or never built, we leave the stamp behind so reads keep
    scanning until ``task reindex`` rebuilds it — never claiming freshness for a
    partial index.
    """
    if was_fresh:
        index.set_head(store.head())


def _read_content_file(content_file: str | None) -> str:
    """Read the ``--content-file`` argument, converting a missing path to a CLI error.

    The reading itself lives in :func:`maelstrom.util.read_content_file`, shared
    with ``mael wiki update``; this wrapper is the CLI-layer error conversion.
    """
    try:
        return read_content_file(content_file)
    except FileNotFoundError:
        raise click.ClickException(f"Content file not found: {content_file}")


def _run_task(
    store: GitFileStore,
    project: str,
    task: "model.Task",
    *,
    here: bool = False,
    fresh: bool = False,
) -> None:
    """Mark a task in-progress and launch its Claude session.

    With ``here=True`` the session runs in the current shell via
    ``run_cmd(..., replace_process=True)`` — an ``execvp`` that never returns —
    so every store write MUST complete before it, and the status move re-stamps
    HEAD up-front. No worktree reconciliation, no new cmux workspace.

    Otherwise the session is placed **inside cmux** (cmux-or-fail): the task is
    moved in-progress before ``launch_claude_in_worktree`` runs; if placement
    fails (cmux unreachable) it is rolled back to TODO so it never lingers
    in-progress without a session and the next run retries.

    ``fresh=True`` marks a just-created task (load-many head, ``add --run``, a
    scheduled run): it has no prior conversation, so it must always launch with
    ``--session-id`` (create) and never ``--resume`` — even when a stale
    transcript for its deterministic id already sits in a reused worktree. The
    relaunch callers (``task run`` / ``task next``) leave it ``False`` so they
    still resume a previously-stopped session.
    """
    index, was_fresh = _mutate_index(store)
    # Deterministic session id (same task → same id), passed to `claude
    # --session-id` so the live process — and the registry — can be mapped back
    # to this task. Computed up-front because the run-guard keys on it.
    session_id = model.session_id_for(project, task.id)
    # Refuse a second parallel launch *of this task*: a live `claude` whose
    # `--session-id` is this task's own id (see session_discovery). Keying on the
    # session-id, not on worktree occupancy, means a sibling task sharing the
    # worktree (one PR per parent → one branch) can run concurrently and never
    # trips this guard. A *finished* session leaves nothing running, so a finished
    # task stays re-runnable and is deliberately NOT blocked.
    existing = session_discovery.LiveSessionSet().for_session_id(session_id)
    if existing is not None:
        raise click.ClickException(
            f"Task {task.id} already has a live Claude session "
            f"(pid {existing.pid}). Close it before relaunching, or run "
            f"`mael task reconcile` to inspect."
        )

    # Skills running inside the session self-reference via these — e.g. to
    # `mael task done $MAEL_TASK_ID` and `--follow-end linear.<parent>`.
    session_env = {
        "MAEL_TASK_ID": task.id,
        # A parentless task self-parents: children it emits nest under it and
        # share its branch (one PR per chain), instead of each becoming a fresh
        # orphan. A real parent wins over the task.id fallback. A scheduled run is
        # an intended case of this: it is created parentless (its dot-id already
        # names it under its template), so `task.id` becomes MAEL_TASK_PARENT and
        # its follow-ups nest under the run, not the template. See docs/dev/tasks.md.
        "MAEL_TASK_PARENT": task.parent or task.id,
    }
    perm = model._permission_mode_for(task.mode)
    # The prompt is produced lazily by `mael task prompt` inside the launch
    # pipeline, not passed here — keeps the launch command line short.

    if here:
        # No live session exists (the guard above ruled that out), so the only
        # question is whether this task's deterministic session was started before
        # and stopped: an on-disk transcript means `--session-id` would fail with
        # "already exists", so we resume it instead. `--here` runs in the cwd.
        # fresh ⇒ never resume; see docstring.
        resume = (not fresh) and has_claude_transcript(Path.cwd(), session_id)
        task_actions.move_with_actions(
            store, project, task.id, model.STATUS_IN_PROGRESS, index=index
        )  # write BEFORE launch; fires pre_action
        _restamp(store, index, was_fresh=was_fresh)
        suffix = " (resuming)" if resume else ""
        click.echo(f"Running {task.id} here (current shell){suffix}")
        run_cmd(
            build_task_launch_line(
                project, task.id, perm, env=session_env,
                session_id=session_id, resume=resume, model=task.model or None,
            ),
            cwd=None,
            env=session_env,
            replace_process=True,
        )
        return

    ctx = resolve_context(project, require_project=True, arg_is_project=True)
    project_path = ctx.project_path
    if project_path is None or not project_path.exists():
        raise click.ClickException(
            f"Project '{project}' not found at {project_path}"
        )
    branch = task.branch or model.default_branch(task.id, task.parent)
    # The launcher owns install (shell pane on create, blocking in non-cmux).
    result = setup_worktree_for_branch(
        project_path, project, branch, run_install=False
    )
    # Resume a previously-started (now-stopped) session rather than re-creating
    # its id: the worktree the session lives in is the one just set up.
    # fresh ⇒ never resume; see docstring.
    resume = (not fresh) and has_claude_transcript(result.path, session_id)
    task_actions.move_with_actions(
        store, project, task.id, model.STATUS_IN_PROGRESS, index=index
    )  # write BEFORE launch; fires pre_action
    _restamp(store, index, was_fresh=was_fresh)
    suffix = " (resuming)" if resume else ""
    click.echo(f"Running {task.id} on {branch}{suffix}")
    click.echo(f"  → {project}/{result.name} ({result.action})")
    placed = launch_claude_in_worktree(
        result.path,
        project=project,
        worktree=result.name,
        task_id=task.id,
        permission_mode=perm,
        env=session_env,
        session_id=session_id,
        resume=resume,
        model=task.model or None,
    )
    if not placed:
        # cmux couldn't be reached, so no session opened. Roll the task back to
        # TODO — a task that never launched must never be left in-progress. It
        # stays re-runnable and the next hourly scheduler fire retries it. No
        # execvp happens on this path now, so this write always runs. The
        # rollback is best-effort: if the store write itself raises, the run
        # aborts loudly (leaving the task in-progress) rather than silently — an
        # acceptable failure mode, since a raised store error is already fatal.
        task_actions.move_with_actions(
            store, project, task.id, model.STATUS_TODO, index=index
        )
        _restamp(store, index, was_fresh=was_fresh)
        click.echo(
            f"cmux unavailable; left {task.id} TODO (re-fires next run)", err=True
        )


def _resolve_project(project: str | None) -> str:
    """Return the project name, defaulting to the cwd's project."""
    if project:
        return project
    ctx = resolve_context(None, require_project=True)
    assert ctx.project is not None  # require_project guarantees this
    return ctx.project


def _resolve_task_id(id: str | None) -> str:
    """Return the task id from the arg, falling back to ``MAEL_TASK_ID``."""
    task_id = id or os.environ.get("MAEL_TASK_ID")
    if not task_id:
        raise click.ClickException(
            "No task id given and MAEL_TASK_ID is not set."
        )
    return task_id


def _default_parent(parent: str) -> str:
    """Default an unset ``--parent`` to the launching session's parent.

    A session launched by ``mael task run`` exports ``MAEL_TASK_PARENT`` — the
    launching task's parent, or the task's own id when it has none — so a
    parentless planning session still chains its children under one
    parent/branch (one PR per chain) instead of each becoming a fresh orphan.
    For a Linear-rooted task this is the ``linear.<ID>`` parent. Chain tasks a
    skill emits nest under it without spelling it out; an explicit ``parent``
    always wins.
    """
    return parent or os.environ.get("MAEL_TASK_PARENT", "")


@dataclass(frozen=True)
class _Opt:
    """CLI presentation for a block-settable field. Keyed by TASK_FIELDS key.

    ``help`` is the create-time wording (``task add`` / ``linear plan``);
    ``update_help`` is the edit-time wording for ``task update``, which reads as
    "Set the task's …" and, for the clearable fields, documents ``''``. A field
    with no ``update_help`` is not offered by ``task update`` at all.
    """

    help: str
    short: str | None = None
    choices: tuple[str, ...] | None = None
    update_help: str | None = None


# One row per ``block=True`` field in ``model.TASK_FIELDS`` — except ``title``,
# which is block-settable but never a flag (a positional argument on ``task add``,
# hardcoded on ``linear plan``). A field added to ``TASK_FIELDS`` without a row
# here fails the guard test in tests/test_task_cli.py rather than silently going
# missing from every task-creating command.
_BLOCK_OPTIONS: dict[str, _Opt] = {
    "command": _Opt(
        "Command to launch the session with.",
        short="-c",
        update_help="Set the task's command/skill the session launches with.",
    ),
    "mode": _Opt(
        "Session mode (default: plan; 'auto' for an unattended execute session, "
        "'normal' for a non-planning session).",
        short="-m",
        update_help="Set the task's mode (e.g. normal, plan).",
    ),
    "branch": _Opt(
        "Branch for the task (default: task/<id>).",
        short="-b",
        update_help="Set the task's branch.",
    ),
    # `parent` is create-time only: re-parenting an existing task would move its
    # id and branch, which is `task update --id`'s job, not a field edit.
    "parent": _Opt("Parent task id (creates a child id).", short="-P"),
    "pre-action": _Opt(
        "Lifecycle action fired when the task starts (e.g. linear.in-progress).",
        update_help="Set the start lifecycle action (pass '' to clear).",
    ),
    "post-action": _Opt(
        "Lifecycle action fired when the task finishes (e.g. linear.done).",
        update_help="Set the finish lifecycle action (pass '' to clear).",
    ),
    "priority": _Opt(
        "Task priority (default: medium; affects list ordering and `task next`).",
        choices=model.PRIORITIES,
        update_help="Set the task's priority (affects list ordering and `task next`).",
    ),
    "model": _Opt(
        "LLM model for the session, e.g. 'opus' or a full id "
        "(default: your Claude Code default).",
        update_help="Set the task's LLM model, e.g. 'opus' (pass '' to clear).",
    ),
}

# Block-settable fields that are never CLI flags. ``title`` is the task's
# positional argument, not an option.
_NON_OPTION_BLOCK_KEYS = frozenset({"title"})


def _option_specs():
    """Yield the ``(spec, _Opt)`` pairs that become CLI flags, in field order.

    The one place that walks ``TASK_FIELDS`` and enforces that every
    block-settable field has presentation metadata, so both derivations below
    fail loudly on a new field rather than silently omitting it.
    """
    for spec in model.TASK_FIELDS:
        if not spec.block or spec.key in _NON_OPTION_BLOCK_KEYS:
            continue
        opt = _BLOCK_OPTIONS.get(spec.key)
        if opt is None:
            raise RuntimeError(
                f"Block-settable field {spec.key!r} has no _BLOCK_OPTIONS row; "
                "add one (or list it in _NON_OPTION_BLOCK_KEYS) so every "
                "task-creating command exposes it."
            )
        yield spec, opt


def block_task_options(f=None, *, distinguish_unset: bool = False):
    """Apply the click options for every block-settable field, plus
    --follow/--follow-end. Mirrors _BLOCK_KEYS so the CLI vocabulary and the
    load-many block vocabulary cannot drift.

    Derived from ``model.TASK_FIELDS`` (the single field declaration) so a new
    block-settable field appears on every task-creating command at once, with the
    presentation — help text, short flag, choices — coming from ``_BLOCK_OPTIONS``.
    Decorators apply bottom-up, so the list is reversed to keep ``--help`` order
    matching ``TASK_FIELDS`` order.

    ``distinguish_unset=True`` defaults the options to ``None`` instead of ``''``,
    so a command that supplies its own defaults (``mael linear plan``) can tell
    "flag not passed" from an explicit ``--post-action ''`` meaning "clear it".
    Without it an empty value is indistinguishable from unset, and the command's
    default would silently win — see ``cmd_plan``.
    """
    unset = None if distinguish_unset else ""

    def wrap(f):
        return _apply_block_options(f, unset)

    return wrap if f is None else wrap(f)


def _apply_block_options(f, unset: str | None):
    """Attach one click option per block-settable field. See the caller above."""
    decorators = []
    for spec, opt in _option_specs():
        flags = [f"--{spec.key}"]
        if opt.short:
            flags.insert(0, opt.short)
        kwargs: dict = {"help": opt.help}
        if opt.choices is not None:
            # A choice-typed option defaults to None ("unset") rather than "",
            # so click doesn't have to validate an empty default.
            kwargs["type"] = click.Choice(opt.choices)
            kwargs["default"] = None
        else:
            kwargs["default"] = unset
        # Name the dest explicitly: click would derive it from the flag anyway,
        # but stating it keeps kebab→snake mapping visible at this seam.
        decorators.append(click.option(*flags, spec.attr, **kwargs))
    # The follow keys are not task fields (they resolve into `follows` at
    # creation time), so they ride as an explicit addendum — exactly as
    # ``_BLOCK_KEYS`` unions them in.
    decorators.append(
        click.option(
            "--follow", "follows", multiple=True,
            help="Id this task follows (repeatable).",
        )
    )
    decorators.append(
        click.option(
            "--follow-end", "follow_ends", multiple=True,
            help="Follow the end leaves of the given id's follows-chain (repeatable).",
        )
    )
    for decorator in reversed(decorators):
        f = decorator(f)
    return f


def block_task_update_options(f):
    """Apply ``task update``'s field options, derived from the same declaration.

    The edit-time sibling of :func:`block_task_options`. Two things differ from
    the create-time flags, which is why this is a separate derivation rather than
    a shared one:

    - **Unset means "leave alone".** Every option defaults to ``None`` so
      :func:`task.update` can distinguish "not passed" from an explicit ``''``
      that clears the field. The create-time flags default to ``''`` because
      there is nothing to preserve.
    - **Fewer fields.** Only those with an ``update_help`` are offered; ``parent``
      is create-time only (re-parenting is ``--id``'s job).

    Short flags are deliberately *not* carried over: ``task update`` has never had
    them, and ``-b``/``-c`` on an edit command are easy to fire by accident.
    """
    decorators = []
    for spec, opt in _option_specs():
        if opt.update_help is None:
            continue
        kwargs: dict = {"help": opt.update_help, "default": None}
        if opt.choices is not None:
            kwargs["type"] = click.Choice(opt.choices)
        decorators.append(click.option(f"--{spec.key}", spec.attr, **kwargs))
    for decorator in reversed(decorators):
        f = decorator(f)
    return f


@click.group("task")
def task() -> None:
    """Manage the git-backed task notebook."""


@task.command("add")
@click.argument("title", required=False, default=None)
@click.option(
    "-p", "--project", default=None, help="Project name (default: from cwd)."
)
@block_task_options
@click.option(
    "--content-file",
    default=None,
    help="File whose contents become the task's Content section ('-' reads stdin).",
)
@click.option(
    "--from",
    "from_id",
    default=None,
    help="Seed the new task by duplicating this task's recipe; other flags override.",
)
@click.option(
    "--template",
    "is_template",
    is_flag=True,
    help="Park the new task in 'template' status (a reusable, non-actionable recipe).",
)
@click.option(
    "--schedule",
    default=None,
    help="Cron expression (acted on only for template tasks); e.g. '0 9 * * 1-5'.",
)
@click.option(
    "-e",
    "--edit",
    "edit",
    is_flag=True,
    help="Open the new task in $EDITOR after creating it.",
)
@click.option(
    "-r", "--run", is_flag=True, help="Launch the task as a session immediately."
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_add(
    title: str | None,
    project: str | None,
    command: str,
    mode: str,
    # NB: shadows the `task as model` module alias for this function's body — it
    # is the --model flag's value here, and the body never needs the module.
    model: str,
    priority: str | None,
    branch: str,
    parent: str,
    pre_action: str,
    post_action: str,
    follows: tuple[str, ...],
    follow_ends: tuple[str, ...],
    content_file: str | None,
    from_id: str | None,
    is_template: bool,
    schedule: str | None,
    edit: bool,
    run: bool,
    here: bool,
) -> None:
    """Add a new task and print its id."""
    content = _read_content_file(content_file) if content_file is not None else None
    add_task(
        title=title,
        project=project,
        command=command,
        mode=mode,
        model=model,
        priority=priority,
        branch=branch,
        parent=parent,
        pre_action=pre_action,
        post_action=post_action,
        follows=follows,
        follow_ends=follow_ends,
        content=content,
        from_id=from_id,
        is_template=is_template,
        schedule=schedule,
        edit=edit,
        run=run,
        here=here,
    )


def add_task(
    *,
    title: str | None = None,
    project: str | None,
    command: str = "",
    mode: str = "",
    # The task's LLM model (`claude --model`). This name shadows the `task as
    # model` module alias for this body, which therefore reaches the model layer
    # via the `task_model` binding instead.
    model: str = "",
    priority: str | None = None,
    branch: str = "",
    parent: str = "",
    pre_action: str = "",
    post_action: str = "",
    follows: tuple[str, ...] = (),
    follow_ends: tuple[str, ...] = (),
    content: str | None = None,
    from_id: str | None = None,
    is_template: bool = False,
    schedule: str | None = None,
    edit: bool = False,
    run: bool = False,
    here: bool = False,
) -> "task_model.Task":
    """Create a task (and optionally launch it), echoing its id.

    The single create-and-launch path shared by ``mael task add`` and any other
    command that creates a task (e.g. ``mael linear plan``), so there is exactly
    one place that resolves follows, creates the task, and runs it.

    With ``from_id`` the task is seeded by :func:`task.duplicate` from that
    source; the remaining flags override the copied recipe. ``is_template`` parks
    the new task in ``template/`` status and ``schedule`` sets its cron metadata.
    ``content`` of ``None`` means "unspecified" (so a duplicate keeps the
    source's content); pass ``""`` to deliberately blank it.
    """
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    parent = _default_parent(parent)

    if from_id is None and not (title and title.strip()):
        raise click.ClickException("A title is required (or pass --from <task-id>).")

    follow_list = list(follows)
    for end_id in follow_ends:
        follow_list.extend(task_model._resolve_follow_end(store, proj, end_id, parent))
    # De-dupe while preserving first-seen order.
    deduped = list(dict.fromkeys(follow_list))

    status = task_model.STATUS_TEMPLATE if is_template else task_model.STATUS_TODO

    if from_id is not None:
        try:
            new = task_model.duplicate(
                store,
                proj,
                from_id,
                title=title,
                command=command or None,
                mode=mode or None,
                model=model or None,
                priority=priority,
                content=content,
                pre_action=pre_action or None,
                post_action=post_action or None,
                branch=branch,
                parent=parent,
                follows=deduped,
                schedule=schedule or "",
                status=status,
                index=index,
            )
        except KeyError:
            raise click.ClickException(f"Task not found: {from_id}")
    else:
        new = task_model.create(
            store,
            project=proj,
            title=title or "",
            command=command,
            mode=mode,
            model=model,
            priority=priority or task_model.DEFAULT_PRIORITY,
            branch=branch,
            parent=parent,
            pre_action=pre_action,
            post_action=post_action,
            follows=deduped,
            content=content or "",
            schedule=schedule or "",
            status=status,
            index=index,
        )
    click.echo(new.id)
    if edit:
        try:
            task_model.edit_in_editor(store, proj, new.id, index=index)
        except KeyError:
            raise click.ClickException(f"Task not found: {new.id}")
        except RuntimeError as e:
            raise click.ClickException(str(e))
    _restamp(store, index, was_fresh=was_fresh)
    if run:
        _run_task(store, proj, new, here=here, fresh=True)
    return new


@task.command("load-many")
@click.argument("file")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--run",
    is_flag=True,
    help=(
        "Launch every unblocked task created (blocked ones wait for "
        "`mael task next --run`)."
    ),
)
@click.option(
    "--here",
    is_flag=True,
    help=(
        "With --run, launch only the head task in the current shell "
        "(no worktree, no new workspace)."
    ),
)
def task_load_many(file: str, project: str | None, run: bool, here: bool) -> None:
    """Create one or more tasks from a marked plan file ('-' reads stdin)."""
    text = _read_content_file(file)
    try:
        blocks, warnings = model.parse_task_blocks(text)
    except ValueError as e:
        raise click.ClickException(str(e))
    for w in warnings:
        click.echo(f"warning: {w}", err=True)
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    created = model.load_many(
        store, project=proj, blocks=blocks,
        default_parent=_default_parent(""), index=index,
    )
    _restamp(store, index, was_fresh=was_fresh)
    for t in created:
        click.echo(f"{t.id}\t{t.title}")
    if not (run and created):
        return

    if here:
        # `run_cmd(..., replace_process=True)` is an execvp that never returns, so
        # only one task can be launched this way: the head, matching the
        # single-launch behaviour --run had before it went multi. Print BEFORE
        # _run_task — after the execvp nothing here reaches the terminal.
        head = created[0]
        click.echo(f"{head.id} starting in this shell.")
        _run_task(store, proj, head, here=True, fresh=True)
        return

    # Launch every task in the batch that isn't waiting on a follow — the same
    # predicate `task next` and the `task list --all-todo` actionable column use.
    # Blocked siblings stay in todo/ and advance later via `mael task next --run`.
    # `created` order is preserved so the head still launches first.
    #
    # The set is computed once, before any launch: a launch only moves a task to
    # in-progress, never to done, so no blocked task in the batch can *become*
    # actionable partway through the loop.
    head_sha = store.head()
    launch = [
        t
        for t in created
        if model.is_actionable(t, store, index=index, head=head_sha)
    ]
    if not launch:
        return
    # Start the cmux app once for the whole batch; each _run_task still guards
    # liveness individually and rolls its own task back to TODO on a failed
    # placement. Sequential, never parallel: worktree-name and port-base
    # allocation are unlocked, so concurrent launches would race.
    ensure_cmux_running()
    failed = 0
    for t in launch:
        click.echo(
            f"{t.id} started in a separate claude session "
            "— do *not* work on it yourself."
        )
        # One task's launch must not abandon its siblings. ClickException is the
        # duplicate-live-session guard; RuntimeError covers the worktree/port
        # allocators (`allocate_port_base` exhausting the 300-999 range is a
        # genuine batch failure mode — every launch consumes a base). Both are
        # raised before the status move, so the task is still in todo/ and stays
        # re-runnable via `mael task next --run`.
        try:
            _run_task(store, proj, t, here=False, fresh=True)
        except click.ClickException as e:
            failed += 1
            click.echo(f"warning: {t.id} — {e.format_message()}", err=True)
        except RuntimeError as e:
            failed += 1
            click.echo(f"warning: {t.id} — {e}", err=True)
    if failed:
        raise click.ClickException(
            f"{failed} of {len(launch)} tasks failed to launch"
        )


def _scheduled_projects(project: str | None, all_projects: bool) -> list[str]:
    """Resolve the project set ``add-scheduled`` should scan.

    ``--all-projects`` enumerates every maelstrom-managed project (the launchd
    entry point); otherwise it's the single ``-p`` project or the cwd's.
    """
    if all_projects:
        from .context import load_global_config
        from .worktree import find_all_projects

        projects = find_all_projects(load_global_config().projects_dir)
        return [p.name for p in projects]
    return [_resolve_project(project)]


def _fire_due_templates(
    store: GitFileStore, project: str, *, now: datetime, run: bool, here: bool
) -> list["model.Task"]:
    """Create (and optionally launch) one run per due template in ``project``.

    Each fired template, in its own transaction: duplicate it into a date-keyed
    run (skipped if that id already exists → idempotent across RunAtLoad+interval
    double-fires) and advance its ``last-run`` watermark to the boundary. Returns
    the run tasks that were created this call.

    The run's id (``<tmpl>.<date>``) names it as a dot-child of the template, but
    its ``parent`` is deliberately left **empty** so it roots its own chain: the
    launcher exports ``MAEL_TASK_PARENT = run.id`` (via ``task.parent or task.id``),
    making each firing's follow-ups grandchildren of the template rather than
    piling onto the template's own chain. See docs/dev/tasks.md.
    """
    from . import schedule as sched

    index, was_fresh = _mutate_index(store)
    created: list[model.Task] = []
    for tmpl, date in sched.due_templates(store, project, now=now):
        run_id = model.allocate_run_id(tmpl.id, date)
        if model.find_key(store, project, run_id, no_index=True) is not None:
            continue  # already fired this boundary
        prev = sched.previous_fire(tmpl.schedule, now)
        assert prev is not None  # due_templates only yields when a boundary exists
        # Buffer the index inside the store txn (index txn nested within) so a
        # rollback discards both — the duplicate + template watermark update are
        # a single unit.
        with store.transaction(message=f"task: scheduled run {run_id}"):
            with index.transaction():
                new = model.duplicate(
                    store,
                    project,
                    tmpl.id,
                    parent="",  # parentless → run roots its own chain (see docstring)
                    branch=tmpl.branch,
                    id=run_id,
                    index=index,
                )
                model.update(
                    store, project, tmpl.id, last_run=prev.isoformat(), index=index
                )
        created.append(new)
    _restamp(store, index, was_fresh=was_fresh)
    if run and created and not here:
        # Start the cmux app once for the whole batch (N due runs share one app
        # start); each _run_task still guards liveness individually and rolls its
        # own task back to TODO on failure. With no execvp fallback, the launch
        # loop always completes — a failed placement never abandons later runs.
        ensure_cmux_running()
    if run:
        for t in created:
            _run_task(store, project, t, here=here, fresh=True)
    return created


@task.command("add-scheduled")
@click.option("-p", "--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--all-projects",
    "all_projects",
    is_flag=True,
    help="Scan every maelstrom project (the launchd entry point).",
)
@click.option(
    "--run", is_flag=True, help="Launch each due run into a session (cmux workspace)."
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_add_scheduled(
    project: str | None, all_projects: bool, run: bool, here: bool
) -> None:
    """Fire every due template: duplicate it into a dated run and advance its watermark.

    The scheduler entry point invoked by the launchd agent. Thin: it computes the
    due templates and reuses the canonical duplicate/launch path — it owns only
    the cron/last-run/catch-up logic, never creation or launch.
    """
    now = datetime.now().astimezone()
    # Stamp every run so schedule.log records when the agent fired, even when
    # nothing is due — the answer to "did the scheduler run?" at diagnosis time.
    click.echo(f"[{now.isoformat(timespec='seconds')}] add-scheduled")
    store = _store()
    total = 0
    for proj in _scheduled_projects(project, all_projects):
        for t in _fire_due_templates(store, proj, now=now, run=run, here=here):
            click.echo(f"{proj}/{t.id}\t{t.title}")
            total += 1
    if total == 0:
        click.echo("No scheduled tasks due.")


@task.command("list")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--status", default=None, help="Filter by status (folder).")
@click.option("--parent", default=None, help="Filter by parent id.")
@click.option(
    "--all-todo",
    "all_todo",
    is_flag=True,
    help="Also show waiting and parked tasks (incomplete deps, or in blocked/); "
    "still hides done/cancelled.",
)
@click.option(
    "--all",
    "all_",
    is_flag=True,
    help="Show everything, including done and cancelled. Takes precedence over --all-todo.",
)
def task_list(
    project: str | None,
    status: str | None,
    parent: str | None,
    all_todo: bool,
    all_: bool,
) -> None:
    """List actionable tasks (those that can be started now).

    By default only actionable tasks are shown. ``--all-todo`` also includes
    tasks waiting on incomplete ``follows`` deps and tasks parked in ``blocked/``;
    ``--all`` additionally includes done and cancelled. ``--status`` still
    constrains the folder scanned, so e.g. ``--status done`` without ``--all``
    naturally shows nothing.
    """
    proj = _resolve_project(project)
    store = _store()
    index, head = _read_index(store)
    tasks = model.list_tasks(
        store, project=proj, status=status, parent=parent, index=index, head=head
    )
    if not tasks:
        click.echo("No tasks.")
        return

    # An explicit ``--status template`` is a direct window into the template
    # folder: templates are never actionable (so the default view hides them),
    # but asking for the folder by name should list them.
    show_all_in_folder = status == model.STATUS_TEMPLATE

    # Display order is priority-first (id as the within-band tie-break); the
    # gatherer stays id-sorted for dependency resolution, so sort here.
    tasks.sort(key=lambda t: (model.priority_rank(t.priority), t.id))

    rows = []
    for t in tasks:
        actionable = model.is_actionable(t, store, index=index, head=head)
        terminal = model.is_terminal(t.status)
        blocked = not actionable and not terminal
        if all_ or show_all_in_folder:
            visible = True
        elif all_todo:
            visible = actionable or blocked
        else:
            visible = actionable
        if not visible:
            continue
        row = {"ID": t.id, "STATUS": t.status, "PRIORITY": t.priority}
        if all_ or all_todo:
            row["ACTIONABLE"] = "yes" if actionable else "no"
        if show_all_in_folder:
            row["SCHEDULE"] = t.schedule or ""
            row["NEXT-FIRE"] = _next_fire_display(t)
        row["BRANCH"] = t.branch or model.default_branch(t.id, t.parent)
        row["TITLE"] = t.title
        rows.append(row)

    if not rows:
        click.echo("No tasks.")
        return

    if show_all_in_folder:
        columns = ["ID", "STATUS", "PRIORITY", "SCHEDULE", "NEXT-FIRE", "BRANCH", "TITLE"]
    elif all_ or all_todo:
        columns = ["ID", "STATUS", "PRIORITY", "ACTIONABLE", "BRANCH", "TITLE"]
    else:
        columns = ["ID", "STATUS", "PRIORITY", "BRANCH", "TITLE"]
    draw_table(rows, columns)


def _next_fire_display(task: "model.Task") -> str:
    """Render a template's next scheduled fire for the listing, or ''."""
    if not task.schedule:
        return ""
    from . import schedule as sched

    try:
        nxt = sched.next_fire(task.schedule, datetime.now().astimezone())
    except ValueError:
        return "(invalid)"
    return nxt.isoformat(timespec="minutes") if nxt else ""


@task.command("next")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option("--parent", default=None, help="Restrict to children of this id.")
@click.option("--run", is_flag=True, help="Launch the next actionable task as a session.")
@click.option(
    "-b",
    "--branch",
    default=None,
    help="Restrict to this branch (no fallback to other branches).",
)
@click.option(
    "--here",
    is_flag=True,
    help="With --run, launch in the current shell (no worktree, no new workspace).",
)
def task_next(
    project: str | None,
    parent: str | None,
    run: bool,
    branch: str | None,
    here: bool,
) -> None:
    """Print the id of the next actionable task.

    By default, prefers a task on the current git branch and falls back to the
    global next task. With ``--branch``, restricts strictly to that branch (no
    fallback).
    """
    proj = _resolve_project(project)
    store = _store()
    index, head = _read_index(store)
    if branch is not None:
        effective_branch, fallback = branch, False
    else:
        effective_branch, fallback = _current_branch_or_none(), True
    nxt = model.next_task(
        store, proj, parent=parent, branch=effective_branch, fallback=fallback,
        index=index, head=head,
    )
    if nxt is None:
        raise click.ClickException("No actionable task.")
    if run:
        _run_task(store, proj, nxt, here=here)
    else:
        click.echo(nxt.id)


@task.command("run")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--here",
    is_flag=True,
    help="Launch in the current shell (no worktree, no new workspace).",
)
def task_run(id: str, project: str | None, here: bool) -> None:
    """Launch a task as a Claude session (ensures its worktree first)."""
    proj = _resolve_project(project)
    store = _store()
    try:
        t = model.load(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    _run_task(store, proj, t, here=here)


def _live_sessions_by_task(
    store: GitFileStore, project: str
) -> dict[str, "session_discovery.LiveSession"]:
    """Map ``task_id -> live LiveSession`` for every task in ``project``.

    Correlates live ``claude`` processes to the task notebook by *session-id*: a
    running session carries the ``--session-id`` ``mael`` launched it with
    (:attr:`session_discovery.LiveSession.session_id`), so each task matches only
    the session whose id is ``session_id_for(project, task.id)``. This is
    task-precise even when chain siblings share one branch/worktree (one PR per
    parent) — the exact fix the run-guard needed, applied here too, so the two
    stay in lockstep off the same sweep. Tasks with no live session of their own
    are omitted (a sibling's session no longer spuriously attributes to them).
    """
    live = session_discovery.LiveSessionSet()
    if not live.sessions:
        return {}

    mapping: dict[str, session_discovery.LiveSession] = {}
    # Reconcile wants a definitive store view (it pairs with model.reconcile, which
    # also scans); no HEAD is threaded here, so read the .md tree directly.
    for task in model.list_tasks(store, project=project, no_index=True):
        session = live.for_session_id(model.session_id_for(project, task.id))
        if session is not None:
            mapping[task.id] = session
    return mapping


def _ran_task_ids(
    store: GitFileStore, project: str, project_path: Path
) -> set[str]:
    """In-progress task ids whose session left an on-disk transcript (it ran).

    A stale in-progress task (no live session) is either *finished* or *never
    ran*; the two look identical to the live sweep. A transcript file at the
    task's worktree for its deterministic session id means it ran at some point
    (stopped = finished), so reconcile closes it; no transcript means it never
    launched, so reconcile sends it back to todo. We map each in-progress task to
    the worktree hosting its branch (one PR per parent → several tasks may share a
    worktree) and test :func:`has_claude_transcript` there. Tasks whose branch has
    no live worktree are skipped — there is nowhere for a transcript to live, so
    they are treated as never-run.
    """
    # branch → worktree is 1:1 (git allows one checkout per branch), so this
    # dict never loses a distinct worktree to key collision.
    by_branch = {wt.branch: wt.path for wt in list_worktrees(project_path) if wt.branch}
    ran: set[str] = set()
    for task in model.list_tasks(
        store, project=project, status=model.STATUS_IN_PROGRESS, no_index=True
    ):
        branch = task.branch or model.default_branch(task.id, task.parent)
        worktree_path = by_branch.get(branch)
        if worktree_path is None:
            continue
        session_id = model.session_id_for(project, task.id)
        if has_claude_transcript(worktree_path, session_id):
            ran.add(task.id)
    return ran


@task.command("reconcile")
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--fix",
    is_flag=True,
    help="Apply the suggested corrections (default: dry-run table only).",
)
def task_reconcile(project: str | None, fix: bool) -> None:
    """Reconcile in-progress tasks against live Claude sessions.

    Liveness comes from live ``claude`` processes by cwd (via
    ``session_discovery``), the same source ``mael task run``'s duplicate-launch
    guard uses, so the two always agree. Lists the full picture — healthy (OK)
    rows included — and flags mismatch classes: an ``in-progress`` task with no
    live session that *ran* before (a transcript persists → ``done``) versus one
    that *never ran* (no transcript → ``todo``), plus a live session whose task
    isn't ``in-progress`` (→ ``in-progress``). With ``--fix`` the suggested moves
    are applied; without it, nothing changes and a hint is printed if any fix is
    pending.

    Because correlation keys strictly off tasks that still exist, a live
    session whose task was *deleted* mid-run is no longer surfaced as an orphan;
    every existing task's session is still reconciled.
    """
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    session_task_ids = _live_sessions_by_task(store, proj)
    # A stale in-progress task that left a transcript ran (stopped = finished →
    # done); one with no transcript never launched (→ todo). Transcript existence
    # is resolved here (per worktree) and injected so `reconcile` stays pure.
    ctx = resolve_context(proj, require_project=True, arg_is_project=True)
    ran_ids: set[str] = set()
    if ctx.project_path is not None and ctx.project_path.exists():
        ran_ids = _ran_task_ids(store, proj, ctx.project_path)
    rows = model.reconcile(
        store, proj,
        session_task_ids=session_task_ids,
        ran_ids=ran_ids,
    )

    if not rows:
        click.echo("No in-progress tasks or live task sessions.")
        return

    _STATE_LABEL = {
        model.RECONCILE_OK: "OK",
        model.RECONCILE_FINISHED: "FINISHED",
        model.RECONCILE_NEVER_RAN: "NEVER RAN",
        model.RECONCILE_ORPHAN: "NO TASK",
    }
    _FIX_LABEL = {
        model.STATUS_DONE: "→ done",
        model.STATUS_TODO: "→ todo",
        model.STATUS_IN_PROGRESS: "→ in-progress",
    }

    table_rows = []
    for r in rows:
        sess = str(r.session.pid) if r.session is not None else ""
        table_rows.append({
            "STATE": _STATE_LABEL.get(r.state, r.state),
            "TASK": f"{r.task_id} ({r.task_status})",
            "SESSION/PID": sess,
            "SUGGESTED FIX": _FIX_LABEL.get(r.fix_status or "", ""),
        })
    draw_table(
        table_rows, ["STATE", "TASK", "SESSION/PID", "SUGGESTED FIX"]
    )

    fixable = [r for r in rows if r.fix_status is not None]
    if not fix:
        if fixable:
            click.echo(
                f"\n{len(fixable)} task(s) need correcting — re-run with --fix."
            )
        return

    if not fixable:
        click.echo("\nNothing to fix.")
        return

    for r in fixable:
        try:
            task_actions.move_with_actions(
                store, proj, r.task_id, r.fix_status, index=index
            )
        except KeyError:
            click.echo(f"  skipped {r.task_id}: task no longer exists", err=True)
            continue
        click.echo(f"  {r.task_id}: {r.task_status} -> {r.fix_status}")
    _restamp(store, index, was_fresh=was_fresh)


@task.command("show")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_show(id: str, project: str | None) -> None:
    """Show a summary of a task."""
    proj = _resolve_project(project)
    store = _store()
    index, head = _read_index(store)
    try:
        t = model.load(store, proj, id)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(f"id:      {t.id}")
    click.echo(f"title:   {t.title}")
    click.echo(f"status:  {t.status}")
    click.echo(f"project: {t.project}")
    click.echo(f"command: {t.command}")
    click.echo(f"mode:    {t.mode}")
    # Conditional like parent/schedule: empty means "inherit the user's Claude
    # Code default", which is nothing to report.
    if t.model:
        click.echo(f"model:   {t.model}")
    click.echo(f"priority: {t.priority}")
    click.echo(f"branch:  {t.branch}")
    if t.parent:
        click.echo(f"parent:  {t.parent}")
    if t.follows:
        click.echo(f"follows: {', '.join(t.follows)}")
    if t.schedule:
        click.echo(f"schedule: {t.schedule}")
    if t.last_run:
        click.echo(f"last-run: {t.last_run}")
    click.echo(f"created: {t.created}")
    click.echo(f"updated: {t.updated}")
    click.echo(
        f"actionable: {'yes' if model.is_actionable(t, store, index=index, head=head) else 'no'}"
    )
    if t.content:
        click.echo("\n## Content\n")
        click.echo(t.content)


@task.command("get-status")
@click.argument("id", required=False)
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_get_status(id: str | None, project: str | None) -> None:
    """Print a task's status and nothing else.

    Sits outside the "task status" group, whose subcommands are the lifecycle
    moves. The output is the bare status word, so a shell prompt or a script
    can embed it without parsing.
    """
    task_id = _resolve_task_id(id)
    proj = _resolve_project(project)
    store = _store()
    try:
        t = model.load(store, proj, task_id)
    except KeyError:
        raise click.ClickException(f"Task not found: {task_id}")
    click.echo(t.status)


@task.command("current")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_current(project: str | None) -> None:
    """Print the session's task as "ID:STATUS", or nothing.

    Built for a shell prompt or status line, which redraws constantly and has
    nowhere to show an error. So both "no task in this session" and "the task
    has gone away" print an empty line and exit 0, and the caller can use the
    output as-is.
    """
    task_id = os.environ.get("MAEL_TASK_ID")
    status: str | None = None
    if task_id:
        try:
            status = model.load(_store(), _resolve_project(project), task_id).status
        except Exception:
            # Every lookup failure is the same answer here: nothing to show. The
            # catch is broad on purpose — a cwd outside any project, an unsafe
            # id and a missing task all raise differently, and a prompt must
            # render whatever the caller's context.
            status = None
    click.echo(f"{task_id}:{status}" if status is not None else "")


@task.command("read")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_read(id: str, project: str | None) -> None:
    """Print the raw task file."""
    proj = _resolve_project(project)
    store = _store()
    index, head = _read_index(store)
    key = model.find_key(store, proj, id, index=index, head=head)
    if key is None:
        raise click.ClickException(f"Task not found: {id}")
    text = store.read(key)
    if text is None:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(text, nl=False)


@task.command("prompt")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_prompt(id: str, project: str | None) -> None:
    """Print the initial Claude prompt for a task (for ``... | claude``)."""
    proj = _resolve_project(project)
    try:
        task = model.load(_store(), proj, id)  # raises if not found
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    click.echo(model.build_prompt(task), nl=False)


@task.command("log")
@click.argument("id")
@click.argument("msg")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_log(id: str, msg: str, project: str | None) -> None:
    """Append a line to a task's log."""
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    try:
        model.append_log(store, proj, id, msg, index=index)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    _restamp(store, index, was_fresh=was_fresh)
    click.echo(f"Logged to {id}.")


@task.command("update")
@click.argument("id")
@click.argument("title", required=False)
@click.option("--project", default=None, help="Project name (default: from cwd).")
@click.option(
    "--id",
    "new_id",
    default=None,
    help="Re-key the task to this id, rewriting follows/parent references.",
)
@block_task_update_options
@click.option(
    "--schedule",
    default=None,
    help="Set the cron schedule (acted on only for template tasks; '' clears).",
)
@click.option(
    "--content-file",
    default=None,
    help="File whose contents replace the Content section ('-' reads stdin).",
)
def task_update(
    id: str,
    title: str | None,
    project: str | None,
    new_id: str | None,
    branch: str | None,
    command: str | None,
    mode: str | None,
    # Shadows the `task as model` module alias for this body, which therefore
    # reaches the model layer via the `task_model` binding (see the imports).
    model: str | None,
    priority: str | None,
    pre_action: str | None,
    post_action: str | None,
    schedule: str | None,
    content_file: str | None,
) -> None:
    """Update a task's fields (title, branch, command, mode, model, actions, schedule, content).

    With ``--id`` the task is re-keyed first (rewriting follows/parent references
    that point at it), then the remaining field updates apply to the new id.
    """
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    content = _read_content_file(content_file) if content_file is not None else None

    target = id
    renamed = False
    if new_id is not None and new_id != id:
        # Refuse re-keying a running task — its deterministic session_id and its
        # worktree/branch are tied to the old id, so renaming would orphan a live
        # Claude session.
        try:
            t = task_model.load(store, proj, id)
        except KeyError:
            raise click.ClickException(f"Task not found: {id}")
        if t.status == task_model.STATUS_IN_PROGRESS:
            raise click.ClickException(
                f"Cannot change the id of in-progress task {id}; move it back to todo first."
            )
        # Rename intentionally uses the registry check (any *registered*
        # session, not just a live one): re-keying a task out from under a
        # session that recorded the old id — even a stale entry — is unsafe,
        # whereas a relaunch (which uses the stricter is_live discovery) only
        # needs to avoid a genuinely racing process.
        if session_store.find_live_session_for_task(proj, id) is not None:
            raise click.ClickException(
                f"Task {id} has an open Claude session; close it before changing its id."
            )
        try:
            task_model.rename(store, proj, id, new_id, index=index)
        except KeyError:
            raise click.ClickException(f"Task not found: {id}")
        except ValueError as e:
            raise click.ClickException(str(e))
        target = new_id
        renamed = True

    try:
        task_model.update(
            store, proj, target, title=title, branch=branch, content=content,
            command=command, mode=mode, model=model, priority=priority,
            pre_action=pre_action, post_action=post_action,
            schedule=schedule, index=index,
        )
    except KeyError:
        raise click.ClickException(f"Task not found: {target}")
    except ValueError as e:
        raise click.ClickException(str(e))
    _restamp(store, index, was_fresh=was_fresh)
    if renamed:
        click.echo(f"Renamed {id} -> {target}.")
    click.echo(f"Updated {target}.")


@task.command("edit")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_edit(id: str, project: str | None) -> None:
    """Open the task file in $EDITOR (vi); commit if changed."""
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    try:
        _task, changed = model.edit_in_editor(store, proj, id, index=index)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    except RuntimeError as e:
        raise click.ClickException(str(e))
    _restamp(store, index, was_fresh=was_fresh)
    click.echo(f"Updated {id}." if changed else f"No changes to {id}.")


@task.command("rm")
@click.argument("id")
@click.option("--project", default=None, help="Project name (default: from cwd).")
def task_rm(id: str, project: str | None) -> None:
    """Delete a task and strip it from any dependents' follows lists."""
    proj = _resolve_project(project)
    store = _store()
    index, was_fresh = _mutate_index(store)
    try:
        model.delete(store, proj, id, index=index)
    except KeyError:
        raise click.ClickException(f"Task not found: {id}")
    _restamp(store, index, was_fresh=was_fresh)
    click.echo(f"Deleted {id}.")


@task.command("reindex")
def task_reindex() -> None:
    """Rebuild the metadata index from the task notebook across all projects.

    The index (``index.db`` in the task-store root) is a rebuildable cache of the
    ``.md`` tree; this command drops it and re-derives every row, then stamps it to
    the current store HEAD so the fast read paths trust it. Run it after deleting
    ``index.db`` or when a manual/out-of-band edit may have diverged the cache.
    """
    from .context import load_global_config
    from .worktree import find_all_projects

    store = _store()
    index = open_index(store)
    projects = [p.name for p in find_all_projects(load_global_config().projects_dir)]
    count = model.reindex(store, index, projects=projects, head=store.head())
    click.echo(f"Reindexed {count} tasks across {len(projects)} projects.")


@task.group("status")
def task_status() -> None:
    """Move a task between lifecycle states."""


def _status_command(name: str, status: str, help_text: str):
    @task_status.command(name)
    @click.argument("id", required=False)
    @click.option("--project", default=None, help="Project name (default: from cwd).")
    def _cmd(id: str | None, project: str | None) -> None:
        task_id = _resolve_task_id(id)
        proj = _resolve_project(project)
        store = _store()
        index, was_fresh = _mutate_index(store)
        try:
            task_actions.move_with_actions(store, proj, task_id, status, index=index)
        except KeyError:
            raise click.ClickException(f"Task not found: {task_id}")
        _restamp(store, index, was_fresh=was_fresh)
        click.echo(f"{task_id} -> {status}")
        if status == model.STATUS_DONE:
            head = store.head()
            running = model.running_follower(store, proj, task_id, index=index, head=head)
            if running is not None:
                title = f" - {running.title}" if running.title else ""
                click.echo()
                click.echo(
                    f"The following task is already in-progress:\n  {running.id}{title}"
                )
            else:
                nxt = model.next_follower(store, proj, task_id, index=index, head=head)
                if nxt is not None:
                    title = f" - {nxt.title}" if nxt.title else ""
                    click.echo()
                    click.echo(
                        "mael task next --run will run the following task in a new session:"
                    )
                    click.echo(f"  {nxt.id}{title}")

    _cmd.__doc__ = help_text
    return _cmd


_status_command("todo", model.STATUS_TODO, "Move a task back to todo.")
_status_command("start", model.STATUS_IN_PROGRESS, "Move a task to in-progress.")
_status_command("done", model.STATUS_DONE, "Move a task to done.")
_status_command("cancel", model.STATUS_CANCELLED, "Move a task to cancelled.")
_status_command("block", model.STATUS_BLOCKED, "Move a task to blocked.")
_status_command(
    "template", model.STATUS_TEMPLATE, "Park a task as a reusable template."
)
