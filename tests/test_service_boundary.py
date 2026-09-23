"""Import boundaries: the daemon meets its clients only at the wire contract,
and no domain module reaches click or a CLI module.

A static walk over ``src/maelstrom``: every ``import`` at any depth, including
function-local and ``TYPE_CHECKING`` ones, counts. A lazy import still ties a
module to the module it names, and a later file move breaks on it all the same.
"""

import ast
from collections import deque
from pathlib import Path

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
    return _imports_in(MODULES[name].read_text(), name, _is_package(name))


def _imports_in(source: str, name: str, is_package: bool) -> set[str]:
    """Every in-package module ``source`` imports, read as module ``name``."""
    tree = ast.parse(source)
    package = name if is_package else name.rpartition(".")[0]
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


def _foreign(name: str) -> set[str]:
    """The top-level names of the non-package imports of ``name``, at any depth."""
    return _foreign_in(MODULES[name].read_text())


def _foreign_in(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            names = [node.module]
        else:
            continue
        found |= {n.split(".")[0] for n in names} - {PACKAGE}
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

#: Every module but the daemon's own is a client, bar the two that wire the
#: daemon's CLI group in.
CLIENTS = sorted(
    set(MODULES) - DAEMON_ALLOWED - _qualified("agent_daemon_cli", "cli", "__main__")
)


def test_the_daemon_reaches_only_its_own_modules():
    closure = _closure(f"{PACKAGE}.agent_server")
    strays = sorted(set(closure) - DAEMON_ALLOWED)
    assert not strays, "\n".join(_chain(closure, s) for s in strays)


def test_no_client_reaches_the_daemon_internals():
    chains = []
    for client in CLIENTS:
        closure = _closure(client)
        chains += [_chain(closure, s) for s in sorted(set(closure) & DAEMON_ONLY)]
    assert not chains, "\n".join(chains)


def _is_domain(name: str) -> bool:
    short = name.removeprefix(f"{PACKAGE}.")
    if short.endswith("_cli") or short == "worktree_launcher":
        return False
    return short.startswith(("task", "worktree", "github", "env", "state_db")) or (
        short.startswith("integrations.")
        or short in ("list_all", "orchestrator.protocol", "orchestrator.normalise")
    )


#: The domain modules: none may reach click or a CLI module.
DOMAIN = sorted(n for n in MODULES if _is_domain(n))


def _is_cli(name: str) -> bool:
    return (
        name in _qualified("cli", "cli_async")
        or name.endswith("_cli")
        or "click" in _foreign(name)
    )


def test_the_domain_reaches_no_cli():
    chains = []
    for module in DOMAIN:
        closure = _closure(module)
        chains += [_chain(closure, m) for m in sorted(closure) if _is_cli(m)]
    assert not chains, "\n".join(chains)


# --- the walker itself: a bug here passes the closure tests vacuously -------


def _walk(source: str, name: str = f"{PACKAGE}.orchestrator.server") -> set[str]:
    return _imports_in(source, name, is_package=False)


def test_the_walk_sees_a_function_local_import():
    source = "def f():\n    from ..agent_model import AgentState\n"
    assert _walk(source) == {f"{PACKAGE}.agent_model"}


def test_the_walk_sees_a_type_checking_import():
    source = "if TYPE_CHECKING:\n    from ..agent_server import AgentDaemon\n"
    assert _walk(source) == {f"{PACKAGE}.agent_server"}


def test_a_bare_relative_import_names_the_submodule():
    """``from .. import agent_model`` imports the module, not the package."""
    assert _walk("from .. import agent_model, __version__\n") == {
        f"{PACKAGE}.agent_model",
        PACKAGE,
    }


def test_a_sibling_import_resolves_inside_the_package():
    assert _walk("from .daemon_bridge import DaemonRouter\n") == {
        f"{PACKAGE}.orchestrator.daemon_bridge"
    }


def test_an_absolute_import_counts_and_a_foreign_one_does_not():
    source = "import json\nfrom maelstrom.agent_wire import IDLE\n"
    assert _walk(source) == {f"{PACKAGE}.agent_wire"}


def test_the_foreign_walk_sees_a_function_local_import():
    source = (
        "def f():\n    import click\nfrom maelstrom.util import x\nfrom . import y\n"
    )
    assert _foreign_in(source) == {"click"}


def test_the_domain_holds_the_named_modules():
    assert set(DOMAIN) >= _qualified(
        "task",
        "task_actions",
        "worktree",
        "list_all",
        "integrations.linear",
        "orchestrator.protocol",
        "state_db",
    )
    assert not set(DOMAIN) & _qualified("task_cli", "worktree_launcher")


def test_the_cli_test_names_the_cli_modules():
    assert all(
        _is_cli(m)
        for m in _qualified("cli", "cli_async", "task_cli", "integrations.group_cli")
    )
    assert not any(_is_cli(m) for m in _qualified("task", "integrations.linear"))
