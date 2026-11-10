"""Pure worktree domain logic — no subprocess, no filesystem.

This is the model layer for the worktree subsystem, mirroring how ``task.py`` is
the model for the task subsystem (see ``docs/dev/architecture-patterns.md``). It
holds the NATO-naming and branch/name helpers, the ``.env`` merge/substitution
logic, the worktree domain errors, and the planners that decide a sync or a
squash before any git call runs. The IO adapter ``worktree.py`` imports from
here; this module must never import the adapter (that would create a circular
dependency).

A planner takes the git facts its caller gathered and returns what to do, so the
decision is table-testable — see ``docs/dev/architecture-patterns.md``.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

# Fixed worktree names (NATO phonetic alphabet)
WORKTREE_NAMES = [
    "alpha",
    "bravo",
    "charlie",
    "delta",
    "echo",
    "foxtrot",
    "golf",
    "hotel",
    "india",
    "juliet",
    "kilo",
    "lima",
    "mike",
    "november",
    "oscar",
    "papa",
    "quebec",
    "romeo",
    "sierra",
    "tango",
    "uniform",
    "victor",
    "whiskey",
    "xray",
    "yankee",
    "zulu",
]

# Single-letter shortcodes for worktree names (all 26 first letters are unique)
WORKTREE_SHORTCODES = {name[0]: name for name in WORKTREE_NAMES}


def worktree_num(worktree_name: str) -> int:
    """Return the WORKTREE_NUM for a worktree.

    The number is unique per worktree: the NATO name's index, 0-25, 26 for
    ``_main``, and ``100 + N`` for the Build worktree ``_buildN``.

    Args:
        worktree_name: A full NATO worktree name, ``_main``, or ``_buildN``.

    Returns:
        The worktree number.

    Raises:
        ValueError: If worktree_name is neither a NATO name nor ``_main``.
    """
    if worktree_name == MAIN_WORKTREE_FOLDER:
        return len(WORKTREE_NAMES)
    if is_build_worktree(worktree_name):
        return 100 + int(worktree_name[len(BUILD_WORKTREE_PREFIX) :])
    return WORKTREE_NAMES.index(worktree_name)


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

# Where the working history of an uncommitted branch is kept. `uncommit_branch`
# writes one ref under here per run and `delete_branch` prunes them with the
# branch. One constant, so the two agree.
HISTORY_REF_PREFIX = "refs/mael/history/"


def history_ref_prefix(branch: str) -> str:
    """The ref namespace holding every working history of ``branch``."""
    return f"{HISTORY_REF_PREFIX}{branch}/"


def history_ref(branch: str, stamp: str) -> str:
    """The ref holding one working history of ``branch``, taken at ``stamp``.

    ``stamp`` is a UTC time rather than a sequence number, so a second run on the
    same branch keeps the first run's chronology and nothing has to count what is
    already there.
    """
    return f"{history_ref_prefix(branch)}{stamp}"


# Where a trashed branch goes: `<b>` becomes `trash/<b>`, locally and on origin.
TRASH_PREFIX = "trash/"


def trash_name(branch: str) -> str:
    """The name ``branch`` takes when it is trashed."""
    return f"{TRASH_PREFIX}{branch}"


# How much of a branch a collapse takes in. ``remote`` is every commit ahead of
# the fork point from main — the whole branch. ``local`` is only ``origin/<branch>..HEAD``,
# the part that was never pushed, so a re-review reads the new work alone.
SquashScope = Literal["remote", "local"]

# The subject every squashed commit gets. Fixed rather than derived: `/present`
# re-cuts it away, so no human ever reads it.
SQUASH_MESSAGE = "wip: squashed for review"


@dataclass(frozen=True)
class CollapsedCommit:
    """One commit a squash or an uncommit took in."""

    sha: str
    """Its SHA. The history ref keeps it reachable after the collapse."""
    message: str
    """Its full message."""


def describe_commits(commits: Sequence[CollapsedCommit]) -> str:
    """List commits as a bullet each: short SHA and subject, the body indented beneath.

    The squashed commit's body is this list, so the build commits' reasons
    survive the squash, and ``uncommit-branch`` prints it for ``/present`` to
    read. The SHA lets a reader inspect the commit itself. A message that is
    itself such a list nests one level deeper.
    """
    items = []
    for commit in commits:
        subject, *body = commit.message.strip().split("\n")
        lines = [f"- {commit.sha[:12]} {subject}"]
        lines += [f"  {line}" if line else "" for line in body]
        items.append("\n".join(lines))
    return "\n".join(items)


@dataclass(frozen=True)
class SquashResult:
    """What ``squash_branch`` did: what it collapsed, and into what."""

    history_ref: str
    """The ref now holding the chronological commits that were collapsed."""
    commits: int
    """How many commits were collapsed."""
    stat: str
    """``git diff --stat`` of what the squashed commit holds."""
    scope: SquashScope
    """Which part of the branch was collapsed."""
    sha: str
    """The squashed commit."""
    collapse_point: str
    """The commit the squash rewound to, after the rebase moved it.

    ``uncommit_branch`` resets here rather than to ``HEAD^``, which does not
    resolve when the squashed commit is a branch's first. Empty for that root
    case, where there is no parent to name."""
    collapsed: tuple[CollapsedCommit, ...] = ()
    """Each collapsed commit, oldest first."""


@dataclass(frozen=True)
class UncommitResult:
    """What ``uncommit_branch`` did: where it reset to, and what it kept."""

    history_ref: str
    """The ref now holding the chronological commits that were collapsed."""
    commits: int
    """How many commits were collapsed."""
    stat: str
    """``git diff --stat`` of what is now unstaged in the working tree."""
    scope: SquashScope = "remote"
    """Which part of the branch was returned to the working tree."""
    collapsed: tuple[CollapsedCommit, ...] = ()
    """Each uncommitted commit, oldest first."""


# Printed when an autorepair session resolved the conflicts. Every command that
# takes --autorepair reports it: a repaired tree holds commits an agent
# rewrote, and it must never read as a clean rebase.
REPAIRED_MESSAGE = "Rebase conflicts resolved by a headless Claude session."


def print_flushed(line: str) -> None:
    """Default ``announce`` for the model layer: print a line and flush it.

    A streamed subprocess writes straight to the shared stdout, so an unflushed
    line would land after the output it introduces. CLI callers pass
    ``click.echo`` instead, which keeps the model layer click-free.
    """
    print(line, flush=True)


# Folder holding the main branch, beside the NATO worktrees. The leading
# underscore keeps it out of the `<project>-<nato>` pattern. It is a real
# worktree — see `is_worktree_closable` and `docs/guide/worktrees.md`.
MAIN_WORKTREE_FOLDER = "_main"

#: A Build worktree's folder is this prefix and its slot number: ``_build1``.
#: The Build queue owns it; it is not in the world. See ``CONTEXT.md``, "Build worktree".
BUILD_WORKTREE_PREFIX = "_build"


def is_build_worktree(name: str) -> bool:
    """True for a Build worktree's name or folder: ``_build1`` … ``_buildN``."""
    rest = name[len(BUILD_WORKTREE_PREFIX) :]
    return name.startswith(BUILD_WORKTREE_PREFIX) and rest.isdigit()


def is_worktree_closable(worktree_name: str) -> bool:
    """True if the worktree may be closed, recycled or removed.

    ``_main`` holds the project's `main` checkout, and its port allocation is
    reserved rather than pooled. A Build worktree belongs to the Build queue.
    Every site that frees an allocation or reuses a folder gates on this.
    """
    return worktree_name != MAIN_WORKTREE_FOLDER and not is_build_worktree(
        worktree_name
    )


class WorktreeError(Exception):
    """Base for the worktree states that neither builtin domain error describes.

    One base so a CLI catches the whole family by name instead of listing every
    subclass. Catch a subclass where the handler treats that case differently;
    catch this where it does not.
    """


class UnclosableWorktreeError(WorktreeError):
    """Raised when a caller asks to remove the unclosable worktree (``_main``)."""


class WorktreeNamesExhaustedError(WorktreeError):
    """Raised when all 26 NATO worktree names in a project are taken."""


class WorktreeSetupError(WorktreeError):
    """Raised when a git command that builds a worktree fails."""


class PrePushFailed(WorktreeError):
    """The project's ``pre_push_cmd`` exited non-zero, so nothing was pushed."""

    def __init__(self, cmd: str, returncode: int) -> None:
        super().__init__(cmd, returncode)
        self.cmd = cmd
        self.returncode = returncode

    def __str__(self) -> str:
        return (
            f"Pre-push check `{self.cmd}` failed (exit {self.returncode}); "
            "nothing was pushed."
        )


def sanitize_branch_name(branch: str) -> str:
    """Convert branch name to directory-safe name (slashes → dashes)."""
    return branch.replace("/", "-")


def get_worktree_folder_name(project_name: str, worktree_name: str) -> str:
    """Get the folder name for a worktree.

    Args:
        project_name: The project name (e.g., 'askastro').
        worktree_name: The NATO phonetic worktree name (e.g., 'alpha'), or
            ``_main``.

    Returns:
        The folder name (e.g., 'askastro-alpha'). ``_main`` and a Build
        worktree map to themselves — they take no project prefix.
    """
    if worktree_name == MAIN_WORKTREE_FOLDER or is_build_worktree(worktree_name):
        return worktree_name
    return f"{project_name}-{worktree_name}"


def extract_worktree_name_from_folder(
    project_name: str, folder_name: str
) -> str | None:
    """Extract the worktree name from a folder name.

    Args:
        project_name: The project name (e.g., 'askastro').
        folder_name: The folder name (e.g., 'askastro-alpha').

    Returns:
        The worktree name (e.g., 'alpha') or None if not a valid worktree folder.
        The ``_main`` folder yields ``'_main'`` — it is a real worktree, and it
        carries no project prefix. A Build worktree folder yields itself too.
        Use :func:`is_worktree_closable` to tell either apart from a NATO
        worktree.
    """
    if folder_name == MAIN_WORKTREE_FOLDER or is_build_worktree(folder_name):
        return folder_name
    prefix = f"{project_name}-"
    if folder_name.startswith(prefix):
        potential_name = folder_name[len(prefix) :]
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


def render_copy_back_block(added: dict[str, str]) -> str:
    """Render new keys as ``KEY=value`` lines to append to the parent ``.env``."""
    lines = [f"{key}={value}" for key, value in added.items()]
    return "\n".join(lines) + "\n"


_VAR_PATTERN = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_SOURCE_PATTERN = re.compile(r"  # source: \[(.+)\]$")


def substitute_vars(text: str, generated_vars: dict[str, str]) -> str:
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

    resolved = substitute_vars(template, generated_vars)

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


def resolve_template_lines(text: str, generated_vars: dict[str, str]) -> str:
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


def render_managed_section(generated_vars: dict[str, str]) -> str:
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


def apply_preserved_values(text: str, preserved: dict[str, str]) -> str:
    """Return ``text`` with each key in *preserved* set to its preserved value.

    A blank-sentinel key is one the parent ``.env`` declares as ``KEY=``, marking
    a value the worktree owns. A regenerated worktree ``.env`` drops that line
    (see :func:`resolve_template_lines`), so each preserved key is normally
    missing and is appended as ``KEY=value``. A legacy ``.env`` that still holds
    a literal ``KEY=`` line has it rewritten in place instead, so no duplicate is
    produced. Every assignment of a preserved key is rewritten, not just the
    first: a dotenv reader takes the last one, so a stale later line would win.

    Pure: it takes the file's text and returns the text to write. The adapter
    owns the locked read and write.

    Args:
        text: The current ``.env`` contents.
        preserved: ``key -> value`` pairs to re-assert.

    Returns:
        The new contents, always newline-terminated. Empty ``preserved``
        returns ``text`` unchanged.
    """
    if not preserved:
        return text

    out: list[str] = []
    rewritten: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in preserved:
                out.append(f"{key}={preserved[key]}")
                rewritten.add(key)
                continue
        out.append(line)

    for key, value in preserved.items():
        if key not in rewritten:
            out.append(f"{key}={value}")
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class RebaseCommand:
    """A ``git rebase`` invocation."""

    argv: list[str]
    """The full argv, ``git`` included."""
    env: dict[str, str] | None = None
    """Environment overrides, or ``None`` when the rebase needs none."""


ORIGIN_MAIN = f"origin/{MAIN_BRANCH}"

# --autosquash makes git open the rebase todo in an editor. Stubbing both editors
# out with `true` applies the generated todo verbatim, unattended.
NON_INTERACTIVE_REBASE_ENV = {"GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}


def plan_squash_rebase(*, squash: bool) -> RebaseCommand:
    """Build the rebase command that lands the branch on ``origin/main``.

    Args:
        squash: Autosquash ``fixup!`` commits into their targets.

    Returns:
        The :class:`RebaseCommand` to run.
    """
    if not squash:
        return RebaseCommand(argv=["git", "rebase", "--autostash", ORIGIN_MAIN])
    return RebaseCommand(
        argv=["git", "rebase", "--autosquash", "--autostash", ORIGIN_MAIN],
        env=dict(NON_INTERACTIVE_REBASE_ENV),
    )


def describe_branch_deletion(branch: str, *, remote_deleted: bool) -> str:
    """The message for a branch that was empty and got deleted."""
    scope = "local + remote" if remote_deleted else "local"
    return (
        f"{branch} is empty (merged into {ORIGIN_MAIN}); "
        f"deleted branch ({scope}) and closed worktree."
    )
