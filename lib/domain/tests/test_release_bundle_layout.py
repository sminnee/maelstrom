"""The Python side of the install layout: the manifest and the web root."""

from pathlib import Path

from mael_domain.release_bundle.layout import COMPONENTS, Manifest, installed_web_root

BASE = {name: f"{name}-v1" for name in COMPONENTS}


def test_the_manifest_round_trips(tmp_path):
    manifest = Manifest(tag="v0.3.0", commit="abc", dirty=True, versions=BASE)
    path = tmp_path / "manifest.toml"
    manifest.write(path)
    assert Manifest.read(path) == manifest
    assert 'kind = "host"' in path.read_text()


class TestInstalledWebRoot:
    """The orchestrator finds the UI from the venv it runs in."""

    def _install(self, root: Path) -> Path:
        """The layout install.sh leaves: a release, its venv, `current`."""
        release = root / "releases" / "v1"
        (release / "venv").mkdir(parents=True)
        Manifest(tag="v1", commit="c", dirty=False, versions=BASE).write(
            release / "manifest.toml"
        )
        web = root / "components" / "web" / "web-v1"
        web.mkdir(parents=True)
        (web / "index.html").write_text("<p>")
        (root / "current").symlink_to("releases/v1")
        return web

    def test_an_installed_venv_names_its_releases_web_component(self, tmp_path):
        web = self._install(tmp_path)

        assert installed_web_root(str(tmp_path / "current" / "venv")) == web

    def test_a_worktree_venv_has_no_web_root(self, tmp_path):
        prefix = tmp_path / "worktree" / ".venv"
        prefix.mkdir(parents=True)
        assert installed_web_root(str(prefix)) is None
