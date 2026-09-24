"""Every workspace member carries the root's version.

`bin/publish` moves them in lockstep, so a member that disagrees was edited by
hand, or was added without joining `VERSIONED`.
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


VERSIONED = _publish().VERSIONED
ROOT_VERSION = tomllib.loads((REPO / "pyproject.toml").read_text())["project"][
    "version"
]


def test_publish_versions_every_workspace_member():
    workspace = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["uv"][
        "workspace"
    ]["members"]
    listed = {str(pyproject.parent) for pyproject, _ in VERSIONED}
    assert listed == {".", *workspace}


@pytest.mark.parametrize(
    ("pyproject", "init"), VERSIONED, ids=[str(p.parent) for p, _ in VERSIONED]
)
def test_each_member_agrees_with_the_root(pyproject: Path, init: Path):
    declared = tomllib.loads((REPO / pyproject).read_text())["project"]["version"]
    match = re.search(r'^__version__ = "(.+?)"', (REPO / init).read_text(), re.M)
    assert match, f"{init} sets no __version__"
    assert (declared, match.group(1)) == (ROOT_VERSION, ROOT_VERSION)


def test_the_wheel_bundles_every_library_and_no_service():
    """`mael` imports the libraries, so the root wheel and the root's editable
    install must both carry them. `mael self-update` installs the root alone."""
    root = tomllib.loads((REPO / "pyproject.toml").read_text())
    wheel = root["tool"]["hatch"]["build"]["targets"]["wheel"]
    libraries = {
        f"{member}/src/{package.name}"
        for member in root["tool"]["uv"]["workspace"]["members"]
        if member.startswith("lib/")
        for package in (REPO / member / "src").iterdir()
        if (package / "__init__.py").exists()
    }
    assert set(wheel["packages"]) == {"src/maelstrom", *libraries}
    assert set(wheel["dev-mode-dirs"]) == {
        "src",
        *(p.rsplit("/", 1)[0] for p in libraries),
    }


def _third_party(requirements: list[str]) -> set[str]:
    """The distribution names in ``requirements``, less the workspace members."""
    names = {re.split(r"[<>=!~;\[ ]", r, maxsplit=1)[0].lower() for r in requirements}
    return {n for n in names if not n.startswith("mael-")}


def test_the_root_declares_every_bundled_librarys_dependencies():
    """The root wheel bundles each library, so PyPI installs only the root's
    dependencies. A library's own list never reaches a `mael` user."""
    root = tomllib.loads((REPO / "pyproject.toml").read_text())
    declared = _third_party(root["project"]["dependencies"])
    for member in root["tool"]["uv"]["workspace"]["members"]:
        if not member.startswith("lib/"):
            continue
        project = tomllib.loads((REPO / member / "pyproject.toml").read_text())
        needed = _third_party(project["project"].get("dependencies", []))
        assert needed <= declared, f"{member} needs {needed - declared}"
