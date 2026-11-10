"""``mael merge``: the dispatch by ``merge:``, and the local-mode commands.

A real git project; the orchestrator's answers are stubbed at the client, and
the GitHub side at the function the legacy ``gh`` command shares.
"""

from dataclasses import replace

import pytest
from click.testing import CliRunner
from git_helpers import create_commit, push_to_origin_main, remote_tip, rev_parse

from mael_cli import github_cli
from mael_cli.cli import cli
from mael_cli.orchestrator_client import OrchestratorClient
from mael_domain.merge_request import Build, MergeRequest, merge_request_json


@pytest.fixture
def project(project_with_worktree, monkeypatch, tmp_path):
    """The project ``test-repo``, run from its worktree, with a task session."""
    project_path, worktree, remote = project_with_worktree
    root = tmp_path / "maelstrom"
    root.mkdir(exist_ok=True)
    (root / "config.yaml").write_text(f"projects_dir: {project_path.parent}\n")
    monkeypatch.chdir(worktree)
    monkeypatch.setenv("MAEL_TASK_ID", "k1")
    monkeypatch.delenv("MAEL_PARENT_URL", raising=False)
    return project_path, worktree, remote


def merge_local(worktree):
    create_commit(worktree, ".maelstrom.yaml", "merge: local\n", "chore: merge local")


class Server:
    """The orchestrator's replies, and the calls it took."""

    def __init__(self, monkeypatch, replies=None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.replies = list(replies or [])

        def answer(method):
            def call(_client, path, body=None):
                self.calls.append((method, path, body))
                return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

            return call

        monkeypatch.setattr(OrchestratorClient, "_call", None)
        for method in ("get", "put", "delete"):
            monkeypatch.setattr(OrchestratorClient, method, answer(method.upper()))
        monkeypatch.setattr(
            OrchestratorClient, "from_config", classmethod(lambda cls, **kw: cls("x"))
        )


def a_request(status="queued"):
    """The server's reply, built by the mapper the server answers with."""
    build_state = {"building": "running", "withdrawn": "cancelled"}.get(status, status)
    mr = MergeRequest(
        "mr1", "test-repo/k1", "test-repo", "k1", "feature/work", "a" * 40,
        "c" * 40, "open", "b1", "t0", "t0",
    )  # fmt: skip
    if status in ("withdrawn", "merged"):
        mr = replace(mr, state=status)
    build = Build("b1", "mr1", "test-repo", "a" * 40, "c" * 40, build_state, "t0")
    return merge_request_json(mr, [build])


def test_a_github_project_submits_through_create_pr(project, monkeypatch):
    seen = {}

    async def create_pr_command(**flags):
        seen.update(flags)

    monkeypatch.setattr(github_cli, "create_pr_command", create_pr_command)
    result = CliRunner().invoke(cli, ["merge", "submit", "--squash", "--draft"])
    assert result.exit_code == 0, result.output
    assert (seen["squash"], seen["draft"], seen["wait"]) == (True, True, False)


def test_a_local_submit_pushes_and_submits_head_and_its_base(project, monkeypatch):
    project_path, worktree, _ = project
    merge_local(worktree)
    server = Server(monkeypatch, [a_request()])

    result = CliRunner().invoke(cli, ["merge", "submit"])

    assert result.exit_code == 0, result.output
    head = rev_parse(worktree)
    assert remote_tip(worktree, "feature/work") == head
    (call,) = server.calls
    assert call == (
        "PUT",
        "/api/tasks/test-repo/k1/merge-request",
        {
            "branch": "feature/work",
            "sha": head,
            "base": rev_parse(project_path, "origin/main"),
        },
    )
    assert "Merge request mr1: Queue build b1" in result.output


def test_a_local_submit_behind_main_is_refused_and_names_sync(project, monkeypatch):
    project_path, worktree, remote = project
    merge_local(worktree)
    push_to_origin_main(project_path, remote)
    server = Server(monkeypatch, [a_request()])

    result = CliRunner().invoke(cli, ["merge", "submit"])

    assert result.exit_code == 1
    assert "mael sync" in result.output
    assert server.calls == []
    assert remote_tip(worktree, "feature/work") is None


def test_a_local_submit_refuses_a_github_only_flag(project, monkeypatch):
    merge_local(project[1])
    Server(monkeypatch, [a_request()])
    result = CliRunner().invoke(cli, ["merge", "submit", "--draft"])
    assert result.exit_code == 2
    assert "--draft: not used with merge: local" in result.output


@pytest.mark.parametrize(
    ("final", "code"), [("passed", 0), ("merged", 0), ("failed", 1), ("stale", 1)]
)
def test_local_status_wait_exits_on_the_settled_status(
    project, monkeypatch, final, code
):
    merge_local(project[1])
    Server(monkeypatch, [a_request("queued"), a_request("building"), a_request(final)])
    result = CliRunner().invoke(cli, ["merge", "status", "--wait", "--interval", "0"])
    assert result.exit_code == code, result.output
    assert f"Merge request mr1: {final}" in result.output


def test_local_status_wait_times_out_with_2(project, monkeypatch):
    merge_local(project[1])
    Server(monkeypatch, [a_request("building")])
    result = CliRunner().invoke(
        cli, ["merge", "status", "--wait", "--timeout", "0", "--interval", "0"]
    )
    assert result.exit_code == 2


def test_a_local_withdraw_deletes_the_request(project, monkeypatch):
    merge_local(project[1])
    server = Server(monkeypatch, [a_request("withdrawn")])
    result = CliRunner().invoke(cli, ["merge", "withdraw", "--task", "k2"])
    assert result.exit_code == 0, result.output
    assert server.calls == [("DELETE", "/api/tasks/test-repo/k2/merge-request", None)]
    assert "Withdrew Merge request mr1" in result.output
