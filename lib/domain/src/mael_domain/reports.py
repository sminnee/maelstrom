"""Reports: scan output kept whole in the task notebook.

See CONTEXT.md, "Report", "Finding", "Finding key" and "Reference". This module
reads findings from a report's SARIF file each time, and mints and resolves
references. The task notebook stores each reference as an opaque string.
Writes go through a :class:`~mael_domain.task_store.TaskStore`, so a
``GitFileStore`` makes an import one notebook commit.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .task import Task
from .task_store import TaskStore

REPORTS_DIR = "reports"

#: Short slugs for known SARIF driver names, used in the report name. Any
#: other driver name is slugged whole.
_TOOL_SLUGS = {
    "claude security plugin for claude code": "claude-security",
    "owasp zap": "zap",
}

_SARIF_SUFFIXES = (".sarif", ".sarif.json")


@dataclass(frozen=True)
class Report:
    """One report directory: its name, and the files in it."""

    name: str
    files: tuple[str, ...]

    @property
    def path(self) -> str:
        """The report's path relative to the project's notebook directory."""
        return f"{REPORTS_DIR}/{self.name}"

    @property
    def sarif(self) -> str | None:
        """The report's SARIF file name, or ``None`` when it has none.

        A report with more than one SARIF file reads the first, by name.
        """
        return next((f for f in self.files if _is_sarif(f)), None)


@dataclass(frozen=True)
class Finding:
    """One SARIF result, flattened for a reader.

    ``label`` is the finding's name inside this one report: the Claude Security
    ``F<n>`` id, else ``F<position>``. ``key`` is stable across scans; see
    :func:`finding_key`. ``level`` is the SARIF level. ``detail`` holds the
    prose sections a tool gives, such as ``impact`` and ``recommendation``.
    """

    key: str
    label: str
    ref: str
    level: str
    rule: str
    cwe: str
    title: str
    file: str
    line: int | None
    symbol: str
    snippet: str
    detail: dict[str, str] = field(default_factory=dict)


def import_report(store: TaskStore, project: str, src: Path, *, now: datetime) -> str:
    """Copy a report file, or the top-level files of a directory, into the store.

    Returns the report path. The date comes from the SARIF run, else ``now``.
    A dotfile stays behind: a scan's own ``.gitignore`` would hide the report
    from the notebook commit.

    Raises:
        ValueError: ``src`` holds no SARIF file, the SARIF has no run, or a
            file is not UTF-8 text. The notebook store holds text only.
    """
    files = (
        sorted(p for p in src.iterdir() if p.is_file() and not p.name.startswith("."))
        if src.is_dir()
        else [src]
    )
    sarif_file = next((p for p in files if _is_sarif(p.name)), None)
    if sarif_file is None:
        raise ValueError(f"No SARIF file in {src}")
    texts = {}
    for f in files:
        try:
            texts[f.name] = f.read_text()
        except UnicodeDecodeError:
            raise ValueError(f"{f} is not UTF-8 text") from None
    run = _first_run(json.loads(texts[sarif_file.name]))
    stem = f"{_run_date(run, now)}-{_tool_slug(run)}"
    taken = {r.name for r in list_reports(store, project)}
    name, n = stem, 1
    while name in taken:
        n += 1
        name = f"{stem}-{n}"
    with store.transaction(message=f"report: import {project}/{name}"):
        for file_name, text in texts.items():
            store.write(f"{project}/{REPORTS_DIR}/{name}/{file_name}", text)
    return f"{REPORTS_DIR}/{name}"


def list_reports(store: TaskStore, project: str) -> list[Report]:
    """Every report of ``project``, sorted by name, which sorts them by date."""
    prefix = f"{project}/{REPORTS_DIR}/"
    by_name: dict[str, list[str]] = {}
    for key in store.list_dir(prefix):
        name, _, file = key[len(prefix) :].partition("/")
        if file and "/" not in file:
            by_name.setdefault(name, []).append(file)
    return [Report(n, tuple(sorted(f))) for n, f in sorted(by_name.items())]


def load_report(store: TaskStore, project: str, report: str) -> Report:
    """The report named ``report``: a name, or a ``reports/<name>`` path.

    Raises:
        ValueError: No such report.
    """
    name = report.removeprefix(f"{REPORTS_DIR}/").strip("/")
    found = next((r for r in list_reports(store, project) if r.name == name), None)
    if found is None:
        raise ValueError(f"No report {report} in {project}")
    return found


def read_findings(store: TaskStore, project: str, report: str) -> list[Finding]:
    """The findings of a SARIF report, in the order the SARIF lists them.

    Two results with the same key, such as one ZAP rule on two parameters of
    one URL, are told apart by a ``-<n>`` suffix in SARIF order.

    Raises:
        ValueError: No such report, or it is not a SARIF report.
    """
    found = load_report(store, project, report)
    if found.sarif is None:
        raise ValueError(f"Report {found.name} has no SARIF file")
    text = store.read(f"{project}/{found.path}/{found.sarif}")
    if text is None:
        raise ValueError(f"Report {found.name}: {found.sarif} is missing")
    run = _first_run(json.loads(text))
    tool = _tool_slug(run)
    rules = {r.get("id"): r for r in _get(run, "tool", "driver", "rules") or []}
    seen: dict[str, int] = {}
    findings = []
    for i, result in enumerate(run.get("results") or []):
        key = finding_key(result, tool)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}-{seen[key]}"
        findings.append(_finding(result, i, key, rules, f"{found.path}/{found.sarif}"))
    return findings


def find_finding(findings: list[Finding], label_or_key: str) -> Finding | None:
    """The finding with this label (``F3``) or key, or ``None``."""
    return next((f for f in findings if label_or_key in (f.label, f.key)), None)


def tasks_by_finding(tasks: list[Task]) -> dict[str, list[Task]]:
    """Each reference the tasks hold, and the tasks that hold it."""
    by_ref: dict[str, list[Task]] = {}
    for t in tasks:
        for ref in t.references:
            by_ref.setdefault(ref, []).append(t)
    return by_ref


def resolve_reference(store: TaskStore, project: str, ref: str) -> str:
    """The stored form of ``ref``: ``reports/<name>/<file>#<finding key>``.

    ``ref`` may name the report by name or path, and the finding by its label
    (``F3``) or its key.

    Raises:
        ValueError: No such report, or no such finding in it.
    """
    report, fragment = parse_reference(ref)
    if report.endswith(_SARIF_SUFFIXES):
        report = report.rsplit("/", 1)[0]
    findings = read_findings(store, project, report)
    found = find_finding(findings, fragment)
    if found is None:
        raise ValueError(f"No finding {fragment} in report {report}")
    return found.ref


def parse_reference(ref: str) -> tuple[str, str]:
    """Split a reference into its path and its fragment.

    Raises:
        ValueError: ``ref`` has no ``#<fragment>``.
    """
    path, sep, fragment = ref.partition("#")
    if not sep or not path or not fragment:
        raise ValueError(f"A reference is <report>#<finding>, not {ref!r}")
    return path, fragment


def finding_key(result: dict[str, Any], tool: str) -> str:
    """The key that names one finding across scans.

    The Claude Security finding id, else a SARIF fingerprint, else a hash of
    the tool, the rule and the location. See CONTEXT.md, "Finding key".
    """
    plugin = _get(result, "properties", "claudeSecurityPlugin") or {}
    if plugin.get("claudeSecurityPluginFindingId"):
        return str(plugin["claudeSecurityPluginFindingId"])
    for prints in ("partialFingerprints", "fingerprints"):
        values = list((result.get(prints) or {}).values())
        if values:
            return str(values[0])
    location = _location(result)
    where = _symbol(location) or _uri(location)
    parts = urlsplit(where)
    if parts.scheme and parts.netloc:
        where = parts.path + (f"?{parts.query}" if parts.query else "")
    seed = "\0".join((tool, str(result.get("ruleId", "")), where))
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


# --- private helpers ---


def _is_sarif(name: str) -> bool:
    return name.endswith(_SARIF_SUFFIXES)


def _get(obj: Any, *keys: str | int) -> Any:
    """``obj[k1][k2]…``, or ``None`` when any step is missing."""
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int) and k < len(obj):
            obj = obj[k]
        else:
            return None
    return obj


def _first_run(sarif: dict[str, Any]) -> dict[str, Any]:
    runs = sarif.get("runs") or []
    if not runs:
        raise ValueError("The SARIF file has no run")
    return runs[0]


def _tool_slug(run: dict[str, Any]) -> str:
    name = str(_get(run, "tool", "driver", "name") or "tool").strip().lower()
    return _TOOL_SLUGS.get(name) or re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def _run_date(run: dict[str, Any], now: datetime) -> str:
    """The run's date: the invocation start, the Claude Security time, or ``now``."""
    for stamp in (
        _get(run, "invocations", 0, "startTimeUtc"),
        _get(run, "properties", "claudeSecurityPlugin", "generated_at"),
    ):
        if stamp:
            return (
                datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                .date()
                .isoformat()
            )
    return now.date().isoformat()


def _location(result: dict[str, Any]) -> dict[str, Any]:
    return _get(result, "locations", 0) or {}


def _uri(location: dict[str, Any]) -> str:
    return str(_get(location, "physicalLocation", "artifactLocation", "uri") or "")


def _symbol(location: dict[str, Any]) -> str:
    logical = _get(location, "logicalLocations", 0) or {}
    return str(logical.get("fullyQualifiedName") or logical.get("name") or "")


def _text(obj: Any) -> str:
    """A SARIF message object's text, or ``""``."""
    return str(_get(obj, "text") or "")


def _cwe(rule: dict[str, Any], plugin: dict[str, Any]) -> str:
    """The finding's CWE: its own, a rule tag, or a CWE taxonomy relationship."""
    if plugin.get("cwe_id"):
        return str(plugin["cwe_id"])
    for tag in _get(rule, "properties", "tags") or []:
        m = re.fullmatch(r"external/cwe/cwe-(\d+)", str(tag))
        if m:
            return f"CWE-{m[1]}"
    for rel in rule.get("relationships") or []:
        if _get(rel, "target", "toolComponent", "name") == "CWE":
            return f"CWE-{_get(rel, 'target', 'id')}"
    return ""


def _finding(
    result: dict[str, Any],
    index: int,
    key: str,
    rules: dict[Any, dict[str, Any]],
    path: str,
) -> Finding:
    plugin = _get(result, "properties", "claudeSecurityPlugin") or {}
    rule = rules.get(result.get("ruleId")) or {}
    location = _location(result)
    line = _get(location, "physicalLocation", "region", "startLine")
    preconditions = plugin.get("preconditions") or []
    detail = {
        "description": str(
            plugin.get("description") or _text(rule.get("fullDescription"))
        ),
        "impact": str(plugin.get("impact") or ""),
        "exploit": str(plugin.get("exploit_scenario") or ""),
        "preconditions": "\n".join(str(p) for p in preconditions),
        "recommendation": str(
            plugin.get("recommendation")
            or _text(_get(rule, "properties", "solution"))
            or _text(rule.get("help"))
        ),
        "message": _text(result.get("message")),
    }
    return Finding(
        key=key,
        label=str(plugin.get("id") or f"F{index + 1}"),
        ref=f"{path}#{key}",
        level=str(result.get("level") or "warning"),
        rule=str(result.get("ruleId") or ""),
        cwe=_cwe(rule, plugin),
        title=str(
            plugin.get("title")
            or _text(rule.get("shortDescription"))
            or _text(result.get("message")).split("\n", 1)[0]
        ),
        file=str(plugin.get("file") or _uri(location)),
        line=int(line) if isinstance(line, int) else None,
        symbol=str(plugin.get("symbol") or _symbol(location)),
        snippet=str(
            plugin.get("snippet")
            or _text(_get(location, "physicalLocation", "region", "snippet"))
        ),
        detail={k: v for k, v in detail.items() if v},
    )
