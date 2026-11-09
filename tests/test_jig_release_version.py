"""What `bin/jig-release-version` plans and stamps for the Release jig workflow.

Each test runs the script in a temporary git repo, because the tags are its input.
The tests run real `git`, but take well under a second and touch nothing outside
`tmp_path`, so they stay in the fast suite.
"""

import subprocess
import sys
from pathlib import Path

import pytest
from git_helpers import create_commit, run_git, setup_git_repo

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "jig-release-version"
MANIFEST = '{\n  "name": "vite-plugin-mael-jig",\n  "version": "%s"\n}\n'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    setup_git_repo(tmp_path)
    (tmp_path / "vite-plugin-mael-jig").mkdir()
    create_commit(
        tmp_path, "vite-plugin-mael-jig/package.json", MANIFEST % "0.0.0-dev", "first"
    )
    return tmp_path


def _tag_and_move_on(repo: Path, *versions: str) -> None:
    for version in versions:
        run_git(repo, "tag", f"vite-plugin-mael-jig@{version}")
    run_git(repo, "commit", "--allow-empty", "-m", "next")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


def test_patch_bumps_the_numerically_highest_plain_tag(repo: Path):
    # 0.10.0 sorts below 0.9.0 as a string. A pre-release tag is not a release.
    _tag_and_move_on(repo, "0.9.0", "0.10.0", "0.11.0-rc.1")

    result = _run(repo, "next", "patch")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "version=0.10.1\nprevious=0.10.0\n"


def test_first_release_has_no_previous(repo: Path):
    # The workflow passes --notes-start-tag only when previous is not empty.
    assert _run(repo, "next", "minor").stdout == "version=0.1.0\nprevious=\n"


def test_head_already_released_is_refused(repo: Path):
    run_git(repo, "tag", "vite-plugin-mael-jig@0.1.0")

    result = _run(repo, "next", "patch")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "already released as vite-plugin-mael-jig@0.1.0" in result.stderr


def test_exact_version_not_above_the_highest_is_refused(repo: Path):
    _tag_and_move_on(repo, "0.1.0")

    result = _run(repo, "next", "0.1.0")

    assert result.returncode != 0
    assert result.stdout == ""
    assert "not above" in result.stderr


def test_exact_version_above_the_highest_is_planned(repo: Path):
    _tag_and_move_on(repo, "0.1.0")

    assert _run(repo, "next", "0.2.0").stdout == "version=0.2.0\nprevious=0.1.0\n"


@pytest.mark.parametrize("command", ["next", "stamp"])
@pytest.mark.parametrize("version", ["1.2", "1.2.3-beta"])
def test_a_version_that_is_not_plain_is_refused(repo: Path, command: str, version: str):
    # npm would make a pre-release the `latest` dist-tag.
    manifest = repo / "vite-plugin-mael-jig" / "package.json"

    result = _run(repo, command, version)

    assert result.returncode != 0
    assert result.stdout == ""
    assert manifest.read_text() == MANIFEST % "0.0.0-dev"


def test_stamp_replaces_the_placeholder_once(repo: Path):
    manifest = repo / "vite-plugin-mael-jig" / "package.json"

    assert _run(repo, "stamp", "0.1.1").returncode == 0
    assert manifest.read_text() == MANIFEST % "0.1.1"

    # The placeholder is gone, so a second stamp fails rather than writing nothing.
    result = _run(repo, "stamp", "0.1.2")
    assert result.returncode != 0
    assert "0 times" in result.stderr
