"""``mael self setup``'s plugin step, on a tmp home with a fake ``run``."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import tomlkit

from mael_cli.self.plugins import Plugins

CORE = "maelstrom@maelstrom"
PRACTICES = "minnstack@maelstrom"


@pytest.fixture
def shared(tmp_path) -> Path:
    """A release's ``shared/``: one core skill, one practice skill."""
    root = tmp_path / "release" / "shared"
    (root / "plugins" / "maelstrom" / "skills" / "mael").mkdir(parents=True)
    (root / "plugins" / "minnstack" / "skills" / "grilling").mkdir(parents=True)
    return root


@dataclass
class Host:
    """The tools on the host, and the commands they were given.

    The fake ``codex plugin add`` enables what it adds, as the real one does.
    """

    home: Path
    tools: tuple[str, ...] = ("claude", "codex")
    calls: list[list[str]] = field(default_factory=list)
    #: Command prefixes that exit 1, with this stderr.
    failing: dict[tuple[str, ...], str] = field(default_factory=dict)

    def which(self, name: str) -> str | None:
        return f"/bin/{name}" if name in self.tools else None

    def run(self, argv: list[str]) -> subprocess.CompletedProcess:
        command = [Path(argv[0]).name, *argv[1:]]
        self.calls.append(command)
        for prefix, stderr in self.failing.items():
            if tuple(command[: len(prefix)]) == prefix:
                return subprocess.CompletedProcess(argv, 1, "", stderr)
        if command[:3] == ["codex", "plugin", "add"]:
            doc = tomlkit.parse(self.codex_config.read_text())
            doc.setdefault("plugins", tomlkit.table(True))[command[3]] = {
                "enabled": True
            }
            self.codex_config.write_text(tomlkit.dumps(doc))
        return subprocess.CompletedProcess(argv, 0, "", "")

    @property
    def codex_config(self) -> Path:
        return self.home / ".codex" / "config.toml"

    def plugins(self, shared: Path) -> Plugins:
        return Plugins(home=self.home, shared=shared, run=self.run, which=self.which)

    def commands(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


@pytest.fixture
def host(tmp_path) -> Host:
    home = tmp_path / "home"
    home.mkdir()
    return Host(home)


def _old_links(home: Path, skills_dir: str, release: Path) -> Path:
    """The links an older setup made: one per skill, into a release."""
    skills = home / skills_dir
    skills.mkdir(parents=True, exist_ok=True)
    for name in ("mael", "grilling"):
        (skills / name).symlink_to(release / "shared" / "skills" / name)
    return skills


# -- the old links -------------------------------------------------------


def test_setup_removes_every_old_skill_link(host, shared, tmp_path):
    """Into this release, into another one, and broken: all old."""
    skills = _old_links(host.home, ".claude/skills", tmp_path / "release")
    (skills / "gone").symlink_to(tmp_path / "old-release/shared/skills/gone")
    agents = _old_links(host.home, ".agents/skills", tmp_path / "other-release")

    host.plugins(shared).setup()

    assert list(skills.iterdir()) == []
    assert list(agents.iterdir()) == []


def test_setup_keeps_the_users_own_skills(host, shared, tmp_path):
    skills = host.home / ".claude" / "skills"
    (skills / "mine").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    (skills / "linked").symlink_to(tmp_path / "elsewhere")

    host.plugins(shared).setup()

    assert sorted(p.name for p in skills.iterdir()) == ["linked", "mine"]


def test_an_agents_skills_dir_that_links_to_claudes_is_left(host, shared, tmp_path):
    skills = _old_links(host.home, ".claude/skills", tmp_path / "release")
    (host.home / ".agents").mkdir()
    (host.home / ".agents" / "skills").symlink_to(skills)

    host.plugins(shared).setup()

    assert (host.home / ".agents" / "skills").is_symlink()
    assert list(skills.iterdir()) == []


def test_a_skills_dir_that_links_into_dotfiles_is_cleaned(host, shared, tmp_path):
    """The old setup linked through it, so the old links are in its target."""
    dotfiles = _old_links(tmp_path, "dotfiles/skills", tmp_path / "release")
    (host.home / ".claude").mkdir()
    (host.home / ".claude" / "skills").symlink_to(dotfiles)

    host.plugins(shared).setup()

    assert list(dotfiles.iterdir()) == []


# -- the marketplace -----------------------------------------------------


def test_setup_points_the_stable_path_at_this_release(host, shared, tmp_path):
    link = host.home / ".maelstrom" / "plugins"
    link.parent.mkdir()
    link.symlink_to(tmp_path / "old-release" / "shared" / "plugins")

    host.plugins(shared).setup()

    assert link.resolve() == (shared / "plugins").resolve()


def test_a_real_dir_at_the_stable_path_stops_registration(host, shared):
    (host.home / ".maelstrom" / "plugins").mkdir(parents=True)

    messages = host.plugins(shared).setup()

    assert any(m.startswith("Warning: ") and "is not a link" in m for m in messages)
    assert host.calls == []


def test_setup_registers_the_marketplace_with_claude_once(host, shared):
    link = str(host.home / ".maelstrom" / "plugins")
    host.plugins(shared).setup()
    assert host.commands("claude", "plugin", "marketplace") == [
        ["claude", "plugin", "marketplace", "add", link]
    ]

    known = host.home / ".claude" / "plugins" / "known_marketplaces.json"
    known.parent.mkdir(parents=True)
    known.write_text(json.dumps({"maelstrom": {"source": {"path": link}}}))
    host.calls.clear()
    host.plugins(shared).setup()

    assert host.commands("claude", "plugin", "marketplace") == []


def test_a_failed_command_is_a_warning_and_setup_goes_on(host, shared):
    host.failing[("claude", "plugin", "marketplace")] = "boom\nno such dir"

    messages = host.plugins(shared).setup()

    link = host.home / ".maelstrom" / "plugins"
    assert (
        f"Warning: `claude plugin marketplace add {link}` failed: no such dir"
        in messages
    )
    assert host.commands("codex", "plugin", "add") == [["codex", "plugin", "add", CORE]]


def test_setup_without_claude_warns_and_goes_on(host, shared):
    host.tools = ("codex",)

    messages = host.plugins(shared).setup()

    assert any("claude is not installed" in m for m in messages)
    assert host.commands("claude") == []
    assert host.commands("codex", "plugin", "add") == [["codex", "plugin", "add", CORE]]


# -- the practices -------------------------------------------------------


def test_a_removed_practice_link_installs_the_practices(host, shared, tmp_path):
    """An upgrading host had the practices as links, so it keeps them."""
    _old_links(host.home, ".claude/skills", tmp_path / "release")

    host.plugins(shared).setup()

    assert host.commands("claude", "plugin", "install") == [
        ["claude", "plugin", "install", PRACTICES, "--scope", "user"]
    ]
    # Once: the refresh before it finds no practices to refresh.
    assert host.calls.count(["codex", "plugin", "add", PRACTICES]) == 1


def test_practices_claude_already_has_are_not_installed_again(host, shared, tmp_path):
    _old_links(host.home, ".claude/skills", tmp_path / "release")
    installed = host.home / ".claude" / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True)
    installed.write_text(json.dumps({"version": 2, "plugins": {PRACTICES: []}}))

    host.plugins(shared).setup()

    assert host.commands("claude", "plugin", "install") == []


def test_a_host_with_no_old_links_gets_no_practices(host, shared):
    host.plugins(shared).setup()

    assert host.commands("claude", "plugin", "install") == []
    assert ["codex", "plugin", "add", PRACTICES] not in host.calls


# -- Codex ---------------------------------------------------------------


def test_setup_installs_core_into_codex_disabled(host, shared):
    host.plugins(shared).setup()

    doc = tomlkit.parse(host.codex_config.read_text())
    assert doc["marketplaces"]["maelstrom"] == {
        "source_type": "local",
        # The link, not its target: Codex keeps the path, and the next
        # release moves the target.
        "source": str(host.home / ".maelstrom" / "plugins"),
    }
    assert doc["plugins"][CORE]["enabled"] is False


def test_setup_moves_codex_off_an_old_release_and_then_leaves_it(host, shared):
    host.codex_config.parent.mkdir()
    host.codex_config.write_text(
        '[marketplaces.maelstrom]\nsource_type = "local"\n'
        'source = "/old-release/shared/plugins"\n'
    )

    host.plugins(shared).setup()
    text = host.codex_config.read_text()
    assert tomlkit.parse(text)["marketplaces"]["maelstrom"]["source"] == str(
        host.home / ".maelstrom" / "plugins"
    )

    host.calls.clear()
    host.plugins(shared).setup()

    # A second run only refreshes the cached copy.
    assert host.codex_config.read_text() == text
    assert host.commands("codex") == [["codex", "plugin", "add", CORE]]


def test_setup_keeps_the_users_codex_config(host, shared):
    host.codex_config.parent.mkdir()
    host.codex_config.write_text(
        '# my settings\nmodel = "gpt-5"\n\n'
        f'[plugins."{CORE}"]\nenabled = true\n\n'
        f'[plugins."{PRACTICES}"]\nenabled = true\n'
    )

    host.plugins(shared).setup()

    text = host.codex_config.read_text()
    assert text.startswith('# my settings\nmodel = "gpt-5"\n')
    doc = tomlkit.parse(text)
    # The user enabled core for plain codex: that stays.
    assert doc["plugins"][CORE]["enabled"] is True
    # Each enabled plugin's cached copy is refreshed.
    assert ["codex", "plugin", "add", PRACTICES] in host.calls


def test_setup_without_codex_skips_codex(host, shared):
    host.tools = ("claude",)

    messages = host.plugins(shared).setup()

    assert host.commands("codex") == []
    assert not host.codex_config.exists()
    # Codex is optional, so its absence is no warning.
    assert not any(m.startswith("Warning") for m in messages)
