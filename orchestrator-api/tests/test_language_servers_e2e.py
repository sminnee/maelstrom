"""A definition from the real Pyright, read at the rev and not at the working tree."""

import pytest
from git_helpers import create_commit, setup_git_repo, setup_origin_main
from git_helpers import run_git as git

from mael_domain.language_servers import LANGUAGE_SERVERS
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
