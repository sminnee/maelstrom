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


def _sanitise_path_for_claude(path: Path) -> str:
    """Convert a filesystem path to Claude Code's sanitised project directory name.

    Claude Code stores per-project data in ~/.claude/projects/<sanitised>/
    where the sanitised name is the absolute path with '/' replaced by '-'.

    Args:
        path: Absolute path to sanitise.

    Returns:
        Sanitised path string (e.g., '-Users-sminnee-Projects-foo').
    """
    return str(path.resolve()).replace("/", "-")


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
