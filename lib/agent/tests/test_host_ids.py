"""The host prefix on a remote agent's id."""

import pytest

from mael_agent.host_ids import split_host, with_host


@pytest.mark.parametrize(
    ("agent_id", "expected"),
    [
        ("cobalt:r1", ("cobalt", "r1")),
        ("zinc-2:r1.sub", ("zinc-2", "r1.sub")),
        ("r1", ("", "r1")),
        # A local id with a `:` of its own keeps it.
        ("2026-01-01T00:00:00Z", ("", "2026-01-01T00:00:00Z")),
        ("Cobalt:r1", ("", "Cobalt:r1")),
        ("co.balt:r1", ("", "co.balt:r1")),
        (":r1", ("", ":r1")),
    ],
)
def test_split_host(agent_id, expected):
    assert split_host(agent_id) == expected


@pytest.mark.parametrize(
    ("host", "bare", "expected"),
    [("cobalt", "r1", "cobalt:r1"), ("", "r1", "r1"), ("cobalt", "", "")],
)
def test_with_host(host, bare, expected):
    assert with_host(host, bare) == expected
