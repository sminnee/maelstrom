"""Tests for the shape of the plugins and skills in ``shared/plugins/``."""

import json
from collections import Counter
from pathlib import Path

import yaml

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
#: The plugins the marketplace lists. ``maelstrom`` is the core plugin.
PLUGIN_NAMES = ("maelstrom", "minnstack")


def _frontmatter(skill_file: Path) -> dict:
    """Parse a SKILL.md YAML frontmatter block, or ``{}`` if it has none."""
    text = skill_file.read_text()
    if not text.startswith("---\n"):
        return {}
    _, _, rest = text.partition("---\n")
    block, sep, _ = rest.partition("\n---")
    if not sep:
        return {}
    return yaml.safe_load(block) or {}


def _skill_dirs() -> list[Path]:
    return sorted(
        p
        for name in PLUGIN_NAMES
        for p in (PLUGINS / name / "skills").iterdir()
        # A dot-dir is a tool's working state, such as `.claude/`, not a skill.
        if p.is_dir() and not p.name.startswith(".")
    )


class TestSkillFrontmatterNames:
    """Every skill's ``name`` must equal its directory name.

    A skill is invoked as ``/<name>``, and launch sites hard-code those slash
    names: ``mael task add --command plan-task`` and the literal
    ``/resolve-rebase-conflicts`` in ``rebase_repair.py``. A plugin skill is
    namespaced (``/maelstrom:plan-task``); the bare name resolves only when
    ``name:`` sets it. So guard the pairing rather than trust each file.
    """

    def test_every_skill_name_matches_its_directory(self):
        missing = []
        mismatches = []
        for skill_dir in _skill_dirs():
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                missing.append(skill_dir.name)
                continue
            name = _frontmatter(skill_file).get("name")
            if name != skill_dir.name:
                mismatches.append((skill_dir.name, name))

        assert not missing, f"skill directories with no SKILL.md: {missing}"
        assert not mismatches, f"name: does not match directory: {mismatches}"

    def test_each_skill_is_in_one_plugin(self):
        """Two plugins with one bare name: neither resolves bare."""
        counts = Counter(d.name for d in _skill_dirs())
        assert [name for name, n in counts.items() if n > 1] == []


class TestManifests:
    def test_the_marketplace_lists_each_plugin_by_its_dir(self):
        market = json.loads(
            (PLUGINS / ".claude-plugin" / "marketplace.json").read_text()
        )
        assert market["name"] == "maelstrom"
        assert [(p["name"], p["source"]) for p in market["plugins"]] == [
            (name, f"./{name}") for name in PLUGIN_NAMES
        ]

    def test_each_plugin_manifest_names_its_dir(self):
        for name in PLUGIN_NAMES:
            manifest = PLUGINS / name / ".claude-plugin" / "plugin.json"
            assert json.loads(manifest.read_text())["name"] == name
