"""Core model for the task notebook.

A task is one row in the ``tasks`` table of the state database, its prose
included: ``content``, ``steps`` and ``log`` are columns beside the frontmatter
fields. Status is a column too, so moving a task is a single-column update
rather than a relocation. Tasks chain via a ``follows`` graph and each carries
the ``command``/``mode`` needed to launch a real Claude session.

This module is the pure model: it never touches git or the filesystem directly,
only the injected table, so it can be exercised against an
:class:`~maelstrom.task_table.InMemoryTaskTable` in tests.

**Async throughout**, because the table underneath is the state database. The
surface is async for reversibility rather than for I/O — see
``docs/dev/architecture-patterns.md``, convention 7. The pure helpers here
(serialisation, id shapes, prompt building) stay sync: they have no table to
reach and no await point to yield at.

The git-committed markdown tree at ``~/.maelstrom/tasks`` is now an *export*,
for audit and backup only. :meth:`Task.to_markdown` and
:meth:`Task.from_markdown` survive as the render and import functions; they are
no longer the storage format.
"""

import os
import re
import subprocess
import tempfile
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import branch_name
from .shell import run_cmd
from .task_store import tasks_root
from .util import now_iso

if TYPE_CHECKING:
    # Only needed for annotations, so keep these type-checking-only and
    # reference them in string form below.
    from .session_discovery import LiveSession

    # The storage layer sits *below* the model and imports ``Task`` from here,
    # so importing it back at runtime would close a cycle. The model only ever
    # names it as an annotation, which is what makes this safe.
    from .task_table import TaskTable


# --- statuses (folder names) ---

STATUS_TODO = "todo"
STATUS_IN_PROGRESS = "in-progress"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
# A task a human has parked by hand. Never actionable, whatever its ``follows``
# say, so it stays out of the default scans. Distinct from a *todo* task waiting
# on its ``follows`` — that is the ordinary blocking mechanism and needs no
# status change.
STATUS_BLOCKED = "blocked"
# A parking folder for tasks you duplicate *from* regularly (templates). Kept
# out of the actionable/WIP scans (next_task/list_tasks default, is_actionable),
# which only consider todo + in-progress, yet trivially listed via
# ``list_tasks(..., status=STATUS_TEMPLATE)``. Optional ``schedule``/``last_run``
# metadata on a template drives the scheduler.
STATUS_TEMPLATE = "template"

VALID_STATUSES = (
    STATUS_TODO,
    STATUS_IN_PROGRESS,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_CANCELLED,
    STATUS_TEMPLATE,
)

DEFAULT_STATUS = STATUS_TODO

# New tasks default to plan mode: a fresh task should start by planning unless
# the caller explicitly passes ``--mode normal``. Applied in ``create()`` when
# ``mode`` is left unset.
DEFAULT_MODE = "plan"

# The model a session runs on when its task names none. Applied at launch, not
# at ``create()``: storing it would pin every task written before the default
# moved, and an empty ``model`` stays the notebook's word for "unset".
DEFAULT_MODEL = "opus"

# Where a planning session writes its drafts. Named here because the draft
# concept lives in this module; `worktree.py` gitignores it and
# `project_scaffold.py` seeds the same entry, and both must stay in step.
DRAFTS_DIR = ".drafts/"

# What lets an agent sculpt a draft without a permission ask per edit. Seeded
# into a new project and merged into an existing worktree's settings.
DRAFT_WRITE_RULES = (f"Write({DRAFTS_DIR}**)", f"Edit({DRAFTS_DIR}**)")

# Task priorities, highest first; the tuple index *is* the sort rank
# (critical=0 … low=3, lower sorts first). A missing/blank priority is treated
# as the default, so existing on-disk tasks keep working with no migration.
PRIORITIES = ("critical", "high", "medium", "low")
DEFAULT_PRIORITY = "medium"


def priority_rank(priority: str) -> int:
    """Return the sort rank of ``priority`` (lower sorts first).

    Unknown/blank values sort as the default rather than raising here —
    validation lives in ``create()``/``update()``; this is only for ordering.
    """
    try:
        return PRIORITIES.index(priority)
    except ValueError:
        return PRIORITIES.index(DEFAULT_PRIORITY)


def validate_priority(priority: str) -> None:
    """Raise ``ValueError`` unless ``priority`` is one of ``PRIORITIES``.

    The strict counterpart to :func:`priority_rank` (which tolerates unknowns
    for ordering); the mutation paths (``create``/``update``) validate here so a
    typo is rejected at write time rather than silently coerced.
    """
    if priority not in PRIORITIES:
        raise ValueError(
            f"Invalid priority: {priority!r} (expected one of {PRIORITIES})."
        )


@dataclass(frozen=True)
class _FieldSpec:
    """One task field, declared once and used to derive the key tables below."""

    key: str  # frontmatter/block key (kebab-case)
    block: bool = False  # settable from a ``---CREATE TASK---`` block

    @property
    def attr(self) -> str:
        """The :class:`Task` attribute this key maps to (kebab → snake)."""
        return self.key.replace("-", "_")


# The single declaration of the task fields. ``FRONTMATTER_KEYS`` (below) emits
# them in this order, so the order is load-bearing for stable file diffs — append
# new fields at the end rather than inserting.
#
# ``block=True`` marks a field a ``load-many`` block may set. Deliberately *not*
# block-settable: ``id``/``project``/``created``/``updated``/``follows`` are
# derived or allocated, never user-set; ``schedule``/``last-run`` only mean
# anything on a ``template/`` task and a block cannot create one (there is no
# ``status`` block key), so accepting them would be inert.
TASK_FIELDS = (
    _FieldSpec("id"),
    _FieldSpec("title", block=True),
    _FieldSpec("project"),
    _FieldSpec("command", block=True),
    _FieldSpec("mode", block=True),
    _FieldSpec("branch", block=True),
    _FieldSpec("parent", block=True),
    _FieldSpec("pre-action", block=True),
    _FieldSpec("post-action", block=True),
    _FieldSpec("follows"),
    _FieldSpec("created"),
    _FieldSpec("updated"),
    # Scheduling metadata. Ordinary task fields (settable/shown like any other),
    # but only *acted on* when the task is a ``template/`` task. Appended at the
    # end so existing files keep a stable diff.
    _FieldSpec("schedule"),
    _FieldSpec("last-run"),
    # Sort priority (critical/high/medium/low). Appended at the end so existing
    # files keep a stable diff; missing key defaults to medium on load.
    _FieldSpec("priority", block=True),
    # LLM model for the session (``claude --model``). Free-form: an alias (opus)
    # or a full id. Empty = launch on ``DEFAULT_MODEL``.
    _FieldSpec("model", block=True),
    # The branch this task's branch is stacked on -- a declarative input that
    # seeds the branch's stored base when the worktree is set up. Empty = use the
    # project's stack tip. Appended at the end so existing files keep a stable
    # diff. Not to be confused with ``parent``: near-identical name, near-opposite
    # meaning. ``parent`` groups tasks onto ONE branch and ONE PR; ``base`` stacks
    # a DIFFERENT branch as its own PR. See ``docs/dev/stacking.md``.
    _FieldSpec("base", block=True),
)

# The frontmatter keys, always emitted in this order for stable diffs. Most
# names match a ``Task`` attr 1:1; the kebab-case lifecycle keys map to the
# snake_case attrs via ``_FRONTMATTER_ATTR`` below.
FRONTMATTER_KEYS = tuple(f.key for f in TASK_FIELDS)

# Frontmatter keys whose name differs from the dataclass attr (kebab vs snake) —
# just the kebab-case ones. Any key absent here uses its own name as the attr.
_FRONTMATTER_ATTR = {f.key: f.attr for f in TASK_FIELDS if f.attr != f.key}


def _today() -> str:
    # Local calendar date: .astimezone() yields an aware datetime in the
    # machine's local zone, so the ID date prefix follows the user's day rather
    # than UTC.
    return datetime.now().astimezone().date().isoformat()


# --- safety / key construction ---


def is_safe_id(id: str) -> bool:
    """Return whether ``id`` is safe to use in a key (no path traversal).

    Only ``[A-Za-z0-9._-]`` are allowed, and the bare ``.``/``..`` forms and any
    ``/`` are rejected. This guards every key construction.
    """
    if not id or id in (".", ".."):
        return False
    return re.fullmatch(r"[A-Za-z0-9._-]+", id) is not None


def task_key(project: str, status: str, id: str) -> str:
    """Build the export path for a task. Raises ``ValueError`` on an unsafe part.

    The markdown export lays tasks out as ``<project>/<status>/<id>.md``, so this
    survives for the exporter and for ``mael mv-project``. It is not how a task
    is looked up: that is :meth:`~maelstrom.task_table.TaskTable.load`.

    All three parts are checked, because all three are joined onto the export
    root and written. ``project`` and ``status`` reach here from stored rows
    rather than from the wire today, so this is the guard holding that true
    rather than one catching a live attack.
    """
    if not is_safe_id(id):
        raise ValueError(f"Unsafe task id: {id!r}")
    if not is_safe_id(project):
        raise ValueError(f"Unsafe project name: {project!r}")
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    return f"{project}/{status}/{id}.md"


# --- Task dataclass ---


@dataclass
class Task:
    """A single task. ``status`` is derived from the key, never serialized."""

    id: str
    title: str
    project: str
    command: str = ""
    mode: str = DEFAULT_MODE
    branch: str = ""
    # Groups this task into a linear PR-sharing chain (one PR per parent); empty
    # = roots its own chain. Dots in `id` express nesting independently — see
    # docs/dev/tasks.md.
    parent: str = ""
    pre_action: str = ""
    post_action: str = ""
    follows: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    # Cron expression (only consulted by the scheduler on ``template/`` tasks).
    schedule: str = ""
    # ISO watermark of the most recent scheduled boundary the scheduler has
    # satisfied; the authoritative "what's due" state for a template.
    last_run: str = ""
    # Sort priority; drives list ordering and ``task next`` selection. Missing
    # from an on-disk file ⇒ medium (see ``from_markdown``).
    priority: str = DEFAULT_PRIORITY
    # LLM model for the launched session (``claude --model``). Free-form
    # passthrough — an alias or a full id; empty launches on ``DEFAULT_MODEL``.
    model: str = ""
    # Branch to stack this task's branch on; empty uses the project's stack tip.
    # Declarative input only — the stored base in git config is the live value.
    base: str = ""
    content: str = ""
    steps: str = ""
    log: str = ""
    status: str = DEFAULT_STATUS

    # --- serialization ---

    def to_markdown(self) -> str:
        """Render the task as markdown with YAML frontmatter.

        All ten frontmatter keys are always emitted (in a fixed order) and the
        three body sections always appear, so files round-trip with stable diffs.
        """
        lines = ["---"]
        for k in FRONTMATTER_KEYS:
            if k == "follows":
                lines.append(f"follows: {_dump_follows(self.follows)}")
            else:
                attr = _FRONTMATTER_ATTR.get(k, k)
                lines.append(f"{k}: {_dump_scalar(getattr(self, attr))}")
        lines.append("---")
        lines.append("")
        lines.append("## Content")
        lines.append("")
        lines.append(self.content.strip())
        lines.append("")
        lines.append("## Steps")
        lines.append("")
        lines.append(self.steps.strip())
        lines.append("")
        lines.append("## Log")
        lines.append("")
        lines.append(self.log.strip())
        # Normalise to a single trailing newline.
        return "\n".join(lines).rstrip("\n") + "\n"

    @classmethod
    def from_markdown(cls, text: str, *, status: str = DEFAULT_STATUS) -> "Task":
        """Parse a task from markdown. ``status`` comes from the folder/key."""
        frontmatter, body = _split_frontmatter(text)
        sections = _split_sections(body)
        return cls(
            id=str(frontmatter.get("id", "")),
            title=str(frontmatter.get("title", "")),
            project=str(frontmatter.get("project", "")),
            command=str(frontmatter.get("command", "")),
            mode=str(frontmatter.get("mode", DEFAULT_MODE)) or DEFAULT_MODE,
            branch=str(frontmatter.get("branch", "")),
            parent=str(frontmatter.get("parent", "")),
            pre_action=str(frontmatter.get("pre-action", "")),
            post_action=str(frontmatter.get("post-action", "")),
            follows=_coerce_follows(frontmatter.get("follows")),
            created=str(frontmatter.get("created", "")),
            updated=str(frontmatter.get("updated", "")),
            schedule=str(frontmatter.get("schedule", "")),
            last_run=str(frontmatter.get("last-run", "")),
            priority=str(frontmatter.get("priority", DEFAULT_PRIORITY))
            or DEFAULT_PRIORITY,
            model=str(frontmatter.get("model", "")),
            base=str(frontmatter.get("base", "")),
            content=sections.get("content", ""),
            steps=sections.get("steps", ""),
            log=sections.get("log", ""),
            status=status,
        )


# --- (de)serialization helpers ---


def _dump_scalar(value: str) -> str:
    """Render a scalar frontmatter value, quoting only when necessary."""
    s = "" if value is None else str(value)
    if s == "":
        return '""'
    # Quote if it could be misparsed as YAML — leading/trailing space, special
    # leading chars, a colon followed by a space, or a scalar YAML would
    # auto-type (timestamps, ints, bools) and thus not round-trip as a string.
    if (
        s != s.strip()
        or s[0] in "[]{}#&*!|>'\"%@`,?-:"
        or ": " in s
        or s.endswith(":")
        or _is_yaml_autotyped(s)
    ):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _is_yaml_autotyped(s: str) -> bool:
    """Return whether YAML would parse ``s`` as a non-string scalar.

    Such values (timestamps, ints, floats, bools, null) must be quoted so they
    round-trip back to the original string when reloaded.
    """
    import yaml

    try:
        loaded = yaml.safe_load(s)
    except yaml.YAMLError:
        # Doesn't parse as a bare scalar at all (e.g. unbalanced brackets); it
        # will be quoted anyway by the special-char checks in _dump_scalar.
        return False
    # A bare string round-trips fine; anything else (datetime, int, float,
    # bool) — or a non-empty string YAML reads as null (e.g. "null", "~") —
    # needs quoting.
    return not isinstance(loaded, str)


def _dump_follows(follows: list[str]) -> str:
    """Render the ``follows`` list as an inline YAML list."""
    if not follows:
        return "[]"
    return "[" + ", ".join(_dump_scalar(f) for f in follows) + "]"


def _coerce_follows(value: object) -> list[str]:
    """Coerce a ``follows`` frontmatter value to a list of ids.

    Accepts an actual list, a single scalar (scalar->list), or an empty/None
    value (-> empty list).
    """
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v) != ""]
    return [str(value)]


def _split_frontmatter(text: str, *, strict: bool = False) -> tuple[dict, str]:
    """Split ``text`` into (frontmatter dict, body). Tolerant of missing fm.

    With ``strict=True``, a YAML *parse* failure re-raises instead of being
    swallowed to ``{}`` — callers that need to surface a precise error (e.g.
    :func:`parse_task_blocks`) opt in. A missing opening/closing fence is "no
    frontmatter present", not a parse failure, and still returns ``({}, text)``
    regardless of ``strict``. The default preserves the tolerant behaviour the
    round-trip :meth:`Task.from_markdown` relies on.
    """
    import yaml

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return ({}, text)
    # Find the closing fence.
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_text = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            try:
                data = yaml.safe_load(fm_text) or {}
            except yaml.YAMLError:
                if strict:
                    raise
                data = {}
            if not isinstance(data, dict):
                data = {}
            return (data, body)
    return ({}, text)


_SECTION_ALIASES = {
    "content": "content",
    "steps": "steps",
    "log": "log",
}


def _split_sections(body: str) -> dict[str, str]:
    """Split a body into the ``content``/``steps``/``log`` sections.

    Splits only on the known top-level ``## Content``/``## Steps``/``## Log``
    headings; any other ``##`` line (e.g. a heading that happens to appear inside
    a section's prose) is kept verbatim as part of the current section.
    """
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current is not None:
            sections[current] = "\n".join(buf).strip()

    for line in body.split("\n"):
        stripped = line.strip()
        heading = None
        if stripped.startswith("## "):
            name = stripped[3:].strip().lower()
            if name in _SECTION_ALIASES:
                heading = _SECTION_ALIASES[name]
        if heading is not None:
            flush()
            current = heading
            buf = []
        else:
            buf.append(line)
    flush()
    return sections


# --- id allocation ---


async def allocate_orphan_id(
    table: "TaskTable", project: str, *, today: str | None = None
) -> str:
    """Allocate a top-level (orphan) id of the form ``YYYY-MM-DD.<n>``.

    ``<n>`` is one more than the highest existing counter for ``today`` across
    all statuses. The dot before the counter keeps every id uniformly
    dot-segmented.
    """
    date = today if today is not None else _today()
    pattern = re.compile(rf"^{re.escape(date)}\.(\d+)$")
    return f"{date}.{await _next_counter(table, project, pattern)}"


async def allocate_child_id(table: "TaskTable", project: str, parent: str) -> str:
    """Allocate a child id of the form ``<parent>.<n>``.

    Only *direct* children are counted (anchored ``^{parent}\\.(\\d+)$``), so
    counters at each nesting level stay independent. A Linear virtual parent
    (e.g. ``linear.NORT-123``) works purely from scanning its children, so the
    first child is ``linear.NORT-123.1``.
    """
    pattern = re.compile(rf"^{re.escape(parent)}\.(\d+)$")
    return f"{parent}.{await _next_counter(table, project, pattern)}"


def allocate_run_id(template_id: str, date: str) -> str:
    """Allocate a date-keyed run id ``<template_id>.<date>`` for a scheduled run.

    Distinct from the numeric :func:`allocate_child_id`: a scheduled run is keyed
    by the boundary date it satisfies (e.g. ``maintenance.2026-06-18``), so the
    id is both meaningful and idempotent — re-firing the same boundary produces
    the same id, which the caller skips if it already exists. Raises
    ``ValueError`` (via :func:`task_key` callers) only when the result is unsafe;
    a date-keyed id is ``is_safe_id``-legal.
    """
    return f"{template_id}.{date}"


async def _next_counter(table: "TaskTable", project: str, pattern: re.Pattern) -> int:
    """Return max matching counter + 1 across all of ``project``'s tasks."""
    highest = 0
    for task in await table.list(project):
        m = pattern.match(task.id)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


# --- follows graph ---


def is_done(status: str) -> bool:
    return status == STATUS_DONE


def is_terminal(status: str) -> bool:
    return status in (STATUS_DONE, STATUS_CANCELLED)


async def is_actionable(task: Task, table: "TaskTable") -> bool:
    """Return whether ``task`` can be started now.

    A task is actionable when it is not terminal, not parked, and every id it
    follows is done. Two statuses park a task. A ``template`` task is a recipe
    to duplicate from, never something to launch directly. A ``blocked`` task is
    one a human has parked by hand. Neither is ever actionable, so both stay out
    of the default ``task list``/``task next`` views.

    Each dependency resolves to a single-row query, which is what killed the
    per-dep full scan that used to dominate this hot path.
    """
    if is_terminal(task.status) or task.status in (STATUS_TEMPLATE, STATUS_BLOCKED):
        return False
    for dep in task.follows:
        found = await table.load(task.project, dep)
        if found is None or not is_done(found.status):
            return False
    return True


async def follow_end_leaves(table: "TaskTable", project: str, id: str) -> list[str]:
    """Return the terminal leaves of the ``follows`` chain starting at ``id``.

    Builds forward adjacency (``x`` follows ``id`` => edge ``id -> x``) from a
    single listing, then BFS-walks forward. Nodes with no outgoing edge are
    leaves. A visited-set guards against cycles. An ``id`` that nothing follows
    yields ``[id]``.
    """
    # Forward adjacency: parent_id -> [ids that follow it]
    forward: dict[str, list[str]] = {}
    for task in await table.list(project):
        for dep in task.follows:
            forward.setdefault(dep, []).append(task.id)

    leaves: set[str] = set()
    visited: set[str] = set()
    queue: deque[str] = deque([id])
    while queue:
        node = queue.popleft()
        if node in visited:
            continue
        visited.add(node)
        children = forward.get(node, [])
        if not children:
            leaves.add(node)
            continue
        for child in children:
            if child not in visited:
                queue.append(child)
    return sorted(leaves)


async def child_chain_leaves(
    table: "TaskTable", project: str, parent: str
) -> list[str]:
    """Return the chain-leaves among ``parent``'s existing children.

    The children of ``parent`` form their own ``follows`` chain; a new child
    appended "to the end" (the ``follow-end: *`` wildcard) should follow the tail
    of that chain — the siblings that **no other sibling follows**. Returns an
    empty list when ``parent`` has no children yet (the new task becomes the
    first child, following nothing).

    Only direct children (``task.parent == parent``) are considered, so a
    grandchild's own sub-chain never leaks into a sibling's leaf set.
    """
    siblings = await list_tasks(table, project=project, parent=parent)
    sibling_ids = {t.id for t in siblings}
    # A sibling is followed-from-within the set if any other sibling follows it.
    followed_within: set[str] = set()
    for t in siblings:
        for dep in t.follows:
            if dep in sibling_ids:
                followed_within.add(dep)
    return sorted(t.id for t in siblings if t.id not in followed_within)


# --- mutations ---


async def create(
    table: "TaskTable",
    *,
    project: str,
    title: str,
    command: str = "",
    mode: str = "",
    model: str = "",
    base: str = "",
    branch: str = "",
    parent: str = "",
    pre_action: str = "",
    post_action: str = "",
    follows: list[str] | None = None,
    content: str = "",
    schedule: str = "",
    last_run: str = "",
    priority: str = "",
    id: str | None = None,
    status: str = DEFAULT_STATUS,
    now: str | None = None,
    today: str | None = None,
) -> Task:
    """Create a new task and write its row (one write).

    ``branch`` defaults to ``task/<id>`` when falsy, so a task always has a
    stable branch and tasks chained from it can derive the same one. A child
    also inherits an existing sibling's branch, or — for the first child — the
    parent task's own branch, before falling back to that derivation ("one PR
    per parent").

    ``id`` overrides id allocation (used by scheduled runs, which key the id by
    boundary date via :func:`allocate_run_id`); when ``None`` an id is allocated
    as a child of ``parent`` or a fresh orphan. ``status`` places the task in a
    folder other than ``todo/`` (e.g. ``template/`` for a parked template).
    """
    timestamp = now if now is not None else now_iso()
    if id is None:
        if parent:
            id = await allocate_child_id(table, project, parent)
        else:
            id = await allocate_orphan_id(table, project, today=today)
    # When mode is left unset, fall back to the global default; an explicit
    # ``mode`` always wins.
    resolved_mode = mode or DEFAULT_MODE
    # Same shape for priority: unset ⇒ default; a supplied value is validated.
    resolved_priority = priority or DEFAULT_PRIORITY
    validate_priority(resolved_priority)
    # One PR per parent: the first task under a parent owns the branch; later
    # siblings and children reuse it rather than generating a fresh (and
    # divergent) name. Precedence: explicit arg > an existing sibling's branch >
    # the parent task's own branch > derived/generated default. Only a
    # branch-owning task pays the cost of generating a descriptive name.
    resolved_branch = (
        branch
        or await _sibling_branch(table, project, parent)
        or await _parent_branch(table, project, parent)
    )
    if not resolved_branch:
        resolved_branch = default_branch(
            id, parent, title=title, content=content, generate=True
        )
    task = Task(
        id=id,
        title=title,
        project=project,
        command=command,
        mode=resolved_mode,
        branch=resolved_branch,
        parent=parent,
        pre_action=pre_action,
        post_action=post_action,
        follows=list(follows or []),
        created=timestamp,
        updated=timestamp,
        schedule=schedule,
        last_run=last_run,
        priority=resolved_priority,
        model=model,
        base=base,
        content=content,
        status=status,
    )
    await table.save(task)
    return task


async def duplicate(
    table: "TaskTable",
    project: str,
    src_id: str,
    *,
    title: str | None = None,
    command: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    content: str | None = None,
    pre_action: str | None = None,
    post_action: str | None = None,
    branch: str = "",
    parent: str = "",
    follows: list[str] | None = None,
    schedule: str = "",
    priority: str | None = None,
    status: str = DEFAULT_STATUS,
    id: str | None = None,
    now: str | None = None,
    today: str | None = None,
) -> Task:
    """Duplicate ``src_id``'s recipe into a fresh task (in one write).

    The model primitive behind ``mael task add --from``. Copies the source's
    title/command/mode/model/content/pre_action/post_action; any non-``None`` override
    wins over the copied default. Source-agnostic — works from any status,
    including ``template/`` — and never mutates the source. ``schedule``/
    ``last_run`` are intentionally *not* copied: ``schedule`` is set only from the
    explicit override (so a run never inherits its template's cron).

    ``branch``/``follows``/``status`` compose the remaining ``add`` flags onto the
    duplicate. For a scheduled run pass ``parent=""`` and
    ``id=allocate_run_id(...)``: the dot-id (``<tmpl>.<date>``) names and dedups the
    run under its template, while the empty ``parent`` roots its own chain so each
    firing's follow-ups nest under the run (see docs/dev/tasks.md). Ad-hoc
    duplicates omit both and get a normal id.
    """
    src = await load(table, project, src_id)
    return await create(
        table,
        project=project,
        title=title if title is not None else src.title,
        command=command if command is not None else src.command,
        mode=mode if mode is not None else src.mode,
        model=model if model is not None else src.model,
        branch=branch,
        pre_action=pre_action if pre_action is not None else src.pre_action,
        post_action=post_action if post_action is not None else src.post_action,
        content=content if content is not None else src.content,
        parent=parent,
        follows=follows,
        schedule=schedule,
        priority=priority if priority is not None else src.priority,
        status=status,
        id=id,
        now=now,
        today=today,
    )


# --- draft files ---


def draft_markdown(
    *,
    title: str,
    command: str = "",
    mode: str = "",
    model: str = "",
    base: str = "",
    branch: str = "",
    parent: str = "",
    pre_action: str = "",
    post_action: str = "",
    priority: str = "",
    content: str = "",
) -> str:
    """Render a draft task file: a task-file-format recipe outside the notebook.

    A draft carries only the recipe fields; the identity fields (``id``,
    ``project``, ``created``, ``updated``, ``follows``) stay empty — they are
    allocated when :func:`parse_draft` + ``create()`` promote it into the table.
    ``mode``/``priority`` fall back to their defaults so the file states what
    the promoted task will actually get.
    """
    resolved_priority = priority or DEFAULT_PRIORITY
    validate_priority(resolved_priority)
    task = Task(
        id="",
        title=title,
        project="",
        command=command,
        mode=mode or DEFAULT_MODE,
        branch=branch,
        parent=parent,
        pre_action=pre_action,
        post_action=post_action,
        priority=resolved_priority,
        model=model,
        base=base,
        content=content,
    )
    return task.to_markdown()


def parse_draft(text: str) -> Task:
    """Parse a draft task file into an unsaved :class:`Task`.

    Stricter than ``from_markdown`` where a silent fallback would swallow a
    user's edit: a YAML error in the frontmatter raises ``ValueError`` instead
    of degrading to an empty recipe, and a missing title raises too (there is
    nothing sensible to promote). Missing keys are still fine — a hand-written
    minimal draft loads with defaults.
    """
    import yaml

    # Pre-flight validation only: the result is discarded, and from_markdown
    # below re-parses tolerantly. Splitting strictly first is what turns a YAML
    # error into a loud failure instead of an empty recipe.
    try:
        _split_frontmatter(text, strict=True)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid draft frontmatter: {e}") from e
    task = Task.from_markdown(text)
    if not task.title.strip():
        raise ValueError("Draft has no title.")
    return task


#: The draft fields a promote may override. Each is a recipe field a caller may
#: state instead of the file: everything ``mael task draft`` writes, and nothing
#: identity holds.
PROMOTABLE_FIELDS = (
    "command",
    "mode",
    "model",
    "base",
    "priority",
    "branch",
    "parent",
    "pre_action",
    "post_action",
)


def read_draft(path: "Path") -> Task:
    """The unsaved task a draft file describes.

    Raises:
        FileNotFoundError: If ``path`` names no file.
        ValueError: If the draft does not parse (see :func:`parse_draft`).
    """
    if not path.is_file():
        raise FileNotFoundError(f"Draft file not found: {path}")
    return parse_draft(path.read_text())


async def promote_draft(
    table: "TaskTable",
    *,
    project: str,
    path: "Path",
    overrides: dict[str, str | None] | None = None,
    follows: list[str] | None = None,
    consume: bool = True,
    draft: Task | None = None,
) -> Task:
    """Create the task a draft file describes, then consume the file.

    The one promote step, shared by ``mael task promote`` and the orchestrator's
    approval of a task-set document. The draft's recipe fields seed the task;
    an ``overrides`` entry that is not ``None`` wins over the file's value, so a
    caller with no opinion passes ``None`` rather than the field's default.
    ``follows`` is wired here, at the moment the ids it names exist.

    The file is deleted only after the task is in the table: a draft that fails
    to parse is left where the user can fix it, and nothing was created.

    ``consume=False`` leaves the file for a caller that promotes a whole set in
    one transaction and calls :func:`consume_draft` after it commits. ``draft``
    reuses a :func:`read_draft` the caller already did.

    Raises:
        FileNotFoundError: If ``path`` names no file.
        ValueError: If the draft does not parse (see :func:`parse_draft`).
    """
    if draft is None:
        draft = read_draft(path)
    given = overrides or {}
    fields = {
        name: value if (value := given.get(name)) is not None else getattr(draft, name)
        for name in PROMOTABLE_FIELDS
    }
    task = await create(
        table,
        project=project,
        title=draft.title,
        content=draft.content,
        follows=list(follows or []),
        **fields,
    )
    if consume:
        consume_draft(path)
    return task


def consume_draft(path: "Path") -> None:
    """Delete a draft whose task now exists.

    A promoted draft left on disk gets promoted a second time, so consuming it
    is part of promoting it — separated only so a caller promoting a set can
    defer every deletion until the whole set is committed.

    ``missing_ok``: a set's deletions run after its transaction commits, so a
    file already gone must not raise and undo a promote that succeeded.
    """
    path.unlink(missing_ok=True)


# --- plan-file (load-many) parsing + batch creation ---

# Frontmatter keys allowed in a `---CREATE TASK---` block. These are
# *task-creation arguments* (mirroring `mael task add`'s flags), not the
# serialized task frontmatter. Anything else is a typo that should fail loudly
# rather than silently drop a dependency.
#
# Derived from the block-settable fields in ``TASK_FIELDS``, plus the two
# ``follow*`` keys: those are creation *arguments* that resolve into the
# ``follows`` field rather than fields in their own right, so they stay an
# explicit addendum.
_BLOCK_KEYS = frozenset(
    {f.key for f in TASK_FIELDS if f.block} | {"follow", "follow-end"}
)

_BAD_WILDCARD_ESCAPE = re.compile(r'"\\(\*)"')  # the "\*" double-quoted-escape case


def _normalise_block_frontmatter(fm_text: str) -> tuple[str, bool]:
    r"""Salvage the known ``\*`` wildcard-escape slip; return (cleaned, changed).

    In a YAML double-quoted scalar ``\*`` is an *invalid escape sequence* and
    fails to parse, but the documented canonical form is the unescaped
    ``follow-end: "*"``. Repair only this one known slip; any other invalid YAML
    is left to error loudly downstream.
    """
    cleaned = _BAD_WILDCARD_ESCAPE.sub(r'"\1"', fm_text)
    return cleaned, cleaned != fm_text


_OPEN_MARKER = re.compile(r"^---CREATE TASK ([A-Za-z0-9._-]+)---$")
_END_MARKER = re.compile(r"^---END TASK ([A-Za-z0-9._-]+)---$")
# A line that *looks like* a marker (so we can reject a malformed one — e.g. a
# name with a space or trailing junk — rather than silently treat it as prose).
_LOOSE_MARKER = re.compile(r"^---(?:CREATE|END) TASK\b.*---$")


def parse_task_blocks(text: str) -> tuple[list[dict], list[str]]:
    """Parse a marked plan file into a list of task-creation blocks.

    A plan file is human-readable preamble (ignored) followed by one or more
    blocks, each opening with ``---CREATE TASK <name>---`` on its own line. A
    block runs until the next open marker, an optional ``---END TASK <name>---``
    close marker, or EOF. ``<name>`` (``[A-Za-z0-9._-]+``) is a local handle for
    intra-file ``follow`` references — not the task id.

    Each block's inner text is split with :func:`_split_frontmatter` into
    ``(frontmatter, body)``: the frontmatter keys are creation arguments and the
    body becomes the task's Content. Returns ``(blocks, warnings)`` where
    ``blocks`` is a list of ``{"name", "args", "content"}`` dicts and
    ``warnings`` is a list of human-readable salvage notes (e.g. a normalised
    ``\\*`` wildcard escape) for the caller to surface.

    Raises ``ValueError`` on: no blocks, a duplicate block name, a block missing
    ``title``, invalid frontmatter YAML (naming the block and the real YAML
    error), an unknown frontmatter key, or a malformed marker line (one that
    resembles a ``CREATE``/``END TASK`` marker but has a bad name or spacing).
    """
    lines = text.split("\n")
    blocks: list[dict] = []
    warnings: list[str] = []
    seen_names: set[str] = set()
    current_name: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if current_name is None:
            return
        # A block body opens straight into frontmatter keys (no leading `---`
        # fence — the `---CREATE TASK` marker already delimited the block), with
        # a `---` separator before the markdown body. Synthesize the opening
        # fence so `_split_frontmatter` parses it the usual way.
        fm_text = "\n".join(buf)
        cleaned, changed = _normalise_block_frontmatter(fm_text)
        if changed:
            warnings.append(
                f"normalised invalid escape \\* -> * in block {current_name!r}"
            )
        import yaml

        try:
            frontmatter, body = _split_frontmatter("---\n" + cleaned, strict=True)
        except yaml.YAMLError as e:
            raise ValueError(f"Block {current_name!r} has invalid frontmatter: {e}")
        unknown = set(frontmatter) - _BLOCK_KEYS
        if unknown:
            raise ValueError(
                f"Unknown key(s) in block {current_name!r}: "
                f"{', '.join(sorted(unknown))}"
            )
        if not str(frontmatter.get("title", "")).strip():
            raise ValueError(f"Block {current_name!r} is missing a title.")
        blocks.append(
            {"name": current_name, "args": frontmatter, "content": body.strip()}
        )

    for line in lines:
        stripped = line.strip()
        open_m = _OPEN_MARKER.match(stripped)
        if open_m:
            flush()
            name = open_m.group(1)
            if name in seen_names:
                raise ValueError(f"Duplicate block name: {name!r}")
            seen_names.add(name)
            current_name = name
            buf = []
            continue
        if _END_MARKER.match(stripped) is not None:
            flush()
            current_name = None
            buf = []
            continue
        # A line that resembles a marker but matched neither strict pattern is a
        # malformed marker (bad name, stray spacing); reject it loudly so a typo
        # isn't silently swallowed as prose/body.
        if _LOOSE_MARKER.match(stripped) is not None:
            raise ValueError(
                f"Malformed task marker: {stripped!r} "
                "(name must match [A-Za-z0-9._-]+)."
            )
        if current_name is not None:
            buf.append(line)
    flush()

    if not blocks:
        raise ValueError("No task blocks found (expected '---CREATE TASK <name>---').")
    return blocks, warnings


async def load_many(
    table: "TaskTable",
    *,
    project: str,
    blocks: list[dict],
    default_parent: str = "",
    now: str | None = None,
    today: str | None = None,
) -> list[Task]:
    """Create every block as a task in one transaction (a single commit).

    A block's ``parent`` defaults to ``default_parent`` (the launching session's
    ``$MAEL_TASK_PARENT``) when its frontmatter omits one.

    Each block's ``follow`` values are resolved against the tasks created earlier
    in this batch (block name -> allocated id) and otherwise passed through as
    real ids. ``follow-end`` values resolve to the live table's chain leaves;
    the wildcard ``*`` resolves to the chain-leaves of the block's *parent's*
    existing children (see :func:`child_chain_leaves`) — "append me to the end of
    my siblings". Each ``create()``'s row is visible to the next iteration's id
    allocation and leaf queries inside the open transaction, so forward-chaining
    within the batch is correct. Returns the created tasks in block order.
    """
    created: dict[str, Task] = {}  # block name -> created Task
    async with table.transact():
        for b in blocks:
            args = b["args"]
            parent = str(args.get("parent", "")) or default_parent
            follows: list[str] = []
            for f in _coerce_follows(args.get("follow")):
                # Intra-file ref wins; otherwise treat as a real id.
                follows.append(created[f].id if f in created else f)
            for end_id in _coerce_follows(args.get("follow-end")):
                follows.extend(
                    await _resolve_follow_end(table, project, end_id, parent)
                )
            deduped = list(dict.fromkeys(follows))
            t = await create(
                table,
                project=project,
                title=str(args["title"]),
                command=str(args.get("command", "")),
                mode=str(args.get("mode", "")),
                model=str(args.get("model", "")),
                priority=str(args.get("priority", "")),
                # An explicit branch: opts the task out of "one PR per parent" —
                # create() gives it precedence over sibling/parent inheritance,
                # so the task gets its own branch and worktree.
                branch=str(args.get("branch", "")),
                parent=parent,
                pre_action=str(args.get("pre-action", "")),
                post_action=str(args.get("post-action", "")),
                follows=deduped,
                content=b["content"],
                now=now,
                today=today,
            )
            created[b["name"]] = t
    return list(created.values())


async def _resolve_follow_end(
    table: "TaskTable", project: str, end_id: str, parent: str
) -> list[str]:
    """Resolve one ``follow-end`` value to a list of ids to follow.

    ``*`` means "the end of my parent's child-chain" — :func:`child_chain_leaves`
    against ``parent`` (empty when there are no siblings yet, or when the task
    has no parent). Any other value is a real task id resolved via
    :func:`follow_end_leaves`.
    """
    if end_id == "*":
        return await child_chain_leaves(table, project, parent) if parent else []
    return await follow_end_leaves(table, project, end_id)


async def load(table: "TaskTable", project: str, id: str) -> Task:
    """Load a task by id. Raises ``KeyError`` if not found.

    One single-row query, prose included: the row carries ``content``, ``steps``
    and ``log``, so there is no second read for the body.
    """
    if not is_safe_id(id):
        raise ValueError(f"Unsafe task id: {id!r}")
    task = await table.load(project, id)
    if task is None:
        raise KeyError(f"Task not found: {project}/{id}")
    return task


async def move(
    table: "TaskTable",
    project: str,
    id: str,
    new_status: str,
    *,
    now: str | None = None,
) -> Task:
    """Move a task to ``new_status``, bumping ``updated``.

    Status is a column, so this is a single-row update rather than the
    write-new + delete-old pair the folder layout forced.
    """
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status!r}")
    task = await load(table, project, id)
    if task.status == new_status:
        return task
    task.status = new_status
    task.updated = now if now is not None else now_iso()
    await table.save(task)
    return task


async def append_log(
    table: "TaskTable",
    project: str,
    id: str,
    msg: str,
    *,
    now: str | None = None,
) -> Task:
    """Append a timestamped line to a task's log section (one write)."""
    task = await load(table, project, id)
    timestamp = now if now is not None else now_iso()
    entry = f"- {timestamp} {msg}"
    task.log = f"{task.log}\n{entry}".strip() if task.log else entry
    task.updated = timestamp
    await table.save(task)
    return task


async def update(
    table: "TaskTable",
    project: str,
    id: str,
    *,
    title: str | None = None,
    branch: str | None = None,
    content: str | None = None,
    command: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    base: str | None = None,
    pre_action: str | None = None,
    post_action: str | None = None,
    schedule: str | None = None,
    last_run: str | None = None,
    priority: str | None = None,
    follows: list[str] | None = None,
    now: str | None = None,
) -> Task:
    """Update provided fields in place (one write, bumps ``updated``).

    Status is intentionally not touched here (use ``move`` for lifecycle
    transitions). Only fields passed non-``None`` are changed, so an omitted
    argument leaves that field as-is.
    """
    task = await load(table, project, id)
    if title is not None:
        task.title = title
    if branch is not None:
        task.branch = branch
    if content is not None:
        task.content = content
    if command is not None:
        task.command = command
    if mode is not None:
        task.mode = mode
    if model is not None:
        task.model = model
    if base is not None:
        task.base = base
    if pre_action is not None:
        task.pre_action = pre_action
    if post_action is not None:
        task.post_action = post_action
    if schedule is not None:
        task.schedule = schedule
    if last_run is not None:
        task.last_run = last_run
    if priority is not None:
        validate_priority(priority)
        task.priority = priority
    if follows is not None:
        task.follows = follows
    task.updated = now if now is not None else now_iso()
    await table.save(task)
    return task


async def edit_in_editor(
    table: "TaskTable",
    project: str,
    id: str,
    *,
    editor: str | None = None,
) -> tuple[Task, bool]:
    """Open the task in ``$EDITOR``/vi; write it back only if it changed.

    Returns ``(task, changed)``. A no-op save (open + quit, no edits) writes
    nothing. The row is rendered to a temporary file for the editor and
    re-parsed afterwards, so the round trip is the markdown a user expects while
    the notebook itself stays a table. On a real change ``updated`` bumps and
    the whole row is written back.

    The temp file is the one place the model still touches the filesystem, and
    it is the same sanctioned exception the ``$EDITOR`` launch always was — see
    ``docs/dev/architecture-patterns.md``, convention 2.
    """
    task = await load(table, project, id)
    before = task.to_markdown()
    ed = editor or os.environ.get("EDITOR") or "vi"
    # Named for the task, so an editor showing its filename shows something
    # meaningful, and suffixed .md so syntax highlighting works.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{id}.md"
        path.write_text(before)
        # Routed through ``run_cmd`` in the ``shell.py`` leaf (stdlib-only, imports
        # nothing from maelstrom), so there is no storage/model/CLI layering concern.
        # ``stream=True`` is required so the editor inherits the terminal's
        # stdout/stderr; without it ``run_cmd`` captures the child's output into pipes
        # (``capture_output=True``) and a full-screen editor like ``vi`` can't draw its
        # screen, leaving it unusable. ``stream=True`` is the fork-and-wait equivalent
        # of the original bare ``subprocess.run`` — same terminal inheritance, control
        # returns here afterwards so the post-edit save logic below still runs. The only
        # other change versus that bare call is a benign ``$ <editor> <path>`` echo
        # before the editor opens; ``check=True`` is the default, so the
        # ``CalledProcessError`` wrapping below still applies.
        try:
            run_cmd([ed, str(path)], stream=True)
        except FileNotFoundError:
            raise RuntimeError(f"Editor not found: {ed}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Editor exited with status {e.returncode}: {ed}")
        after = path.read_text()
    if after == before:
        return Task.from_markdown(after, status=task.status), False
    edited = Task.from_markdown(after, status=task.status)
    # Identity is the table's, not the file's: an edit that rewrote `id` or
    # `project` would otherwise re-key the row and orphan the original.
    edited.id = task.id
    edited.project = task.project
    edited.updated = now_iso()
    await table.save(edited)
    return edited, True


async def delete(table: "TaskTable", project: str, id: str) -> Task:
    """Delete a task and strip it from every dependent's ``follows`` list.

    Removes the row, then rewrites every non-terminal task that ``follows``
    ``id`` without it. Terminal tasks (done/cancelled) are left untouched —
    they're historical and their ``follows`` no longer gates anything. Returns
    the deleted task.

    One transaction covers the removal and every dependent rewrite, so a failure
    part-way leaves the task and its dependents exactly as they were.
    """
    deleted = await load(table, project, id)
    async with table.transact():
        await table.delete(project, id)
        for dep in await list_tasks(table, project=project):
            if dep.id == id or is_terminal(dep.status) or id not in dep.follows:
                continue
            dep.follows = [f for f in dep.follows if f != id]
            await table.save(dep)
    return deleted


async def rename(
    table: "TaskTable",
    project: str,
    old_id: str,
    new_id: str,
    *,
    now: str | None = None,
) -> Task:
    """Re-key a task and fix every reference that points at it.

    Writes the task under its new id (preserving status/content/log, bumping
    ``updated``), then in the same transaction rewrites non-terminal dependents'
    ``follows`` (old->new) and non-terminal direct children's ``parent``
    (old->new). Terminal tasks (done/cancelled) are left untouched, mirroring
    :func:`delete`. Children's own ids are NOT cascaded.

    Raises ``KeyError`` (task not found), ``ValueError`` (unsafe ``new_id`` or
    ``new_id`` already taken). Returns the renamed task unchanged when
    ``new_id == old_id``.
    """
    task = await load(table, project, old_id)
    if not is_safe_id(new_id):
        raise ValueError(f"Unsafe task id: {new_id!r}")
    if new_id == old_id:
        return task
    if await table.load(project, new_id) is not None:
        raise ValueError(f"Task already exists: {project}/{new_id}")

    task.id = new_id
    task.updated = now if now is not None else now_iso()

    # One transaction for the re-key plus every dependent rewrite, so a failure
    # part-way cannot leave a dangling reference to an id that no longer exists.
    async with table.transact():
        await table.delete(project, old_id)
        await table.save(task)

        # Fix cross-references in non-terminal tasks: rewrite follows (old->new)
        # and re-parent direct children (old->new). The renamed task itself
        # never references its old id, so skip it.
        for dep in await list_tasks(table, project=project):
            if dep.id == new_id or is_terminal(dep.status):
                continue
            changed = False
            if old_id in dep.follows:
                dep.follows = [new_id if f == old_id else f for f in dep.follows]
                changed = True
            if dep.parent == old_id:
                dep.parent = new_id
                changed = True
            if changed:
                await table.save(dep)
    return task


async def list_tasks(
    table: "TaskTable",
    *,
    project: str,
    status: str | None = None,
    parent: str | None = None,
) -> list[Task]:
    """List tasks under ``project``, optionally filtered by status and parent.

    One indexed query, id-sorted. Every task comes back whole — the row carries
    the prose — so there is no metadata-only variant to fall back from.
    """
    return await table.list(project, status=status, parent=parent)


# --- session launch helpers (pure) ---


# Linear issue identifiers look like NORT-123, ABC-7, TEAM2-99.
_LINEAR_PARENT_RE = re.compile(r"^linear\.([A-Z][A-Z0-9]*-\d+)$")


async def _sibling_branch(table: "TaskTable", project: str, parent: str) -> str:
    """Return an existing sibling's branch under ``parent``, or ``""``.

    Enforces "one PR per parent": once any task under ``parent`` has a branch,
    later siblings reuse it instead of generating a fresh, divergent name. With
    no ``parent`` (orphan task) or no existing sibling, returns ``""`` so the
    caller falls back to :func:`default_branch`.

    This covers only *second-and-later* children (there is an existing sibling
    to copy from); :func:`_parent_branch` handles the first child by looking up
    the parent task itself.
    """
    if not parent:
        return ""
    for sibling in await list_tasks(table, project=project, parent=parent):
        if sibling.branch:
            return sibling.branch
    return ""


async def _parent_branch(table: "TaskTable", project: str, parent: str) -> str:
    """Return the parent task's own branch, or ``""``.

    Completes "one PR per parent": the *first* child of a parent that already
    owns a real branch must inherit it, not regenerate a divergent name.
    :func:`_sibling_branch` only handles second-and-later children (off an
    existing sibling); this handles the first by looking up the parent task
    itself by id.

    ``parent`` may be *virtual* — a Linear id like ``linear.NORT-123`` with no
    task file, or absent. When no task with id ``parent`` exists, or it exists
    with an empty branch, returns ``""`` so the caller falls back to
    :func:`default_branch` derivation.
    """
    if not parent:
        return ""
    try:
        parent_task = await load(table, project, parent)
    except KeyError:
        return ""
    return parent_task.branch


def default_branch(
    id: str,
    parent: str = "",
    *,
    title: str = "",
    content: str = "",
    generate: bool = False,
) -> str:
    """Return the default branch name for a task.

    The branch derives from the *parent* when present, so all children of one
    parent share a branch (one PR per parent). When ``generate`` is set and a
    ``title`` is supplied, the branch-owning cases (orphan, or first task under a
    Linear parent) get a descriptive ``<type>/<desc>`` name generated from the
    title/content; otherwise the cheap deterministic shapes are used:

    - ``linear.NORT-123`` (Linear parent), ``generate`` + title → e.g.
      ``fix/123-flaky-port-test`` (bare issue number leads the desc)
    - ``linear.NORT-123``, no generation                        → ``feat/123``
    - any other parent (e.g. ``2026-06-09.3``)                  → ``task/2026-06-09.3``
    - no parent, ``generate`` + title                           → e.g. ``fix/flaky-port-test``
    - no parent, no generation                                  → ``task/<id>``

    ``generate`` is opt-in so call sites that don't have a meaningful title
    (e.g. running an already-persisted task) keep the cheap deterministic path
    and never invoke the model. Only the immediate parent is resolved (no
    ancestor-chain walk).
    """
    if parent:
        m = _LINEAR_PARENT_RE.match(parent)
        if m:
            number = m.group(1).split("-")[-1]  # "NORT-123" -> "123"
            if generate and title:
                return branch_name.generate_branch_name(title, content, prefix=number)
            return f"feat/{number}"
        # Child of a non-Linear parent: keep sharing the parent's branch.
        return f"task/{parent}"
    if generate and title:
        return branch_name.generate_branch_name(title, content)
    return f"task/{id}"


# Portable placeholder for the task's per-project repo dir, written into stored
# task content (e.g. by `mael linear plan` for localized image paths) and
# expanded to a concrete absolute path at prompt-build time. Kept machine-
# independent in the `.md` so a re-clone on a different home dir still resolves.
# Shared with the writer (integrations/linear.py) — keep the two in sync.
MAEL_TASK_DIR_TOKEN = "{{MAEL_TASK_DIR}}"


def _expand_task_dir(task: Task) -> str:
    """Expand ``{{MAEL_TASK_DIR}}`` in the task content to its absolute path.

    The token resolves to the task's per-project repo root
    (``~/.maelstrom/tasks/<project>``). Pure string substitution — no filesystem
    access — so committed content stays portable while a launched session sees a
    concrete path it can ``Read``.
    """
    task_dir = str(tasks_root() / task.project)
    return task.content.replace(MAEL_TASK_DIR_TOKEN, task_dir)


def build_prompt(task: Task) -> str:
    """Build the initial Claude prompt for a task.

    The shape is ``/<command> <title>`` followed by a blank line and the task's
    content. ``command`` names a Claude skill/slash-command, so it is prefixed
    with ``/`` to invoke it, and may carry arguments (``impeccable shape``). The
    leading ``/<command> `` is omitted when ``command`` is empty (a plain
    execute), and the trailing ``\\n\\n<content>`` is omitted when the task has
    no content. Any ``{{MAEL_TASK_DIR}}`` token in the content is expanded to
    the task's absolute repo dir.
    """
    head = f"/{task.command} {task.title}" if task.command else task.title
    content = _expand_task_dir(task).strip()
    if content:
        return f"{head}\n\n{content}"
    return head


def mode_for_command(command: str) -> str:
    """The ``mode`` a task takes from the skill it runs.

    An execute task (empty ``command``) runs its plan unattended in ``"auto"``.
    A planning skill runs in ``"normal"``: the skill itself forbids code edits,
    so plan mode adds only a review prompt for an operator who is already there.
    ``mael linear plan`` seeds a ``plan-task`` this way today. Anything else is
    an unknown command, which keeps :data:`DEFAULT_MODE`.

    Only a starting point — the mode stays editable wherever it is offered.
    """
    if not command:
        return "auto"
    if command in ("plan-task", "plan-next-step"):
        return "normal"
    return DEFAULT_MODE


def permission_mode_for(mode: str) -> str | None:
    """Map a task ``mode`` to Claude's ``--permission-mode`` value.

    ``"plan"`` → ``"plan"``; ``"auto"`` → ``"auto"`` (Claude's classifier-vetted
    unattended mode); anything else uses Claude's default (None, no flag).
    """
    return mode if mode in {"plan", "auto"} else None


# Fixed namespace UUID for deriving per-task session ids. Generated once and
# frozen here so the mapping (project, task-id) → session-id is stable across
# machines and over time; changing it would orphan every existing session.
_SESSION_NS = uuid.UUID("5b970d0a-51ab-49ae-ba93-0f7b0f615908")


def session_id_for(project: str, task_id: str) -> str:
    """Stable Claude ``--session-id`` for a task (same task → same id).

    Deterministic uuid5 over ``project`` and ``task_id`` (NUL-separated so no
    pair of distinct ids can collide by concatenation). This is the
    first-class link between a task and its session: ``mael task run`` passes
    it to ``claude --session-id``, the session channel records it, and
    ``reconcile`` matches a live session back to its task by recomputing it.
    """
    return str(uuid.uuid5(_SESSION_NS, f"{project}\x00{task_id}"))


# Reconcile classifications. Each in-progress task / live session is sorted into
# exactly one of these states (see :func:`reconcile`).
RECONCILE_OK = "ok"  # in-progress task with a live session — healthy, no fix
# An in-progress task with no live session splits by whether it ever ran (a
# transcript persists on disk). A stopped session just means *finished* — there
# is no on-disk distinction between a completed and an aborted session — so a
# task that ran is closed. A task with no transcript never actually launched, so
# its in-progress status is bogus and it is sent back to todo to be run.
RECONCILE_FINISHED = "finished"  # in-progress task, ran before, now stopped → done
RECONCILE_NEVER_RAN = "never-ran"  # in-progress task, no transcript ever → todo
RECONCILE_ORPHAN = "orphan-session"  # live session, task not in-progress → start


@dataclass
class ReconcileRow:
    """One reconcile finding: a task/session pair and its suggested correction.

    ``state`` is one of the ``RECONCILE_*`` constants. ``fix_status`` is the
    status the task should move to (``None`` for OK rows — nothing to do).
    ``session`` is the matched live :class:`~maelstrom.session_discovery.LiveSession`,
    or ``None`` for a task that has no live session (finished or never-ran).
    """

    state: str
    task_id: str
    task_status: str
    session: "LiveSession | None"
    fix_status: str | None


async def reconcile(
    table: "TaskTable",
    project: str,
    *,
    session_task_ids: dict[str, "LiveSession"],
    ran_ids: set[str] | None = None,
) -> list[ReconcileRow]:
    """Classify in-progress tasks and live sessions into reconcile rows.

    Pure: the caller supplies ``session_task_ids`` — a map from task id to the
    live :class:`~maelstrom.session_discovery.LiveSession` that owns it (built
    from live-process discovery in the CLI layer) — and this function reads only
    the injected table. It never moves tasks; ``--fix`` application is the
    caller's job, driven off ``fix_status``.

    Four states (see the ``RECONCILE_*`` constants):

    - **OK**: an ``in-progress`` task that has a live session. No fix.
    - **finished**: an ``in-progress`` task with no live session whose id is in
      ``ran_ids`` — it ran at some point (a transcript persists) and has stopped.
      A stopped session just means *finished* (no on-disk completed-vs-aborted
      distinction), so the suggested fix → ``done``.
    - **never-ran**: an ``in-progress`` task with no live session and no
      transcript — it never actually launched, so the in-progress status is bogus.
      Suggested fix → ``todo`` (send it back to be run).
    - **orphan-session**: a live session whose task is *not* ``in-progress``
      (todo/blocked/etc.). Suggested fix → ``in-progress``. A session whose
      task is already terminal (done/cancelled) or missing is reported with no
      fix — a finished task whose window lingers is not a corruption to flip.

    ``ran_ids`` is built in the CLI layer (transcript existence per in-progress
    task's worktree/session), mirroring how ``session_task_ids`` is injected —
    keeping this function pure and side-effect free.

    Rows are returned id-sorted for stable rendering.
    """
    ran_ids = ran_ids or set()
    rows: list[ReconcileRow] = []
    in_progress = await list_tasks(table, project=project, status=STATUS_IN_PROGRESS)
    in_progress_ids = {t.id for t in in_progress}

    for task in in_progress:
        session = session_task_ids.get(task.id)
        if session is not None:
            rows.append(
                ReconcileRow(
                    state=RECONCILE_OK,
                    task_id=task.id,
                    task_status=task.status,
                    session=session,
                    fix_status=None,
                )
            )
        else:
            # A stopped session just means finished; a task with no transcript
            # never ran. Close the former, send the latter back to todo.
            ran = task.id in ran_ids
            rows.append(
                ReconcileRow(
                    state=RECONCILE_FINISHED if ran else RECONCILE_NEVER_RAN,
                    task_id=task.id,
                    task_status=task.status,
                    session=None,
                    fix_status=STATUS_DONE if ran else STATUS_TODO,
                )
            )

    for task_id, session in session_task_ids.items():
        if task_id in in_progress_ids:
            continue  # already an OK row above
        found = await table.load(project, task_id)
        status = found.status if found is not None else None
        # Only flip a non-terminal task (todo/blocked) into in-progress. A
        # terminal or missing task with a lingering session is listed, not
        # auto-corrected. NB: the current CLI caller
        # (task_cli._live_sessions_by_task) keys strictly off tasks that still
        # exist, so ``status is None`` (a deleted task with a live session)
        # never arises there — the ``(missing)`` branch stays for the model's
        # own generality and its unit test.
        fixable = status is not None and status not in (STATUS_DONE, STATUS_CANCELLED)
        rows.append(
            ReconcileRow(
                state=RECONCILE_ORPHAN,
                task_id=task_id,
                task_status=status or "(missing)",
                session=session,
                fix_status=STATUS_IN_PROGRESS if fixable else None,
            )
        )

    rows.sort(key=lambda r: r.task_id)
    return rows


async def next_task(
    table: "TaskTable",
    project: str,
    *,
    parent: str | None = None,
    branch: str | None = None,
    fallback: bool = True,
) -> Task | None:
    """Return the next actionable task, or ``None`` if there isn't one.

    Considers only ``todo`` tasks (id-sorted), optionally filtered to a
    ``parent``. When ``branch`` is given, prefers actionable tasks whose
    ``branch`` matches; if none and ``fallback`` is true, falls back to the
    next actionable task on any branch. In-progress tasks are **excluded** so
    an already-running task is not re-offered.
    """
    candidates = await list_tasks(
        table, project=project, status=STATUS_TODO, parent=parent
    )
    candidates.sort(key=lambda t: (priority_rank(t.priority), t.id))
    actionable = [t for t in candidates if await is_actionable(t, table)]
    if branch is not None:
        on_branch = next((t for t in actionable if t.branch == branch), None)
        if on_branch is not None:
            return on_branch
        if not fallback:
            return None
    return actionable[0] if actionable else None


async def next_follower(
    table: "TaskTable",
    project: str,
    done_id: str,
) -> Task | None:
    """Return the next actionable task that directly follows ``done_id``.

    A *direct follower* is a todo task whose ``follows`` list contains
    ``done_id`` and that is now actionable (all of its dependencies are done).
    Returns the id-sorted first such task, or ``None`` when nothing actionable
    directly follows ``done_id``. Unlike :func:`next_task`, this is scoped to the
    completed task's own successors — it never falls back to unrelated global work.
    Followers are matched across all parents: a ``follows`` edge is not constrained
    to a single parent, so no ``parent`` filter is applied.
    """
    candidates = await list_tasks(table, project=project, status=STATUS_TODO)
    candidates.sort(key=lambda t: (priority_rank(t.priority), t.id))
    for t in candidates:
        if done_id in t.follows and await is_actionable(t, table):
            return t
    return None


async def running_follower(
    table: "TaskTable",
    project: str,
    done_id: str,
) -> Task | None:
    """Return an in-progress task that directly follows ``done_id``.

    A *direct follower* whose ``follows`` list contains ``done_id`` and which is
    already ``in-progress`` — i.e. its session is already running, so a new one
    should **not** be launched. Returns the id-sorted first such task, or
    ``None`` when no direct follower is in progress.
    """
    candidates = await list_tasks(table, project=project, status=STATUS_IN_PROGRESS)
    candidates.sort(key=lambda t: (priority_rank(t.priority), t.id))
    for t in candidates:
        if done_id in t.follows:
            return t
    return None
