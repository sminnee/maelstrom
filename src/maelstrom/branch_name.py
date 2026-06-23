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

import re
import subprocess
import tempfile
from collections.abc import Callable

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
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
        "into", "is", "it", "of", "on", "or", "the", "to", "with", "this",
        "that", "these", "those", "via", "vs",
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


def _run_claude(prompt: str) -> str:
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
                _SYSTEM_PROMPT,
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
