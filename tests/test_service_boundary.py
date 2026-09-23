"""The agent daemon and its clients meet only at the wire contract.

A static walk over ``src/maelstrom``: every ``import`` at any depth, including
function-local and ``TYPE_CHECKING`` ones, counts. A lazy import still ties the
daemon to the module it names, and a later file move breaks on it all the same.
"""

import ast
from collections import deque
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"
PACKAGE = "maelstrom"


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _modules() -> dict[str, Path]:
    return {_module_name(p): p for p in (SRC / PACKAGE).rglob("*.py")}


MODULES = _modules()


def _is_package(name: str) -> bool:
    return MODULES[name].name == "__init__.py"


def _resolve(name: str) -> str | None:
    """``name``, or the nearest enclosing module that exists, or ``None``."""
    while name:
        if name in MODULES:
            return name
        name = name.rpartition(".")[0]
    return None


def _imports(name: str) -> set[str]:
    """Every in-package module ``name`` imports, at any depth."""
    tree = ast.parse(MODULES[name].read_text())
    package = name if _is_package(name) else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                base = f"{base}.{node.module}" if node.module else base
            else:
                base = node.module or ""
            # ``from . import x`` names the submodule when there is one.
            targets = [
                f"{base}.{alias.name}" if f"{base}.{alias.name}" in MODULES else base
                for alias in node.names
            ]
        else:
            continue
        for target in targets:
            if target.split(".")[0] != PACKAGE:
                continue
            resolved = _resolve(target)
            if resolved and resolved != name:
                found.add(resolved)
    return found


def _closure(root: str) -> dict[str, str | None]:
    """Every module ``root`` reaches, mapped to the module that imported it."""
    seen: dict[str, str | None] = {root: None}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for target in sorted(_imports(current)):
            if target not in seen:
                seen[target] = current
                queue.append(target)
    return seen


def _chain(closure: dict[str, str | None], target: str) -> str:
    """The import chain from the closure's root to ``target``, for a failure."""
    links = [target]
    while (parent := closure[links[-1]]) is not None:
        links.append(parent)
    return " -> ".join(reversed(links))


def _qualified(*names: str) -> set[str]:
    return {f"{PACKAGE}.{n}" if n else PACKAGE for n in names}


DAEMON_ALLOWED = _qualified(
    "",
    "agent_server",
    "agent_model",
    "agent_wire",
    "agent_transport",
    "agent_spec_store",
    "agent_reconcile",
    "harness_model",
    "codex_daemon",
    "codex_bridge",
    "transcript_store",
    "claude_paths",
    "process_table",
    "image",
    "util",
    "shell",
)

DAEMON_ONLY = _qualified("agent_model", "agent_server")

CLIENTS = sorted(
    _qualified("agent_cli", "agent_view", "agent_tui")
    | {name for name in MODULES if name.startswith(f"{PACKAGE}.orchestrator.")}
)


def test_the_daemon_reaches_only_its_own_modules():
    closure = _closure(f"{PACKAGE}.agent_server")
    strays = sorted(set(closure) - DAEMON_ALLOWED)
    assert not strays, "\n".join(_chain(closure, s) for s in strays)


@pytest.mark.xfail(strict=True, reason="the boundary is drawn step by step")
def test_no_client_reaches_the_daemon_internals():
    chains = []
    for client in CLIENTS:
        closure = _closure(client)
        chains += [_chain(closure, s) for s in sorted(set(closure) & DAEMON_ONLY)]
    assert not chains, "\n".join(chains)


def test_the_walk_sees_a_function_local_import():
    """The walker's own seam: a lazy import is still an import."""
    assert f"{PACKAGE}.agent_tui" in _imports(f"{PACKAGE}.agent_cli")
