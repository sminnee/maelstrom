"""The installer, given release bundles a test writes.

A bundle carries every component. The installer copies only the versions it
does not have, and reports which components differ from the release it
replaces. A real venv is the e2e suite's job: here ``build_venv`` is a fake
that records what it was given.
"""

import io
import os
import tarfile
from pathlib import Path

import pytest

from mael_domain.release_bundle.install import (
    COMPONENTS,
    InstallError,
    InstallReport,
    Manifest,
    install_bundle,
    installed_web_root,
)

BASE = {name: f"{name}-v1" for name in COMPONENTS}


def write_bundle(path: Path, tag: str, versions: dict[str, str]) -> Path:
    """A ``maelstrom.tar.gz`` with one file per component, named for its version."""
    manifest = Manifest(tag=tag, commit="c0ffee", dirty=False, versions=versions)
    files = {"maelstrom/manifest.toml": manifest.dumps()}
    for name, version in versions.items():
        if name == "web":
            files["maelstrom/components/web/index.html"] = f"<p>{version}</p>"
        elif name == "python-deps":
            files["maelstrom/components/python-deps/requirements.txt"] = version
        else:
            whl = f"maelstrom/components/{name}/{name}-{version}-py3-none-any.whl"
            files[whl] = version
    with tarfile.open(path, "w:gz") as tar:
        for member, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(member)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class FakeVenv:
    """A ``build_venv`` that writes a marker, or fails when told to."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, list[Path]]] = []
        self.fail = False

    def __call__(self, venv: Path, requirements: Path, wheels: list[Path]) -> None:
        self.calls.append((venv, requirements, wheels))
        # A real failure comes after `uv venv` has written the interpreter.
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("")
        if self.fail:
            raise OSError("uv exploded")


@pytest.fixture
def venv():
    return FakeVenv()


def install(tmp_path, venv, tag, versions):
    bundle = write_bundle(tmp_path / f"{tag}.tar.gz", tag, versions)
    return install_bundle(bundle, tmp_path / "install", build_venv=venv)


def current(tmp_path) -> Path:
    return (tmp_path / "install" / "current").resolve()


def test_a_first_install_writes_every_component_and_points_current_at_it(
    tmp_path, venv
):
    report = install(tmp_path, venv, "v1", BASE)

    root = tmp_path / "install"
    for name, version in BASE.items():
        assert (root / "components" / name / version).is_dir(), name
    assert current(tmp_path) == (root / "releases" / "v1").resolve()
    assert Manifest.read(root / "current" / "manifest.toml").versions == BASE
    assert report == InstallReport(tag="v1", changed=list(COMPONENTS), unchanged=[])


def test_the_venv_gets_the_pinned_requirements_and_every_wheel(tmp_path, venv):
    install(tmp_path, venv, "v1", BASE)

    [(path, requirements, wheels)] = venv.calls
    root = tmp_path / "install"
    assert path == root / "releases" / "v1" / "venv"
    assert requirements.read_text() == "python-deps-v1"
    assert sorted(w.name for w in wheels) == sorted(
        f"{name}-{name}-v1-py3-none-any.whl"
        for name in COMPONENTS
        if name not in ("web", "python-deps")
    )


def test_a_second_bundle_copies_only_the_component_that_moved(tmp_path, venv):
    install(tmp_path, venv, "v1", BASE)
    components = tmp_path / "install" / "components"
    inodes = {n: (components / n / v).stat().st_ino for n, v in BASE.items()}

    report = install(tmp_path, venv, "v2", {**BASE, "orchestrator": "orch-v2"})

    assert report == InstallReport(
        tag="v2",
        changed=["orchestrator"],
        unchanged=[n for n in COMPONENTS if n != "orchestrator"],
    )
    assert (components / "orchestrator" / "orch-v2").is_dir()
    for name, version in BASE.items():
        assert (components / name / version).stat().st_ino == inodes[name], name
    assert current(tmp_path).name == "v2"


def test_a_failed_venv_leaves_current_on_the_old_release(tmp_path, venv):
    install(tmp_path, venv, "v1", BASE)
    venv.fail = True

    with pytest.raises(OSError, match="uv exploded"):
        install(tmp_path, venv, "v2", {**BASE, "cli": "cli-v2"})

    assert current(tmp_path).name == "v1"
    venv.fail = False
    install(tmp_path, venv, "v2", {**BASE, "cli": "cli-v2"})
    assert current(tmp_path).name == "v2"
    assert len(venv.calls) == 3, "the half-built venv is built again"


def test_reinstalling_the_current_release_changes_nothing(tmp_path, venv):
    install(tmp_path, venv, "v1", BASE)

    report = install(tmp_path, venv, "v1", BASE)

    assert report == InstallReport(tag="v1", changed=[], unchanged=list(COMPONENTS))
    assert len(venv.calls) == 1, "the release's venv is reused"


def test_rolling_back_reports_what_differs_from_the_release_it_replaces(tmp_path, venv):
    """Every version is already on disk, but the orchestrator still changes."""
    install(tmp_path, venv, "v1", BASE)
    install(tmp_path, venv, "v2", {**BASE, "orchestrator": "orch-v2"})

    report = install(tmp_path, venv, "v1", BASE)

    assert report.changed == ["orchestrator"]
    assert current(tmp_path).name == "v1"


def test_the_current_release_is_not_replaced_by_other_versions_under_its_tag(
    tmp_path, venv
):
    install(tmp_path, venv, "v1", BASE)

    with pytest.raises(InstallError, match="v1"):
        install(tmp_path, venv, "v1", {**BASE, "cli": "cli-v2"})

    assert Manifest.read(current(tmp_path) / "manifest.toml").versions == BASE


def test_a_release_that_does_not_run_is_rebuilt_with_its_new_versions(tmp_path, venv):
    install(tmp_path, venv, "v1", BASE)
    install(tmp_path, venv, "v2", {**BASE, "orchestrator": "orch-v2"})
    rebuilt = {**BASE, "cli": "cli-v2"}

    install(tmp_path, venv, "v1", rebuilt)

    assert Manifest.read(current(tmp_path) / "manifest.toml").versions == rebuilt
    assert len(venv.calls) == 3, "the stale venv is not reused"
    assert "cli-v2" in str(venv.calls[-1][2])


def test_a_version_that_is_not_a_path_segment_is_refused(tmp_path, venv):
    with pytest.raises(InstallError, match="not a tag or a version"):
        install(tmp_path, venv, "v1", {**BASE, "web": "../../etc"})

    assert not (tmp_path / "install" / "current").exists()


def test_a_bundle_missing_a_component_is_refused(tmp_path, venv):
    versions = {k: v for k, v in BASE.items() if k != "web"}

    with pytest.raises(InstallError, match="web"):
        install(tmp_path, venv, "v1", versions)

    assert not (tmp_path / "install" / "current").exists()


def test_the_manifest_round_trips(tmp_path):
    manifest = Manifest(tag="v0.3.0", commit="abc", dirty=True, versions=BASE)
    path = tmp_path / "manifest.toml"
    manifest.write(path)
    assert Manifest.read(path) == manifest
    assert 'kind = "host"' in path.read_text()


class TestInstalledWebRoot:
    """The orchestrator finds the UI from the venv it runs in."""

    def test_an_installed_venv_names_its_releases_web_component(self, tmp_path, venv):
        install(tmp_path, venv, "v1", BASE)
        prefix = tmp_path / "install" / "current" / "venv"

        web = installed_web_root(str(prefix))

        assert web == (tmp_path / "install" / "components" / "web" / "web-v1")
        assert (web / "index.html").is_file()

    def test_a_worktree_venv_has_no_web_root(self, tmp_path):
        prefix = tmp_path / "worktree" / ".venv"
        prefix.mkdir(parents=True)
        assert installed_web_root(str(prefix)) is None


def test_no_temporary_files_are_left_behind(tmp_path, venv):
    install(tmp_path, venv, "v1", BASE)
    install(tmp_path, venv, "v2", {**BASE, "cli": "cli-v2"})

    root = tmp_path / "install"
    leftovers = [
        p for p in root.rglob("*") if ".tmp" in p.name or p.name.startswith(".")
    ]
    assert leftovers == []
    assert sorted(os.listdir(root)) == ["components", "current", "releases"]
