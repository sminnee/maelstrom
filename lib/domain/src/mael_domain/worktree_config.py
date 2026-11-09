"""The **Worktree config**: the files maelstrom writes into a worktree.

Some are generated and gitignored: ``.claude/CLAUDE.local.md``, ``AGENTS.md``
and the ``.claude/settings.local.json`` symlink. The rest are edits to tracked
files: ignore entries in ``.gitignore``, the import in ``CLAUDE.md``, and the
draft write rules in ``.claude/settings.json``.

:func:`apply_worktree_config` writes all of it, then commits the tracked edits,
so a worktree does not start dirty. Each writer reports the tracked paths it
changed, and the commit takes exactly those.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mael_common.shell import run_cmd

from .git_status import read_dirty_files
from .shared_dir import get_shared_dir
from .task import DRAFT_WRITE_RULES, DRAFTS_DIR
from .worktree_model import MAIN_BRANCH

CLAUDE_LOCAL_IMPORT = "@.claude/CLAUDE.local.md"
CONFIG_COMMIT_MESSAGE = "chore: maelstrom config"

IMPORT_PATTERN = re.compile(r"^@(\S+)\s*$", re.MULTILINE)
MAX_IMPORT_DEPTH = 3

_GITIGNORE = ".gitignore"
_CLAUDE_MD = "CLAUDE.md"
_SETTINGS_JSON = ".claude/settings.json"


@dataclass(frozen=True)
class ConfigWrite:
    """What :func:`apply_worktree_config` did."""

    local_md_written: bool  # False when the header template is missing
    committed: bool


def apply_worktree_config(
    project_path: Path, worktree_path: Path, worktree_name: str
) -> ConfigWrite:
    """Write the worktree config, and commit the tracked files it changed.

    The commit is ``chore: maelstrom config``. It is made only on a named
    branch other than ``main``: on ``main`` or a detached HEAD it would be
    local-only and ahead of origin, so those worktrees keep the edits
    uncommitted. A file that was already dirty is left out, so a user's own
    edit is never taken.

    Args:
        project_path: Path to the project root (bare repo).
        worktree_path: Path to the worktree directory.
        worktree_name: NATO phonetic name of the worktree (e.g., "alpha").
    """
    dirty_before = _dirty_paths(worktree_path)

    changed: set[str] = set()
    local_md_written = _write_claude_local_md(
        project_path, worktree_path, worktree_name
    )
    if local_md_written:
        changed |= _ensure_claude_md_import(worktree_path)
        changed |= _ensure_gitignore_entry(worktree_path, ".claude/CLAUDE.local.md")
    # Drafts are scratch — an abandoned one must not reach a commit.
    changed |= _ensure_gitignore_entry(worktree_path, DRAFTS_DIR)
    changed |= _ensure_draft_write_rules(worktree_path)
    changed |= _setup_claude_settings_symlink(worktree_path)
    # Last, so it picks up the local context just written.
    changed |= _write_agents_md(worktree_path)

    committed = _commit(worktree_path, changed - dirty_before)
    return ConfigWrite(local_md_written=local_md_written, committed=committed)


def _write_claude_local_md(
    project_path: Path, worktree_path: Path, worktree_name: str
) -> bool:
    """Write ``.claude/CLAUDE.local.md``: the workflow header and environment.

    Returns:
        True if the file was written, False if the header template is missing.
    """
    from .ports import get_app_url

    try:
        header_file = get_shared_dir() / "claude-header.md"
        if not header_file.exists():
            return False
        header_content = header_file.read_text().rstrip()
    except FileNotFoundError:
        return False

    env_lines = [
        "",
        "## Environment",
        "",
        f"The current working directory is {worktree_path}",
    ]
    app_url_result = get_app_url(project_path, worktree_name)
    if app_url_result is not None:
        url, _ = app_url_result
        env_lines.append("")
        env_lines.append(f"The app URL is {url}")

    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "CLAUDE.local.md").write_text(
        header_content + "\n" + "\n".join(env_lines) + "\n"
    )
    return True


def _ensure_claude_md_import(worktree_path: Path) -> set[str]:
    """Put the ``@.claude/CLAUDE.local.md`` import on CLAUDE.md's first line.

    A missing CLAUDE.md stays missing: the import only makes sense in a file
    the project already has.
    """
    claude_md = worktree_path / _CLAUDE_MD
    if not claude_md.exists():
        return set()
    content = claude_md.read_text()
    if CLAUDE_LOCAL_IMPORT in content:
        return set()
    claude_md.write_text(CLAUDE_LOCAL_IMPORT + "\n\n" + content)
    return {_CLAUDE_MD}


def _ensure_gitignore_entry(worktree_path: Path, entry: str) -> set[str]:
    """Append ``entry`` to .gitignore when it is absent. Create the file if needed."""
    gitignore = worktree_path / _GITIGNORE
    if gitignore.exists():
        content = gitignore.read_text()
        if entry in content.splitlines():
            return set()
        if content and not content.endswith("\n"):
            content += "\n"
        gitignore.write_text(content + entry + "\n")
    else:
        gitignore.write_text(entry + "\n")
    return {_GITIGNORE}


def _ensure_draft_write_rules(worktree_path: Path) -> set[str]:
    """Let an agent write `.drafts/` without asking.

    A planning session edits a draft many times, and each edit would otherwise
    raise a permission ask. The rules are added, never removed: the file
    belongs to the project, so anything already in it survives. A file that is
    not valid JSON is left alone rather than overwritten.
    """
    claude_dir = worktree_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = worktree_path / _SETTINGS_JSON
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (OSError, json.JSONDecodeError):
            return set()
        if not isinstance(settings, dict):
            return set()
    else:
        settings = {}

    permissions = settings.setdefault("permissions", {})
    allow = permissions.setdefault("allow", [])
    if not isinstance(allow, list):
        return set()
    missing = [rule for rule in DRAFT_WRITE_RULES if rule not in allow]
    if not missing:
        return set()
    allow.extend(missing)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return {_SETTINGS_JSON}


def _setup_claude_settings_symlink(worktree_path: Path) -> set[str]:
    """Link .claude/settings.local.json to settings.json, and gitignore the link.

    Tool-use approvals saved to settings.local.json then land in the tracked
    settings.json, so every worktree has them. A real settings.local.json
    stays: it is the project's choice.
    """
    claude_dir = worktree_path / ".claude"
    settings_local = claude_dir / "settings.local.json"
    if not (claude_dir / "settings.json").exists():
        return set()
    if settings_local.exists() and not settings_local.is_symlink():
        print(
            "Warning: .claude/settings.local.json already exists and is not a symlink, skipping"
        )
        return set()
    if settings_local.is_symlink():
        settings_local.unlink()
    settings_local.symlink_to("settings.json")
    return _ensure_gitignore_entry(worktree_path, ".claude/settings.local.json")


def _inline_imports(text: str, worktree_path: Path, depth: int, seen: set[Path]) -> str:
    """Replace ``@path`` import lines with the referenced files' contents.

    Missing files are noted in an HTML comment rather than raising, matching
    Claude Code's tolerance of a dangling import (e.g. before the first
    ``mael add`` writes ``CLAUDE.local.md``).
    """

    def replace(match: re.Match[str]) -> str:
        rel = match.group(1)
        target = (worktree_path / rel).resolve()
        if not target.is_file():
            return f"<!-- @{rel}: not found -->"
        if target in seen or depth >= MAX_IMPORT_DEPTH:
            return f"<!-- @{rel}: skipped (nested too deeply or circular) -->"
        inlined = "<!-- from @{0} -->\n{1}".format(
            rel,
            _inline_imports(
                target.read_text(), worktree_path, depth + 1, seen | {target}
            ),
        )
        return inlined.rstrip("\n")

    return IMPORT_PATTERN.sub(replace, text)


def _write_agents_md(worktree_path: Path) -> set[str]:
    """Generate ``AGENTS.md`` from ``CLAUDE.md`` with ``@`` imports inlined.

    OpenCode reads only ``AGENTS.md`` and does not resolve Claude Code's
    ``@file`` imports, so the generated file inlines each import (recursively,
    depth-limited, cycle-safe). ``AGENTS.md`` is derived output: regenerated
    whenever the worktree's local context is rewritten, and gitignored.
    """
    claude_md = worktree_path / _CLAUDE_MD
    if not claude_md.exists():
        return set()
    (worktree_path / "AGENTS.md").write_text(
        _inline_imports(claude_md.read_text(), worktree_path, 0, set()).rstrip("\n")
        + "\n"
    )
    return _ensure_gitignore_entry(worktree_path, "AGENTS.md")


def _dirty_paths(worktree_path: Path) -> set[str]:
    return {f.path for f in read_dirty_files(worktree_path)}


def _current_branch(worktree_path: Path) -> str | None:
    """The checked-out branch, or None for a detached HEAD or an unreadable repo."""
    result = run_cmd(
        ["git", "symbolic-ref", "-q", "--short", "HEAD"],
        cwd=worktree_path,
        quiet=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _commit(worktree_path: Path, changed: set[str]) -> bool:
    """Commit ``changed`` as the config commit, by path, without hooks.

    Hooks are skipped: the commit is mechanical, and it runs before install,
    so a hook that needs the project's dependencies would fail. A path git
    does not report as dirty (a globally ignored file, say) is left out. A
    failed commit is a warning, not a failed setup.
    """
    if not changed:
        return False
    branch = _current_branch(worktree_path)
    if branch is None or branch == MAIN_BRANCH:
        return False
    paths = sorted(changed & _dirty_paths(worktree_path))
    if not paths:
        return False
    try:
        for args in (
            ["add", "--", *paths],
            ["commit", "--no-verify", "-m", CONFIG_COMMIT_MESSAGE, "--", *paths],
        ):
            run_cmd(["git", *args], cwd=worktree_path, quiet=True)
    except subprocess.CalledProcessError as e:
        print(
            f"Warning: Could not commit maelstrom config: {e.stderr or e}",
            file=sys.stderr,
        )
        return False
    return True
