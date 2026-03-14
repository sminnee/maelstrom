"""Global test fixtures for maelstrom test suite."""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True, scope="session")
def _block_real_cmux():
    """Prevent any test from accidentally invoking the real cmux binary.

    This autouse session-scoped fixture:
    - Patches _find_cmux_cli to return None (no binary found)
    - Patches subprocess.run inside maelstrom.cmux to raise RuntimeError
    - Removes CMUX_SOCKET_PATH from the environment if present
    """
    saved = os.environ.pop("CMUX_SOCKET_PATH", None)
    with patch("maelstrom.cmux._find_cmux_cli", return_value=None):
        yield
    if saved is not None:
        os.environ["CMUX_SOCKET_PATH"] = saved


@pytest.fixture()
def mock_cmux_workspace():
    """Return a MagicMock pre-configured as a CmuxWorkspace.

    Default return values:
    - ensure_browser -> "surface:test-browser"
    - close_browser -> True
    - find_browser_by_url -> None
    - browsers -> []
    - panels -> []

    Tests can override individual methods as needed.
    """
    ws = MagicMock()
    ws.ensure_browser.return_value = "surface:test-browser"
    ws.close_browser.return_value = True
    ws.find_browser_by_url.return_value = None
    ws.browsers.return_value = []
    ws.panels = []
    return ws


@pytest.fixture()
def mock_cmux_cmd():
    """Patch maelstrom.cmux.cmux_cmd and set CMUX_SOCKET_PATH.

    Returns the MagicMock so tests can configure side_effect as needed.
    The autouse _block_real_cmux fixture is overridden for cmux_cmd only;
    subprocess.run remains blocked as a safety net.
    """
    with (
        patch.dict(os.environ, {"CMUX_SOCKET_PATH": "/tmp/test-cmux.sock"}),
        patch("maelstrom.cmux.cmux_cmd", return_value=None) as mock,
    ):
        yield mock
