"""`mael self-update`'s install: `cli/` alone, as an editable uv tool.

The tool gets no other workspace member, so this checks that `cli/`'s own
editable install reaches the libraries in the checkout, and not a copy.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def tool(tmp_path_factory) -> Path:
    """The installed tool's directory. Its `bin/` holds the tool's Python."""
    root = tmp_path_factory.mktemp("uv-tools")
    env = {
        **os.environ,
        "UV_TOOL_DIR": str(root / "tools"),
        "UV_TOOL_BIN_DIR": str(root / "bin"),
    }
    installed = subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--editable",
            str(REPO / "cli"),
            "--python",
            sys.executable,
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    return root


@pytest.mark.slow
@pytest.mark.e2e
def test_the_installed_mael_runs(tool: Path):
    result = subprocess.run(
        [str(tool / "bin" / "mael"), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.slow
@pytest.mark.e2e
@pytest.mark.parametrize(
    "member", sorted(p.parent.name for p in REPO.glob("lib/*/src"))
)
def test_the_libraries_are_the_checkout(tool: Path, member: str):
    """A copy in the tool's site-packages would hide every later edit."""
    python = tool / "tools" / "sminnee-maelstrom" / "bin" / "python"
    package = f"mael_{member}"
    found = subprocess.run(
        [str(python), "-c", f"import {package}; print({package}.__file__)"],
        capture_output=True,
        text=True,
    )
    assert found.returncode == 0, found.stderr
    assert Path(found.stdout.strip()).is_relative_to(REPO / "lib" / member / "src")
