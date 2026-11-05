"""The rules ``bin/build-bundle`` checks before it builds anything.

`tests/test_release_bundle.py` builds a real bundle.
"""

import subprocess

import pytest

from mael_domain.release_bundle.build import BuildError, build_bundle, default_tag


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def dirty_repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(
        tmp_path,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "start",
    )
    (tmp_path / "new.txt").write_text("uncommitted")
    return tmp_path


def test_a_dirty_tree_is_refused_without_allow_dirty(dirty_repo, tmp_path):
    out = tmp_path / "out" / "maelstrom.tar.gz"

    with pytest.raises(BuildError, match="--allow-dirty"):
        build_bundle(dirty_repo, out)

    assert not out.exists()


def test_a_clean_tag_names_the_commit():
    assert default_tag("0123456789abcdef", None) == "dev-0123456789ab"


def test_a_dirty_tag_carries_the_stamp():
    assert (
        default_tag("0123456789abcdef", "-dirty-20261105120000")
        == "dev-0123456789ab-dirty-20261105120000"
    )
