"""Fixtures for the suite that drives the CLI against a real orchestrator server.

The same isolation as ``cli/tests``: no real notebook, no naming model on the
network, no real cmux.
"""

from domain_fixtures import (  # noqa: F401  (pytest fixtures, found by name)
    _block_real_cmux,
    _block_real_naming_model,
    _isolate_notebook_root,
    _mark_test_commands_production,
    _plain_terminal,
)
