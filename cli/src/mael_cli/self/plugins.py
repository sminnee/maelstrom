"""The skills, shipped as two plugins from one local marketplace.

``shared/plugins/`` is the marketplace. Its ``maelstrom`` plugin is the core
plugin: every maelstrom launch names it, so nothing for it goes into the
user's config, except a disabled Codex install (Codex has no ``--plugin-dir``).
Its ``minnstack`` plugin is optional, and a user installs it globally. See
"Skills and plugins" in ``docs/dev/host-services.md``.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from mael_agent.harness_model import CORE_PLUGIN, PRACTICES_PLUGIN
from mael_common.util import read_json
from mael_domain.system_info import practices_installed

from .host_service import Run

MARKETPLACE = "maelstrom"
PRACTICES = "minnstack"


class PluginsError(Exception):
    """The host is in a state setup does not change by itself."""


#: The text in an old skill link's target. ``mael self setup`` linked each
#: skill from ``shared/skills/`` into ``~/.claude/skills/``.
_OLD_SKILLS_DIR = "/shared/skills/"


def codex_home_from_env() -> Path | None:
    """``$CODEX_HOME``, or ``None`` for Codex's default, ``~/.codex``."""
    value = os.environ.get("CODEX_HOME")
    return Path(value) if value else None


@dataclass
class Plugins:
    """The plugin state of one host, with every outside effect injected.

    ``which`` finds a tool on the agents' PATH. ``codex_home`` is
    ``$CODEX_HOME``, else ``~/.codex``.
    """

    home: Path
    #: maelstrom's ``shared/``, which holds the marketplace.
    shared: Path
    run: Run
    which: Callable[[str], str | None]
    codex_home: Path | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def link(self) -> Path:
        """The stable marketplace path both harnesses register."""
        return self.home / ".maelstrom" / "plugins"

    @property
    def claude_plugins(self) -> Path:
        return self.home / ".claude" / "plugins"

    @property
    def codex_config(self) -> Path:
        return (self.codex_home or self.home / ".codex") / "config.toml"

    # -- setup -------------------------------------------------------------

    def setup(self) -> list[str]:
        """Bring the host to the plugin layout. Safe to repeat."""
        removed_practices = self.remove_old_links()
        try:
            self.register()
        except PluginsError as exc:
            self.messages.append(f"Warning: {exc} The plugins are not registered.")
            return self.messages
        self.refresh_codex()
        if removed_practices:
            # The host had the practices as links; keep them as a plugin.
            self.install_practices()
        return self.messages

    def remove_old_links(self) -> bool:
        """Remove each skill link an older setup made. True if one was a practice.

        A link is old when its target is in a ``shared/skills/`` dir, of any
        release, and whether or not the target still exists. Any other link,
        and any real dir, is the user's.
        """
        practices = {
            p.name for p in (self.shared / "plugins" / PRACTICES / "skills").iterdir()
        }
        removed_practices = False
        seen: set[Path] = set()
        for skills in (
            self.home / ".claude" / "skills",
            self.home / ".agents" / "skills",
        ):
            # Either dir may be a link, into dotfiles or to the other one.
            if not skills.is_dir() or skills.resolve() in seen:
                continue
            seen.add(skills.resolve())
            for entry in sorted(skills.iterdir()):
                if not entry.is_symlink() or _OLD_SKILLS_DIR not in os.readlink(entry):
                    continue
                entry.unlink()
                removed_practices |= entry.name in practices
                self.messages.append(f"Removed old skill link {entry}")
        return removed_practices

    def register(self) -> None:
        """Point the stable path at this release's marketplace, and register it."""
        self.point_link()
        self.register_claude()
        self.register_codex()

    def point_link(self) -> None:
        """Move :attr:`link` to this release's marketplace with one rename."""
        target = self.shared / "plugins"
        if self.link.is_symlink() and Path(os.readlink(self.link)) == target:
            return
        if self.link.exists() and not self.link.is_symlink():
            raise PluginsError(
                f"{self.link} is not a link. Move it, then run setup again."
            )
        self.link.parent.mkdir(parents=True, exist_ok=True)
        staging = self.link.with_name(f".plugins.tmp-{os.getpid()}")
        staging.unlink(missing_ok=True)
        staging.symlink_to(target)
        os.replace(staging, self.link)
        self.messages.append(f"Linked {self.link} -> {target}")

    # -- Claude ------------------------------------------------------------

    def register_claude(self) -> None:
        """Register the marketplace with Claude Code, once.

        Claude keeps the path as given, so the stable link reaches each new
        release with no new registration.
        """
        if not self.which("claude"):
            self.messages.append(
                "Warning: claude is not installed, so the plugin marketplace is "
                "not registered. Run `mael self setup` after you install it."
            )
            return
        known = read_json(self.claude_plugins / "known_marketplaces.json")
        if MARKETPLACE in known:
            return
        self._run(["claude", "plugin", "marketplace", "add", str(self.link)])

    def claude_has_practices(self) -> bool:
        return practices_installed(self.claude_plugins)

    # -- Codex -------------------------------------------------------------

    def register_codex(self) -> None:
        """Point Codex's ``maelstrom`` marketplace at the stable link.

        Written to the config, not added with ``codex plugin marketplace add``:
        that command stores the resolved path, which the next release moves.
        Without ``codex``, nothing: Codex is optional.
        """
        if not self.which("codex"):
            return
        doc = self._codex_doc()
        markets = doc.setdefault("marketplaces", tomlkit.table(True))
        entry = markets.get(MARKETPLACE)
        if (
            isinstance(entry, dict)
            and entry.get("source_type") == "local"
            and entry.get("source") == str(self.link)
        ):
            return
        table = tomlkit.table()
        table["source_type"] = "local"
        table["source"] = str(self.link)
        markets[MARKETPLACE] = table
        self._write_codex(doc)
        self.messages.append(f"Registered the {MARKETPLACE} marketplace with Codex")

    def refresh_codex(self) -> None:
        """Install the core plugin into Codex, disabled, and refresh each copy.

        Codex copies a local plugin into its cache, so ``codex plugin add``
        runs again on each setup. Core stays disabled, so plain ``codex`` does
        not get it, unless the user enabled it. Each maelstrom thread turns
        it on.
        """
        if not self.which("codex"):
            return
        enabled = self.codex_plugins()
        user_enabled_core = enabled.get(CORE_PLUGIN) is True
        self._run(["codex", "plugin", "add", CORE_PLUGIN])
        for plugin, on in enabled.items():
            if on and plugin != CORE_PLUGIN:
                self._run(["codex", "plugin", "add", plugin])
        # `codex plugin add` enables what it adds.
        doc = self._codex_doc()
        plugins = doc.setdefault("plugins", tomlkit.table(True))
        core = plugins.setdefault(CORE_PLUGIN, tomlkit.table())
        if core.get("enabled") != user_enabled_core:
            core["enabled"] = user_enabled_core
            self._write_codex(doc)

    def codex_plugins(self) -> dict[str, Any]:
        """Each ``@maelstrom`` plugin in the Codex config, and its ``enabled``."""
        plugins = self._codex_doc().get("plugins", {})
        return {
            name: (value.get("enabled") if isinstance(value, dict) else None)
            for name, value in plugins.items()
            if name.endswith(f"@{MARKETPLACE}")
        }

    # -- the practices -----------------------------------------------------

    def install_practices(self) -> None:
        """Install ``minnstack`` into Claude, and into Codex when it is there."""
        if self.which("claude") and not self.claude_has_practices():
            self._run(
                ["claude", "plugin", "install", PRACTICES_PLUGIN, "--scope", "user"]
            )
        if (
            self.which("codex")
            and self.codex_plugins().get(PRACTICES_PLUGIN) is not True
        ):
            self._run(["codex", "plugin", "add", PRACTICES_PLUGIN])

    def has_practices(self) -> bool:
        """Whether each harness on the host has ``minnstack``."""
        if not self.claude_has_practices():
            return False
        return not self.which("codex") or (
            self.codex_plugins().get(PRACTICES_PLUGIN) is True
        )

    # -- effects -----------------------------------------------------------

    def _run(self, argv: list[str]) -> None:
        # By the path `which` found: the caller's PATH may not have the tool.
        result = self.run([self.which(argv[0]) or argv[0], *argv[1:]])
        command = " ".join(argv)
        if result.returncode == 0:
            self.messages.append(f"Ran `{command}`")
            return
        reason = (result.stderr or result.stdout or "").strip().splitlines()
        self.messages.append(
            f"Warning: `{command}` failed"
            + (f": {reason[-1]}" if reason else f" (exit {result.returncode})")
        )

    def _codex_doc(self) -> tomlkit.TOMLDocument:
        if not self.codex_config.is_file():
            return tomlkit.document()
        return tomlkit.parse(self.codex_config.read_text())

    def _write_codex(self, doc: tomlkit.TOMLDocument) -> None:
        self.codex_config.parent.mkdir(parents=True, exist_ok=True)
        self.codex_config.write_text(tomlkit.dumps(doc))
