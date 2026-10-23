"""The **External event**: a turn the server writes to an agent from outside its conversation.

One envelope for every source, so the agent and the transcript tell it from
the user's words. The facts are attributes; the body is what to do. See
``docs/dev/orchestrator-server.md``, "External events".
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any

from .github_model import PrStatus

GATE = "gate"
GITHUB = "github"
JIG = "jig"
ORCHESTRATOR = "orchestrator"
ORCHESTRATOR_UI = "orchestrator-ui"

DOCUMENT_APPROVED = "document.approved"
DOCUMENT_CHANGES_REQUESTED = "document.changes_requested"
CHANGES_COMMENTED = "changes.commented"
JIG_MONKEYPATCH = "jig.monkeypatch"
JIG_PARAM_SET = "jig.param_set"
JIG_PICK = "jig.pick"
JIG_CONTROLS = "jig.controls"
GATE_PASSED = "gate.passed"
GATE_FAILED = "gate.failed"
GATE_CANCELLED = "gate.cancelled"
PR_WATCHING = "pr.watching"
PR_CHECKS_FAILED = "pr.checks_failed"
PR_CHECKS_PASSED = "pr.checks_passed"
PR_CONFLICT = "pr.conflict"
PR_MERGED = "pr.merged"
SESSION_COMPACTED = "session.compacted"
SESSION_NOT_COMPACTED = "session.not_compacted"

_TAG = "external-event"


@dataclass(frozen=True)
class ExternalEvent:
    source: str
    type: str
    attrs: dict[str, str] = field(default_factory=dict)
    body: str = ""


def render(event: ExternalEvent) -> str:
    """The envelope as the agent reads it: ``source`` and ``type`` first."""
    pairs = {"source": event.source, "type": event.type, **event.attrs}
    attrs = " ".join(f'{name}="{html.escape(value)}"' for name, value in pairs.items())
    # A body carries outside text: a comment, CSS, a param label. An envelope
    # tag in it would end this one early, or forge a second one.
    body = _TAG_IN_BODY.sub(r"&lt;\1", event.body.strip())
    return f"<{_TAG} {attrs}>\n{body}\n</{_TAG}>"


_TAG_IN_BODY = re.compile(rf"<(/?{_TAG})")


#: The whole turn is one envelope and nothing else. The body may not run past
#: its own closing tag, so two envelopes either side of real text are not one;
#: see `_TASK_NOTIFICATION` in `normalise.py`.
_ENVELOPE = re.compile(
    rf'^<{_TAG}((?:\s+[a-z][a-z0-9_-]*="[^"]*")*)\s*>'
    rf"((?:(?!</{_TAG}>)[\s\S])*)</{_TAG}>$"
)
_ATTR = re.compile(r'([a-z][a-z0-9_-]*)="([^"]*)"')


def parse(text: str) -> ExternalEvent | None:
    """The event ``text`` is, or ``None`` when it is anything else."""
    match = _ENVELOPE.match(text.strip())
    if match is None:
        return None
    attrs = {name: html.unescape(value) for name, value in _ATTR.findall(match[1])}
    source, type_ = attrs.pop("source", ""), attrs.pop("type", "")
    if not source or not type_:
        return None
    return ExternalEvent(source, type_, attrs, match[2].strip())


@dataclass(frozen=True)
class PrLedger:
    """What the agents of a PR were last told, so nothing is told twice."""

    state: str
    conflicting: bool
    told_failed_oid: str
    told_green_oid: str


#: States whose checks are green. A conflict or an unknown mergeability is
#: read only after the checks, so the checks passed.
_GREEN = frozenset({"ready", "conflict", "unknown"})


def pr_events(
    pr: PrStatus, told: PrLedger | None, withdrawn: bool
) -> tuple[list[ExternalEvent], PrLedger]:
    """The events ``pr`` is news for, and the ledger after them.

    ``told`` is ``None`` the first time the PR is seen. A **Withdrawn PR** hears
    of its merge only, but its ledger still moves, so registering it again
    replays nothing.
    """
    number, short = pr.number, pr.head_oid[:7]
    failed_oid = told.told_failed_oid if told else ""
    green_oid = told.told_green_oid if told else ""
    events: list[ExternalEvent] = []

    def add(type_: str, body: str, *, head: bool = False) -> None:
        attrs = {"pr": str(number), **({"head": short} if head else {})}
        events.append(ExternalEvent(GITHUB, type_, attrs, body))

    if pr.state == "merged":
        if told is None or told.state != "merged":
            add(
                PR_MERGED,
                f"PR #{number} has merged. The work is done: write `<end-session>` "
                "and end your turn.",
            )
    else:
        if told is None:
            add(
                PR_WATCHING,
                f"Maelstrom will tell you when CI on PR #{number} fails or passes, "
                "when it conflicts, and when it merges. End your turn after the "
                "push; do not wait on CI.",
            )
        if pr.state == "ci-failed" and pr.head_oid != failed_oid:
            failed_oid = pr.head_oid
            add(
                PR_CHECKS_FAILED,
                f"CI failed on PR #{number} at {short}. Investigate with "
                "`mael gh read-pr` and `mael gh check-log <run> --failed-only`, "
                "fix the failures, commit fixups, and run `mael sync`.",
                head=True,
            )
        if pr.state in _GREEN and pr.checks_seen and pr.head_oid != green_oid:
            green_oid = pr.head_oid
            add(
                PR_CHECKS_PASSED,
                f"CI passed on PR #{number} at {short}. No action needed.",
                head=True,
            )
        if pr.conflicting and not (told and told.conflicting):
            add(
                PR_CONFLICT,
                f"PR #{number} conflicts with its base. Run `mael sync`, resolve "
                "the conflicts, and continue the rebase.",
            )
        if withdrawn:
            events = []
    return events, PrLedger(pr.state, pr.conflicting, failed_oid, green_oid)


#: The gate states tangier counts as a pass.
_GATE_OK = frozenset({"passed", "not-needed", "verified"})
#: The gate states that did not run: not started, cut off, or cancelled.
_GATE_NOT_RUN = frozenset({"pending", "running", "cancelled"})
#: The gate states that are not a failure. Every other state is a failed gate:
#: it makes a cancelled job ``gate.failed``, and the failure body names it, so a
#: state this version does not know still names its gate.
_GATE_NOT_FAILED = _GATE_OK | _GATE_NOT_RUN


def _gate_summary(gates: list[dict[str, Any]]) -> list[str]:
    """The ``Passed:`` and ``Not run:`` lines for *gates*, each left out when empty."""
    lines = []
    for label, states in (("Passed", _GATE_OK), ("Not run", _GATE_NOT_RUN)):
        names = [str(g.get("name")) for g in gates if g.get("state") in states]
        if names:
            lines.append(f"{label}: {', '.join(names)}")
    return lines


def gate_event(job: dict[str, Any]) -> ExternalEvent:
    """The event a finished gate job is, as ``tangier gate status --json`` gives it.

    Any end but ``passed`` and ``cancelled`` is a failure: a job that ``died``,
    or a state this version does not know, must not read as a pass. A
    ``cancelled`` job with a failed gate is a failure too: tangier ends a
    fail-fast job ``cancelled`` when one gate fails.
    """
    number, short = str(job["id"]), str(job.get("head") or "")[:7]
    gates = [g for g in job.get("gates") or [] if isinstance(g, dict)]
    attrs = {"job": number, "head": short}
    state = job.get("state")
    if state == "passed":
        names = ", ".join(str(g.get("name")) for g in gates)
        return ExternalEvent(
            GATE, GATE_PASSED, attrs, f"Gate job {number} passed: {names}."
        )
    failed = [g for g in gates if g.get("state") not in _GATE_NOT_FAILED]
    if state == "cancelled" and not failed:
        lines = [f"Gate job {number} was cancelled.", *_gate_summary(gates)]
        return ExternalEvent(GATE, GATE_CANCELLED, attrs, "\n".join(lines))
    lines = [f"Gate job {number} failed at {short}:"]
    for gate in failed:
        gate_state = gate.get("state")
        code = gate.get("code")
        line = f"- {gate.get('name')}: {gate_state}"
        if code is not None:
            line += f" (exit {code})"
        if gate_state == "unrecorded":
            line += (
                ": the worktree changed while the gate ran, so its pass wrote no record"
            )
        lines.append(line)
    lines += [
        *_gate_summary(gates),
        f"Log: {job.get('log')}",
        "Read the log, fix the failures, commit, and run the gates again.",
    ]
    return ExternalEvent(GATE, GATE_FAILED, attrs, "\n".join(lines))
