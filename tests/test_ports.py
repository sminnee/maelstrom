"""Tests for maelstrom.ports module."""

import socket
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from maelstrom.ports import (
    allocate_port_base,
    check_ports_free,
    generate_port_env_vars,
    get_allocated_port_bases,
    get_app_url,
    get_port_allocation,
    is_port_free,
    load_port_allocations,
    record_port_allocation,
    remove_port_allocation,
    save_port_allocations,
    wait_for_port,
)


class TestIsPortFree:
    """Tests for is_port_free function."""

    def test_free_port(self):
        """Test that an unused port returns True."""
        # Port 59999 is unlikely to be in use
        assert is_port_free(59999) is True

    def test_occupied_port(self):
        """Test that an occupied port returns False."""
        # Bind to a port and check it's detected as occupied
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 59998))
            s.listen(1)
            assert is_port_free(59998) is False


class TestWaitForPort:
    """Tests for wait_for_port function."""

    def test_port_already_listening(self):
        """Test immediate return when port is already listening."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 59997))
            s.listen(1)
            assert wait_for_port(59997, timeout=1.0) is True

    def test_timeout_when_port_never_listens(self):
        """Test that False is returned after timeout when port stays free."""
        assert wait_for_port(59996, timeout=0.5, interval=0.1) is False

    def test_port_becomes_available_during_wait(self):
        """Test detection when port starts listening mid-wait."""
        import threading

        def start_listening():
            time.sleep(0.3)
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 59995))
            srv.listen(1)
            # Keep socket open until test completes
            time.sleep(2.0)
            srv.close()

        t = threading.Thread(target=start_listening, daemon=True)
        t.start()
        assert wait_for_port(59995, timeout=2.0, interval=0.1) is True


class TestCheckPortsFree:
    """Tests for check_ports_free function."""

    def test_all_ports_free(self):
        """Test when all ports in range are free."""
        # Use a high port base that's unlikely to be in use
        assert check_ports_free(5999, num_ports=5) is True

    def test_one_port_occupied(self):
        """Test when one port in range is occupied."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Occupy port 59993 (base 5999, suffix 3)
            s.bind(("127.0.0.1", 59993))
            s.listen(1)
            assert check_ports_free(5999, num_ports=5) is False


class TestAllocatePortBase:
    """Tests for allocate_port_base function."""

    def test_finds_first_available(self, tmp_path, monkeypatch):
        """Test that it finds the first available port base."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Mock check_ports_free to return False for first few bases
        with patch("maelstrom.ports.check_ports_free") as mock_check:
            mock_check.side_effect = lambda base, num: base >= 305
            result = allocate_port_base(Path("/tmp"), num_ports=5)
            assert result == 305

    def test_no_available_ports(self, tmp_path, monkeypatch):
        """Test that RuntimeError is raised when no ports available."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch("maelstrom.ports.check_ports_free", return_value=False):
            with pytest.raises(RuntimeError, match="No available port ranges"):
                allocate_port_base(Path("/tmp"))

    def test_skips_allocated_bases(self, tmp_path, monkeypatch):
        """Test that allocate_port_base skips already-allocated bases."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        # Allocate port base 300 to another worktree
        record_port_allocation(project_path, "alpha", 300)

        # Mock all sockets as free
        with patch("maelstrom.ports.check_ports_free", return_value=True):
            result = allocate_port_base(project_path, num_ports=5)
            # Should skip 300 (allocated) and return 301
            assert result == 301

    def test_skips_multiple_allocated_bases(self, tmp_path, monkeypatch):
        """Test that multiple allocated bases are all skipped."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        record_port_allocation(project_path, "alpha", 300)
        record_port_allocation(project_path, "bravo", 301)
        record_port_allocation(project_path, "charlie", 302)

        with patch("maelstrom.ports.check_ports_free", return_value=True):
            result = allocate_port_base(project_path, num_ports=5)
            assert result == 303


class TestGeneratePortEnvVars:
    """Tests for generate_port_env_vars function."""

    def test_generates_correct_vars(self):
        """Test that correct environment variables are generated."""
        port_names = ["FRONTEND", "SERVER", "DB"]
        result = generate_port_env_vars(300, port_names)

        assert result == {
            "PORT_BASE": "300",
            "FRONTEND_PORT": "3000",
            "SERVER_PORT": "3001",
            "DB_PORT": "3002",
        }

    def test_empty_port_names(self):
        """Test with empty port names list."""
        result = generate_port_env_vars(300, [])
        assert result == {"PORT_BASE": "300"}

    def test_different_port_base(self):
        """Test with a different port base."""
        port_names = ["WEB", "API"]
        result = generate_port_env_vars(350, port_names)

        assert result == {
            "PORT_BASE": "350",
            "WEB_PORT": "3500",
            "API_PORT": "3501",
        }


class TestPortAllocations:
    """Tests for persistent port allocation tracking."""

    def test_load_empty_allocations(self, tmp_path, monkeypatch):
        """Test loading when no file exists."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        allocations = load_port_allocations()
        assert allocations == {}

    def test_record_and_load(self, tmp_path, monkeypatch):
        """Test recording and loading a port allocation."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        record_port_allocation(project_path, "alpha", 300)

        allocations = load_port_allocations()
        project_key = str(project_path.resolve())
        assert allocations[project_key]["alpha"] == 300

    def test_record_multiple_worktrees(self, tmp_path, monkeypatch):
        """Test recording allocations for multiple worktrees."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        record_port_allocation(project_path, "alpha", 300)
        record_port_allocation(project_path, "bravo", 301)

        allocations = load_port_allocations()
        project_key = str(project_path.resolve())
        assert allocations[project_key]["alpha"] == 300
        assert allocations[project_key]["bravo"] == 301

    def test_record_across_projects(self, tmp_path, monkeypatch):
        """Test recording allocations for different projects."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_a = tmp_path / "Projects" / "project-a"
        project_b = tmp_path / "Projects" / "project-b"
        project_a.mkdir(parents=True)
        project_b.mkdir(parents=True)

        record_port_allocation(project_a, "alpha", 300)
        record_port_allocation(project_b, "alpha", 301)

        allocations = load_port_allocations()
        assert allocations[str(project_a.resolve())]["alpha"] == 300
        assert allocations[str(project_b.resolve())]["alpha"] == 301

    def test_remove_allocation(self, tmp_path, monkeypatch):
        """Test removing a port allocation."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        record_port_allocation(project_path, "alpha", 300)
        remove_port_allocation(project_path, "alpha")

        allocations = load_port_allocations()
        project_key = str(project_path.resolve())
        # Empty project entry should be cleaned up
        assert project_key not in allocations

    def test_remove_one_of_multiple(self, tmp_path, monkeypatch):
        """Test removing one allocation leaves others intact."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        record_port_allocation(project_path, "alpha", 300)
        record_port_allocation(project_path, "bravo", 301)
        remove_port_allocation(project_path, "alpha")

        allocations = load_port_allocations()
        project_key = str(project_path.resolve())
        assert "alpha" not in allocations[project_key]
        assert allocations[project_key]["bravo"] == 301

    def test_remove_nonexistent_is_noop(self, tmp_path, monkeypatch):
        """Test removing a nonexistent allocation does nothing."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        # Should not raise
        remove_port_allocation(project_path, "alpha")
        assert load_port_allocations() == {}

    def test_get_allocated_port_bases(self, tmp_path, monkeypatch):
        """Test extracting all allocated port bases."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_a = tmp_path / "Projects" / "project-a"
        project_b = tmp_path / "Projects" / "project-b"
        project_a.mkdir(parents=True)
        project_b.mkdir(parents=True)

        record_port_allocation(project_a, "alpha", 300)
        record_port_allocation(project_a, "bravo", 301)
        record_port_allocation(project_b, "alpha", 305)

        allocations = load_port_allocations()
        bases = get_allocated_port_bases(allocations)
        assert bases == {300, 301, 305}

    def test_get_port_allocation_exists(self, tmp_path, monkeypatch):
        """Test getting an existing allocation."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        record_port_allocation(project_path, "alpha", 300)
        assert get_port_allocation(project_path, "alpha") == 300

    def test_get_port_allocation_missing(self, tmp_path, monkeypatch):
        """Test getting a nonexistent allocation returns None."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        assert get_port_allocation(project_path, "alpha") is None

    def test_corrupt_json_returns_empty(self, tmp_path, monkeypatch):
        """Test that corrupt JSON file returns empty dict."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        alloc_dir = tmp_path / ".maelstrom"
        alloc_dir.mkdir()
        alloc_file = alloc_dir / "port_allocations.json"
        alloc_file.write_text("{invalid json}")

        allocations = load_port_allocations()
        assert allocations == {}

    def test_creates_maelstrom_dir(self, tmp_path, monkeypatch):
        """Test that saving creates ~/.maelstrom/ directory if needed."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert not (tmp_path / ".maelstrom").exists()

        save_port_allocations({"test": {"alpha": 300}})

        assert (tmp_path / ".maelstrom").exists()
        assert (tmp_path / ".maelstrom" / "port_allocations.json").exists()

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        """Test that save and load produce identical data."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        data = {
            "/path/to/project-a": {"alpha": 300, "bravo": 301},
            "/path/to/project-b": {"charlie": 400},
        }
        save_port_allocations(data)
        loaded = load_port_allocations()
        assert loaded == data


class TestGetAppUrl:
    """Tests for get_app_url function."""

    def test_no_allocation(self, tmp_path, monkeypatch):
        """Test that None is returned when no port allocation exists."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)

        assert get_app_url(project_path, "alpha") is None

    def test_with_allocation_port_free(self, tmp_path, monkeypatch):
        """Test URL returned with is_running=False when port is free."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)
        # Create worktree dir with config containing a web port name
        worktree_path = project_path / "alpha"
        worktree_path.mkdir()
        (worktree_path / ".maelstrom.yaml").write_text("port_names: [APP, SERVER]")

        record_port_allocation(project_path, "alpha", 300)

        with patch("maelstrom.ports.is_port_free", return_value=True):
            result = get_app_url(project_path, "alpha")

        assert result is not None
        url, is_running = result
        assert url == "http://localhost:3000"
        assert is_running is False

    def test_with_allocation_port_in_use(self, tmp_path, monkeypatch):
        """Test URL returned with is_running=True when port is in use."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)
        # Create worktree dir with config containing a web port name
        worktree_path = project_path / "bravo"
        worktree_path.mkdir()
        (worktree_path / ".maelstrom.yaml").write_text("port_names: [APP, DB]")

        record_port_allocation(project_path, "bravo", 599)

        with patch("maelstrom.ports.is_port_free", return_value=False):
            result = get_app_url(project_path, "bravo")

        assert result is not None
        url, is_running = result
        assert url == "http://localhost:5990"
        assert is_running is True

    def test_no_web_port_name(self, tmp_path, monkeypatch):
        """Test None returned when config has no web-facing port name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)
        worktree_path = project_path / "alpha"
        worktree_path.mkdir()
        (worktree_path / ".maelstrom.yaml").write_text("port_names: [SERVER, DB]")

        record_port_allocation(project_path, "alpha", 300)
        assert get_app_url(project_path, "alpha") is None

    def test_frontend_port_name(self, tmp_path, monkeypatch):
        """Test URL returned when FRONTEND port name is used."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        project_path = tmp_path / "Projects" / "myproject"
        project_path.mkdir(parents=True)
        worktree_path = project_path / "alpha"
        worktree_path.mkdir()
        (worktree_path / ".maelstrom.yaml").write_text("port_names: [SERVER, FRONTEND]")

        record_port_allocation(project_path, "alpha", 300)

        with patch("maelstrom.ports.is_port_free", return_value=True):
            result = get_app_url(project_path, "alpha")

        assert result is not None
        url, is_running = result
        # FRONTEND is at index 1, so port = 300 * 10 + 1 = 3001
        assert url == "http://localhost:3001"
        assert is_running is False
