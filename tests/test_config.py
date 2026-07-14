"""Tests for maelstrom.config module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from maelstrom.config import (
    CONFIG_FILENAME,
    MaelstromConfig,
    PortSpec,
    find_config_file,
    load_config,
    load_config_or_default,
    service_port_names,
    shared_service_port_names,
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

    def test_from_dict_uptimerobot_monitors(self):
        """Test parsing uptimerobot.monitors list."""
        data = {"uptimerobot": {"monitors": ["111", "222"]}}
        config = MaelstromConfig.from_dict(data)
        assert config.uptimerobot_monitors == ["111", "222"]

    def test_from_dict_uptimerobot_missing(self):
        """Test that missing uptimerobot block leaves monitors as None."""
        config = MaelstromConfig.from_dict({})
        assert config.uptimerobot_monitors is None

    def test_from_dict_uptimerobot_invalid_block(self):
        """Test that a non-dict uptimerobot block is ignored gracefully."""
        config = MaelstromConfig.from_dict({"uptimerobot": "garbage"})
        assert config.uptimerobot_monitors is None


class TestServicesConfig:
    """Tests for structured `services:` parsing and derived helpers."""

    def test_parse_command_service(self):
        """A command service infers type and keeps dir/command."""
        data = {
            "services": {
                "frontend": {
                    "dir": "frontend",
                    "command": "node server.ts",
                    "ports": ["FRONTEND", "FRONTEND_HMR"],
                },
            }
        }
        config = MaelstromConfig.from_dict(data)
        assert len(config.services) == 1
        svc = config.services[0]
        assert svc.name == "frontend"
        assert svc.engine is None
        assert svc.is_container is False
        assert svc.dir == "frontend"
        assert svc.command == "node server.ts"
        assert svc.ports == [PortSpec(name="FRONTEND"), PortSpec(name="FRONTEND_HMR")]

    def test_parse_docker_service(self):
        """A docker service infers container type."""
        data = {
            "services": {
                "db": {
                    "shared": True,
                    "engine": "docker",
                    "image": "postgres:16",
                    "ports": ["DB"],
                    "publish": ["${DB_PORT}:5432"],
                    "volume": "/var/lib/postgresql/data",
                },
            }
        }
        config = MaelstromConfig.from_dict(data)
        svc = config.services[0]
        assert svc.engine == "docker"
        assert svc.is_container is True
        assert svc.shared is True
        assert svc.image == "postgres:16"
        assert svc.publish == ["${DB_PORT}:5432"]
        assert svc.volume == "/var/lib/postgresql/data"

    def test_parse_apple_container_service(self):
        """An apple-container service parses host_var and env."""
        data = {
            "services": {
                "db": {
                    "shared": True,
                    "engine": "apple-container",
                    "image": "pgvector/pgvector:pg16",
                    "ports": ["DB"],
                    "host_var": "DB_HOST",
                    "env": {"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"},
                },
            }
        }
        config = MaelstromConfig.from_dict(data)
        svc = config.services[0]
        assert svc.engine == "apple-container"
        assert svc.host_var == "DB_HOST"
        assert svc.env == {"POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}"}

    def test_shared_defaults_false(self):
        """shared defaults to False when omitted."""
        data = {"services": {"worker": {"command": "serve"}}}
        config = MaelstromConfig.from_dict(data)
        assert config.services[0].shared is False

    def test_no_services_key(self):
        """Absent services key yields an empty list, legacy fields intact."""
        config = MaelstromConfig.from_dict({"port_names": ["APP"]})
        assert config.services == []
        assert config.port_names == ["APP"]

    def test_command_service_requires_command(self):
        """A non-container service with no command is rejected."""
        data = {"services": {"broken": {"dir": "x"}}}
        with pytest.raises(ValueError, match="requires 'command'"):
            MaelstromConfig.from_dict(data)

    def test_container_service_requires_image(self):
        """A container service with no image is rejected."""
        data = {"services": {"db": {"engine": "docker"}}}
        with pytest.raises(ValueError, match="requires 'image'"):
            MaelstromConfig.from_dict(data)

    def test_unknown_engine_rejected(self):
        """An unrecognised engine is rejected."""
        data = {"services": {"db": {"engine": "podman", "image": "x"}}}
        with pytest.raises(ValueError, match="unknown engine"):
            MaelstromConfig.from_dict(data)

    def test_ports_must_be_list_of_strings(self):
        """A non-list (or non-string-element) `ports` is rejected clearly."""
        data = {"services": {"a": {"command": "x", "ports": "FRONTEND"}}}
        with pytest.raises(ValueError, match="'ports' must be a list"):
            MaelstromConfig.from_dict(data)

    def test_host_var_only_on_apple_container(self):
        """host_var on a docker service is rejected (apple-container only)."""
        data = {
            "services": {
                "db": {"engine": "docker", "image": "postgres", "host_var": "DB_HOST"}
            }
        }
        with pytest.raises(ValueError, match="host_var"):
            MaelstromConfig.from_dict(data)

    def test_service_port_names_declaration_order(self):
        """Non-shared port names come out in declaration order, deduped."""
        data = {
            "services": {
                "frontend": {"command": "x", "ports": ["FRONTEND", "FRONTEND_HMR"]},
                "server": {"command": "y", "ports": ["SERVER"]},
                "db": {"shared": True, "engine": "docker", "image": "z", "ports": ["DB"]},
            }
        }
        config = MaelstromConfig.from_dict(data)
        assert service_port_names(config) == ["FRONTEND", "FRONTEND_HMR", "SERVER"]

    def test_shared_service_port_names(self):
        """Only shared service ports appear in the shared list."""
        data = {
            "services": {
                "frontend": {"command": "x", "ports": ["FRONTEND"]},
                "db": {"shared": True, "engine": "docker", "image": "z", "ports": ["DB"]},
                "redis": {"shared": True, "engine": "docker", "image": "r", "ports": ["REDIS"]},
            }
        }
        config = MaelstromConfig.from_dict(data)
        assert shared_service_port_names(config) == ["DB", "REDIS"]

    def test_port_names_deduped(self):
        """A port name owned by two services appears once, first-seen order."""
        data = {
            "services": {
                "a": {"command": "x", "ports": ["SHARED_PORT", "A"]},
                "b": {"command": "y", "ports": ["SHARED_PORT", "B"]},
            }
        }
        config = MaelstromConfig.from_dict(data)
        assert service_port_names(config) == ["SHARED_PORT", "A", "B"]

    def test_load_from_yaml(self):
        """A services block round-trips through the YAML loader."""
        yaml_text = (
            "services:\n"
            "  worker:\n"
            "    command: serve worker\n"
            "  db:\n"
            "    shared: true\n"
            "    engine: apple-container\n"
            "    image: postgres:16\n"
            "    ports: [DB]\n"
            "    host_var: DB_HOST\n"
        )
        with TemporaryDirectory() as tmpdir:
            wt = Path(tmpdir)
            (wt / CONFIG_FILENAME).write_text(yaml_text)
            config = load_config(wt)
        names = [s.name for s in config.services]
        assert names == ["worker", "db"]
        assert config.services[1].engine == "apple-container"


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
