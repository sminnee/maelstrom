"""Which language server reads a file, and the command that starts it."""

from pathlib import Path

import pytest

from mael_domain.language_servers import server_for


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def bin_on_path(tmp_path, monkeypatch) -> Path:
    """A ``PATH`` that holds only an empty folder, so the real machine's tools stay out."""
    folder = tmp_path / "bin"
    folder.mkdir()
    monkeypatch.setenv("PATH", str(folder))
    return folder


def test_a_python_file_uses_the_worktrees_venv_server(tmp_path, bin_on_path):
    worktree = tmp_path / "alpha"
    venv = _executable(worktree / ".venv/bin/pyright-langserver")
    _executable(bin_on_path / "pyright-langserver")

    found = server_for(worktree, "lib/domain/src/mael_domain/diff_groups.py")

    assert found is not None
    server, command = found
    assert server.name == "pyright"
    assert command == [str(venv), "--stdio"]


def test_a_python_file_falls_back_to_the_server_on_path(tmp_path, bin_on_path):
    on_path = _executable(bin_on_path / "pyright-langserver")

    found = server_for(tmp_path / "alpha", "orders.py")

    assert found is not None
    assert found[1] == [str(on_path), "--stdio"]


def test_a_python_file_with_no_server_installed_is_not_navigable(tmp_path, bin_on_path):
    assert server_for(tmp_path / "alpha", "orders.py") is None


def test_a_file_that_is_not_python_has_no_server(tmp_path, bin_on_path):
    _executable(bin_on_path / "pyright-langserver")

    assert server_for(tmp_path / "alpha", "readme.md") is None
    assert server_for(tmp_path / "alpha", "orders.pyc") is None
