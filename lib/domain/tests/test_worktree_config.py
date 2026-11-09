"""The worktree config: the files maelstrom writes."""

import json

from mael_domain.worktree_config import (
    _setup_claude_settings_symlink,
    _write_agents_md,
    apply_worktree_config,
)


class TestWriteAgentsMd:
    def _worktree(self, tmp_path):
        project_path = tmp_path / "myproject"
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir(parents=True)
        return worktree_path

    def test_no_claude_md_is_noop(self, tmp_path):
        worktree_path = self._worktree(tmp_path)
        _write_agents_md(worktree_path)
        assert not (worktree_path / "AGENTS.md").exists()

    def test_inlines_import(self, tmp_path):
        worktree_path = self._worktree(tmp_path)
        (worktree_path / "CLAUDE.md").write_text(
            "@.claude/CLAUDE.local.md\n\n# Project\n\nBody.\n"
        )
        (worktree_path / ".claude").mkdir()
        (worktree_path / ".claude" / "CLAUDE.local.md").write_text("local context")

        _write_agents_md(worktree_path)

        content = (worktree_path / "AGENTS.md").read_text()
        assert "<!-- from @.claude/CLAUDE.local.md -->" in content
        assert "local context" in content
        assert "@.claude/CLAUDE.local.md\n\n# Project" not in content
        assert "# Project" in content
        assert "AGENTS.md" in (worktree_path / ".gitignore").read_text()

    def test_missing_import_noted_in_comment(self, tmp_path):
        worktree_path = self._worktree(tmp_path)
        (worktree_path / "CLAUDE.md").write_text(
            "@.claude/CLAUDE.local.md\n\n# Project\n"
        )

        _write_agents_md(worktree_path)

        content = (worktree_path / "AGENTS.md").read_text()
        assert "<!-- @.claude/CLAUDE.local.md: not found -->" in content

    def test_nested_import_inlined(self, tmp_path):
        worktree_path = self._worktree(tmp_path)
        (worktree_path / "CLAUDE.md").write_text("@a.md\n")
        (worktree_path / "a.md").write_text("outer\n@b.md\n")
        (worktree_path / "b.md").write_text("inner")

        _write_agents_md(worktree_path)

        content = (worktree_path / "AGENTS.md").read_text()
        assert "outer" in content
        assert "<!-- from @b.md -->" in content
        assert "inner" in content

    def test_circular_import_terminated(self, tmp_path):
        worktree_path = self._worktree(tmp_path)
        (worktree_path / "CLAUDE.md").write_text("@a.md\n")
        (worktree_path / "a.md").write_text("a\n@b.md\n")
        (worktree_path / "b.md").write_text("b\n@a.md\n")

        _write_agents_md(worktree_path)

        content = (worktree_path / "AGENTS.md").read_text()
        assert "a" in content
        assert "b" in content
        assert "circular" in content

    def test_regenerates_on_reuse(self, tmp_path):
        worktree_path = self._worktree(tmp_path)
        (worktree_path / "CLAUDE.md").write_text("# Project\n")
        _write_agents_md(worktree_path)
        (worktree_path / "AGENTS.md").write_text("stale")
        _write_agents_md(worktree_path)
        assert "stale" not in (worktree_path / "AGENTS.md").read_text()


class TestWriteConfig:
    """The files apply_worktree_config writes, outside a git repo."""

    def test_creates_claude_local_md(self, tmp_path, monkeypatch):
        """apply_worktree_config generates .claude/CLAUDE.local.md."""
        # Set up project structure: project_path/projectname-alpha
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        # Mock get_app_url to return None (no web port)
        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        result = apply_worktree_config(project_path, worktree_path, "alpha")

        assert result.local_md_written

        local_md = worktree_path / ".claude" / "CLAUDE.local.md"
        assert local_md.exists()

        content = local_md.read_text()
        assert "Always load the `/mael` skill" in content
        assert "## Environment" in content
        assert str(worktree_path) in content
        assert "app URL" not in content

    def test_includes_app_url_when_available(self, tmp_path, monkeypatch):
        """Test that app URL is included when the project has a web port."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr(
            "mael_domain.ports.get_app_url",
            lambda *a: ("http://localhost:3010", False),
        )

        result = apply_worktree_config(project_path, worktree_path, "alpha")

        assert result.local_md_written

        content = (worktree_path / ".claude" / "CLAUDE.local.md").read_text()
        assert "The app URL is http://localhost:3010" in content

    def test_overwrites_existing_file(self, tmp_path, monkeypatch):
        """Test that the file is regenerated on every call."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        # Write an old file
        claude_dir = worktree_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "CLAUDE.local.md").write_text("old content")

        result = apply_worktree_config(project_path, worktree_path, "alpha")

        assert result.local_md_written
        content = (claude_dir / "CLAUDE.local.md").read_text()
        assert "old content" not in content
        assert "Always load the `/mael` skill" in content

    def test_adds_import_to_existing_claude_md(self, tmp_path, monkeypatch):
        """Test that @.claude/CLAUDE.local.md is prepended to CLAUDE.md."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        # Create a CLAUDE.md without the import
        (worktree_path / "CLAUDE.md").write_text("# My Project\n\nSome docs.\n")

        apply_worktree_config(project_path, worktree_path, "alpha")

        content = (worktree_path / "CLAUDE.md").read_text()
        assert content.startswith("@.claude/CLAUDE.local.md\n")
        assert "# My Project" in content

    def test_does_not_duplicate_import(self, tmp_path, monkeypatch):
        """Test that import line is not added if already present."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        original = "@.claude/CLAUDE.local.md\n\n# My Project\n"
        (worktree_path / "CLAUDE.md").write_text(original)

        apply_worktree_config(project_path, worktree_path, "alpha")

        content = (worktree_path / "CLAUDE.md").read_text()
        assert content.count("@.claude/CLAUDE.local.md") == 1

    def test_adds_gitignore_entry(self, tmp_path, monkeypatch):
        """Test that the entries mael generates are added to .gitignore."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        # Create a .gitignore without the entry
        (worktree_path / ".gitignore").write_text(".env\nnode_modules/\n")

        apply_worktree_config(project_path, worktree_path, "alpha")

        lines = (worktree_path / ".gitignore").read_text().splitlines()
        assert ".claude/CLAUDE.local.md" in lines
        assert ".drafts/" in lines

    def test_allows_writes_to_the_drafts_directory(self, tmp_path, monkeypatch):
        """A planning session sculpts drafts; each edit must not cost a prompt."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        apply_worktree_config(project_path, worktree_path, "alpha")

        settings = json.loads((worktree_path / ".claude" / "settings.json").read_text())
        allow = settings["permissions"]["allow"]
        assert "Write(.drafts/**)" in allow
        assert "Edit(.drafts/**)" in allow

    def test_keeps_the_rules_a_project_already_set(self, tmp_path, monkeypatch):
        """The merge adds; it never drops a rule the user owns."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()
        claude_dir = worktree_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(
            json.dumps(
                {"permissions": {"allow": ["Bash(uv run:*)"]}, "env": {"A": "1"}}
            )
        )

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        apply_worktree_config(project_path, worktree_path, "alpha")
        apply_worktree_config(project_path, worktree_path, "alpha")

        settings = json.loads((claude_dir / "settings.json").read_text())
        allow = settings["permissions"]["allow"]
        assert "Bash(uv run:*)" in allow
        assert settings["env"] == {"A": "1"}
        assert allow.count("Write(.drafts/**)") == 1

    def test_does_not_duplicate_gitignore_entry(self, tmp_path, monkeypatch):
        """Test that .gitignore entry is not duplicated."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        (worktree_path / ".gitignore").write_text(
            ".env\n.claude/CLAUDE.local.md\n.drafts/\n"
        )

        apply_worktree_config(project_path, worktree_path, "alpha")

        content = (worktree_path / ".gitignore").read_text()
        assert content.count(".claude/CLAUDE.local.md") == 1
        assert content.count(".drafts/") == 1

    def test_creates_gitignore_if_missing(self, tmp_path, monkeypatch):
        """Test that .gitignore is created if it doesn't exist."""
        project_path = tmp_path / "myproject"
        project_path.mkdir()
        worktree_path = project_path / "myproject-alpha"
        worktree_path.mkdir()

        monkeypatch.setattr("mael_domain.ports.get_app_url", lambda *a: None)

        apply_worktree_config(project_path, worktree_path, "alpha")

        gitignore = worktree_path / ".gitignore"
        assert gitignore.exists()
        lines = gitignore.read_text().splitlines()
        assert ".claude/CLAUDE.local.md" in lines
        assert ".drafts/" in lines


class TestSettingsSymlink:
    def test_the_symlink_is_gitignored(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text("{}")
        (tmp_path / ".gitignore").write_text(".env\n")

        _setup_claude_settings_symlink(tmp_path)

        assert (tmp_path / ".claude" / "settings.local.json").is_symlink()
        lines = (tmp_path / ".gitignore").read_text().splitlines()
        assert lines == [".env", ".claude/settings.local.json"]
