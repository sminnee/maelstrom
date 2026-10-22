"""A worktree's changes: its dirty files, its branch's commits, and their diffs.

The pass-through reads behind the orchestrator UI's Changes tab. The revs are
in ``docs/dev/orchestrator-server.md``, "A worktree's changes".
"""

import asyncio
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Literal, NamedTuple

from .base_store import GitConfigBaseStore
from .git_status import read_dirty_files_async
from .protocol import (
    BranchCommit,
    ChangeComment,
    ChangedFile,
    DiffHunk,
    DiffLine,
    FileDiff,
    FileVersions,
    WorktreeChanges,
)
from .worktree import get_current_branch, run_git_async
from .worktree_model import MAELSTROM_MANAGED_FILES, MAIN_BRANCH

UNCOMMITTED = "uncommitted"
BRANCH = "branch"

#: Lines kept per file. A lockfile past this is marked truncated rather than
#: sent whole, so one generated file cannot freeze the panel.
MAX_LINES_PER_FILE = 5000

#: Bytes kept per side of a file's versions. A side past this is dropped and
#: marked truncated, as a diff past :data:`MAX_LINES_PER_FILE` is.
MAX_FILE_BYTES = 1_000_000

#: Bytes served per side of a raw read. A side past this is refused, not cut:
#: half an image does not draw.
MAX_RAW_BYTES = 20_000_000

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

#: Untracked files diffed at once, so a tree of new files does not fork
#: one git per file all together.
_UNTRACKED_DIFFS_AT_ONCE = 4

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FILES_CHANGED = re.compile(r"(\d+) files? changed")
_FIELD = "\x1f"
_RECORD = "\x1e"


class UnknownRev(LookupError):
    """A rev that is none of ``uncommitted``, ``branch`` or a commit on the branch."""


class BadPath(ValueError):
    """A path that is absolute or climbs out of the worktree."""


class FileTooLarge(ValueError):
    """A side past :data:`MAX_RAW_BYTES`."""


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


async def read_file_versions(
    path: Path, rev: str, file: str, old_file: str | None = None
) -> FileVersions:
    """``file`` before and after ``rev``, read whole.

    ``old_file`` names the old side of a rename. Raises :class:`UnknownRev` and
    :class:`BadPath`.
    """
    old_file = old_file or file
    _check_paths(file, old_file)
    refs = await _refs(path, rev)
    old = await _blob(path, f"{refs.old}:{old_file}")
    if refs.new is None:
        new = await asyncio.to_thread(_worktree_file, path, file)
    else:
        new = await _blob(path, f"{refs.new}:{file}")
    return {
        "rev": rev,
        "path": file,
        "old": old.text,
        "new": new.text,
        "truncated": old.too_large or new.too_large,
    }


async def read_file_bytes(
    path: Path,
    rev: str,
    file: str,
    side: Literal["old", "new"],
    old_file: str | None = None,
) -> bytes | None:
    """One side of ``file`` at ``rev``, as raw bytes; ``None`` when the side is missing.

    ``side`` is ``old`` or ``new``; ``old_file`` names the old side of a
    rename. Raises :class:`UnknownRev`, :class:`BadPath` and
    :class:`FileTooLarge`.
    """
    old_file = old_file or file
    _check_paths(file, old_file)
    refs = await _refs(path, rev)
    if side == "old":
        return await _blob_bytes(path, f"{refs.old}:{old_file}")
    if refs.new is None:
        return await asyncio.to_thread(_worktree_bytes, path, file)
    return await _blob_bytes(path, f"{refs.new}:{file}")


def _check_paths(*names: str) -> None:
    for name in names:
        parts = PurePosixPath(name).parts
        if not parts or PurePosixPath(name).is_absolute() or ".." in parts:
            raise BadPath(name)


class _Refs(NamedTuple):
    """The refs a rev reads each side at. ``new`` is ``None`` for the worktree."""

    old: str
    new: str | None


async def _refs(path: Path, rev: str) -> _Refs:
    if rev == UNCOMMITTED:
        return _Refs("HEAD", None)
    _, merge_base = await _merge_base(path)
    if rev == BRANCH:
        # No base: the branch has no commits of its own, so both sides are HEAD.
        return _Refs(merge_base or "HEAD", "HEAD")
    commits = await _commits(path, merge_base) if merge_base else []
    if not any(c["sha"] == rev for c in commits):
        raise UnknownRev(rev)
    return _Refs(f"{rev}^", rev)


async def _blob_bytes(path: Path, spec: str) -> bytes | None:
    size = await run_git_async(
        [*_GIT_OPTIONS, "cat-file", "-s", spec], cwd=path, quiet=True, check=False
    )
    if size.returncode != 0:
        return None
    if int(size.stdout) > MAX_RAW_BYTES:
        raise FileTooLarge(spec)
    result = await run_git_async(
        [*_GIT_OPTIONS, "cat-file", "blob", spec],
        cwd=path,
        quiet=True,
        check=False,
        text=False,
    )
    # A tree has a size too, but no blob to read: a path that names a folder.
    return result.stdout if result.returncode == 0 else None


def _worktree_bytes(root: Path, file: str) -> bytes | None:
    target = root / file
    try:
        # A link is not followed: its target may sit outside the worktree.
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            return None
        if target.stat().st_size > MAX_RAW_BYTES:
            raise FileTooLarge(file)
        return target.read_bytes()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None


class _Side(NamedTuple):
    """One side of a file: its text, or ``None`` when missing, binary or too large."""

    text: str | None = None
    too_large: bool = False


async def _blob(path: Path, spec: str) -> _Side:
    """The blob ``<ref>:<path>`` names. The size is read first, so a large blob is never read."""
    size = await run_git_async(
        [*_GIT_OPTIONS, "cat-file", "-s", spec], cwd=path, quiet=True, check=False
    )
    if size.returncode != 0:
        return _Side()
    if int(size.stdout) > MAX_FILE_BYTES:
        return _Side(too_large=True)
    result = await run_git_async(
        [*_GIT_OPTIONS, "cat-file", "blob", spec], cwd=path, quiet=True
    )
    return _text(result.stdout)


def _worktree_file(root: Path, file: str) -> _Side:
    target = root / file
    try:
        if target.is_symlink():
            # git stores a link as its target path, so the view shows the same.
            return _Side(os.readlink(target))
        if not target.resolve().is_relative_to(root.resolve()):
            return _Side()
        if target.stat().st_size > MAX_FILE_BYTES:
            return _Side(too_large=True)
        return _text(target.read_bytes().decode("utf-8", "replace"))
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return _Side()


def _text(text: str) -> _Side:
    return _Side() if "\0" in text else _Side(text)


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
    return [
        {
            "path": f.path,
            "status": "?" if f.untracked else (f.index if f.staged else f.worktree),
        }
        for f in await read_dirty_files_async(path)
    ]


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
    names = [name for name in untracked.stdout.split("\0") if name]
    limit = asyncio.Semaphore(_UNTRACKED_DIFFS_AT_ONCE)

    async def added(name: str) -> list[FileDiff]:
        async with limit:
            # --no-index exits 1 when the files differ, which is every time.
            return await _diff(path, ["--no-index", "--", "/dev/null", name])

    # The tasks keep the ls-files order whatever order the diffs finish in.
    # A task group stops the other diffs when one fails.
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(added(name)) for name in names]
    for task in tasks:
        files += task.result()
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
    # A large diff takes seconds to parse; a thread keeps it off the server's loop.
    return await asyncio.to_thread(parse_diff, result.stdout)


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


def format_change_comments(comments: list[ChangeComment]) -> str:
    """The body of the one event that carries ``comments`` to the agents of a worktree.

    Each comment quotes the rows the user selected, so it stays readable when
    the lines have moved since.
    """
    blocks: list[str] = []
    for comment in comments:
        start, end = comment["startLine"], comment["endLine"]
        span = f"line {start}" if start == end else f"lines {start}-{end}"
        if comment["side"] == "old":
            span = f"old {span}"
        quoted = [f"> {line}" for line in comment["lines"]]
        blocks.append(
            "\n".join(
                [
                    f"{comment['path']} {span} ({_rev_label(comment['rev'])}):",
                    *quoted,
                    # A line right after a quote continues it in markdown.
                    "",
                    comment["body"].strip(),
                ]
            )
        )
    return "\n\n".join(blocks)


def _rev_label(rev: str) -> str:
    return rev if rev in (UNCOMMITTED, BRANCH) else f"commit {rev[:7]}"
