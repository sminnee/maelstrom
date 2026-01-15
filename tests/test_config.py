"""Tests for maelstrom.config module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from maelstrom.config import (
    CONFIG_FILENAME,
    MaelstromConfig,
    find_config_file,
    load_config,
    load_config_or_default,
)


class TestMaelstromConfig:
    """Tests for MaelstromConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = MaelstromConfig()
        assert config.port_names == []
        assert config.start_cmd == ""
        assert config.install_cmd == ""

    def test_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "port_names": ["FRONTEND", "SERVER", "DB"],
            "start_cmd": "ult",
            "install_cmd": "npm install",
        }
        config = MaelstromConfig.from_dict(data)
        assert config.port_names == ["FRONTEND", "SERVER", "DB"]
        assert config.start_cmd == "ult"
        assert config.install_cmd == "npm install"

    def test_from_dict_partial(self):
        """Test creating config from partial dictionary."""
        data = {"port_names": ["WEB"]}
        config = MaelstromConfig.from_dict(data)
        assert config.port_names == ["WEB"]
        assert config.start_cmd == ""
        assert config.install_cmd == ""

    def test_from_dict_empty(self):
        """Test creating config from empty dictionary."""
        config = MaelstromConfig.from_dict({})
        assert config.port_names == []
        assert config.start_cmd == ""
        assert config.install_cmd == ""


class TestFindConfigFile:
    """Tests for find_config_file function."""

    def test_finds_config_in_current_dir(self):
        """Test finding config in current directory."""
        with TemporaryDirectory() as tmpdir:
            tmpdir_resolved = Path(tmpdir).resolve()
            config_path = tmpdir_resolved / CONFIG_FILENAME
            config_path.write_text("port_names: []")

            result = find_config_file(tmpdir_resolved)
            assert result == config_path

    def test_finds_config_in_parent_dir(self):
        """Test finding config in parent directory."""
        with TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir).resolve()
            child = parent / "subdir"
            child.mkdir()

            config_path = parent / CONFIG_FILENAME
            config_path.write_text("port_names: []")

            result = find_config_file(child)
            assert result == config_path

    def test_returns_none_when_not_found(self):
        """Test returning None when config not found."""
        with TemporaryDirectory() as tmpdir:
            result = find_config_file(Path(tmpdir))
            assert result is None

    def test_handles_file_path(self):
        """Test finding config when given a file path."""
        with TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir).resolve()
            config_path = parent / CONFIG_FILENAME
            config_path.write_text("port_names: []")

            some_file = parent / "some_file.txt"
            some_file.write_text("content")

            result = find_config_file(some_file)
            assert result == config_path


class TestLoadConfig:
    """Tests for load_config function."""

    def test_loads_valid_config(self):
        """Test loading a valid configuration file."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / CONFIG_FILENAME
            config_path.write_text(
                """
port_names:
  - FRONTEND
  - SERVER
  - DB
start_cmd: ult
install_cmd: npm install
"""
            )

            config = load_config(Path(tmpdir))
            assert config.port_names == ["FRONTEND", "SERVER", "DB"]
            assert config.start_cmd == "ult"
            assert config.install_cmd == "npm install"

    def test_raises_on_missing_config(self):
        """Test that FileNotFoundError is raised when config missing."""
        with TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                load_config(Path(tmpdir))

    def test_handles_empty_file(self):
        """Test loading an empty config file."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / CONFIG_FILENAME
            config_path.write_text("")

            config = load_config(Path(tmpdir))
            assert config.port_names == []
            assert config.start_cmd == ""
            assert config.install_cmd == ""


class TestLoadConfigOrDefault:
    """Tests for load_config_or_default function."""

    def test_loads_existing_config(self):
        """Test loading existing config."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / CONFIG_FILENAME
            config_path.write_text("port_names: [WEB]")

            config = load_config_or_default(Path(tmpdir))
            assert config.port_names == ["WEB"]

    def test_returns_default_when_missing(self):
        """Test returning default config when file missing."""
        with TemporaryDirectory() as tmpdir:
            config = load_config_or_default(Path(tmpdir))
            assert config.port_names == []
            assert config.start_cmd == ""
            assert config.install_cmd == ""
