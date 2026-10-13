"""The **External event**: a turn the server writes to an agent from outside its conversation.

One envelope for every source, so the agent and the transcript tell it from
the user's words. The facts are attributes; the body is what to do. See
``docs/dev/orchestrator-server.md``, "External events".
"""

import html
import re
from dataclasses import dataclass, field

from .github_model import PrStatus

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
                f"PR #{number} has merged. The work is done: run `mael session end`.",
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
