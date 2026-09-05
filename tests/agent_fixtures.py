"""Recorded daemon streams, in the shape the server reads them.

The server never sees a bare stream event: it reads the daemon's attach stream,
where every event already carries ``mael_ts``. A test that fed a raw fixture
line would be asserting against a shape production never produces, so both
suites that replay these fixtures read them through here.
"""

import json
from pathlib import Path

from maelstrom.agent_model import TS_KEY, _stamp

FIXTURES = Path(__file__).parent / "fixtures" / "agent_events"

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
