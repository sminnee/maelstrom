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
        for method in ("get", "put", "post", "delete"):
            monkeypatch.setattr(OrchestratorClient, method, answer(method.upper()))
        monkeypatch.setattr(
            OrchestratorClient, "from_config", classmethod(lambda cls, **kw: cls("x"))
        )


def a_request(status="queued", sha="a" * 40):
    """The server's reply, built by the mapper the server answers with."""
    build_state = {"building": "running", "withdrawn": "cancelled"}.get(status, status)
    mr = MergeRequest(
        "mr1", "test-repo/k1", "test-repo", "k1", "feature/work", sha,
        "c" * 40, "open", "b1", "t0", "t0",
    )  # fmt: skip
    if status in ("withdrawn", "merged"):
        mr = replace(mr, state=status)
    build = Build("b1", "mr1", "test-repo", sha, "c" * 40, build_state, "t0")
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


def test_a_local_submit_sends_the_pr_draft_and_leaves_it(project, monkeypatch):
    _, worktree, _ = project
    merge_local(worktree)
    create_commit(worktree, ".gitignore", ".drafts/\n", "chore: ignore drafts")
    draft = worktree / ".drafts" / "pr.md"
    draft.parent.mkdir()
    draft.write_text("## Why\n\nFaster builds.\n")
    server = Server(monkeypatch, [a_request()])

    result = CliRunner().invoke(cli, ["merge", "submit"])

    assert result.exit_code == 0, result.output
    (call,) = server.calls
    assert call[2]["description"] == "## Why\n\nFaster builds.\n"
    assert draft.exists()


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


def test_a_local_retry_posts_the_rebuild(project, monkeypatch):
    merge_local(project[1])
    server = Server(monkeypatch, [a_request("queued")])
    result = CliRunner().invoke(cli, ["merge", "retry", "k2"])
    assert result.exit_code == 0, result.output
    assert server.calls == [
        ("POST", "/api/tasks/test-repo/k2/merge-request/retry", None)
    ]
    assert "Queue build b1 of aaaaaaa is queued" in result.output


def test_retry_in_a_github_project_is_refused(project, monkeypatch):
    server = Server(monkeypatch, [a_request("queued")])
    result = CliRunner().invoke(cli, ["merge", "retry"])
    assert result.exit_code == 2
    assert "not used with merge: github" in result.output
    assert server.calls == []


# -- `mael git merge` in a merge: local project --


def an_operation(state="done", words="Merged mr1 at aaaaaaa into main"):
    steps = [{"name": "branch", "state": "done", "words": "could not delete"}]
    return {
        "operations": [{"id": "op4", "state": state, "words": words, "steps": steps}]
    }


def test_git_merge_posts_the_passed_sha_and_waits_for_the_merge(project, monkeypatch):
    _, worktree, _ = project
    merge_local(worktree)
    head = rev_parse(worktree)
    server = Server(
        monkeypatch,
        [a_request("passed", sha=head), {"operationId": "op4"}, an_operation()],
    )

    result = CliRunner().invoke(cli, ["git", "merge"])

    assert result.exit_code == 0, result.output
    assert server.calls[1] == (
        "POST",
        "/api/tasks/test-repo/k1/merge-request/merge",
        {"sha": head},
    )
    assert "Merged mr1 at aaaaaaa into main" in result.output
    assert "branch: could not delete" in result.output


def test_git_merge_reports_a_refused_merge(project, monkeypatch):
    _, worktree, _ = project
    merge_local(worktree)
    Server(
        monkeypatch,
        [
            a_request("passed", sha=rev_parse(worktree)),
            {"operationId": "op4"},
            an_operation("refused", "main moved since the submit: rebase and rebuild"),
        ],
    )
    result = CliRunner().invoke(cli, ["git", "merge"])
    assert result.exit_code == 1
    assert "main moved since the submit" in result.output


@pytest.mark.parametrize(
    ("status", "at_head", "message"),
    [
        ("building", True, "is building, not passed"),
        ("passed", False, "Run `mael merge submit` first"),
    ],
)
def test_git_merge_refuses_unless_passed_at_head(
    project, monkeypatch, status, at_head, message
):
    _, worktree, _ = project
    merge_local(worktree)
    sha = rev_parse(worktree) if at_head else "a" * 40
    server = Server(monkeypatch, [a_request(status, sha=sha)])
    result = CliRunner().invoke(cli, ["git", "merge"])
    assert result.exit_code == 1
    assert message in result.output
    assert [c[0] for c in server.calls] == ["GET"]


@pytest.mark.parametrize("flag", ["--close", "--no-squash"])
def test_git_merge_refuses_a_flag_merge_local_has_no_use_for(
    project, monkeypatch, flag
):
    merge_local(project[1])
    server = Server(monkeypatch, [a_request("passed")])
    result = CliRunner().invoke(cli, ["git", "merge", flag])
    assert result.exit_code == 2
    assert f"{flag}: not used with merge: local" in result.output
    assert server.calls == []
