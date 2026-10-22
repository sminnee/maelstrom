"""Test fixtures for the domain suites. See ``domain_fixtures``."""

from domain_fixtures import (  # noqa: F401  (pytest fixtures, found by name)
    _always_fetch,
    _block_real_cmux,
    _block_real_naming_model,
    _isolate_notebook_root,
    _mark_test_commands_production,
    _plain_terminal,
    caller,
    collapsible_project,
    fake_cmux,
    fake_tailscale,
    project_with_worktree,
    quiet_finalize,
    stamp_honoured,
    state_db,
    store,
)
