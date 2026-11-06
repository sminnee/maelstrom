"""What the Welcome dialog shows about this install: its skills and its version."""

import json

from mael_domain.release_bundle.layout import MANIFEST, Manifest
from mael_domain.system_info import (
    SystemPaths,
    plugin_skills,
    practices_installed,
    system_reply,
)


def test_the_skills_are_the_plugin_dirs_with_a_skill_file(tmp_path):
    skills = tmp_path / "plugins" / "maelstrom" / "skills"
    for name in ("planning", "mael"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text("---\nname: x\n---\n")
    (skills / "half-made").mkdir()

    assert plugin_skills(skills) == ["mael", "planning"]


def test_no_skills_dir_lists_nothing(tmp_path):
    assert plugin_skills(tmp_path / "missing") == []


def test_the_practices_are_installed_when_claude_lists_them(tmp_path):
    plugins = tmp_path / ".claude" / "plugins"
    assert practices_installed(plugins) is False
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"minnstack@maelstrom": []}})
    )
    assert practices_installed(plugins) is True


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
