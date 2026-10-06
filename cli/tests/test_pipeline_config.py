"""The repo's pipeline.toml against its .maelstrom.yaml.

A service version comes from the tangier bucket named after the service. A
renamed service or bucket stops its restarts without an error, so this pins
the names.
"""

import tomllib
from pathlib import Path

from mael_cli.admin_cli import CLI_BUCKET
from mael_domain.config import load_config

REPO = Path(__file__).resolve().parents[2]


def _buckets() -> set[str]:
    pipeline = tomllib.loads((REPO / "pipeline.toml").read_text())
    return {name for name, table in pipeline.items() if table.get("sha") is True}


def test_every_core_service_has_a_bucket():
    """Otherwise `mael self-update` never restarts it."""
    services = {s.name for s in load_config(REPO).services if not s.optional}
    assert services <= _buckets()


def test_the_cli_has_a_bucket():
    """Otherwise every `mael self-update` reinstalls the CLI."""
    assert CLI_BUCKET in _buckets()
