"""The isolation every end-to-end suite starts from.

Each e2e ``conftest.py`` imports these, so the domain's and the CLI's e2e
suites do not each carry a copy. Each function has a name of its own, apart
from the fixture's. A test module's parameter that names the fixture would
otherwise redefine the import.
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from mael_domain.context import GlobalConfig


@dataclass
class IsolatedMaelstrom:
    maelstrom_dir: Path
    projects_dir: Path


def _isolate(tmp_path: Path, mp: pytest.MonkeyPatch) -> IsolatedMaelstrom:
    """Redirect ~/.maelstrom/ and ~/Projects/ to temp dirs below ``tmp_path``."""
    maelstrom_dir = tmp_path / ".maelstrom"
    maelstrom_dir.mkdir()
    projects_dir = tmp_path / "Projects"
    projects_dir.mkdir()

    # The domain reads ~/.maelstrom/ through this one symbol.
    mp.setattr("mael_domain.context.get_maelstrom_dir", lambda: maelstrom_dir)
    # The state database, the desk and the task export all hang off the
    # notebook root, so a test that opens any of them would otherwise write
    # into the developer's live notebook.
    mp.setenv("MAEL_NOTEBOOK_ROOT", str(maelstrom_dir))
    mp.setattr(
        "mael_domain.context.load_global_config",
        lambda: GlobalConfig(projects_dir=projects_dir),
    )
    return IsolatedMaelstrom(maelstrom_dir=maelstrom_dir, projects_dir=projects_dir)


@pytest.fixture(name="isolated_maelstrom")
def isolated_maelstrom_fixture(tmp_path, monkeypatch):
    """Isolation for one test."""
    return _isolate(tmp_path, monkeypatch)


@pytest.fixture(name="isolated_maelstrom_module", scope="module")
def isolated_maelstrom_module_fixture(tmp_path_factory):
    """Isolation for a module of tests sharing a git_project."""
    mp = pytest.MonkeyPatch()
    yield _isolate(tmp_path_factory.mktemp("maelstrom"), mp)
    mp.undo()
