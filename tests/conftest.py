"""Global test fixtures for maelstrom test suite."""

import os
from unittest.mock import patch

import pytest

from maelstrom.cmux.client import RecordingCmuxClient
from maelstrom.cmux.model import CmuxLayout


@pytest.fixture(autouse=True, scope="session")
def _block_real_cmux():
    """Prevent any test from accidentally invoking the real cmux binary.

    Patches the binary discovery used by the real transport to return None (no
    binary found) and removes CMUX_SOCKET_PATH from the environment, so
    ``current_client()`` returns None and nothing shells out.
    """
    saved = os.environ.pop("CMUX_SOCKET_PATH", None)
    with patch("maelstrom.cmux.client._find_cmux_cli", return_value=None):
        yield
    if saved is not None:
        os.environ["CMUX_SOCKET_PATH"] = saved


@pytest.fixture()
def recording_layout():
    """Return a factory for a :class:`CmuxLayout` over a :class:`RecordingCmuxClient`.

    Call ``recording_layout(responses, name="ws")`` to build a layout whose
    client records every ``run`` call in ``client.calls`` and returns scripted
    results. ``responses`` is either a dict keyed by the exact args tuple or a
    callable ``fn(*args) -> str | None``. The returned tuple is
    ``(layout, client)`` so tests can assert on ``client.calls``.
    """

    def make(responses=None, name="myproject-alpha"):
        client = RecordingCmuxClient(responses)
        return CmuxLayout(client, name), client

    return make
