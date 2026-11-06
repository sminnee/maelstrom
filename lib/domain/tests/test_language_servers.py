"""Which language server reads a file, the command that starts it, and its options."""

from pathlib import Path

import pytest

from mael_domain.language_servers import language_id, server_for


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
    assert found.server.name == "pyright"
    assert found.command == [str(venv), "--stdio"]
    assert found.options == {}


def test_a_python_file_falls_back_to_the_server_on_path(tmp_path, bin_on_path):
    on_path = _executable(bin_on_path / "pyright-langserver")

    found = server_for(tmp_path / "alpha", "orders.py")

    assert found is not None
    assert found.command == [str(on_path), "--stdio"]


def test_a_python_file_with_no_server_installed_is_not_navigable(tmp_path, bin_on_path):
    assert server_for(tmp_path / "alpha", "orders.py") is None


def test_a_file_no_server_reads_has_no_server(tmp_path, bin_on_path):
    _executable(bin_on_path / "pyright-langserver")
    _executable(bin_on_path / "typescript-language-server")

    assert server_for(tmp_path / "alpha", "readme.md") is None
    assert server_for(tmp_path / "alpha", "orders.pyc") is None
    assert server_for(tmp_path / "alpha", "ui/main.js") is None


@pytest.mark.parametrize("file", ["ui/src/app/App.tsx", "ui/src/store.ts"])
def test_a_typescript_file_uses_the_worktrees_shim_before_path(
    tmp_path, bin_on_path, file
):
    worktree = tmp_path / "alpha"
    shim = _executable(worktree / "node_modules/.bin/typescript-language-server")
    _executable(bin_on_path / "typescript-language-server")

    found = server_for(worktree, file)

    assert found is not None
    assert found.server.name == "typescript"
    assert found.command == [str(shim), "--stdio"]


def test_the_typescript_server_uses_the_nearest_typescript_above_the_file(
    tmp_path, bin_on_path
):
    worktree = tmp_path / "alpha"
    _executable(bin_on_path / "typescript-language-server")
    tsserver = "node_modules/typescript/lib/tsserver.js"
    for folder in (worktree, worktree / "ui"):
        (folder / tsserver).parent.mkdir(parents=True)
        (folder / tsserver).write_text("")

    in_ui = server_for(worktree, "ui/src/App.tsx")
    elsewhere = server_for(worktree, "jig/src/plugin.ts")

    assert in_ui is not None and elsewhere is not None
    assert in_ui.options == {"tsserver": {"path": str(worktree / "ui" / tsserver)}}
    assert elsewhere.options == {"tsserver": {"path": str(worktree / tsserver)}}


def test_with_no_project_typescript_the_server_picks_its_own(tmp_path, bin_on_path):
    _executable(bin_on_path / "typescript-language-server")

    found = server_for(tmp_path / "alpha", "ui/src/App.tsx")

    assert found is not None
    assert found.options == {}


@pytest.mark.parametrize(
    ("file", "expected"),
    [
        ("orders.py", "python"),
        ("ui/src/store.ts", "typescript"),
        ("ui/src/App.tsx", "typescriptreact"),
    ],
)
def test_the_language_id_comes_from_the_suffix(file, expected):
    assert language_id(file) == expected
