"""Fixtures for the mael CLI's suite."""

import pytest
from domain_fixtures import (  # noqa: F401  (pytest fixtures, found by name)
    _always_fetch,
    _block_real_cmux,
    _block_real_naming_model,
    _isolate_notebook_root,
    _mark_test_commands_production,
    _plain_terminal,
    caller,
    fake_cmux,
    migrated_notebook,
    project_with_worktree,
    stamp_honoured,
    state_db,
    store,
)


@pytest.fixture(autouse=True)
def _reset_task_db():
    """Forget the task CLI's cached database between tests.

    ``task_cli`` opens the state database once and keeps it in a module global,
    which is right in production — one process is one invocation. In the suite
    one process runs every test, so without this a test's temporary database
    would serve the next one, and the leak is invisible: the reads succeed and
    answer about the wrong notebook.
    """
    from mael_cli import task_cli

    task_cli._DB = None
    task_cli._CHECKED = False
    yield
    if task_cli._DB is not None:
        task_cli._DB.close()
    task_cli._DB = None
    task_cli._CHECKED = False
