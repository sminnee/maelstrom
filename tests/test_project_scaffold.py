"""Tests for the new-project stub files."""

import json

import yaml

from maelstrom.project_scaffold import scaffold_files


class TestScaffoldFiles:
    def test_gitignore_covers_the_generated_files(self):
        lines = scaffold_files("proj")[".gitignore"].splitlines()
        assert ".env" in lines
        assert ".claude/CLAUDE.local.md" in lines
        assert ".drafts/" in lines

    def test_settings_allow_writes_to_the_drafts_directory(self):
        """A planning session sculpts drafts; each edit must not cost a prompt."""
        settings = json.loads(scaffold_files("proj")[".claude/settings.json"])
        allow = settings["permissions"]["allow"]
        assert "Write(.drafts/**)" in allow
        assert "Edit(.drafts/**)" in allow

    def test_maelstrom_yaml_is_all_comments(self):
        """A fully commented stub parses to None, which the loader accepts."""
        assert yaml.safe_load(scaffold_files("proj")[".maelstrom.yaml"]) is None

    def test_claude_md_starts_with_the_local_import(self):
        content = scaffold_files("proj")["CLAUDE.md"]
        assert content.splitlines()[0] == "@.claude/CLAUDE.local.md"

    def test_claude_md_and_readme_name_the_project(self):
        files = scaffold_files("myproj")
        assert "# myproj" in files["CLAUDE.md"]
        assert "# myproj" in files["README.md"]
