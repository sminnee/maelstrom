"""SARIF: read a scan's findings, and the key that names each one across scans.

See ``CONTEXT.md``, "Finding" and "Finding key".
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

#: Short slugs for known SARIF driver names. Any other driver name is slugged whole.
_TOOL_SLUGS = {
    "claude security plugin for claude code": "claude-security",
    "owasp zap": "zap",
}


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
    level: str
    rule: str
    cwe: str
    title: str
    file: str
    line: int | None
    symbol: str
    snippet: str
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SarifReport:
    """A SARIF file's first run: the tool's slug, the scan date, the findings."""

    tool: str
    date: str | None
    findings: tuple[Finding, ...]


def parse(text: str) -> SarifReport:
    """The report ``text`` holds, its findings in the order the SARIF lists them.

    Two results with the same key, such as one ZAP rule on two parameters of
    one URL, are told apart by a ``-<n>`` suffix in SARIF order.

    Raises:
        ValueError: ``text`` is not JSON, or holds no SARIF run.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not JSON: {exc}") from None
    runs = parsed.get("runs") if isinstance(parsed, dict) else None
    if not isinstance(runs, list) or not runs or not isinstance(runs[0], dict):
        raise ValueError("The SARIF file has no run")
    run = runs[0]
    try:
        tool = _tool_slug(run)
        rules = {r.get("id"): r for r in _get(run, "tool", "driver", "rules") or []}
    except (AttributeError, TypeError) as exc:
        raise ValueError(f"Not a SARIF run: {exc}") from None
    seen: dict[str, int] = {}
    findings = []
    try:
        for i, result in enumerate(run.get("results") or []):
            key = finding_key(result, tool)
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                key = f"{key}-{seen[key]}"
            findings.append(_finding(result, i, key, rules))
        date = _run_date(run)
    except (AttributeError, TypeError) as exc:
        # A part of the run that is not the shape SARIF gives it.
        raise ValueError(f"Not a SARIF run: {exc}") from None
    return SarifReport(tool=tool, date=date, findings=tuple(findings))


def finding_key(result: dict[str, Any], tool: str) -> str:
    """The key that names one finding across scans. See ``CONTEXT.md``, "Finding key"."""
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


def _tool_slug(run: dict[str, Any]) -> str:
    name = str(_get(run, "tool", "driver", "name") or "tool").strip().lower()
    return _TOOL_SLUGS.get(name) or re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def _run_date(run: dict[str, Any]) -> str | None:
    """The run's date: the invocation start, else the Claude Security time."""
    for stamp in (
        _get(run, "invocations", 0, "startTimeUtc"),
        _get(run, "properties", "claudeSecurityPlugin", "generated_at"),
    ):
        if stamp:
            try:
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                continue  # An unreadable date does not make the scan unreadable.
            return when.date().isoformat()
    return None


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
    result: dict[str, Any], index: int, key: str, rules: dict[Any, dict[str, Any]]
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
