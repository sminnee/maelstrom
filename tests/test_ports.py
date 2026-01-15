"""Tests for maelstrom.ports module."""

import socket
from unittest.mock import patch

import pytest

from maelstrom.ports import (
    allocate_port_base,
    check_ports_free,
    generate_port_env_vars,
    is_port_free,
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

    def test_finds_first_available(self):
        """Test that it finds the first available port base."""
        from pathlib import Path

        # Mock check_ports_free to return False for first few bases
        with patch("maelstrom.ports.check_ports_free") as mock_check:
            mock_check.side_effect = lambda base, num: base >= 105
            result = allocate_port_base(Path("/tmp"), num_ports=5)
            assert result == 105

    def test_no_available_ports(self):
        """Test that RuntimeError is raised when no ports available."""
        from pathlib import Path

        with patch("maelstrom.ports.check_ports_free", return_value=False):
            with pytest.raises(RuntimeError, match="No available port ranges"):
                allocate_port_base(Path("/tmp"))


class TestGeneratePortEnvVars:
    """Tests for generate_port_env_vars function."""

    def test_generates_correct_vars(self):
        """Test that correct environment variables are generated."""
        port_names = ["FRONTEND", "SERVER", "DB"]
        result = generate_port_env_vars(100, port_names)

        assert result == {
            "PORT_BASE": "100",
            "FRONTEND_PORT": "1000",
            "SERVER_PORT": "1001",
            "DB_PORT": "1002",
        }

    def test_empty_port_names(self):
        """Test with empty port names list."""
        result = generate_port_env_vars(100, [])
        assert result == {"PORT_BASE": "100"}

    def test_different_port_base(self):
        """Test with a different port base."""
        port_names = ["WEB", "API"]
        result = generate_port_env_vars(250, port_names)

        assert result == {
            "PORT_BASE": "250",
            "WEB_PORT": "2500",
            "API_PORT": "2501",
        }
