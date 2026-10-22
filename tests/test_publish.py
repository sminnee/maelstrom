"""What `bin/publish` versions: every workspace member, in step with the root.

`bin/publish` moves every member's version in lockstep, so a member that
disagrees was edited by hand, or was added without joining `VERSIONED`.
"""

import importlib.util
import re
import tomllib
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _publish():
    """`bin/publish`, loaded as a module: it has no `.py` suffix."""
    loader = SourceFileLoader("publish", str(REPO / "bin" / "publish"))
    spec = importlib.util.spec_from_loader("publish", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


VERSIONED = _publish().VERSIONED
WORKSPACE = _toml(REPO / "pyproject.toml")
MEMBERS = WORKSPACE["tool"]["uv"]["workspace"]["members"]
ROOT_VERSION = WORKSPACE["project"]["version"]


def test_publish_versions_every_workspace_member():
    listed = {str(pyproject.parent) for pyproject, _ in VERSIONED}
    assert listed == {".", *MEMBERS}


@pytest.mark.parametrize(
    ("pyproject", "init"),
    [pair for pair in VERSIONED if pair[1] is not None],
    ids=[str(p.parent) for p, init in VERSIONED if init is not None],
)
def test_each_member_agrees_with_the_root(pyproject: Path, init: Path):
    declared = _toml(REPO / pyproject)["project"]["version"]
    match = re.search(r'^__version__ = "(.+?)"', (REPO / init).read_text(), re.M)
    assert match, f"{init} sets no __version__"
    assert (declared, match.group(1)) == (ROOT_VERSION, ROOT_VERSION)
