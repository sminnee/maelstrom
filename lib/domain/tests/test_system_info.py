"""What the Welcome dialog shows about this install: its skills and its version."""

import json

from mael_domain.release_bundle.layout import MANIFEST, Manifest
from mael_domain.release_bundle.update_job import UpdateRecord, write_record
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


def _reply(tmp_path, *, release: bool, production: bool):
    prefix = tmp_path / ".venv"
    if release:
        prefix = tmp_path / "releases" / "v1" / "venv"
        prefix.parent.mkdir(parents=True)
        Manifest(tag="v1", commit="abc", dirty=False).write(prefix.parent / MANIFEST)
    paths = SystemPaths.for_home(
        tmp_path, tmp_path / "Projects", str(prefix), production=production
    )
    return system_reply(paths)


def test_only_an_installed_release_run_as_a_host_service_can_update(tmp_path):
    assert _reply(tmp_path / "a", release=True, production=True)["canUpdate"] is True
    assert _reply(tmp_path / "b", release=True, production=False)["canUpdate"] is False
    assert _reply(tmp_path / "c", release=False, production=True)["canUpdate"] is False


def test_the_update_channel_comes_from_the_global_config(tmp_path):
    assert _reply(tmp_path / "a", release=False, production=False)["updateChannel"] == (
        "stable"
    )
    config = tmp_path / "b" / ".maelstrom" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("update_channel: main\n")

    assert _reply(tmp_path / "b", release=False, production=False)["updateChannel"] == (
        "main"
    )


def test_the_last_update_is_in_the_reply(tmp_path):
    assert _reply(tmp_path / "a", release=False, production=False)["update"] is None
    install = tmp_path / "b" / ".maelstrom" / "install"
    write_record(install, UpdateRecord("succeeded", 7, "t0", "/l"))

    update = _reply(tmp_path / "b", release=False, production=False)["update"]

    assert update == {
        "state": "succeeded",
        "pid": 7,
        "startedAt": "t0",
        "finishedAt": None,
        "fromTag": None,
        "toTag": None,
        "exitCode": None,
        "log": "/l",
    }
