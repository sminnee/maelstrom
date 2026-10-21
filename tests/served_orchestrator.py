"""Helpers for the suites that drive the CLI against a real orchestrator server.

The fixtures that start the server are in ``conftest.py``.
"""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from click.testing import CliRunner

from mael_cli.task_cli import task
from mael_domain import task as model
from mael_domain.task_table import InMemoryTaskTable

PROJECT = "northwind"
OTHER_PROJECT = "acme"
NOW = "2026-09-01T00:00:00Z"


@dataclass
class Served:
    """The table the server reads, and the URL it answers on."""

    table: InMemoryTaskTable
    url: str


def seed(table: InMemoryTaskTable, **fields) -> model.Task:
    """Write one task before the server starts, so its first read holds it."""
    fields.setdefault("now", NOW)
    return asyncio.run(model.create(table, project=PROJECT, **fields))


def point_config_at(url: str) -> None:
    """Name the server in the notebook root's config, as a dev environment does."""
    config = Path(os.environ["MAEL_NOTEBOOK_ROOT"]) / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"orchestrator_url: {url}\n")


def mael_task(*args: str):
    """Run ``mael task <args>`` and return the result."""
    return CliRunner().invoke(task, list(args), catch_exceptions=False)
