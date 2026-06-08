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

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .task_store import TaskStore


# --- statuses (folder names) ---

STATUS_TODO = "todo"
STATUS_IN_PROGRESS = "in-progress"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_BLOCKED = "blocked"

VALID_STATUSES = (
    STATUS_TODO,
    STATUS_IN_PROGRESS,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_CANCELLED,
)

DEFAULT_STATUS = STATUS_TODO

# The ten frontmatter keys, always emitted in this order for stable diffs.
FRONTMATTER_KEYS = (
    "id",
    "title",
    "project",
    "command",
    "mode",
    "branch",
    "parent",
    "follows",
    "created",
    "updated",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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
    mode: str = "normal"
    branch: str = ""
    parent: str = ""
    follows: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
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
                lines.append(f"{k}: {_dump_scalar(getattr(self, k))}")
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
            mode=str(frontmatter.get("mode", "normal")) or "normal",
            branch=str(frontmatter.get("branch", "")),
            parent=str(frontmatter.get("parent", "")),
            follows=_coerce_follows(frontmatter.get("follows")),
            created=str(frontmatter.get("created", "")),
            updated=str(frontmatter.get("updated", "")),
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


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split ``text`` into (frontmatter dict, body). Tolerant of missing fm."""
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

    A task is actionable when it is not terminal and every id it follows is in
    ``done/``.
    """
    if is_terminal(task.status):
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


# --- mutations ---


def create(
    store: TaskStore,
    *,
    project: str,
    title: str,
    command: str = "",
    mode: str = "normal",
    branch: str = "",
    parent: str = "",
    follows: list[str] | None = None,
    content: str = "",
    now: str | None = None,
    today: str | None = None,
) -> Task:
    """Create a new task and write it to the store (one write).

    ``branch`` defaults to ``task/<id>`` when falsy, so a task always has a
    stable branch and tasks chained from it can derive the same one.
    """
    timestamp = now if now is not None else _now_iso()
    if parent:
        id = allocate_child_id(store, project, parent)
    else:
        id = allocate_orphan_id(store, project, today=today)
    task = Task(
        id=id,
        title=title,
        project=project,
        command=command,
        mode=mode or "normal",
        branch=branch or default_branch(id),
        parent=parent,
        follows=list(follows or []),
        created=timestamp,
        updated=timestamp,
        content=content,
        status=DEFAULT_STATUS,
    )
    key = task_key(project, DEFAULT_STATUS, id)
    store.write(key, task.to_markdown(), message=f"task: add {id} ({title})")
    return task


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
    task.updated = now if now is not None else _now_iso()
    new_key = task_key(project, new_status, id)
    store.write(
        new_key,
        task.to_markdown(),
        message=f"task: move {id} -> {new_status}",
    )
    store.delete(old_key, message=f"task: move {id} -> {new_status}")
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
    timestamp = now if now is not None else _now_iso()
    entry = f"- {timestamp} {msg}"
    task.log = f"{task.log}\n{entry}".strip() if task.log else entry
    task.updated = timestamp
    store.write(key, task.to_markdown(), message=f"task: log {id}")
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


def default_branch(id: str) -> str:
    """Return the default branch name for a task id (``task/<id>``)."""
    return f"task/{id}"


def build_prompt(task: Task) -> str:
    """Build the initial Claude prompt for a task.

    The shape is ``<command> <title>`` followed by a blank line and the task's
    content. The leading ``<command> `` is omitted when ``command`` is empty
    (a plain execute), and the trailing ``\\n\\n<content>`` is omitted when the
    task has no content.
    """
    head = f"{task.command} {task.title}" if task.command else task.title
    content = task.content.strip()
    if content:
        return f"{head}\n\n{content}"
    return head


def _permission_mode_for(mode: str) -> str | None:
    """Map a task ``mode`` to Claude's ``--permission-mode`` value.

    ``"plan"`` maps to ``"plan"``; anything else uses Claude's default (None,
    i.e. no flag passed).
    """
    return "plan" if mode == "plan" else None


def next_task(
    store: TaskStore,
    project: str,
    *,
    parent: str | None = None,
) -> Task | None:
    """Return the next actionable task, or ``None`` if there isn't one.

    Considers ``todo`` and ``in-progress`` tasks (id-sorted), optionally
    filtered to a ``parent``, and returns the first actionable one.
    In-progress tasks are included so an interrupted session re-surfaces.
    """
    candidates = list_tasks(store, project=project, status=STATUS_TODO, parent=parent)
    candidates += list_tasks(
        store, project=project, status=STATUS_IN_PROGRESS, parent=parent
    )
    candidates.sort(key=lambda t: t.id)
    for task in candidates:
        if is_actionable(task, store):
            return task
    return None
