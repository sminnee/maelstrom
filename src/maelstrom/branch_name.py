"""Generate descriptive git branch names from a task's title/content.

A branch name has the shape ``<type>/<desc>`` where ``<type>`` is one of
:data:`TYPES` and ``<desc>`` is a 2–4 word kebab-case summary of the work.

The descriptive slug + type are picked by shelling out to the local ``claude``
CLI in print mode (``claude -p``) — no new dependency, no API key, reusing the
binary the project already invokes elsewhere. Any failure (CLI missing, timeout,
non-zero exit, or output that doesn't match the strict format) falls back to a
deterministic offline slug, so a bad or slow model call never breaks task
creation.

This module is imported by the model layer (``task.py``), so its ``claude -p``
subprocess call is a **sanctioned exception** to the "no subprocess in model
code" convention (``docs/dev/architecture-patterns.md`` §2), alongside
``edit_in_editor``. It is kept obvious and contained: every code path is fully
resilient via the deterministic offline fallback, and the subprocess is reached
through an injectable ``runner`` so the model stays exercisable against an
``InMemoryStore`` with no CLI. This is not licence for general I/O in the model.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

TYPES = ("fix", "feat", "chore", "refactor")

# Minimal system prompt forced onto the headless call so an inherited project
# ``CLAUDE.md`` / SessionStart hook can't frame the model as mid-workflow and
# nudge it to editorialize instead of emitting a slug.
_SYSTEM_PROMPT = (
    "You are a branch-name generator. Your only job is to emit a single "
    "git branch-name line in the requested format. Do not explain, do not "
    "ask questions, do not run tools — output one line and nothing else."
)

# Output the model is allowed to produce: ``<type>/<2-4-word-kebab-desc>``.
_OUTPUT_RE = re.compile(r"^(fix|feat|chore|refactor)/[a-z0-9]+(-[a-z0-9]+){0,3}$")

# How long to wait on a `claude -p` call before giving up (seconds). Bounds the
# worst case so a hung CLI never blocks `task add`.
_CLAUDE_TIMEOUT = 20

# Common English stopwords dropped from the deterministic slug so the kept words
# carry the actual meaning of the work.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "this",
        "that",
        "these",
        "those",
        "via",
        "vs",
    }
)


def slugify(text: str, *, max_words: int = 4) -> str:
    """Deterministic fallback slug.

    Lowercase, drop punctuation and stopwords, kebab-case, and keep the first
    ``max_words`` meaningful words. Returns ``""`` when nothing meaningful
    survives (callers seed a fallback from the id/number in that case).
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    kept = [w for w in words if w not in _STOPWORDS]
    # If stripping stopwords left nothing (e.g. a title made entirely of them),
    # fall back to the raw words so we still produce *something*.
    if not kept:
        kept = words
    return "-".join(kept[:max_words])


def _run_claude(prompt: str, system: str = _SYSTEM_PROMPT) -> str:
    """Invoke ``claude -p <prompt>`` and return its stdout (stripped).

    Raises on any failure (missing binary, non-zero exit, timeout) — the caller
    treats every exception as "use the deterministic fallback".

    Isolated from the cwd so a one-line naming prompt is reproducible wherever
    it runs: a minimal ``--system-prompt`` overrides inherited workflow framing,
    ``--strict-mcp-config`` skips project MCP servers, and running in a neutral
    tempdir means no project ``CLAUDE.md`` / SessionStart hook is discovered.
    """
    with tempfile.TemporaryDirectory() as neutral_cwd:
        result = subprocess.run(
            [
                "claude",
                "-p",
                "--strict-mcp-config",
                "--system-prompt",
                system,
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=_CLAUDE_TIMEOUT,
            check=True,
            cwd=neutral_cwd,
        )
    return result.stdout.strip()


def _build_prompt(title: str, content: str) -> str:
    """The instruction handed to ``claude -p`` to pick a type + kebab slug."""
    snippet = content.strip()[:800]
    body = f"Title: {title}"
    if snippet:
        body += f"\n\nDetails:\n{snippet}"
    return (
        "You name git branches for a software task. Reply with EXACTLY ONE LINE "
        "and nothing else, in the form `<type>/<desc>` where:\n"
        "- <type> is one of: fix, feat, chore, refactor (choose by the work "
        "described — fix for bug fixes, feat for new behaviour, refactor for "
        "no-behaviour-change restructuring, chore for everything else).\n"
        "- <desc> is a 2-4 word kebab-case summary (lowercase a-z0-9 and "
        "hyphens only, no leading number, no team prefix).\n"
        "Example: fix/flaky-port-test\n"
        "If you cannot infer a sensible name from the title and details, reply "
        "with exactly `unknown` and nothing else.\n\n"
        f"{body}"
    )


def _compose(type_: str, prefix: str, desc: str) -> str:
    """Assemble ``<type>/<prefix>-<desc>`` (prefix optional)."""
    desc = f"{prefix}-{desc}" if prefix else desc
    return f"{type_}/{desc}"


def _shares_token(desc: str, title: str, content: str) -> bool:
    """Whether the model's kebab desc shares any token with the task text.

    A well-formed slug that overlaps nothing in the title/details is almost
    always the model editorializing (e.g. ``branch-name-not-applicable`` for
    "Mermaid charts") rather than naming the work, so we reject it. Uses the
    same tokenizer as :func:`slugify` for the task text — stopwords dropped —
    and splits the desc on hyphens.
    """
    desc_tokens = set(desc.split("-"))
    # max_words is effectively unbounded here (unlike slugify's 4-word default):
    # we want every task-text token for the overlap check, not just the slug head.
    text_tokens = set(slugify(f"{title} {content}", max_words=1000).split("-"))
    return bool(desc_tokens & text_tokens)


def generate_branch_name(
    title: str,
    content: str = "",
    *,
    default_type: str = "feat",
    prefix: str = "",
    runner: Callable[[str], str] | None = None,
) -> str:
    """Return ``<type>/<desc>`` for a task.

    Calls ``claude -p`` (via ``runner``) to pick the type and a 2–4 word kebab
    slug. Output is "not good" when it is the literal ``unknown``, empty, an
    exception, fails strict validation, or is a well-formed slug that shares no
    token with the task text (the model editorializing rather than naming the
    work). A not-good result triggers **one retry** — LLM sampling is
    non-deterministic, so a second draw frequently succeeds — and if that is
    also not good, falls back to ``f"{default_type}/{slugify(title)}"``.

    When ``prefix`` is set it leads the desc: ``<type>/<prefix>-<desc>`` (e.g.
    ``fix/123-flaky-port-test``). The prefix is spliced in here rather than
    produced by the model, so the number is deterministic and never hallucinated.

    ``runner`` defaults to the real ``claude -p`` invocation; tests inject a fake.
    """
    run = runner or _run_claude

    fallback_desc = slugify(title) or prefix or "task"
    if prefix and fallback_desc == prefix:
        # Title produced no meaningful words; avoid a bare `<prefix>` desc.
        fallback_desc = "task"
    fallback = _compose(default_type, prefix, fallback_desc)

    if not title.strip():
        return fallback

    prompt = _build_prompt(title, content)
    # Two attempts: the model's first draw is sometimes refusal-shaped garbage;
    # a fresh draw usually slugs a clear title fine. If both miss, use fallback.
    for _ in range(2):
        try:
            raw = run(prompt)
        except Exception:
            continue

        line = raw.strip().splitlines()[0].strip() if raw.strip() else ""
        if line == "unknown" or not _OUTPUT_RE.match(line):
            continue

        type_, desc = line.split("/", 1)
        if not _shares_token(desc, title, content):
            continue
        return _compose(type_, prefix, desc)

    return fallback


# --- inferring a whole task from prose ---

#: The task ``command`` values inference may propose, plus ``""`` for an
#: execute task. Each names a skill in ``shared/skills/``; anything else the
#: model invents falls back to ``""``.
#:
#: Deliberately narrower than the web app's ``KNOWN_COMMANDS``
#: (``web/src/protocol/phase.ts``): ``shape`` names no skill, and ``watch-pr``
#: follows a pushed PR rather than starting new work. Neither suits a task the
#: user has only just described; both stay typeable in the task editor.
KNOWN_COMMANDS = ("plan-task", "plan-next-step")

#: The longest title inference will hand back. Long enough for a real sentence,
#: short enough to read in the task list's one line.
MAX_TITLE = 80

# The naming call's system prompt, in the same spirit as _SYSTEM_PROMPT: strip
# the model of any workflow framing so it names the work instead of doing it.
_INFER_SYSTEM_PROMPT = (
    "You name software tasks. Your only job is to emit the requested three "
    "lines. Do not explain, do not ask questions, do not run tools — output "
    "the lines and nothing else."
)


@dataclass(frozen=True)
class TaskNames:
    """What inference reads off a draft: the fields a task needs naming."""

    title: str
    branch: str
    command: str


def _build_infer_prompt(draft: str) -> str:
    """The instruction handed to ``claude -p`` to name a task from its prose."""
    commands = ", ".join(f"`{c}`" for c in KNOWN_COMMANDS)
    return (
        "You name a software task from the description below. Reply with "
        "EXACTLY THREE LINES and nothing else:\n"
        f"- Line 1: a short imperative title, at most {MAX_TITLE} characters, "
        "no trailing full stop.\n"
        "- Line 2: a git branch name `<type>/<desc>` where <type> is one of: "
        "fix, feat, chore, refactor, and <desc> is a 2-4 word kebab-case "
        "summary (lowercase a-z0-9 and hyphens only).\n"
        f"- Line 3: the skill the task runs, one of {commands}, or an empty "
        "line when the task is ready to execute as written.\n"
        "Example:\n"
        "Fix flaky port allocation\n"
        "fix/flaky-port-test\n"
        "\n\n"
        f"Description:\n{draft.strip()[:2000]}"
    )


def _field(value: object) -> str:
    """A JSON field as a stripped string, or ``""`` for anything but a string.

    A number or an object would otherwise land its Python repr in a field.
    """
    return value.strip() if isinstance(value, str) else ""


def _parse_infer(raw: str) -> tuple[str, str, str] | None:
    """Split a reply into ``(title, branch, command)``, or ``None`` for prose.

    Accepts either the three-line form the prompt asks for or a JSON object,
    because a model that ignores "three lines" most often answers with JSON.

    In the line form a well-formed branch on line 2 is the only reliable tell.
    Line count cannot serve: a refusal runs to two lines as readily as one,
    and an answer whose command is empty loses its blank third line to
    ``strip``, so both arrive as two lines and an apology would land as the
    title. A JSON object needs no such test — it is self-identifying, and its
    branch is checked by the caller like every other field.

    Beyond that nothing is validated here. A branch that is well-formed but
    unrelated to the draft still reaches the caller, which rejects it there
    and keeps the title.
    """
    text = raw.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        if not isinstance(parsed, dict):
            return None
        # A JSON object is self-identifying: no line count applies.
        return (
            _field(parsed.get("title")),
            _field(parsed.get("branch")),
            _field(parsed.get("command")),
        )
    lines = [line.strip() for line in text.splitlines()]
    title, branch, command = (lines[i] if i < len(lines) else "" for i in range(3))
    return (title, branch, command) if _OUTPUT_RE.match(branch) else None


def _first_line(draft: str) -> str:
    """The draft's first non-empty line, trimmed to :data:`MAX_TITLE`."""
    for line in draft.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:MAX_TITLE].rstrip()
    return ""


def infer_task_names(
    draft: str, *, runner: Callable[[str], str] | None = None
) -> TaskNames:
    """Read a title, a branch and a command off a draft's prose.

    The machinery :func:`generate_branch_name` uses, over a wider output
    contract. The draft is never rewritten: it becomes the task's content
    verbatim, and this only names it.

    Each field is validated on its own, so a malformed branch keeps a good
    title. A branch is kept when it matches :data:`_OUTPUT_RE` and shares a
    token with the draft; a command is kept when it is in
    :data:`KNOWN_COMMANDS`. Whatever the model does not supply falls back to
    the draft's own first line and ``f"feat/{slugify(title)}"``.
    """
    run = runner or _run_claude

    fallback_title = _first_line(draft)
    fallback_branch = f"feat/{slugify(fallback_title) or 'task'}"
    if not draft.strip():
        return TaskNames(title="", branch=fallback_branch, command="")

    prompt = _build_infer_prompt(draft)
    title = branch = command = ""
    # Two attempts, as the branch-only path takes: a first draw that ignores
    # the contract is usually refusal-shaped, and a fresh draw names the work
    # fine.
    for _ in range(2):
        try:
            raw = run(prompt)
        except Exception:
            continue
        parsed = _parse_infer(raw)
        if parsed is not None:
            title, branch, command = parsed
            break

    title = title[:MAX_TITLE].rstrip() or fallback_title
    if not _OUTPUT_RE.match(branch) or not _shares_token(
        branch.split("/", 1)[1], title, draft
    ):
        branch = f"feat/{slugify(title) or 'task'}"
    if command not in KNOWN_COMMANDS:
        command = ""
    return TaskNames(title=title, branch=branch, command=command)
