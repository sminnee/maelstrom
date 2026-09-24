"""Recorded daemon streams, in the shape the server reads them, and the seed agent.

The server never sees a bare stream event: it reads the daemon's attach stream,
where every event already carries ``mael_ts``. A test that fed a raw fixture
line would be asserting against a shape production never produces, so both
suites that replay these fixtures read them through here.
"""

import json
from pathlib import Path

from mael_agent.agent_wire import TS_KEY
from mael_daemon.agent_model import _stamp

FIXTURES = Path(__file__).parents[3] / "agent-daemon" / "fixtures" / "agent_events"

#: What the daemon's clock read when it first saw an event with no clock of its
#: own. Later than the ``NOW`` the tests replay at, which stands for the moment
#: of reattach, so a golden that keeps an event's own time is telling the truth
#: about the reattach case rather than agreeing by coincidence.
RECEIVED = "2026-09-02T00:00:00Z"


def read_stamped_fixture(name: str) -> list[dict]:
    """``name``'s events, each stamped the way :meth:`AgentRun.record` does."""
    lines = (FIXTURES / name).read_text().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    return [{**e, TS_KEY: _stamp(e, RECEIVED)} for e in events]


def make_agent(**over) -> dict:
    """The seed agent ``web/src/test/fixtures.ts`` replays every fixture into."""
    agent = {
        "id": "agent-1",
        "parent": "",
        "description": "",
        "state": "processing",
        "session": "sess-1",
        "cwd": "/Users/dev/Projects/northwind/northwind-alpha",
        "model": "claude-opus-5",
        "permissionMode": "",
        "waitingOn": "",
        "lastMessage": "",
        "lastMessageAt": "",
        "lastNote": "",
        "lastNoteAt": "",
        "costUsd": 0,
        "totalTokens": 0,
        "subagentTokens": 0,
        "contextTokens": 0,
        "taskId": "NORT-7",
        "project": "northwind",
        "worktreeId": "northwind-alpha",
        "exitCode": None,
        "pendingRequestIds": [],
        "pid": None,
    }
    agent.update(over)
    return agent


def make_document(**over) -> dict:
    doc = {
        "id": "doc-1",
        "agentId": "agent-1",
        "taskId": "NORT-7",
        "kind": "plan",
        "title": "Plan",
        "markdown": "# Plan\n\nDo the thing.\n",
        "version": 1,
        "status": "awaiting-review",
        "source": {"type": "plan_review", "requestId": "req-1", "planFilePath": ""},
    }
    doc.update(over)
    return doc
