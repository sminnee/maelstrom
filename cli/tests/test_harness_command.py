"""The harness command and transport the launcher builds from a model reference.

The alias table and mode flags belong to ``lib/agent/tests/test_harness_model.py``.
"""

import asyncio
from pathlib import Path

import click
import pytest

from mael_cli.task_cli import resolve_harness_or_fail
from mael_cli.worktree_launcher import build_harness_command, launch_agent_in_worktree


def test_cli_command_uses_the_model_prefix_and_omits_a_mismatched_alias():
    assert build_harness_command(model="codex:terra", permission_mode="plan") == [
        "codex",
        "--sandbox",
        "read-only",
        "-c",
        "approvals_reviewer=user",
        "--model",
        "gpt-5.6-terra",
        "-c",
        "model_reasoning_effort=medium",
    ]
    assert build_harness_command(model="codex:terra", harness="claude") == ["claude"]


def test_cli_command_refuses_an_unknown_harness():
    with pytest.raises(ValueError, match="Unknown CLI harness: 'cursor'"):
        build_harness_command(harness="cursor")


def test_conflicting_transport_flags_are_a_click_error():
    with pytest.raises(click.ClickException, match="--cli conflicts with --daemon"):
        resolve_harness_or_fail(cli=True, daemon=True)


def test_daemon_refuses_a_codex_model_before_starting_any_process():
    with pytest.raises(ValueError, match="codex daemon is not available"):
        asyncio.run(launch_agent_in_worktree(Path("/tmp"), model="codex:astra"))
