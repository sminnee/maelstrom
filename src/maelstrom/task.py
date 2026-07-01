"""Core model for the task notebook.

A task is a plaintext markdown file with YAML frontmatter, stored in a
:class:`~maelstrom.task_store.TaskStore` under the key
``<project>/<status>/<id>.md`` — so the folder *is* the status. Tasks chain via
a ``follows`` graph and each carries the ``command``/``mode`` needed to launch a
real Claude session in a later iteration.

This module is the pure model: it never touches git or the filesystem directly,
only the injected store, so it can be exercised against an
:class:`~maelstrom.task_store.InMemoryStore` in tests.
"""

import os
import re
import subprocess
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from . import branch_name
from .shell import run_cmd
from .task_store import GitFileStore, TaskStore
from .util import now_iso


# --- statuses (folder names) ---

STATUS_TODO = "todo"
STATUS_IN_PROGRESS = "in-progress"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
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

# The frontmatter keys, always emitted in this order for stable diffs. Most
# names match a ``Task`` attr 1:1; the kebab-case lifecycle keys map to the
# snake_case attrs via ``_FRONTMATTER_ATTR`` below.
FRONTMATTER_KEYS = (
    "id",
    "title",
    "project",
    "command",
    "mode",
    "branch",
    "parent",
    "pre-action",
    "post-action",
    "follows",
    "created",
    "updated",
    # Scheduling metadata. Ordinary task fields (settable/shown like any other),
    # but only *acted on* when the task is a ``template/`` task. Appended at the
    # end so existing files keep a stable diff.
    "schedule",
    "last-run",
)

# Frontmatter keys whose name differs from the dataclass attr (kebab vs snake).
# Any key absent here uses its own name as the attr.
_FRONTMATTER_ATTR = {
    "pre-action": "pre_action",
    "post-action": "post_action",
    "last-run": "last_run",
}


def _today() -> str:
    # Local calendar date: .astimezone() yields an aware datetime in the
    # machine's local zone, so the ID date prefix follows the user's day
    # (matching --wake-at) rather than UTC.
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
    """Build the store key for a task. Raises ``ValueError`` on an unsafe id."""
    if not is_safe_id(id):
        raise ValueError(f"Unsafe task id: {id!r}")
    return f"{project}/{status}/{id}.md"


def find_key(store: TaskStore, project: str, id: str) -> str | None:
    """Return the key for ``id`` under ``project`` by scanning all status dirs."""
    if not is_safe_id(id):
        raise ValueError(f"Unsafe task id: {id!r}")
    suffix = f"/{id}.md"
    for key in store.list_dir(f"{project}/"):
        if key.endswith(suffix):
            # Confirm it sits directly in a status dir: project/status/id.md
            parts = key.split("/")
            if len(parts) == 3 and parts[2] == f"{id}.md":
                return key
    return None


def status_from_key(key: str) -> str:
    """Extract the status (folder) from a task key."""
    return key.split("/")[1]


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
            body = "\n".join(lines[i + 1:])
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


def allocate_orphan_id(store: TaskStore, project: str, *, today: str | None = None) -> str:
    """Allocate a top-level (orphan) id of the form ``YYYY-MM-DD.<n>``.

    ``<n>`` is one more than the highest existing counter for ``today`` across
    all statuses. The dot before the counter keeps every id uniformly
    dot-segmented.
    """
    date = today if today is not None else _today()
    pattern = re.compile(rf"^{re.escape(date)}\.(\d+)$")
    return f"{date}.{_next_counter(store, project, pattern)}"


def allocate_child_id(store: TaskStore, project: str, parent: str) -> str:
    """Allocate a child id of the form ``<parent>.<n>``.

    Only *direct* children are counted (anchored ``^{parent}\\.(\\d+)$``), so
    counters at each nesting level stay independent. A Linear virtual parent
    (e.g. ``linear.NORT-123``) works purely from scanning its children, so the
    first child is ``linear.NORT-123.1``.
    """
    pattern = re.compile(rf"^{re.escape(parent)}\.(\d+)$")
    return f"{parent}.{_next_counter(store, project, pattern)}"


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


def _next_counter(store: TaskStore, project: str, pattern: re.Pattern) -> int:
    """Return max matching counter + 1 across all of ``project``'s tasks."""
    highest = 0
    for key in store.list_dir(f"{project}/"):
        if not key.endswith(".md"):
            continue
        id = key.split("/")[-1][:-len(".md")]
        m = pattern.match(id)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


# --- follows graph ---


def is_done(status: str) -> bool:
    return status == STATUS_DONE


def is_terminal(status: str) -> bool:
    return status in (STATUS_DONE, STATUS_CANCELLED)


def is_actionable(task: Task, store: TaskStore) -> bool:
    """Return whether ``task`` can be started now.

    A task is actionable when it is not terminal, not a parked template, and
    every id it follows is in ``done/``. A ``template/`` task is a recipe to
    duplicate from, never something to launch directly, so it is never
    actionable and so stays out of the default ``task list``/``task next`` views.
    """
    if is_terminal(task.status) or task.status == STATUS_TEMPLATE:
        return False
    for dep in task.follows:
        dep_key = find_key(store, task.project, dep)
        if dep_key is None or not is_done(status_from_key(dep_key)):
            return False
    return True


def follow_end_leaves(store: TaskStore, project: str, id: str) -> list[str]:
    """Return the terminal leaves of the ``follows`` chain starting at ``id``.

    Builds forward adjacency (``x`` follows ``id`` => edge ``id -> x``) from a
    single ``list_dir`` scan, then BFS-walks forward. Nodes with no outgoing edge
    are leaves. A visited-set guards against cycles. An ``id`` that nothing
    follows yields ``[id]``.
    """
    # Forward adjacency: parent_id -> [ids that follow it]
    forward: dict[str, list[str]] = {}
    for key in store.list_dir(f"{project}/"):
        if not key.endswith(".md"):
            continue
        text = store.read(key)
        if text is None:
            continue
        task = Task.from_markdown(text, status=status_from_key(key))
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


def child_chain_leaves(store: TaskStore, project: str, parent: str) -> list[str]:
    """Return the chain-leaves among ``parent``'s existing children.

    The children of ``parent`` form their own ``follows`` chain; a new child
    appended "to the end" (the ``follow-end: *`` wildcard) should follow the tail
    of that chain — the siblings that **no other sibling follows**. Returns an
    empty list when ``parent`` has no children yet (the new task becomes the
    first child, following nothing).

    Only direct children (``task.parent == parent``) are considered, so a
    grandchild's own sub-chain never leaks into a sibling's leaf set.
    """
    siblings = list_tasks(store, project=project, parent=parent)
    sibling_ids = {t.id for t in siblings}
    # A sibling is followed-from-within the set if any other sibling follows it.
    followed_within: set[str] = set()
    for t in siblings:
        for dep in t.follows:
            if dep in sibling_ids:
                followed_within.add(dep)
    return sorted(t.id for t in siblings if t.id not in followed_within)


# --- mutations ---


def create(
    store: TaskStore,
    *,
    project: str,
    title: str,
    command: str = "",
    mode: str = "",
    branch: str = "",
    parent: str = "",
    pre_action: str = "",
    post_action: str = "",
    follows: list[str] | None = None,
    content: str = "",
    schedule: str = "",
    last_run: str = "",
    id: str | None = None,
    status: str = DEFAULT_STATUS,
    now: str | None = None,
    today: str | None = None,
) -> Task:
    """Create a new task and write it to the store (one write).

    ``branch`` defaults to ``task/<id>`` when falsy, so a task always has a
    stable branch and tasks chained from it can derive the same one.

    ``id`` overrides id allocation (used by scheduled runs, which key the id by
    boundary date via :func:`allocate_run_id`); when ``None`` an id is allocated
    as a child of ``parent`` or a fresh orphan. ``status`` places the task in a
    folder other than ``todo/`` (e.g. ``template/`` for a parked template).
    """
    timestamp = now if now is not None else now_iso()
    if id is None:
        if parent:
            id = allocate_child_id(store, project, parent)
        else:
            id = allocate_orphan_id(store, project, today=today)
    # When mode is left unset, fall back to the global default; an explicit
    # ``mode`` always wins.
    resolved_mode = mode or DEFAULT_MODE
    # One PR per parent: the first task under a parent owns the branch; later
    # siblings reuse it rather than generating a fresh (and divergent) name.
    # Only the branch-owning task pays the cost of generating a descriptive name.
    resolved_branch = branch or _sibling_branch(store, project, parent)
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
        content=content,
        status=status,
    )
    key = task_key(project, status, id)
    store.write(key, task.to_markdown(), message=f"task: add {id} ({title})")
    return task


def duplicate(
    store: TaskStore,
    project: str,
    src_id: str,
    *,
    title: str | None = None,
    command: str | None = None,
    mode: str | None = None,
    content: str | None = None,
    pre_action: str | None = None,
    post_action: str | None = None,
    branch: str = "",
    parent: str = "",
    follows: list[str] | None = None,
    schedule: str = "",
    status: str = DEFAULT_STATUS,
    id: str | None = None,
    now: str | None = None,
    today: str | None = None,
) -> Task:
    """Duplicate ``src_id``'s recipe into a fresh task (in one write).

    The model primitive behind ``mael task add --from``. Copies the source's
    title/command/mode/content/pre_action/post_action; any non-``None`` override
    wins over the copied default. Source-agnostic — works from any status,
    including ``template/`` — and never mutates the source. ``schedule``/
    ``last_run`` are intentionally *not* copied: ``schedule`` is set only from the
    explicit override (so a run never inherits its template's cron).

    ``branch``/``follows``/``status`` compose the remaining ``add`` flags onto the
    duplicate. For a scheduled run pass ``parent=<template-id>`` and
    ``id=allocate_run_id(...)`` to land a date-keyed child on the template's
    shared branch; ad-hoc duplicates omit both and get a normal id.
    """
    src = load(store, project, src_id)
    return create(
        store,
        project=project,
        title=title if title is not None else src.title,
        command=command if command is not None else src.command,
        mode=mode if mode is not None else src.mode,
        branch=branch,
        pre_action=pre_action if pre_action is not None else src.pre_action,
        post_action=post_action if post_action is not None else src.post_action,
        content=content if content is not None else src.content,
        parent=parent,
        follows=follows,
        schedule=schedule,
        status=status,
        id=id,
        now=now,
        today=today,
    )


# --- plan-file (load-many) parsing + batch creation ---

# Frontmatter keys allowed in a `---CREATE TASK---` block. These are
# *task-creation arguments* (mirroring `mael task add`'s flags), not the
# serialized task frontmatter. Anything else is a typo that should fail loudly
# rather than silently drop a dependency.
_BLOCK_KEYS = frozenset(
    {
        "title",
        "command",
        "mode",
        "parent",
        "pre-action",
        "post-action",
        "follow",
        "follow-end",
    }
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


_OPEN_MARKER = re.compile(r"^---CREATE TASK ([A-Za-z0-9]+)---$")
_END_MARKER = re.compile(r"^---END TASK ([A-Za-z0-9]+)---$")
# A line that *looks like* a marker (so we can reject a malformed one — e.g. a
# hyphenated name or stray spacing — rather than silently treat it as prose).
_LOOSE_MARKER = re.compile(r"^---(?:CREATE|END) TASK\b.*---$")


def parse_task_blocks(text: str) -> tuple[list[dict], list[str]]:
    """Parse a marked plan file into a list of task-creation blocks.

    A plan file is human-readable preamble (ignored) followed by one or more
    blocks, each opening with ``---CREATE TASK <name>---`` on its own line. A
    block runs until the next open marker, an optional ``---END TASK <name>---``
    close marker, or EOF. ``<name>`` (``[A-Za-z0-9]+``) is a local handle for
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
            raise ValueError(
                f"Block {current_name!r} has invalid frontmatter: {e}"
            )
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
                "(name must match [A-Za-z0-9]+)."
            )
        if current_name is not None:
            buf.append(line)
    flush()

    if not blocks:
        raise ValueError("No task blocks found (expected '---CREATE TASK <name>---').")
    return blocks, warnings


def load_many(
    store: TaskStore,
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
    real ids. ``follow-end`` values resolve to the live store's chain leaves;
    the wildcard ``*`` resolves to the chain-leaves of the block's *parent's*
    existing children (see :func:`child_chain_leaves`) — "append me to the end of
    my siblings". Because ``GitFileStore`` mutates the filesystem eagerly inside a
    transaction, each ``create()``'s file is visible to the next iteration's id
    allocation and leaf scans, so forward-chaining within the batch is correct.
    Returns the created tasks in block order.
    """
    created: dict[str, Task] = {}  # block name -> created Task
    with store.transaction(message=f"task: load {len(blocks)} task(s)"):
        for b in blocks:
            args = b["args"]
            parent = str(args.get("parent", "")) or default_parent
            follows: list[str] = []
            for f in _coerce_follows(args.get("follow")):
                # Intra-file ref wins; otherwise treat as a real id.
                follows.append(created[f].id if f in created else f)
            for end_id in _coerce_follows(args.get("follow-end")):
                follows.extend(_resolve_follow_end(store, project, end_id, parent))
            deduped = list(dict.fromkeys(follows))
            t = create(
                store,
                project=project,
                title=str(args["title"]),
                command=str(args.get("command", "")),
                mode=str(args.get("mode", "")),
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


def _resolve_follow_end(
    store: TaskStore, project: str, end_id: str, parent: str
) -> list[str]:
    """Resolve one ``follow-end`` value to a list of ids to follow.

    ``*`` means "the end of my parent's child-chain" — :func:`child_chain_leaves`
    against ``parent`` (empty when there are no siblings yet, or when the task
    has no parent). Any other value is a real task id resolved via
    :func:`follow_end_leaves`.
    """
    if end_id == "*":
        return child_chain_leaves(store, project, parent) if parent else []
    return follow_end_leaves(store, project, end_id)


def load(store: TaskStore, project: str, id: str) -> Task:
    """Load a task by id. Raises ``KeyError`` if not found."""
    key = find_key(store, project, id)
    if key is None:
        raise KeyError(f"Task not found: {project}/{id}")
    text = store.read(key)
    if text is None:
        raise KeyError(f"Task not found: {project}/{id}")
    return Task.from_markdown(text, status=status_from_key(key))


def move(
    store: TaskStore,
    project: str,
    id: str,
    new_status: str,
    *,
    now: str | None = None,
) -> Task:
    """Move a task to ``new_status`` (write-new + delete-old, bumps ``updated``)."""
    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status!r}")
    old_key = find_key(store, project, id)
    if old_key is None:
        raise KeyError(f"Task not found: {project}/{id}")
    text = store.read(old_key)
    if text is None:
        raise KeyError(f"Task not found: {project}/{id}")
    task = Task.from_markdown(text, status=status_from_key(old_key))

    if task.status == new_status:
        return task

    task.status = new_status
    task.updated = now if now is not None else now_iso()
    new_key = task_key(project, new_status, id)
    # One commit for the write-new + delete-old pair; the transaction owns the
    # message, so the per-call writes/deletes don't repeat it.
    with store.transaction(message=f"task: move {id} -> {new_status}"):
        store.write(new_key, task.to_markdown())
        store.delete(old_key)
    return task


def append_log(
    store: TaskStore,
    project: str,
    id: str,
    msg: str,
    *,
    now: str | None = None,
) -> Task:
    """Append a timestamped line to a task's log section (one write)."""
    key = find_key(store, project, id)
    if key is None:
        raise KeyError(f"Task not found: {project}/{id}")
    text = store.read(key)
    if text is None:
        raise KeyError(f"Task not found: {project}/{id}")
    task = Task.from_markdown(text, status=status_from_key(key))
    timestamp = now if now is not None else now_iso()
    entry = f"- {timestamp} {msg}"
    task.log = f"{task.log}\n{entry}".strip() if task.log else entry
    task.updated = timestamp
    store.write(key, task.to_markdown(), message=f"task: log {id}")
    return task


def update(
    store: TaskStore,
    project: str,
    id: str,
    *,
    title: str | None = None,
    branch: str | None = None,
    content: str | None = None,
    command: str | None = None,
    mode: str | None = None,
    pre_action: str | None = None,
    post_action: str | None = None,
    schedule: str | None = None,
    last_run: str | None = None,
    now: str | None = None,
) -> Task:
    """Update provided fields in place (one write, bumps ``updated``).

    Status is folder-derived and intentionally not touched here (use ``move``
    for lifecycle transitions). Only fields passed non-``None`` are changed, so
    an omitted argument leaves that field as-is.
    """
    key = find_key(store, project, id)
    if key is None:
        raise KeyError(f"Task not found: {project}/{id}")
    text = store.read(key)
    if text is None:
        raise KeyError(f"Task not found: {project}/{id}")
    task = Task.from_markdown(text, status=status_from_key(key))
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
    if pre_action is not None:
        task.pre_action = pre_action
    if post_action is not None:
        task.post_action = post_action
    if schedule is not None:
        task.schedule = schedule
    if last_run is not None:
        task.last_run = last_run
    task.updated = now if now is not None else now_iso()
    store.write(key, task.to_markdown(), message=f"task: update {id}")
    return task


def edit_in_editor(
    store: GitFileStore,
    project: str,
    id: str,
    *,
    editor: str | None = None,
) -> tuple[Task, bool]:
    """Open the task file in ``$EDITOR``/vi; commit only if it changed.

    Returns ``(task, changed)``. A no-op save (open + quit, no edits) produces
    no commit. On a real change the file is re-rendered through the model so it
    stays canonical (stable frontmatter order / section layout) and ``updated``
    bumps, then committed via the store — keeping git the single committer.
    Needs the on-disk path, which only :class:`GitFileStore` exposes.
    """
    key = find_key(store, project, id)
    if key is None:
        raise KeyError(f"Task not found: {project}/{id}")
    before = store.read(key)
    if before is None:
        raise KeyError(f"Task not found: {project}/{id}")
    path = store._path(key)  # file already exists on disk in the git-fs store
    ed = editor or os.environ.get("EDITOR") or "vi"
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
        return Task.from_markdown(after, status=status_from_key(key)), False
    task = Task.from_markdown(after, status=status_from_key(key))
    task.updated = now_iso()
    store.write(key, task.to_markdown(), message=f"task: edit {id}")
    return task, True


def delete(store: TaskStore, project: str, id: str) -> Task:
    """Delete a task and strip it from every dependent's ``follows`` list.

    Removes the task file, then scans the project for non-terminal tasks that
    ``follows`` ``id`` and rewrites them without it (one write each). Terminal
    tasks (done/cancelled) are left untouched — they're historical and their
    ``follows`` no longer gates anything. Returns the deleted task.
    """
    key = find_key(store, project, id)
    if key is None:
        raise KeyError(f"Task not found: {project}/{id}")
    text = store.read(key)
    if text is None:
        raise KeyError(f"Task not found: {project}/{id}")
    deleted = Task.from_markdown(text, status=status_from_key(key))

    # One commit for the removal plus every dependent rewrite; the transaction
    # owns the message. The store mutates eagerly, so the post-delete list_dir
    # scan below still sees a consistent view.
    with store.transaction(message=f"task: rm {id}"):
        store.delete(key)

        # Drop the deleted id from any non-terminal dependent's follows list.
        for dep_key in store.list_dir(f"{project}/"):
            if not dep_key.endswith(".md") or dep_key == key:
                continue
            if is_terminal(status_from_key(dep_key)):
                continue
            dep_text = store.read(dep_key)
            if dep_text is None:
                continue
            dep = Task.from_markdown(dep_text, status=status_from_key(dep_key))
            if id not in dep.follows:
                continue
            dep.follows = [f for f in dep.follows if f != id]
            store.write(dep_key, dep.to_markdown())
    return deleted


def rename(
    store: TaskStore,
    project: str,
    old_id: str,
    new_id: str,
    *,
    now: str | None = None,
) -> Task:
    """Re-key a task and fix every reference that points at it.

    Moves the task file to the new key (preserving status/content/log, bumping
    ``updated`` and rewriting the ``id`` field), then in the same transaction
    rewrites non-terminal dependents' ``follows`` (old->new) and non-terminal
    direct children's ``parent`` (old->new). Terminal tasks (done/cancelled) are
    left untouched, mirroring :func:`delete`. Children's own ids are NOT cascaded.

    Raises ``KeyError`` (task not found), ``ValueError`` (unsafe ``new_id`` or
    ``new_id`` already taken). Returns the renamed task unchanged when
    ``new_id == old_id``.
    """
    old_key = find_key(store, project, old_id)
    if old_key is None:
        raise KeyError(f"Task not found: {project}/{old_id}")
    if not is_safe_id(new_id):
        raise ValueError(f"Unsafe task id: {new_id!r}")
    if new_id == old_id:
        text = store.read(old_key)
        if text is None:
            raise KeyError(f"Task not found: {project}/{old_id}")
        return Task.from_markdown(text, status=status_from_key(old_key))
    if find_key(store, project, new_id) is not None:
        raise ValueError(f"Task already exists: {project}/{new_id}")

    text = store.read(old_key)
    if text is None:
        raise KeyError(f"Task not found: {project}/{old_id}")
    status = status_from_key(old_key)
    task = Task.from_markdown(text, status=status)
    task.id = new_id
    task.updated = now if now is not None else now_iso()
    new_key = task_key(project, status, new_id)

    # One commit for the relocation plus every dependent rewrite; the transaction
    # owns the message. The store mutates eagerly, so the list_dir scan below
    # sees the post-write/delete view.
    with store.transaction(message=f"task: rename {old_id} -> {new_id}"):
        store.write(new_key, task.to_markdown())
        store.delete(old_key)

        # Fix cross-references in non-terminal tasks: rewrite follows (old->new)
        # and re-parent direct children (old->new). The renamed task itself is
        # already at new_key and never references its old id, so skip it.
        for dep_key in store.list_dir(f"{project}/"):
            if not dep_key.endswith(".md") or dep_key == new_key:
                continue
            if is_terminal(status_from_key(dep_key)):
                continue
            dep_text = store.read(dep_key)
            if dep_text is None:
                continue
            dep = Task.from_markdown(dep_text, status=status_from_key(dep_key))
            changed = False
            if old_id in dep.follows:
                dep.follows = [new_id if f == old_id else f for f in dep.follows]
                changed = True
            if dep.parent == old_id:
                dep.parent = new_id
                changed = True
            if changed:
                store.write(dep_key, dep.to_markdown())
    return task


def list_tasks(
    store: TaskStore,
    *,
    project: str,
    status: str | None = None,
    parent: str | None = None,
) -> list[Task]:
    """List tasks under ``project``, optionally filtered by status and parent."""
    tasks: list[Task] = []
    for key in store.list_dir(f"{project}/"):
        if not key.endswith(".md"):
            continue
        parts = key.split("/")
        if len(parts) != 3:
            continue
        key_status = parts[1]
        if status is not None and key_status != status:
            continue
        text = store.read(key)
        if text is None:
            continue
        task = Task.from_markdown(text, status=key_status)
        if parent is not None and task.parent != parent:
            continue
        tasks.append(task)
    tasks.sort(key=lambda t: t.id)
    return tasks


# --- session launch helpers (pure) ---


# Linear issue identifiers look like NORT-123, ABC-7, TEAM2-99.
_LINEAR_PARENT_RE = re.compile(r"^linear\.([A-Z][A-Z0-9]*-\d+)$")


def _sibling_branch(store: TaskStore, project: str, parent: str) -> str:
    """Return an existing sibling's branch under ``parent``, or ``""``.

    Enforces "one PR per parent": once any task under ``parent`` has a branch,
    later siblings reuse it instead of generating a fresh, divergent name. With
    no ``parent`` (orphan task) or no existing sibling, returns ``""`` so the
    caller falls back to :func:`default_branch`.
    """
    if not parent:
        return ""
    for sibling in list_tasks(store, project=project, parent=parent):
        if sibling.branch:
            return sibling.branch
    return ""


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
                return branch_name.generate_branch_name(
                    title, content, prefix=number
                )
            return f"feat/{number}"
        # Child of a non-Linear parent: keep sharing the parent's branch.
        return f"task/{parent}"
    if generate and title:
        return branch_name.generate_branch_name(title, content)
    return f"task/{id}"


def build_prompt(task: Task) -> str:
    """Build the initial Claude prompt for a task.

    The shape is ``/<command> <title>`` followed by a blank line and the task's
    content. ``command`` names a Claude skill/slash-command, so it is prefixed
    with ``/`` to invoke it. The leading ``/<command> `` is omitted when
    ``command`` is empty (a plain execute), and the trailing ``\\n\\n<content>``
    is omitted when the task has no content.
    """
    head = f"/{task.command} {task.title}" if task.command else task.title
    content = task.content.strip()
    if content:
        return f"{head}\n\n{content}"
    return head


def _permission_mode_for(mode: str) -> str | None:
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
RECONCILE_STALE = "stale-in-progress"  # in-progress task, no live session → done
RECONCILE_ORPHAN = "orphan-session"  # live session, task not in-progress → start


@dataclass
class ReconcileRow:
    """One reconcile finding: a task/session pair and its suggested correction.

    ``state`` is one of the ``RECONCILE_*`` constants. ``fix_status`` is the
    status the task should move to (``None`` for OK rows — nothing to do).
    ``session`` is the matched live-session dict, or ``None`` for a
    stale-in-progress task that has no session.
    """

    state: str
    task_id: str
    task_status: str
    session: dict | None
    fix_status: str | None


def reconcile(
    store: TaskStore,
    project: str,
    *,
    session_task_ids: dict[str, dict],
) -> list[ReconcileRow]:
    """Classify in-progress tasks and live sessions into reconcile rows.

    Pure: the caller supplies ``session_task_ids`` — a map from task id to the
    live session dict that owns it (built from the session registry in the CLI
    layer) — and this function reads only the injected store. It never moves
    tasks; ``--fix`` application is the caller's job, driven off ``fix_status``.

    Three states (see the ``RECONCILE_*`` constants):

    - **OK**: an ``in-progress`` task that has a live session. No fix.
    - **stale-in-progress**: an ``in-progress`` task with no live session.
      Suggested fix → ``done`` (matches session-end auto-close behaviour).
    - **orphan-session**: a live session whose task is *not* ``in-progress``
      (todo/blocked/etc.). Suggested fix → ``in-progress``. A session whose
      task is already terminal (done/cancelled) or missing is reported with no
      fix — a finished task whose window lingers is not a corruption to flip.

    Rows are returned id-sorted for stable rendering.
    """
    rows: list[ReconcileRow] = []
    in_progress = list_tasks(store, project=project, status=STATUS_IN_PROGRESS)
    in_progress_ids = {t.id for t in in_progress}

    for task in in_progress:
        session = session_task_ids.get(task.id)
        if session is not None:
            rows.append(ReconcileRow(
                state=RECONCILE_OK,
                task_id=task.id,
                task_status=task.status,
                session=session,
                fix_status=None,
            ))
        else:
            rows.append(ReconcileRow(
                state=RECONCILE_STALE,
                task_id=task.id,
                task_status=task.status,
                session=None,
                fix_status=STATUS_DONE,
            ))

    for task_id, session in session_task_ids.items():
        if task_id in in_progress_ids:
            continue  # already an OK row above
        key = find_key(store, project, task_id)
        status = status_from_key(key) if key is not None else None
        # Only flip a non-terminal task (todo/blocked) into in-progress. A
        # terminal or missing task with a lingering session is listed, not
        # auto-corrected.
        fixable = status is not None and status not in (STATUS_DONE, STATUS_CANCELLED)
        rows.append(ReconcileRow(
            state=RECONCILE_ORPHAN,
            task_id=task_id,
            task_status=status or "(missing)",
            session=session,
            fix_status=STATUS_IN_PROGRESS if fixable else None,
        ))

    rows.sort(key=lambda r: r.task_id)
    return rows


def next_task(
    store: TaskStore,
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
    candidates = list_tasks(store, project=project, status=STATUS_TODO, parent=parent)
    candidates.sort(key=lambda t: t.id)
    actionable = [t for t in candidates if is_actionable(t, store)]
    if branch is not None:
        on_branch = next((t for t in actionable if t.branch == branch), None)
        if on_branch is not None:
            return on_branch
        if not fallback:
            return None
    return actionable[0] if actionable else None


def next_follower(store: TaskStore, project: str, done_id: str) -> Task | None:
    """Return the next actionable task that directly follows ``done_id``.

    A *direct follower* is a todo task whose ``follows`` list contains
    ``done_id`` and that is now actionable (all of its dependencies are done).
    Returns the id-sorted first such task, or ``None`` when nothing actionable
    directly follows ``done_id``. Unlike :func:`next_task`, this is scoped to the
    completed task's own successors — it never falls back to unrelated global work.
    Followers are matched across all parents: a ``follows`` edge is not constrained
    to a single parent, so no ``parent`` filter is applied.
    """
    candidates = list_tasks(store, project=project, status=STATUS_TODO)
    candidates.sort(key=lambda t: t.id)
    for t in candidates:
        if done_id in t.follows and is_actionable(t, store):
            return t
    return None


def running_follower(store: TaskStore, project: str, done_id: str) -> Task | None:
    """Return an in-progress task that directly follows ``done_id``.

    A *direct follower* whose ``follows`` list contains ``done_id`` and which is
    already ``in-progress`` — i.e. its session is already running, so a new one
    should **not** be launched. Returns the id-sorted first such task, or
    ``None`` when no direct follower is in progress.
    """
    candidates = list_tasks(store, project=project, status=STATUS_IN_PROGRESS)
    candidates.sort(key=lambda t: t.id)
    for t in candidates:
        if done_id in t.follows:
            return t
    return None
