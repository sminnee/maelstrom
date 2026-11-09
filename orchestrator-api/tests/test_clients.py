"""Tests for the locality of a browser: on this server's machine, or not."""

import pytest

from mael_orchestrator.clients import client_is_local


@pytest.mark.parametrize(
    ("peer", "own", "local"),
    [
        ("127.0.0.1", "127.0.0.1", True),
        ("::1", "::1", True),
        ("::ffff:127.0.0.1", "::", True),
        # A browser on this machine that uses its tailnet name.
        ("100.64.0.3", "100.64.0.3", True),
        ("::ffff:100.64.0.3", "100.64.0.3", True),
        ("100.64.0.7", "100.64.0.3", False),
        (None, "100.64.0.3", False),
        ("not an ip", "100.64.0.3", False),
    ],
)
def test_a_client_is_local_on_loopback_or_this_servers_own_address(peer, own, local):
    assert client_is_local(peer, own) is local
