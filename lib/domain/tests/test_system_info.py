"""What the Welcome dialog shows about this install: its skills and its version."""

from mael_domain.release_bundle.layout import MANIFEST, Manifest
from mael_domain.system_info import SystemPaths, linked_skills, system_reply


def test_only_live_links_into_a_shared_skills_dir_count(tmp_path):
    shared = tmp_path / "shared" / "skills"
    (shared / "mael").mkdir(parents=True)
    (shared / "planning").mkdir()
    elsewhere = tmp_path / "elsewhere" / "theirs"
    elsewhere.mkdir(parents=True)
    claude = tmp_path / "claude" / "skills"
    claude.mkdir(parents=True)
    (claude / "planning").symlink_to(shared / "planning")
    (claude / "mael").symlink_to(shared / "mael")
    (claude / "theirs").symlink_to(elsewhere)
    (claude / "own").mkdir()
    (claude / "dead").symlink_to(shared / "removed")

    assert linked_skills(claude) == ["mael", "planning"]


def test_no_skills_dir_lists_nothing(tmp_path):
    assert linked_skills(tmp_path / "missing") == []


def _version(tmp_path, manifest_text: str | None):
    release = tmp_path / "releases" / "v1"
    release.mkdir(parents=True)
    if manifest_text is not None:
        (release / MANIFEST).write_text(manifest_text)
    paths = SystemPaths.for_home(tmp_path, tmp_path / "Projects", str(release / "venv"))
    return system_reply(paths)["version"]


def test_the_version_is_the_running_releases(tmp_path):
    manifest = Manifest(
        tag="v0.9.0", commit="3f2a9c1", dirty=False, versions={"web": "abc"}
    )

    assert _version(tmp_path, manifest.dumps()) == {
        "tag": "v0.9.0",
        "commit": "3f2a9c1",
        "dirty": False,
    }


def test_no_manifest_or_a_broken_one_has_no_version(tmp_path):
    assert _version(tmp_path, None) is None
    assert _version(tmp_path / "broken", "format = 1\ntag = ") is None
