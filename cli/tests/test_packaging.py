"""What `bin/publish` releases: one `mael` wheel, and every version in step.

`bin/publish` moves every member's version in lockstep, so a member that
disagrees was edited by hand, or was added without joining `VERSIONED`.
"""

import importlib.util
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CLI = REPO / "cli"


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
LIBRARIES = [m for m in MEMBERS if m.startswith("lib/")]
SERVICES = ["agent-daemon", "orchestrator-api"]


def _packages(member: str) -> set[str]:
    """The import packages under a member's `src/`."""
    return {
        p.name
        for p in (REPO / member / "src").iterdir()
        if (p / "__init__.py").exists()
    }


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


def _third_party(requirements: list[str]) -> set[str]:
    """``requirements`` with their specifiers, less the workspace members.

    The specifier counts: a library that raises a floor the CLI does not
    would install a version the bundled library cannot run on.
    """
    normalised = {r.replace(" ", "").lower() for r in requirements}
    return {r for r in normalised if not r.startswith("mael-")}


def test_the_cli_declares_every_bundled_librarys_dependencies():
    """The wheel bundles each library, so PyPI installs only the CLI's
    dependencies. A library's own list never reaches a `mael` user."""
    declared = _third_party(_toml(CLI / "pyproject.toml")["project"]["dependencies"])
    for member in LIBRARIES:
        project = _toml(REPO / member / "pyproject.toml")
        needed = _third_party(project["project"].get("dependencies", []))
        assert needed <= declared, f"{member} needs {needed - declared}"


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    """The wheel `bin/publish` uploads, built once for the module."""
    outdir = tmp_path_factory.mktemp("dist")
    _publish().build(outdir)
    wheels = list(outdir.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


@pytest.mark.slow
@pytest.mark.e2e
def test_the_wheel_bundles_every_library_and_no_service(wheel: Path):
    names = zipfile.ZipFile(wheel).namelist()
    top = {
        n.split("/", 1)[0]
        for n in names
        if not n.split("/", 1)[0].endswith(".dist-info")
    }
    assert top == {"mael_cli", *(p for m in LIBRARIES for p in _packages(m))}
    assert not top & {p for m in SERVICES for p in _packages(m)}
    assert "mael_domain/shared/agent-prompt.md" in names


@pytest.mark.slow
@pytest.mark.e2e
def test_the_wheel_requires_no_workspace_member(wheel: Path):
    """PyPI has no `mael-*` distribution to satisfy such a requirement."""
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        )
        requires = [
            line.split(":", 1)[1].strip()
            for line in archive.read(metadata).decode().splitlines()
            if line.startswith("Requires-Dist:")
        ]
    assert requires
    assert not [r for r in requires if r.lower().startswith("mael-")]


@pytest.mark.slow
@pytest.mark.e2e
def test_the_installed_wheel_finds_its_shared_dir(wheel: Path, tmp_path: Path):
    """No repository surrounds a wheel installed from PyPI."""
    # As deep as a venv's site-packages, so every place get_shared_dir()
    # looks is inside tmp_path.
    site = tmp_path / "venv" / "lib" / "python3" / "site-packages"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(site)
    found = subprocess.run(
        [
            sys.executable,
            "-I",  # No user site, no cwd: only the unzipped wheel and the stdlib.
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from mael_domain.shared_dir import get_shared_dir; print(get_shared_dir())",
            str(site),
        ],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
    )
    assert found.returncode == 0, found.stderr
    assert Path(found.stdout.strip()) == site / "mael_domain" / "shared"
