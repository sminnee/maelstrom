"""Pure worktree domain logic — no subprocess, no filesystem.

This is the model layer for the worktree subsystem, mirroring how ``task.py`` is
the model for the task subsystem (see ``docs/dev/architecture-patterns.md``). It
holds the NATO-naming and branch/name helpers, the ``.env`` merge/substitution
logic, and the pure dataclasses they produce. The IO adapter ``worktree.py``
imports from here; this module must never import the adapter (that would create a
circular dependency).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

# Fixed worktree names (NATO phonetic alphabet)
WORKTREE_NAMES = [
    "alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
    "xray", "yankee", "zulu",
]

# Single-letter shortcodes for worktree names (all 26 first letters are unique)
WORKTREE_SHORTCODES = {name[0]: name for name in WORKTREE_NAMES}


def resolve_worktree_shortcode(name: str) -> str:
    """Resolve a single-letter shortcode to its full NATO worktree name.

    Args:
        name: A worktree name or single-letter shortcode.

    Returns:
        The full NATO name if input is a single letter, otherwise the input unchanged.
    """
    if len(name) == 1 and name in WORKTREE_SHORTCODES:
        return WORKTREE_SHORTCODES[name]
    return name


# Files managed by maelstrom that should be ignored when checking for dirty files
MAELSTROM_MANAGED_FILES = {".env"}

# Section markers for managed .env content
ENV_SECTION_START = "# Maelstrom port allocations"
ENV_SECTION_END = "# End Maelstrom port allocations"

# Main branch name (hardcoded - no master support)
MAIN_BRANCH = "main"

# Printed when an autorepair session resolved the conflicts. Every command that
# takes --autorepair reports it: a repaired tree holds commits an agent
# rewrote, and it must never read as a clean rebase.
REPAIRED_MESSAGE = "Rebase conflicts resolved by a headless Claude session."


@dataclass(frozen=True)
class BaseRef:
    """The branch a worktree's work is stacked on, and where it last started.

    Maelstrom has always rebased every branch onto ``origin/main``. That was never
    "rebase onto main" — it was "rebase onto my base", with ``main`` as the only
    base that existed. ``BaseRef`` names the base, so the same auto-rebase that
    already runs on open, on ``create_pr``, and on every ``/watch-pr`` loop keeps a
    stack up to date instead of flattening it.

    ``tip`` is the SHA ``origin/<branch>`` had at the last successful rebase. It is
    the ``<upstream>`` argument of ``git rebase --onto``: the point this branch's
    own commits start at. Without it, a base amended during review leaves the child
    holding a stale copy whose patch-id no longer matches, and the rebase conflicts.
    Review churn is the normal path here (every ``--fixup`` + ``--squash`` cycle
    rewrites the parent), so ``tip`` must be re-recorded on every successful rebase.
    """

    branch: str = MAIN_BRANCH
    tip: str | None = None

    @property
    def is_default(self) -> bool:
        """True when this is the plain "rebase onto origin/main" of old.

        A recorded ``tip`` disqualifies it even on ``main``: a tip means a real
        stacked rebase happened, and the ``--onto`` form must be kept.
        """
        return self.branch == MAIN_BRANCH and self.tip is None


@dataclass(frozen=True)
class RebasePlan:
    """The rebase a worktree should run, decided without touching git."""

    onto: str
    """The ref to land the commits on."""
    upstream: str | None
    """``<upstream>`` for ``git rebase --onto``. ``None`` ⇒ a plain rebase."""
    collapsed: bool = False
    """True when the base's remote branch is gone, so the stack flattened."""
    label: str = f"origin/{MAIN_BRANCH}"
    """What to call the target in messages a human reads."""
    effective_base: str = MAIN_BRANCH
    """The branch this rebase actually lands on — always ``onto`` without its
    ``origin/`` prefix.

    Distinct from the *stored* base, which can name a branch whose remote ref is
    gone. Callers report this as ``SyncResult.base`` and rebuild ``origin/<base>``
    from it, so a value that disagrees with ``onto`` yields a ref that does not
    resolve and turns their next git call into a hard error."""


def plan_rebase(base: BaseRef, base_exists: bool) -> RebasePlan:
    """Decide the rebase for ``base``, given whether ``origin/<base>`` still exists.

    Three cases:

    - **Default base** — a plain rebase onto ``origin/main``, with no ``--onto``.
      This is the pre-stacking argv exactly, so an unstacked worktree cannot
      notice stacking exists.
    - **Base alive** — rebase this branch's own commits (those after ``tip``) onto
      the base's current tip, cascading the base's new work into the child.
    - **Base gone** — the base merged or was abandoned. Replay from ``tip`` onto
      ``origin/main``, which drops the base's commits whether or not a squash
      merge preserved their patch-ids, and flag it so the caller clears the store.

    Args:
        base: The stored base for this branch.
        base_exists: Whether ``origin/<base.branch>`` resolves after a prune-fetch.
            Ignored for the default base — ``main`` is never gone.

    Returns:
        The :class:`RebasePlan` to execute.
    """
    if base.is_default:
        return RebasePlan(onto=f"origin/{MAIN_BRANCH}", upstream=None)

    if base_exists and base.branch != MAIN_BRANCH:
        onto = f"origin/{base.branch}"
        return RebasePlan(
            onto=onto, upstream=base.tip, label=onto, effective_base=base.branch
        )

    return RebasePlan(
        onto=f"origin/{MAIN_BRANCH}",
        upstream=base.tip,
        collapsed=True,
    )


# A stack tip whose branch has had no commits for this long draws a warning at
# `mael add`. Long enough that ordinary review latency never trips it, short
# enough to catch a branch that was quietly shelved.
STALE_TIP_DAYS = 30


@dataclass(frozen=True)
class StackTip:
    """The branch new worktrees stack on, after validation."""

    branch: str
    healed: bool = False
    """True when the stored tip's branch was gone and this fell back to ``main``."""
    stale_days: int | None = None
    """Age in days when the tip's branch is stale; ``None`` when it is fresh."""


def resolve_stack_tip(
    stored: str, ages: dict[str, int], *, stale_days: int = STALE_TIP_DAYS
) -> StackTip:
    """Validate the stored stack tip against the remote branches that exist.

    Two failure modes, handled differently because their costs differ:

    - **The tip's branch is gone** (merged, or abandoned and deleted). Basing new
      work on it would fail outright, so the tip self-heals to ``main``.
    - **The tip's branch is stale** — it exists but has had no commits for a long
      time. That is the shelved-branch case: a base that never merges means the
      child never collapses, so it carries dead commits in its PR diff
      indefinitely. But "stale" is a judgement call, and an unattended agent
      session must not stall on one, so this warns and proceeds.

    Args:
        stored: The stack tip as the store holds it. Empty reads as ``main``.
        ages: ``branch -> days since last commit`` for every branch on the remote.
            One ``git for-each-ref`` over ``refs/remotes/origin`` answers both
            existence and age, with no network call and no per-branch cost.
        stale_days: Age past which a tip is reported stale.

    Returns:
        The :class:`StackTip` to use, and what was wrong with the stored one.
    """
    if not stored or stored == MAIN_BRANCH:
        return StackTip(MAIN_BRANCH)

    if stored not in ages:
        return StackTip(MAIN_BRANCH, healed=True)

    age = ages[stored]
    return StackTip(stored, stale_days=age if age > stale_days else None)


def order_by_stack(branches: list[str], bases: dict[str, str]) -> list[str]:
    """Order ``branches`` so a base sorts before anything stacked on it.

    ``sync-all`` rebases every worktree in one pass. Without an order, a child can
    rebase onto a parent tip the parent then replaces moments later, and the child
    is stale until the next run. This is convergence rather than correctness — a
    second ``sync-all`` fixes it today — so a cycle or a missing base degrades to
    "some order" rather than raising.

    A base with no worktree in ``branches`` simply does not constrain anything.
    Ties keep their input order, so an unstacked project's output is its input.

    Args:
        branches: The branches to order.
        bases: Every stored ``branch -> base`` pair.

    Returns:
        The same branches, parents first.
    """
    present = set(branches)
    ordered: list[str] = []
    placed: set[str] = set()

    def place(branch: str, seen: frozenset[str]) -> None:
        if branch in placed or branch in seen:
            return  # already placed, or a cycle — stop rather than recurse forever
        base = bases.get(branch)
        if base and base in present:
            place(base, seen | {branch})
        if branch not in placed:
            placed.add(branch)
            ordered.append(branch)

    for branch in branches:
        place(branch, frozenset())
    return ordered


def validate_base(branch: str, base: str, bases: dict[str, str]) -> None:
    """Raise if setting ``branch``'s base to ``base`` would be self- or cyclic.

    A cycle would make the cascading rebase never terminate, so it is rejected at
    set time rather than discovered at rebase time. Validation is pure: ``bases``
    is the whole branch→base mapping as the store holds it. A branch missing from
    it is implicitly based on ``main``, which ends the walk.

    Args:
        branch: The branch whose base is being set.
        base: The proposed base branch.
        bases: Every currently stored branch→base pair.

    Raises:
        ValueError: If ``base`` is ``branch`` itself, or reaches it by following
            existing bases.
    """
    if base == branch:
        raise ValueError(f"A branch cannot be based on itself: {branch}")

    seen = {branch}
    current = base
    while current and current != MAIN_BRANCH:
        if current in seen:
            chain = " → ".join([branch, base])
            raise ValueError(
                f"Cycle in stack bases: {chain} loops back through {current}"
            )
        seen.add(current)
        current = bases.get(current, MAIN_BRANCH)


def print_flushed(line: str) -> None:
    """Default ``announce`` for the model layer: print a line and flush it.

    A streamed subprocess writes straight to the shared stdout, so an unflushed
    line would land after the output it introduces. CLI callers pass
    ``click.echo`` instead, which keeps the model layer click-free.
    """
    print(line, flush=True)

# Folder holding the main branch, beside the NATO worktrees. The leading
# underscore keeps it out of the `<project>-<nato>` pattern, so it is a
# reference checkout rather than a workspace: no ports, no .env, and never
# recycled. It still appears as a `mael list` row, with an empty APP column —
# it has `main` checked out, so it is never closed.
MAIN_WORKTREE_FOLDER = "_main"


def sanitize_branch_name(branch: str) -> str:
    """Convert branch name to directory-safe name (slashes → dashes)."""
    return branch.replace("/", "-")


def get_worktree_folder_name(project_name: str, worktree_name: str) -> str:
    """Get the folder name for a worktree.

    Args:
        project_name: The project name (e.g., 'askastro').
        worktree_name: The NATO phonetic worktree name (e.g., 'alpha').

    Returns:
        The folder name (e.g., 'askastro-alpha').
    """
    return f"{project_name}-{worktree_name}"


def extract_worktree_name_from_folder(project_name: str, folder_name: str) -> str | None:
    """Extract the worktree name from a folder name.

    Args:
        project_name: The project name (e.g., 'askastro').
        folder_name: The folder name (e.g., 'askastro-alpha').

    Returns:
        The worktree name (e.g., 'alpha') or None if not a valid worktree folder.
    """
    prefix = f"{project_name}-"
    if folder_name.startswith(prefix):
        potential_name = folder_name[len(prefix):]
        if potential_name in WORKTREE_NAMES:
            return potential_name
    return None


def extract_project_name(git_url: str) -> str:
    """Extract project name from a git URL.

    Args:
        git_url: Git URL (e.g., git@github.com:user/repo.git or https://github.com/user/repo.git)

    Returns:
        Project name (e.g., 'repo')
    """
    # Remove trailing .git if present
    url = git_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    # Extract the last path component
    if "/" in url:
        return url.rsplit("/", 1)[-1]
    if ":" in url:
        return url.rsplit(":", 1)[-1]

    return url


def sanitise_path_for_claude(path: Path) -> str:
    """Convert a filesystem path to Claude Code's sanitised project directory name.

    Claude Code stores per-project data in ~/.claude/projects/<sanitised>/
    where the sanitised name is the absolute path with both '/' and '.'
    replaced by '-'. The '.' substitution matters for real temp paths like
    ``/private/tmp/claude.501`` (→ ``-private-tmp-claude-501``); omitting it
    was a latent mismatch against Claude's own slug.

    Args:
        path: Absolute path to sanitise.

    Returns:
        Sanitised path string (e.g., '-Users-sminnee-Projects-foo').
    """
    return str(path.resolve()).replace("/", "-").replace(".", "-")


def claude_transcript_path(
    worktree_path: Path, session_id: str, *, home: Path | None = None
) -> Path:
    """Where Claude Code writes ``session_id``'s transcript for ``worktree_path``.

    ``~/.claude/projects/<sanitised-worktree-path>/<session-id>.jsonl``, where the
    directory slug comes from :func:`sanitise_path_for_claude`. The ``home`` kwarg
    (defaulting to ``Path.home()``) exists so the derivation is unit-testable
    against a fake home. Pure — it builds a path, it does not touch disk.
    """
    root = home if home is not None else Path.home()
    return (
        root
        / ".claude"
        / "projects"
        / sanitise_path_for_claude(worktree_path)
        / f"{session_id}.jsonl"
    )


def has_claude_transcript(
    worktree_path: Path, session_id: str, *, home: Path | None = None
) -> bool:
    """True iff ``session_id`` has an on-disk transcript for ``worktree_path``.

    The single source of truth for "has this task ever been run in this worktree?"
    A stopped session's transcript file persists; a never-run task has none. This
    is strictly "not necessarily live, but a transcript exists" — liveness stays
    the province of ``session_discovery.LiveSessionSet``.
    """
    return claude_transcript_path(worktree_path, session_id, home=home).exists()


def parse_env_text(text: str) -> dict[str, str]:
    """Parse the text of a ``.env`` file into a flat dict.

    Strips ``# source: [...]`` template comments and surrounding quotes so the
    returned values match what a dotenv reader would see. Used for both worktree
    and parent ``.env`` files so they are parsed identically.

    Args:
        text: Raw ``.env`` file contents.

    Returns:
        Dictionary of environment variables.
    """
    env_vars = {}
    for line in text.splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue
        # Parse KEY=value
        if "=" in line:
            key, value = line.split("=", 1)
            value = value.strip()
            # Strip trailing source comment (double-space + #) that isn't
            # inside quotes.
            if "  #" in value:
                # Check if the value starts with a quote
                if value and value[0] in ('"', "'"):
                    quote = value[0]
                    # Find the closing quote
                    close = value.find(quote, 1)
                    if close != -1:
                        # Only strip comments after the closing quote
                        rest = value[close + 1 :]
                        pos = rest.find("  #")
                        if pos != -1:
                            value = value[: close + 1 + pos]
                else:
                    pos = value.find("  #")
                    value = value[:pos]
            # Strip surrounding quotes
            value = value.strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            env_vars[key.strip()] = value
    return env_vars


@dataclass
class EnvConflict:
    """A key present in both the worktree and parent ``.env`` with differing values."""

    key: str
    parent_value: str
    """The parent value in its canonical (possibly unresolved template) form."""
    worktree_value: str
    """The current worktree value, which a reset would overwrite."""
    resolved_parent_value: str
    """``parent_value`` with worktree vars substituted — the value a reset applies."""


@dataclass
class CopyBackResult:
    """Outcome of :func:`copy_back_new_env_vars`."""

    added: dict[str, str] = field(default_factory=dict)
    """New keys appended to the parent ``.env`` (key -> value)."""
    conflicts: list[EnvConflict] = field(default_factory=list)
    """Keys present in both with differing values (warned, left unchanged)."""


def _format_copy_back_block(added: dict[str, str]) -> str:
    """Render new keys as ``KEY=value`` lines to append to the parent ``.env``."""
    lines = [f"{key}={value}" for key, value in added.items()]
    return "\n".join(lines) + "\n"


_VAR_PATTERN = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_SOURCE_PATTERN = re.compile(r"  # source: \[(.+)\]$")


def _substitute_vars(text: str, generated_vars: dict[str, str]) -> str:
    """Substitute ``$VAR`` / ``${VAR}`` references in ``text`` from ``generated_vars``.

    Unknown references are left intact. This is the shared substitution used both
    when writing a worktree ``.env`` and when resolving parent templates for
    copy-back comparison.
    """

    def _replace(m: re.Match[str]) -> str:
        var = m.group(1) or m.group(2) or m.group(0)
        return generated_vars.get(var, m.group(0))

    return _VAR_PATTERN.sub(_replace, text)


def _resolve_env_line(line: str, generated_vars: dict[str, str]) -> str:
    """Resolve variable references in a single .env line.

    If the line has a ``# source: [...]`` suffix, the bracketed text is used as
    the template instead of the visible value.  After substitution the source
    comment is (re-)appended so that future rewrites can recover the template.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return line

    # If a source comment already exists, use it as the template
    source_match = _SOURCE_PATTERN.search(line)
    if source_match:
        template = source_match.group(1)
    else:
        template = line

    resolved = _substitute_vars(template, generated_vars)

    if resolved != template:
        # Substitution occurred – attach/update source comment
        return f"{resolved}  # source: [{template}]"

    # No substitution – return unchanged (strip old source comment if template
    # had nothing to resolve any more)
    if source_match:
        return template
    return line


def _is_blank_value_assignment(line: str) -> bool:
    """True if *line* is a ``KEY=`` assignment whose value is empty/whitespace.

    Such an entry is a parent-side sentinel marking a var the worktree owns
    independently. It is copied neither back nor forward, so it must be dropped
    when materialising the worktree template (mirrors ``_blank_sentinel_keys``).
    Comments and blank separator lines are not assignments and return ``False``.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return False
    _, value = stripped.split("=", 1)
    return value.strip() == ""


def _resolve_template_lines(text: str, generated_vars: dict[str, str]) -> str:
    """Apply variable resolution to every line in *text*.

    Blank-value assignments (``KEY=`` with no value) are parent-side sentinels
    and are dropped rather than emitted as literal empty lines in the worktree.
    """
    resolved = [
        _resolve_env_line(line, generated_vars)
        for line in text.splitlines()
        if not _is_blank_value_assignment(line)
    ]
    return "\n".join(resolved)


def _build_managed_section(generated_vars: dict[str, str]) -> str:
    """Build the managed section text for a .env file.

    Args:
        generated_vars: Generated environment variables (e.g., ports).

    Returns:
        The managed section text including start/end markers.
    """
    lines = [ENV_SECTION_START]
    for key, value in sorted(generated_vars.items()):
        lines.append(f"{key}={value}")
    lines.append(ENV_SECTION_END)
    return "\n".join(lines)
