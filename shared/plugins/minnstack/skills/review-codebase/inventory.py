#!/usr/bin/env python3
"""Split a repository into review sections for ``/review-codebase``.

Prints JSON: the units (one per package manifest), the sections of each unit,
the files left out, and the reviewer and batch counts. Standard library only,
because the skill runs it in projects that are not Python.

    inventory.py [TARGET] [--max-lines N] [--min-lines N] [--counter auto|cloc|lines] [--exclude GLOB]...
    inventory.py [TARGET] [--exclude GLOB]... --check LEDGER
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath

MANIFESTS = {
    "pyproject.toml",
    "setup.py",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "composer.json",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "mix.exs",
    "Package.swift",
}

# (reason, patterns). A pattern with no "/" matches the file name. A name and
# "/" matches a directory of that name at any depth. A path and "/" matches
# that directory. Any other pattern with a "/" matches the whole path.
# Tracked build output is rare, so "build/" is not here: it is usually source.
EXCLUDE = [
    (
        "lockfile",
        [
            "*.lock",
            "package-lock.json",
            "pnpm-lock.yaml",
            "go.sum",
            "npm-shrinkwrap.json",
        ],
    ),
    (
        "vendored or built",
        [
            "vendor/",
            "node_modules/",
            "third_party/",
            "dist/",
            "*.min.js",
            "*.min.css",
            "*.map",
        ],
    ),
    (
        "test data",
        ["fixtures/", "testdata/", "goldens/", "__snapshots__/", "*.snap", "*.golden"],
    ),
    ("docs", ["*.md", "*.mdx", "*.rst", "*.txt", "*.adoc"]),
    (
        "data or config",
        [
            "*.json",
            "*.jsonl",
            "*.yaml",
            "*.yml",
            "*.toml",
            "*.ini",
            "*.cfg",
            "*.csv",
            "*.svg",
            "*.xml",
            ".*",
        ],
    ),
]

TEST_DIRS = {"tests", "test", "__tests__", "spec", "e2e"}
TEST_NAMES = ["test_*", "*_test.*", "*.test.*", "*.spec.*", "conftest.py"]


class GitError(Exception):
    pass


def run(cmd: list[str], cwd: str, stdin: str | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True)
    if result.returncode:
        raise GitError(f"{' '.join(cmd[:2])}: {result.stderr.strip()}")
    return result.stdout


def git(root: str, *args: str) -> str:
    return run(["git", *args], root)


def exclusion(path: str, extra: list[str]) -> str | None:
    parts = PurePosixPath(path).parts
    for reason, patterns in [*EXCLUDE, ("--exclude", extra)]:
        for pattern in patterns:
            if pattern.endswith("/") and "/" in pattern[:-1]:
                if path.startswith(pattern):
                    return reason
            elif pattern.endswith("/"):
                if pattern[:-1] in parts[:-1]:
                    return reason
            elif "/" in pattern:
                if fnmatch(path, pattern):
                    return reason
            elif fnmatch(parts[-1], pattern):
                return reason
    return None


def is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return bool(TEST_DIRS & set(parts[:-1])) or any(
        fnmatch(parts[-1], p) for p in TEST_NAMES
    )


def count_lines(root: str, files: list[str]) -> dict[str, int]:
    """Non-blank lines per text file. A file with a NUL byte is binary, and absent."""
    counts = {}
    for path in files:
        with open(f"{root}/{path}", "rb") as f:
            data = f.read()
        if b"\0" not in data:
            counts[path] = sum(1 for line in data.splitlines() if line.strip())
    return counts


def count_cloc(root: str, files: list[str]) -> dict[str, int]:
    """Code lines per file, by cloc. A file cloc does not know is absent."""
    out = run(
        [
            "cloc",
            "--list-file=-",
            "--by-file",
            "--json",
            "--quiet",
            "--skip-uniqueness",
        ],
        root,
        stdin="\n".join(files),
    )
    data = json.loads(out) if out.strip() else {}
    return {
        path: info["code"]
        for path, info in data.items()
        if path not in ("header", "SUM")
    }


@dataclass
class Node:
    path: str
    files: dict[str, int] = field(default_factory=dict)
    dirs: dict[str, "Node"] = field(default_factory=dict)

    @property
    def lines(self) -> int:
        return sum(self.files.values()) + sum(d.lines for d in self.dirs.values())

    def all_files(self) -> list[str]:
        return [*self.files, *(f for d in self.dirs.values() for f in d.all_files())]


def build_tree(unit: str, files: dict[str, int]) -> Node:
    top = Node(unit)
    for path, lines in files.items():
        rel = (
            PurePosixPath(path)
            if unit == "."
            else PurePosixPath(path).relative_to(unit)
        )
        node = top
        for part in rel.parts[:-1]:
            child_path = part if node.path == "." else f"{node.path}/{part}"
            node = node.dirs.setdefault(part, Node(child_path))
        node.files[path] = lines
    return top


def collapse(node: Node) -> Node:
    """Descend while a directory holds one subdirectory and no files."""
    while not node.files and len(node.dirs) == 1:
        node = next(iter(node.dirs.values()))
    return node


Section = tuple[str, list[str], list[str]]  # name, paths, files


def split(node: Node, max_lines: int, min_lines: int) -> list[Section]:
    """Sections of at most max_lines, except a single large file."""
    node = collapse(node)
    if node.lines <= max_lines or (not node.dirs and len(node.files) == 1):
        return [(node.path, [node.path], node.all_files())]
    whole: list[tuple[list[str], list[str], int]] = []
    sections: list[Section] = []
    for child in node.dirs.values():
        child = collapse(child)
        if child.lines <= max_lines:
            whole.append(([child.path], child.all_files(), child.lines))
        else:
            sections.extend(split(child, max_lines, min_lines))
    for path, lines in sorted(node.files.items()):
        whole.append(([path], [path], lines))
    # Pack the small whole children together; a child of min_lines or more stands alone.
    sections.extend((p[0], p, f) for p, f, lines in whole if lines >= min_lines)
    pack: tuple[list[str], list[str], int] = ([], [], 0)
    for paths, files, lines in (w for w in whole if w[2] < min_lines):
        if pack[0] and pack[2] + lines > max_lines:
            sections.append((pack_name(node.path, pack[0]), *pack[:2]))
            pack = ([], [], 0)
        pack = (pack[0] + paths, pack[1] + files, pack[2] + lines)
    if pack[0]:
        sections.append((pack_name(node.path, pack[0]), *pack[:2]))
    return sections


def pack_name(parent: str, paths: list[str]) -> str:
    if len(paths) == 1:
        return paths[0]
    names = [p if parent == "." else p.removeprefix(parent + "/") for p in paths]
    joined = (
        ",".join(names)
        if len(names) <= 3
        else f"{names[0]},…,{names[-1]} ({len(names)})"
    )
    return f"{{{joined}}}" if parent == "." else f"{parent}/{{{joined}}}"


def dump(data: dict) -> None:
    json.dump(data, sys.stdout, indent=2, ensure_ascii=False)
    print()


def check(ledger: str, files: dict[str, int], unit_of) -> int:
    """Exit 1 when a counted file is under no path of a ledger checkbox line.

    A path covers only the files of its own unit, so a unit's root section
    does not cover the units inside it.
    """
    lines = [
        line
        for line in open(ledger).read().splitlines()
        if re.match(r"\s*- \[[ xX]\]", line)
    ]
    paths = [p.rstrip("/") for line in lines for p in re.findall(r"`([^`]+)`", line)]
    path_units = {p: unit_of(p, inclusive=True) for p in paths}

    def covered(path: str) -> bool:
        return any(
            (p == "." or path == p or path.startswith(p + "/"))
            and path_units[p] == unit_of(path)
            for p in paths
        )

    uncovered = sorted(f for f in files if not covered(f))
    dump(
        {
            "total_lines": sum(files.values()),
            "uncovered_lines": sum(files[f] for f in uncovered),
            "uncovered": uncovered,
        }
    )
    return 1 if uncovered else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Split a repository into review sections."
    )
    parser.add_argument("target", nargs="?", default=".")
    parser.add_argument("--max-lines", type=int, default=5000)
    parser.add_argument("--min-lines", type=int, default=2000)
    parser.add_argument("--counter", choices=["auto", "cloc", "lines"], default="auto")
    parser.add_argument(
        "--exclude", action="append", default=[], help="another pattern to leave out"
    )
    parser.add_argument(
        "--check", metavar="LEDGER", help="list the counted files no ledger path covers"
    )
    args = parser.parse_args(argv)
    try:
        return inventory(args)
    except GitError as e:
        print(f"inventory.py: {e}", file=sys.stderr)
        return 2


def inventory(args: argparse.Namespace) -> int:
    root = git(args.target, "rev-parse", "--show-toplevel").strip()
    prefix = git(args.target, "rev-parse", "--show-prefix").strip().rstrip("/") or "."
    listed = [p for p in git(root, "ls-files", "-z", "--", prefix).split("\0") if p]
    # A submodule is listed as a directory: it is another repository's code.
    listed = [p for p in listed if os.path.isfile(f"{root}/{p}")]

    excluded: Counter[str] = Counter()
    candidates = []
    for path in listed:
        reason = exclusion(path, args.exclude)
        if reason:
            excluded[reason] += 1
        else:
            candidates.append(path)

    counter = args.counter
    if counter == "auto":
        counter = "cloc" if shutil.which("cloc") else "lines"
    counts = (
        count_cloc(root, candidates)
        if counter == "cloc"
        else count_lines(root, candidates)
    )
    excluded["not code"] += len(candidates) - len(counts)
    excluded["empty"] += sum(1 for n in counts.values() if not n)
    files = {p: n for p, n in counts.items() if n}

    unit_paths = {prefix} | {
        str(PurePosixPath(p).parent)
        for p in listed
        if PurePosixPath(p).name in MANIFESTS
    }

    def unit_of(path: str, inclusive: bool = False) -> str | None:
        """The deepest unit above path, or at it when inclusive."""
        dirs = [*([path] if inclusive else []), *map(str, PurePosixPath(path).parents)]
        return next((u for u in dirs if u in unit_paths), None)

    if args.check:
        return check(args.check, files, unit_of)

    by_unit: dict[str, dict[str, int]] = {}
    for path, lines in files.items():
        by_unit.setdefault(unit_of(path) or prefix, {})[path] = lines

    log = git(root, "log", "--since=1.year", "--format=", "--name-only", "--", prefix)
    churn = Counter(line for line in log.splitlines() if line)

    sections = []
    for unit, unit_files in sorted(by_unit.items()):
        for name, paths, section_files in split(
            build_tree(unit, unit_files), args.max_lines, args.min_lines
        ):
            sections.append(
                {
                    "name": name,
                    "unit": unit,
                    "paths": paths,
                    "lines": sum(files[f] for f in section_files),
                    "files": len(section_files),
                    "churn": sum(churn[f] for f in section_files),
                    "tests_only": all(is_test(f) for f in section_files),
                }
            )
    sections.sort(key=lambda s: s["churn"] * s["lines"], reverse=True)

    dump(
        {
            "target": prefix,
            "counter": counter,
            "total_lines": sum(files.values()),
            "excluded_files": {k: n for k, n in excluded.items() if n},
            "units": [
                {"path": u, "lines": sum(f.values())}
                for u, f in sorted(by_unit.items())
            ],
            "sections": sections,
            # One reviewer per section, three at a time: SKILL.md step 3.
            "reviewers": len(sections),
            "batches": math.ceil(len(sections) / 3),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
