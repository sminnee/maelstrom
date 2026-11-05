"""The wheels a release bundle carries: one per workspace member.

`tests/test_publish.py` checks that every version moves in step.
"""

import os
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

from mael_domain.release_bundle.build import WHEELS, build_wheels

REPO = Path(__file__).resolve().parents[2]


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


WORKSPACE = _toml(REPO / "pyproject.toml")
MEMBERS = WORKSPACE["tool"]["uv"]["workspace"]["members"]


def _packages(member: str) -> set[str]:
    """The import packages under a member's `src/`."""
    return {
        p.name
        for p in (REPO / member / "src").iterdir()
        if (p / "__init__.py").exists()
    }


@pytest.fixture(scope="module")
def wheels(tmp_path_factory) -> dict[str, Path]:
    """Every member's wheel, built once for the module."""
    return build_wheels(REPO, tmp_path_factory.mktemp("dist"))


def _top_packages(wheel: Path) -> set[str]:
    return {
        n.split("/", 1)[0]
        for n in zipfile.ZipFile(wheel).namelist()
        if not n.split("/", 1)[0].endswith(".dist-info")
    }


def test_every_member_is_a_component():
    """A member with no component would be missing from every installed venv."""
    names = {_toml(REPO / m / "pyproject.toml")["project"]["name"] for m in MEMBERS}
    assert {n.replace("-", "_") for n in names} == set(WHEELS.values())


@pytest.mark.e2e
def test_each_wheel_holds_its_own_member_alone(wheels: dict[str, Path]):
    """Two wheels holding one package would overwrite each other in the venv."""
    held = [_top_packages(w) for w in wheels.values()]
    assert sorted(p for packages in held for p in packages) == sorted(
        p for m in MEMBERS for p in _packages(m)
    )


@pytest.mark.e2e
def test_the_domain_wheel_carries_shared(wheels: dict[str, Path]):
    names = zipfile.ZipFile(wheels["domain"]).namelist()
    assert "mael_domain/shared/agent-prompt.md" in names
    assert not [n for n in names if n.startswith("mael_domain/shared/tests/")]


@pytest.mark.e2e
def test_the_installed_domain_wheel_finds_its_shared_dir(
    wheels: dict[str, Path], tmp_path: Path
):
    """No repository surrounds an installed release."""
    # As deep as a venv's site-packages, so every place get_shared_dir()
    # looks is inside tmp_path.
    site = tmp_path / "venv" / "lib" / "python3" / "site-packages"
    for name in ("common", "domain"):
        with zipfile.ZipFile(wheels[name]) as archive:
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
