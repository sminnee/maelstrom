"""A definition from the real Pyright, read at the rev and not at the working tree."""

import pytest
from git_helpers import create_commit, setup_git_repo, setup_origin_main
from git_helpers import run_git as git

from mael_domain.language_servers import LANGUAGE_SERVERS, server_for
from mael_orchestrator.language_servers import LanguageServers

# The ``pyright`` dev dependency puts the server on ``PATH`` under ``uv run``.
pytestmark = pytest.mark.e2e


async def test_a_definition_comes_back_at_the_revs_line(tmp_path):
    repo = tmp_path.resolve()
    setup_git_repo(repo)
    git(repo, "checkout", "-b", "main")
    create_commit(repo, "readme.md", "one\n", "chore: start")
    setup_origin_main(repo)
    git(repo, "checkout", "-b", "feat/orders")
    create_commit(repo, "models.py", "class Order:\n    pass\n", "feat: models")
    create_commit(
        repo, "orders.py", "from models import Order\n\nOrder()\n", "feat: orders"
    )
    # The working tree moves the definition three lines down.
    (repo / "models.py").write_text("\n\n\nclass Order:\n    pass\n")

    servers = LanguageServers(LANGUAGE_SERVERS)
    try:
        definition = await servers.definition(
            "alpha", repo, "branch", "orders.py", line=3, character=0
        )
    finally:
        await servers.stop_all()

    assert definition["reason"] == "ok"
    # The click is on a use, so the target is the class's Fragment at the rev, and
    # no uses are listed.
    assert definition["targets"][0] == {
        "path": "models.py",
        "range": {"start": 1, "end": 2},
        "inRev": True,
        "symbol": "Order",
    }
    assert definition["references"] is None


# ``typescript-language-server`` and ``typescript`` come from ``PATH``, as on a
# host that ``mael self check-host`` set up: the repo has no ``node_modules``.
async def test_a_typescript_definition_comes_back_at_the_revs_line(tmp_path):
    repo = tmp_path.resolve()
    setup_git_repo(repo)
    git(repo, "checkout", "-b", "main")
    create_commit(repo, "readme.md", "one\n", "chore: start")
    setup_origin_main(repo)
    git(repo, "checkout", "-b", "feat/ui")
    (repo / "ui").mkdir()
    create_commit(
        repo,
        "ui/tsconfig.json",
        '{"compilerOptions": {"jsx": "react-jsx", "strict": true}}\n',
        "feat: ui",
    )
    create_commit(
        repo,
        "ui/a.ts",
        "export const total = (n: number) => {\n  const twice = n * 2;\n"
        "  return twice;\n};\n",
        "feat: a",
    )
    create_commit(
        repo,
        "ui/b.tsx",
        'import { total } from "./a";\n\nexport const B = () => <p>{total(1)}</p>;\n',
        "feat: b",
    )
    # The working tree moves the definition three lines down.
    (repo / "ui/a.ts").write_text(
        "\n\n\nexport const total = (n: number) => {\n  return n * 2;\n};\n"
    )

    assert server_for(repo, "ui/a.ts") is not None, (
        "typescript-language-server is not on PATH: `mael self check-host` offers it"
    )
    servers = LanguageServers(LANGUAGE_SERVERS)
    try:
        # The repo loads before the first request arrives: this does not show
        # the cold start. The route tests own that.
        on_name = await servers.definition(
            "alpha", repo, "branch", "ui/a.ts", line=1, character=14
        )
        definition = await servers.definition(
            "alpha", repo, "branch", "ui/b.tsx", line=3, character=29
        )
    finally:
        await servers.stop_all()

    # b.tsx is not open in the server: its Fragment needs the open before the
    # symbol request.
    # The import has no Fragment, so it gets the window.
    assert on_name["references"] == [
        {"path": "ui/b.tsx", "range": {"start": 1, "end": 26}, "inRev": True},
        {
            "path": "ui/b.tsx",
            "range": {"start": 3, "end": 3},
            "inRev": True,
            "symbol": "B",
        },
    ]
    assert definition["reason"] == "ok"
    # The top-level const arrow function is the Fragment, not the const in it.
    assert definition["targets"][0] == {
        "path": "ui/a.ts",
        "range": {"start": 1, "end": 4},
        "inRev": True,
        "symbol": "total",
    }
