"""The **External event**: a turn the server writes to an agent from outside its conversation.

One envelope for every source, so the agent and the transcript tell it from
the user's words. The facts are attributes; the body is what to do. See
``docs/dev/orchestrator-server.md``, "External events".
"""

import html
import re
from dataclasses import dataclass, field

GITHUB = "github"
JIG = "jig"
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
