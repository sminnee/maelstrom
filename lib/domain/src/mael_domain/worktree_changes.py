"""A worktree's changes: its dirty files, its branch's commits, and their diffs.

The pass-through reads behind the orchestrator UI's Changes tab. The revs are
in ``docs/dev/orchestrator-server.md``, "A worktree's changes".
"""

import asyncio
import re
import subprocess
from pathlib import Path

from .base_store import GitConfigBaseStore
from .protocol import (
    BranchCommit,
    ChangedFile,
    DiffHunk,
    DiffLine,
    FileDiff,
    WorktreeChanges,
)
from .worktree import get_current_branch, run_git_async
from .worktree_model import MAELSTROM_MANAGED_FILES, MAIN_BRANCH

UNCOMMITTED = "uncommitted"
BRANCH = "branch"

#: Lines kept per file. A lockfile past this is marked truncated rather than
#: sent whole, so one generated file cannot freeze the panel.
MAX_LINES_PER_FILE = 5000

# Diff output in one shape whatever the user's config says: no colour, no
# external diff or textconv, the a/ b/ prefixes the parser reads, and paths
# relative to the repository root, unquoted.
_DIFF_FLAGS = [
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-relative",
    "--find-renames",
    "--src-prefix=a/",
    "--dst-prefix=b/",
]
# --no-optional-locks: a read must not take index.lock from under an agent's
# commit in the same worktree.
_GIT_OPTIONS = ["--no-optional-locks", "-c", "core.quotepath=off"]

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILES_CHANGED = re.compile(r"(\d+) files? changed")
_FIELD = "\x1f"
_RECORD = "\x1e"


class UnknownRev(LookupError):
    """A rev that is none of ``uncommitted``, ``branch`` or a commit on the branch."""


def base_refs_for_diff(cwd: Path) -> list[str]:
    """Refs to diff a branch against, best first.

    A stacked branch diffs against its base, so a reader sees only this branch's
    own work rather than the whole stack. ``origin/main`` follows as a fallback for
    a base that has merged and been pruned.
    """
    base = resolve_base_branch(cwd)
    refs = [f"origin/{base}"]
    if base != MAIN_BRANCH:
        refs.append(f"origin/{MAIN_BRANCH}")
    return refs


def resolve_base_branch(cwd: Path) -> str:
    """The branch ``cwd``'s work is stacked on, or ``main`` if it is not stacked.

    Never raises: a worktree whose branch or config cannot be read falls back to
    ``main``, which is what every branch used before stacking existed.
    """
    try:
        return GitConfigBaseStore(cwd).read(get_current_branch(cwd)).branch
    except (OSError, subprocess.SubprocessError):
        return MAIN_BRANCH


async def list_changes(path: Path) -> WorktreeChanges:
    """The dirty files, the base, and the commits ahead of it, oldest first."""
    base, merge_base = await _merge_base(path)
    return {
        "dirtyFiles": await _dirty_files(path),
        "base": base,
        "commits": await _commits(path, merge_base) if merge_base else [],
    }


async def read_diff(path: Path, rev: str) -> list[FileDiff]:
    """The diff ``rev`` names, file by file. Raises :class:`UnknownRev`."""
    if rev == UNCOMMITTED:
        return await _uncommitted_diff(path)
    _, merge_base = await _merge_base(path)
    if rev == BRANCH:
        return await _diff(path, [merge_base, "HEAD"]) if merge_base else []
    commits = await _commits(path, merge_base) if merge_base else []
    if not any(c["sha"] == rev for c in commits):
        raise UnknownRev(rev)
    return await _diff(path, [f"{rev}^", rev])


async def _merge_base(path: Path) -> tuple[str, str]:
    """The base the branch is measured against, and its merge-base with ``HEAD``.

    The merge-base is ``""`` when no base ref resolves, as in a repo with no
    remote; the branch then reads as having no commits of its own.
    """
    # The base lives in git config, read by a blocking store; a thread keeps
    # it off the server's loop.
    refs = await asyncio.to_thread(base_refs_for_diff, path)
    for ref in refs:
        result = await run_git_async(
            [*_GIT_OPTIONS, "merge-base", "HEAD", ref],
            cwd=path,
            quiet=True,
            check=False,
        )
        if result.returncode == 0:
            return ref.removeprefix("origin/"), result.stdout.strip()
    return refs[-1].removeprefix("origin/"), ""


async def _dirty_files(path: Path) -> list[ChangedFile]:
    result = await run_git_async(
        [*_GIT_OPTIONS, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=path,
        quiet=True,
    )
    files: list[ChangedFile] = []
    entries = iter(result.stdout.split("\0"))
    for entry in entries:
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            # -z writes a rename's old path as the next entry.
            next(entries, None)
        if name in MAELSTROM_MANAGED_FILES:
            continue
        status = "?" if code == "??" else (code[0] if code[0] != " " else code[1])
        files.append({"path": name, "status": status})
    return files


async def _commits(path: Path, merge_base: str) -> list[BranchCommit]:
    result = await run_git_async(
        [
            *_GIT_OPTIONS,
            "log",
            "--reverse",
            # The body ends in its own separator, because it holds newlines
            # and the --shortstat line follows it.
            f"--format={_RECORD}%H{_FIELD}%h{_FIELD}%s{_FIELD}%an{_FIELD}%aI{_FIELD}%b{_FIELD}",
            "--shortstat",
            f"{merge_base}..HEAD",
        ],
        cwd=path,
        quiet=True,
    )
    commits: list[BranchCommit] = []
    for record in result.stdout.split(_RECORD)[1:]:
        # A body may hold the field separator itself; it is rejoined.
        sha, short, subject, author, date, *body, stat = record.split(_FIELD)
        found = _FILES_CHANGED.search(stat)
        commits.append(
            {
                "sha": sha,
                "shortSha": short,
                "subject": subject,
                "body": _FIELD.join(body).strip(),
                "author": author,
                "date": date,
                "filesChanged": int(found.group(1)) if found else 0,
            }
        )
    return commits


async def _uncommitted_diff(path: Path) -> list[FileDiff]:
    """The working tree against ``HEAD``, plus each untracked file as an addition."""
    files = await _diff(path, ["HEAD"])
    untracked = await run_git_async(
        [*_GIT_OPTIONS, "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=path,
        quiet=True,
    )
    for name in untracked.stdout.split("\0"):
        if name:
            # --no-index exits 1 when the files differ, which is every time.
            files += await _diff(path, ["--no-index", "--", "/dev/null", name])
    return [f for f in files if f["path"] not in MAELSTROM_MANAGED_FILES]


async def _diff(path: Path, args: list[str]) -> list[FileDiff]:
    result = await run_git_async(
        [*_GIT_OPTIONS, "diff", *_DIFF_FLAGS, *args],
        cwd=path,
        quiet=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    return parse_diff(result.stdout)


def parse_diff(text: str) -> list[FileDiff]:
    """``git diff`` output as files, hunks and numbered lines."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: DiffHunk | None = None
    kept = 0
    old_line = new_line = 0

    for raw in text.split("\n"):
        if raw.startswith("diff --git "):
            current = _new_file(raw.removeprefix("diff --git "))
            files.append(current)
            hunk, kept = None, 0
            continue
        if current is None:
            continue
        if raw.startswith("@@"):
            found = _HUNK_HEADER.match(raw)
            if found is None:
                continue
            old_line, new_line = int(found.group(1)), int(found.group(2))
            hunk = {"header": raw, "lines": []}
            if kept < MAX_LINES_PER_FILE:
                current["hunks"].append(hunk)
            else:
                current["truncated"] = True
            continue
        if hunk is None:
            _read_header_line(current, raw)
            continue

        marker, body = raw[:1], raw[1:]
        line: DiffLine
        if marker == "+":
            current["additions"] += 1
            line = {"kind": "add", "text": body, "oldLine": None, "newLine": new_line}
            new_line += 1
        elif marker == "-":
            current["deletions"] += 1
            line = {
                "kind": "remove",
                "text": body,
                "oldLine": old_line,
                "newLine": None,
            }
            old_line += 1
        elif marker == " ":
            line = {
                "kind": "context",
                "text": body,
                "oldLine": old_line,
                "newLine": new_line,
            }
            old_line += 1
            new_line += 1
        else:
            # "\ No newline at end of file", or the empty string after the last line.
            continue
        if kept < MAX_LINES_PER_FILE:
            hunk["lines"].append(line)
            kept += 1
        else:
            current["truncated"] = True
    return files


def _new_file(paths: str) -> FileDiff:
    """A file from its ``diff --git a/<old> b/<new>`` line.

    The two halves are ambiguous when a path holds `` b/``, so this is a first
    guess: the ``+++`` and ``rename`` lines that follow correct it.
    """
    old, new = paths, paths
    length = (len(paths) - 5) // 2
    if paths[2 : 2 + length] == paths[5 + length :]:
        old = new = paths[2 : 2 + length]
    elif " b/" in paths:
        left, _, right = paths.partition(" b/")
        old, new = left.removeprefix("a/"), right
    return {
        "path": new,
        "oldPath": None if old == new else old,
        "status": "modified",
        "binary": False,
        "additions": 0,
        "deletions": 0,
        "truncated": False,
        "hunks": [],
    }


def _read_header_line(file: FileDiff, line: str) -> None:
    """Apply one extended header line, between ``diff --git`` and the first hunk."""
    if line.startswith("new file mode"):
        file["status"] = "added"
    elif line.startswith("deleted file mode"):
        file["status"] = "deleted"
    elif line.startswith("rename from "):
        file["status"] = "renamed"
        file["oldPath"] = line.removeprefix("rename from ")
    elif line.startswith("rename to "):
        file["path"] = line.removeprefix("rename to ")
    elif line.startswith("Binary files ") or line == "GIT binary patch":
        file["binary"] = True
    elif line.startswith("+++ b/"):
        # git ends the line with a tab when the path holds a space.
        file["path"] = line.removeprefix("+++ b/").removesuffix("\t")
