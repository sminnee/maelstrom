"""Tests for context resolution module."""

from pathlib import Path

import pytest

from maelstrom.context import (
    GlobalConfig,
    ResolvedContext,
    detect_context_from_cwd,
    load_global_config,
    parse_target_arg,
    resolve_context,
    validate_project_name,
)


class TestGlobalConfig:
    """Tests for GlobalConfig class."""

    def test_default(self):
        """Test default global config."""
        config = GlobalConfig.default()
        assert config.projects_dir == Path.home() / "Projects"
        assert config.open_command == "code"

    def test_from_dict_with_projects_dir(self):
        """Test creating from dict with projects_dir."""
        config = GlobalConfig.from_dict({"projects_dir": "/custom/path"})
        assert config.projects_dir == Path("/custom/path")

    def test_from_dict_expands_tilde(self):
        """Test that ~ in projects_dir is expanded."""
        config = GlobalConfig.from_dict({"projects_dir": "~/CustomProjects"})
        assert config.projects_dir == Path.home() / "CustomProjects"

    def test_from_dict_empty(self):
        """Test creating from empty dict uses default."""
        config = GlobalConfig.from_dict({})
        assert config.projects_dir == Path.home() / "Projects"
        assert config.open_command == "code"

    def test_from_dict_with_open_command(self):
        """Test creating from dict with custom open_command."""
        config = GlobalConfig.from_dict({"open_command": "cursor"})
        assert config.open_command == "cursor"

    def test_from_dict_with_all_fields(self):
        """Test creating from dict with all fields."""
        config = GlobalConfig.from_dict({
            "projects_dir": "/custom/path",
            "open_command": "vim",
        })
        assert config.projects_dir == Path("/custom/path")
        assert config.open_command == "vim"


class TestResolvedContext:
    """Tests for ResolvedContext class."""

    def test_project_path_with_project(self):
        """Test project_path property when project is set."""
        ctx = ResolvedContext(
            projects_dir=Path("/Projects"),
            project="myproject",
            worktree=None,
        )
        assert ctx.project_path == Path("/Projects/myproject")

    def test_project_path_without_project(self):
        """Test project_path property when project is None."""
        ctx = ResolvedContext(
            projects_dir=Path("/Projects"),
            project=None,
            worktree=None,
        )
        assert ctx.project_path is None

    def test_worktree_path_with_both(self):
        """Test worktree_path when project and worktree are set."""
        ctx = ResolvedContext(
            projects_dir=Path("/Projects"),
            project="myproject",
            worktree="alpha",
        )
        # Folder name is now <project>-<worktree>
        assert ctx.worktree_path == Path("/Projects/myproject/myproject-alpha")

    def test_worktree_path_without_project(self):
        """Test worktree_path when project is None."""
        ctx = ResolvedContext(
            projects_dir=Path("/Projects"),
            project=None,
            worktree="feature-branch",
        )
        assert ctx.worktree_path is None

    def test_worktree_path_without_worktree(self):
        """Test worktree_path when worktree is None."""
        ctx = ResolvedContext(
            projects_dir=Path("/Projects"),
            project="myproject",
            worktree=None,
        )
        assert ctx.worktree_path is None


class TestValidateProjectName:
    """Tests for validate_project_name function."""

    def test_valid_simple_name(self):
        """Test valid simple project name."""
        validate_project_name("myproject")  # Should not raise

    def test_valid_name_with_dashes(self):
        """Test valid project name with dashes."""
        validate_project_name("my-project")  # Should not raise

    def test_valid_name_with_underscores(self):
        """Test valid project name with underscores."""
        validate_project_name("my_project")  # Should not raise

    def test_valid_name_with_numbers(self):
        """Test valid project name with numbers."""
        validate_project_name("MyProject123")  # Should not raise

    def test_dot_raises_error(self):
        """Test that dot in name raises error."""
        with pytest.raises(ValueError, match="cannot contain dots"):
            validate_project_name("my.project")

    def test_multiple_dots_raises_error(self):
        """Test that multiple dots raise error."""
        with pytest.raises(ValueError, match="cannot contain dots"):
            validate_project_name("my.project.name")

    def test_empty_raises_error(self):
        """Test that empty name raises error."""
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_project_name("")


class TestParseTargetArg:
    """Tests for parse_target_arg function."""

    def test_none_returns_none_none(self):
        """Test None input returns (None, None)."""
        assert parse_target_arg(None) == (None, None)

    def test_empty_string_returns_none_none(self):
        """Test empty string returns (None, None)."""
        assert parse_target_arg("") == (None, None)

    def test_project_only(self):
        """Test project name without dot."""
        assert parse_target_arg("myproject") == ("myproject", None)

    def test_project_and_worktree(self):
        """Test project.worktree format."""
        assert parse_target_arg("myproject.feature-branch") == (
            "myproject",
            "feature-branch",
        )

    def test_worktree_with_dashes(self):
        """Test worktree name containing dashes."""
        assert parse_target_arg("proj.feature-avatar-upload") == (
            "proj",
            "feature-avatar-upload",
        )

    def test_worktree_can_contain_dots(self):
        """Test that worktree portion can contain dots (after first)."""
        # "project.v1.0.0" -> project="project", worktree="v1.0.0"
        assert parse_target_arg("project.v1.0.0") == ("project", "v1.0.0")

    def test_leading_dot_raises_error(self):
        """Test that leading dot raises ValueError."""
        with pytest.raises(ValueError, match="cannot start with a dot"):
            parse_target_arg(".worktree")

    def test_empty_project_after_dot_raises_error(self):
        """Test that empty worktree after dot raises error."""
        with pytest.raises(ValueError, match="worktree name cannot be empty"):
            parse_target_arg("project.")


class TestDetectContextFromCwd:
    """Tests for detect_context_from_cwd function."""

    def test_not_in_projects_dir(self, tmp_path):
        """Test when cwd is not in projects_dir."""
        projects_dir = tmp_path / "Projects"
        projects_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        project, worktree = detect_context_from_cwd(projects_dir, other_dir)
        assert project is None
        assert worktree is None

    def test_in_projects_dir_root(self, tmp_path):
        """Test when cwd is the projects_dir itself."""
        projects_dir = tmp_path / "Projects"
        projects_dir.mkdir()

        project, worktree = detect_context_from_cwd(projects_dir, projects_dir)
        assert project is None
        assert worktree is None

    def test_in_project_root(self, tmp_path):
        """Test when cwd is at project root level."""
        projects_dir = tmp_path / "Projects"
        project_dir = projects_dir / "myproject"
        project_dir.mkdir(parents=True)

        project, worktree = detect_context_from_cwd(projects_dir, project_dir)
        assert project == "myproject"
        assert worktree is None

    def test_in_worktree(self, tmp_path):
        """Test when cwd is in a worktree directory with new naming format."""
        projects_dir = tmp_path / "Projects"
        # Folder is now named <project>-<worktree>
        worktree_dir = projects_dir / "myproject" / "myproject-alpha"
        worktree_dir.mkdir(parents=True)

        project, worktree = detect_context_from_cwd(projects_dir, worktree_dir)
        assert project == "myproject"
        # Should extract the worktree name from folder
        assert worktree == "alpha"

    def test_in_worktree_subdir(self, tmp_path):
        """Test when cwd is in a subdirectory of worktree."""
        projects_dir = tmp_path / "Projects"
        # Folder is now named <project>-<worktree>
        subdir = projects_dir / "myproject" / "myproject-bravo" / "src" / "components"
        subdir.mkdir(parents=True)

        project, worktree = detect_context_from_cwd(projects_dir, subdir)
        assert project == "myproject"
        # Should extract the worktree name from folder
        assert worktree == "bravo"


class TestLoadGlobalConfig:
    """Tests for load_global_config function."""

    def test_default_when_no_file(self, tmp_path, monkeypatch):
        """Test default config when ~/.maelstrom.yaml doesn't exist."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config = load_global_config()
        assert config.projects_dir == tmp_path / "Projects"

    def test_loads_from_file(self, tmp_path, monkeypatch):
        """Test loading projects_dir from config file."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text("projects_dir: /custom/path")

        config = load_global_config()
        assert config.projects_dir == Path("/custom/path")

    def test_expands_tilde(self, tmp_path, monkeypatch):
        """Test that ~ in projects_dir is expanded."""
        # Use HOME env var which expanduser() actually uses
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text("projects_dir: ~/CustomProjects")

        config = load_global_config()
        assert config.projects_dir == tmp_path / "CustomProjects"

    def test_invalid_yaml_returns_default(self, tmp_path, monkeypatch):
        """Test that invalid YAML returns default config."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text("invalid: yaml: content: [")

        config = load_global_config()
        assert config.projects_dir == tmp_path / "Projects"


class TestResolveContext:
    """Tests for resolve_context function."""

    def test_explicit_project_and_worktree(self, tmp_path, monkeypatch):
        """Test fully explicit project.worktree argument."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {tmp_path}/Projects")

        ctx = resolve_context("myproject.alpha")
        assert ctx.project == "myproject"
        assert ctx.worktree == "alpha"
        assert ctx.project_path == tmp_path / "Projects" / "myproject"
        # Folder name is now <project>-<worktree>
        assert ctx.worktree_path == tmp_path / "Projects" / "myproject" / "myproject-alpha"

    def test_explicit_project_only(self, tmp_path, monkeypatch):
        """Test project-only argument when not in project dir."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {tmp_path}/Projects")
        (tmp_path / "Projects").mkdir()

        # Call from outside projects dir
        ctx = resolve_context("myproject", cwd=tmp_path)
        assert ctx.project == "myproject"
        assert ctx.worktree is None

    def test_worktree_from_cwd_context(self, tmp_path, monkeypatch):
        """Test arg interpreted as worktree when in project dir."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        project_dir = projects_dir / "detected-project"
        project_dir.mkdir(parents=True)

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        # When inside a project dir, single arg is worktree
        ctx = resolve_context("feature-branch", cwd=project_dir)
        assert ctx.project == "detected-project"
        assert ctx.worktree == "feature-branch"

    def test_both_from_cwd(self, tmp_path, monkeypatch):
        """Test both project and worktree detected from cwd."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        # Folder is now named <project>-<worktree>
        worktree_dir = projects_dir / "myproject" / "myproject-alpha"
        worktree_dir.mkdir(parents=True)

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        ctx = resolve_context(None, cwd=worktree_dir)
        assert ctx.project == "myproject"
        # Should extract the worktree name from folder
        assert ctx.worktree == "alpha"

    def test_require_project_fails_when_missing(self, tmp_path, monkeypatch):
        """Test that require_project=True raises error when project unknown."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        projects_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        with pytest.raises(ValueError, match="Could not determine project"):
            resolve_context(None, require_project=True, cwd=other_dir)

    def test_require_worktree_fails_when_missing(self, tmp_path, monkeypatch):
        """Test that require_worktree=True raises error when worktree unknown."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        project_dir = projects_dir / "myproject"
        project_dir.mkdir(parents=True)

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        with pytest.raises(ValueError, match="Could not determine worktree"):
            resolve_context("myproject", require_worktree=True, cwd=tmp_path)

    def test_explicit_overrides_detected(self, tmp_path, monkeypatch):
        """Test that explicit arg overrides detected context."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        worktree_dir = projects_dir / "detected-project" / "detected-worktree"
        worktree_dir.mkdir(parents=True)

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        # Explicit project.worktree should override detected
        ctx = resolve_context("explicit.worktree", cwd=worktree_dir)
        assert ctx.project == "explicit"
        assert ctx.worktree == "worktree"

    def test_arg_is_project_prevents_reinterpretation(self, tmp_path, monkeypatch):
        """Test that arg_is_project=True keeps arg as project name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        # Create two projects
        project_a = projects_dir / "project-a"
        project_b = projects_dir / "project-b"
        project_a.mkdir(parents=True)
        project_b.mkdir(parents=True)

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        # When inside project-a, passing "project-b" with arg_is_project=True
        # should treat it as a project name, not a worktree name
        ctx = resolve_context("project-b", cwd=project_a, arg_is_project=True)
        assert ctx.project == "project-b"
        assert ctx.worktree is None

    def test_arg_without_arg_is_project_is_reinterpreted(self, tmp_path, monkeypatch):
        """Test that without arg_is_project, arg becomes worktree when in project."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projects_dir = tmp_path / "Projects"
        project_a = projects_dir / "project-a"
        project_a.mkdir(parents=True)

        config_file = tmp_path / ".maelstrom.yaml"
        config_file.write_text(f"projects_dir: {projects_dir}")

        # When inside project-a, passing "alpha" without arg_is_project
        # should treat it as a worktree name (existing behavior)
        ctx = resolve_context("alpha", cwd=project_a, arg_is_project=False)
        assert ctx.project == "project-a"
        assert ctx.worktree == "alpha"
