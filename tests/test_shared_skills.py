"""Tests for the shape of the skills in ``shared/skills/``."""

from pathlib import Path

import yaml

from maelstrom.claude_integration import get_shared_dir


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


class TestSkillFrontmatterNames:
    """Every skill's ``name`` must equal its directory name.

    A skill is invoked as ``/<name>``, and launch sites hard-code those slash
    names: ``mael task add --command plan-task`` and the literal
    ``/resolve-rebase-conflicts`` in ``rebase_repair.py``. A ``name:`` that
    drifts from its directory breaks the invocation silently, so guard the
    pairing rather than trust each file.
    """

    def test_every_skill_name_matches_its_directory(self):
        skills_dir = get_shared_dir() / "skills"

        missing = []
        mismatches = []
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                missing.append(skill_dir.name)
                continue
            name = _frontmatter(skill_file).get("name")
            if name != skill_dir.name:
                mismatches.append((skill_dir.name, name))

        assert not missing, f"skill directories with no SKILL.md: {missing}"
        assert not mismatches, f"name: does not match directory: {mismatches}"
