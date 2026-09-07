"""Tests for GitHub polling helpers."""

import asyncio
import contextlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from maelstrom import github
from maelstrom.base_store import InMemoryBaseStore
from maelstrom.github import (
    create_pr,
    create_project_repo,
    get_open_prs,
    get_open_prs_async,
    get_pr_checks,
    get_pr_comments,
    get_pr_for_branch,
    get_pr_for_branch_async,
    get_repo_info,
    get_run_artifacts,
    get_worktree_code,
    read_pr,
    wait_for_merge,
)
from maelstrom.github_model import (
    CheckRun,
    GitHubCliMissing,
    GitHubCommandFailed,
    GitHubError,
    PRInfo,
    PullRequestNotMergeable,
    SyncFailed,
)
from maelstrom.worktree import SyncResult
from maelstrom.worktree_model import BaseRef


def _pr(state="OPEN", merged=False, number=7):
    return PRInfo(
        number=number,
        title="A PR",
        url="https://example/pr",
        state=state,
        merged=merged,
        head_ref="feature",
    )


def _check(name, state):
    return CheckRun(name=name, state=state, run_id=None, link="")


class TestWaitForMerge:
    def test_returns_when_already_merged(self):
        pr = _pr(state="MERGED", merged=True)
        with (
            patch("maelstrom.github.get_pr_info", return_value=pr),
            patch("maelstrom.github.get_pr_checks", return_value=[]),
        ):
            result = wait_for_merge(Path("."), timeout=10, poll_interval=0)

        assert result is pr

    def test_merges_after_polling(self):
        infos = [_pr(state="OPEN"), _pr(state="OPEN"), _pr(state="MERGED", merged=True)]
        with (
            patch("maelstrom.github.get_pr_info", side_effect=infos),
            patch(
                "maelstrom.github.get_pr_checks", return_value=[_check("ci", "PENDING")]
            ),
            patch("maelstrom.github.time.sleep"),
        ):
            result = wait_for_merge(Path("."), timeout=10, poll_interval=0)

        assert result.merged is True

    def test_closed_unmerged_raises(self):
        with (
            patch("maelstrom.github.get_pr_info", return_value=_pr(state="CLOSED")),
            patch("maelstrom.github.get_pr_checks", return_value=[]),
        ):
            with pytest.raises(PullRequestNotMergeable, match="closed without merging"):
                wait_for_merge(Path("."), timeout=10, poll_interval=0)

    def test_terminal_failed_check_raises(self):
        with (
            patch("maelstrom.github.get_pr_info", return_value=_pr(state="OPEN")),
            patch(
                "maelstrom.github.get_pr_checks",
                return_value=[_check("lint", "SUCCESS"), _check("test", "FAILURE")],
            ),
        ):
            with pytest.raises(PullRequestNotMergeable, match="failing checks: test"):
                wait_for_merge(Path("."), timeout=10, poll_interval=0)

    def test_pending_checks_do_not_raise(self):
        """A pending (non-terminal) check keeps waiting rather than failing."""
        infos = [_pr(state="OPEN"), _pr(state="MERGED", merged=True)]
        with (
            patch("maelstrom.github.get_pr_info", side_effect=infos),
            patch(
                "maelstrom.github.get_pr_checks",
                return_value=[_check("test", "PENDING")],
            ),
            patch("maelstrom.github.time.sleep"),
        ):
            result = wait_for_merge(Path("."), timeout=10, poll_interval=0)

        assert result.merged is True

    def test_timeout_raises(self):
        with (
            patch("maelstrom.github.get_pr_info", return_value=_pr(state="OPEN")),
            patch("maelstrom.github.get_pr_checks", return_value=[]),
            patch("maelstrom.github.time.sleep"),
        ):
            with pytest.raises(TimeoutError, match="to merge"):
                wait_for_merge(Path("."), timeout=0, poll_interval=0)


class TestCreateProjectRepo:
    """`create_project_repo` builds a seed commit, then creates + pushes the repo."""

    @staticmethod
    def _fake_run_cmd(url="https://github.com/me/proj", seen=None):
        """Stand in for run_cmd: record every call, answer `gh repo view`."""

        def _run(cmd, cwd=None, quiet=False, check=True, **kwargs):
            if seen is not None:
                seen.append((cmd, cwd))
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{url}\n", stderr="")

        return _run

    def _run_create(self, *args, url="https://github.com/me/proj", **kwargs):
        """Call create_project_repo with run_cmd mocked; return (result, calls)."""
        seen = []
        with patch(
            "maelstrom.github.run_cmd", side_effect=self._fake_run_cmd(url, seen)
        ):
            result = create_project_repo(*args, **kwargs)
        return result, seen

    def test_seeds_a_commit_then_creates_the_repo(self):
        _, seen = self._run_create("proj")
        cmds = [cmd for cmd, _ in seen]
        assert cmds[0][:2] == ["git", "init"]
        assert cmds[1] == ["git", "add", "-A"]
        assert cmds[2][:2] == ["git", "commit"]
        assert cmds[3][:3] == ["gh", "repo", "create"]

    def test_initial_branch_is_main(self):
        _, seen = self._run_create("proj")
        assert seen[0][0] == ["git", "init", "-b", "main"]

    def test_stub_files_exist_when_the_commit_runs(self):
        written = {}

        def _run(cmd, cwd=None, quiet=False, check=True, **kwargs):
            if cmd[:2] == ["git", "add"]:
                assert cwd is not None
                # rglob, not iterdir: a stub may sit in a subdirectory.
                written.update(
                    {
                        str(p.relative_to(cwd)): p.read_text()
                        for p in cwd.rglob("*")
                        if p.is_file()
                    }
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="url\n", stderr="")

        with patch("maelstrom.github.run_cmd", side_effect=_run):
            create_project_repo("proj")

        assert set(written) == {
            ".gitignore",
            ".claude/settings.json",
            ".maelstrom.yaml",
            "README.md",
            "CLAUDE.md",
        }
        assert "# proj" in written["README.md"]

    def test_private_by_default(self):
        _, seen = self._run_create("proj")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert "--private" in gh_cmd
        assert "--public" not in gh_cmd

    def test_public_flag(self):
        _, seen = self._run_create("proj", private=False)
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert "--public" in gh_cmd
        assert "--private" not in gh_cmd

    def test_description_is_passed_through(self):
        _, seen = self._run_create("proj", description="A thing")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert gh_cmd[gh_cmd.index("--description") + 1] == "A thing"

    def test_description_omitted_when_absent(self):
        _, seen = self._run_create("proj")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert "--description" not in gh_cmd

    def test_owner_qualified_name_passes_to_gh_verbatim(self):
        _, seen = self._run_create("acme/proj")
        gh_cmd = [cmd for cmd, _ in seen if cmd[0] == "gh"][0]
        assert gh_cmd[3] == "acme/proj"
        # The local seed directory uses the bare name.
        assert seen[0][1] is not None and seen[0][1].name == "proj"

    def test_returns_the_stripped_clone_url(self):
        url, _ = self._run_create("proj", url="https://github.com/me/proj")
        assert url == "https://github.com/me/proj"

    def test_returns_the_https_url_gh_reports(self):
        """`gh repo create` follows the user's git_protocol; the returned URL must not.

        Agent pushes authenticate with a PAT over HTTPS, so an SSH remote breaks
        them. ``gh repo view --json url`` reports the HTTPS form whatever
        ``git_protocol`` is set to.
        """

        def _run(cmd, cwd=None, quiet=False, check=True, **kwargs):
            if cmd[:3] == ["gh", "repo", "view"]:
                out = "https://github.com/me/proj\n"
            else:
                out = "git@github.com:me/proj.git\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        with patch("maelstrom.github.run_cmd", side_effect=_run):
            assert create_project_repo("proj") == "https://github.com/me/proj"

    def test_never_reads_the_origin_gh_wrote(self):
        """That remote follows git_protocol, so it must not be the URL source."""
        _, seen = self._run_create("proj")
        assert ["git", "remote", "get-url", "origin"] not in [cmd for cmd, _ in seen]

    def test_called_process_error_becomes_a_typed_command_failure(self):
        err = subprocess.CalledProcessError(1, ["gh"], stderr="name already exists")
        with patch("maelstrom.github.run_cmd", side_effect=err):
            with pytest.raises(
                GitHubCommandFailed, match="Failed to create GitHub repository"
            ):
                create_project_repo("proj")

    def test_missing_gh_becomes_a_typed_missing_cli_error(self):
        with patch("maelstrom.github.run_cmd", side_effect=FileNotFoundError()):
            with pytest.raises(GitHubCliMissing, match="gh.*not installed"):
                create_project_repo("proj")


class TestCreatePrAutorepair:
    """`create_pr` chooses its pre-push sync by the ``autorepair`` argument."""

    def _run(self, tmp_path, **kwargs):
        """Call create_pr with both syncs stubbed; return (plain, repair)."""
        sync_result = SyncResult(success=True, branch="feature/work", message="ok")
        with (
            patch("maelstrom.github.sync_worktree", return_value=sync_result) as plain,
            patch(
                "maelstrom.github.sync_worktree_with_autorepair",
                return_value=sync_result,
            ) as repair,
            patch("maelstrom.github.run_cmd") as run,
            patch("maelstrom.github.run_git") as git,
            patch("maelstrom.github.get_current_branch", return_value="feature/work"),
            patch("maelstrom.github.update_local_main"),
        ):
            run.return_value = subprocess.CompletedProcess(
                args=["gh"],
                returncode=0,
                stdout="https://example/pr OPEN",
                stderr="",
            )
            git.return_value = subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="feature/work",
                stderr="",
            )
            create_pr(cwd=tmp_path, **kwargs)
        return plain, repair

    def test_autorepair_routes_to_the_repairing_sync(self, tmp_path):
        plain, repair = self._run(tmp_path, autorepair=True)

        repair.assert_called_once()
        plain.assert_not_called()

    def test_default_uses_the_plain_sync(self, tmp_path):
        """Off by default: a PR push must not start an agent unasked."""
        plain, repair = self._run(tmp_path)

        plain.assert_called_once()
        repair.assert_not_called()

    def test_squash_carries_through_to_the_repairing_sync(self, tmp_path):
        _, repair = self._run(tmp_path, autorepair=True, squash=True)

        assert repair.call_args.kwargs["squash"] is True

    def test_a_repaired_sync_says_an_agent_resolved_it(self, tmp_path, capsys):
        """Repaired commits are about to be pushed to a PR.

        The push publishes work a headless session wrote, so the user must be
        told before it lands in review.
        """
        repaired = SyncResult(
            success=True,
            branch="feature/work",
            message="ok",
            repaired=True,
        )
        with (
            patch(
                "maelstrom.github.sync_worktree_with_autorepair", return_value=repaired
            ),
            patch("maelstrom.github.run_cmd") as run,
            patch("maelstrom.github.run_git") as git,
            patch("maelstrom.github.get_current_branch", return_value="feature/work"),
            patch("maelstrom.github.update_local_main"),
        ):
            run.return_value = subprocess.CompletedProcess(
                args=["gh"],
                returncode=0,
                stdout="https://example/pr OPEN",
                stderr="",
            )
            git.return_value = subprocess.CompletedProcess(
                args=["git"],
                returncode=0,
                stdout="feature/work",
                stderr="",
            )
            create_pr(cwd=tmp_path, autorepair=True)

        assert "resolved by a headless Claude session" in capsys.readouterr().out

    def test_a_failure_that_left_a_rebase_still_gives_the_manual_steps(self, tmp_path):
        """Not every autorepair failure aborts.

        A session that finished on the wrong branch leaves a tree needing
        hands-on work, so the resolution steps must survive.
        """
        stranded = SyncResult(
            success=False,
            branch="feature/work",
            message="Autorepair finished the rebase but left the worktree on other.",
            had_conflicts=True,
            aborted=False,
        )
        with patch(
            "maelstrom.github.sync_worktree_with_autorepair", return_value=stranded
        ):
            with pytest.raises(SyncFailed, match="git rebase --continue"):
                create_pr(cwd=tmp_path, autorepair=True)


def _graphql_page(by_branch):
    """Build a ``gh api graphql`` response: one aliased connection per branch.

    ``get_open_prs`` sorts the branches before it builds the aliases, so the
    fake answers in that same order.
    """
    return json.dumps(
        {
            "data": {
                "repository": {
                    f"b{i}": {"nodes": by_branch[branch]}
                    for i, branch in enumerate(sorted(by_branch))
                }
            }
        }
    )


def _node(number, branch, commits, **over):
    node = {
        "number": number,
        "headRefName": branch,
        "url": f"https://github.com/acme/repo/pull/{number}",
        "isDraft": False,
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "commits": {
            "totalCount": commits,
            "nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}],
        },
    }
    node.update(over)
    return node


def _ok(stdout):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _query_of(call):
    """The GraphQL document a ``run_cmd`` call carries."""
    argv = call[0][0]
    return argv[argv.index("-f") + 1]


class TestGetPrForBranch:
    """The per-branch fallback, for when the batch call failed.

    It answers from `gh pr list`, which does not carry a check rollup, so the
    PR it returns reads as `unknown` rather than claiming a state it never
    looked up. A degraded row still shows a PR — better than none at all.
    """

    @staticmethod
    def _list(payload):
        return patch("maelstrom.github.run_cmd", return_value=_ok(payload))

    def test_a_branch_with_a_pr_answers_its_number_and_commit_count(self):
        payload = json.dumps(
            {"number": 42, "commits": 5, "url": "https://x/pull/42", "isDraft": False}
        )
        with self._list(payload):
            pr = get_pr_for_branch(Path("."), "feat/a")
        assert pr is not None
        assert (pr.number, pr.commits, pr.url) == (42, 5, "https://x/pull/42")

    def test_it_claims_no_state_it_did_not_look_up(self):
        payload = json.dumps({"number": 42, "commits": 1, "url": "", "isDraft": False})
        with self._list(payload):
            pr = get_pr_for_branch(Path("."), "feat/a")
        assert pr is not None
        assert pr.state == "unknown"

    def test_a_branch_with_no_pr_answers_nothing(self):
        with self._list(""):
            assert get_pr_for_branch(Path("."), "feat/a") is None

    def test_a_failed_call_answers_nothing(self):
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom"),
        ):
            assert get_pr_for_branch(Path("."), "feat/a") is None

    def test_unparseable_output_answers_nothing(self):
        with self._list("not json"):
            assert get_pr_for_branch(Path("."), "feat/a") is None


class TestGetOpenPrs:
    """One GraphQL call answers every branch, in place of one ``gh pr list`` each.

    The per-branch REST call cost ~0.8s and dominated ``mael list``. The batch
    must return the same numbers, and must fail in a way callers can tell apart
    from "this branch has no PR".

    It asks about named branches rather than walking the repo's pull requests:
    merged PRs only accumulate, and this runs on the orchestrator's 15-second
    poll, so a walk would get slower for the life of the repo.
    """

    def test_maps_every_branch_it_asked_about(self):
        page = _graphql_page(
            {
                "refactor/document-derivatives": [
                    _node(1837, "refactor/document-derivatives", 10)
                ],
                "feat/pgsql-users": [_node(1543, "feat/pgsql-users", 2)],
            }
        )
        with patch("maelstrom.github.run_cmd", return_value=_ok(page)):
            prs = get_open_prs(
                Path("."), {"refactor/document-derivatives", "feat/pgsql-users"}
            )
        assert prs is not None
        assert {b: (s.number, s.commits) for b, s in prs.items()} == {
            "refactor/document-derivatives": (1837, 10),
            "feat/pgsql-users": (1543, 2),
        }

    def test_the_query_asks_for_every_field_the_state_rule_reads(self):
        """The parser decides a state from these five. A query that drops one
        would read every PR as `ready` and say nothing was wrong."""
        with patch(
            "maelstrom.github.run_cmd", return_value=_ok(_graphql_page({}))
        ) as run:
            get_open_prs(Path("."), {"feat/a"})
        query = _query_of(run.call_args)
        for field in ("url", "isDraft", "state", "mergeable", "statusCheckRollup"):
            assert field in query

    def test_the_query_and_its_aliases_are_one_value(self):
        """The alias is how an answer finds its branch. Handing the caller the
        document and the mapping together is what stops the two enumerations
        drifting apart and silently keying every PR to the wrong branch."""
        query, aliases = github._open_prs_query(["feat/a", "feat/b"])
        assert aliases == {"b0": "feat/a", "b1": "feat/b"}
        for alias in aliases:
            assert f"{alias}: pullRequests(" in query

    def test_it_asks_only_about_the_branches_it_was_given(self):
        with patch(
            "maelstrom.github.run_cmd", return_value=_ok(_graphql_page({}))
        ) as run:
            get_open_prs(Path("."), {"feat/a", "feat/b"})
        query = _query_of(run.call_args)
        assert 'headRefName: "feat/a"' in query
        assert 'headRefName: "feat/b"' in query

    def test_it_asks_in_one_round_trip(self):
        with patch(
            "maelstrom.github.run_cmd", return_value=_ok(_graphql_page({}))
        ) as run:
            get_open_prs(Path("."), {f"feat/{n}" for n in range(20)})
        assert run.call_count == 1

    def test_it_includes_merged_prs(self):
        """A branch whose PR merged must still resolve, or `merged` never shows."""
        with patch(
            "maelstrom.github.run_cmd", return_value=_ok(_graphql_page({}))
        ) as run:
            get_open_prs(Path("."), {"feat/a"})
        assert "states: [OPEN, MERGED]" in _query_of(run.call_args)

    def test_a_branch_name_with_a_quote_in_it_cannot_break_the_query(self):
        """Branch names are git refs, not literals we control. An unescaped one
        would end the string argument and make the document unparseable."""
        with patch(
            "maelstrom.github.run_cmd", return_value=_ok(_graphql_page({}))
        ) as run:
            get_open_prs(Path("."), {'feat/a"} evil {'})
        assert '"' in _query_of(run.call_args)

    def test_no_branches_asks_nothing_at_all(self):
        """A project whose worktrees are all detached has nothing to look up."""
        with patch("maelstrom.github.run_cmd") as run:
            assert get_open_prs(Path("."), set()) == {}
        run.assert_not_called()

    def test_a_branch_with_no_pr_is_absent_rather_than_an_error(self):
        with patch(
            "maelstrom.github.run_cmd", return_value=_ok(_graphql_page({"one": []}))
        ):
            assert get_open_prs(Path("."), {"one"}) == {}

    def test_an_open_pr_beats_a_merged_one_on_the_same_branch(self):
        """`create-pr` opens a new PR on a branch whose last PR merged, so a
        recycled branch is routine. The open PR is the work in hand."""
        page = _graphql_page(
            {
                "one": [
                    _node(9, "one", 1, state="MERGED"),
                    _node(8, "one", 2, state="OPEN"),
                ]
            }
        )
        with patch("maelstrom.github.run_cmd", return_value=_ok(page)):
            prs = get_open_prs(Path("."), {"one"})
        assert prs is not None
        assert prs["one"].number == 8

    def test_a_partial_answer_is_used_rather_than_thrown_away(self):
        """gh exits 1 when any field was refused, even though it printed the
        rest. A token without the checks scope must still get its PR numbers."""
        payload = json.dumps(
            {
                "data": {"repository": {"b0": {"nodes": [_node(42, "one", 3)]}}},
                "errors": [
                    {"message": "Resource not accessible by personal access token"}
                ],
            }
        )
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=1, stdout=payload, stderr="denied"),
        ):
            prs = get_open_prs(Path("."), {"one"})
        assert prs is not None
        assert prs["one"].number == 42

    def test_a_failed_call_is_distinct_from_a_branch_with_no_pr(self):
        """A batch failure must not blank the whole column silently. ``None``
        lets the caller fall back per branch; ``{}`` would claim no PRs exist."""
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom"),
        ):
            assert get_open_prs(Path("."), {"one"}) is None

    def test_missing_gh_is_a_failure_not_an_empty_repo(self):
        with patch("maelstrom.github.run_cmd", side_effect=FileNotFoundError):
            assert get_open_prs(Path("."), {"one"}) is None

    def test_unparseable_output_is_a_failure_not_an_empty_repo(self):
        with patch("maelstrom.github.run_cmd", return_value=_ok("not json")):
            assert get_open_prs(Path("."), {"one"}) is None

    def test_a_graphql_error_payload_is_a_failure(self):
        """gh exits 0 on a GraphQL error payload, so returncode is not enough."""
        errors = json.dumps({"errors": [{"message": "rate limited"}]})
        with patch("maelstrom.github.run_cmd", return_value=_ok(errors)):
            assert get_open_prs(Path("."), {"one"}) is None


class TestCreatePrRegistersTheStack:
    """`create_pr` registers a stacked chain on GitHub with ``gh stack link``.

    ``link`` is the only ``gh stack`` command used, because every local one is
    unusable from a maelstrom worktree — see ``docs/dev/stacking.md``. The last
    test here is the guard that keeps it that way.
    """

    def _run(
        self, tmp_path, bases, branch="feat/child", *, link_fails=False, pr_open=True
    ):
        """Call create_pr with a fake store and captured gh/git argv."""
        store = InMemoryBaseStore()
        for child, parent in bases.items():
            store.write(child, BaseRef(branch=parent))

        calls: list[list[str]] = []
        sync_result = SyncResult(success=True, branch=branch, message="ok")

        def fake_run_cmd(cmd, *args, **kwargs):
            calls.append(list(cmd))
            if cmd[:3] == ["gh", "stack", "link"] and link_fails:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="",
                    stderr="link exploded",
                )
            if cmd[:3] == ["gh", "pr", "view"]:
                out = (
                    "https://example/pr OPEN"
                    if pr_open
                    else "https://example/pr MERGED"
                )
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout=out, stderr=""
                )
            if cmd[:3] == ["gh", "pr", "create"]:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="https://example/new-pr",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        def fake_run_git(cmd, *args, **kwargs):
            calls.append(["git", *cmd])
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=branch,
                stderr="",
            )

        with (
            patch("maelstrom.github.sync_worktree", return_value=sync_result),
            patch("maelstrom.github.GitConfigBaseStore", return_value=store),
            patch("maelstrom.github.get_current_branch", return_value=branch),
            patch("maelstrom.github.run_cmd", side_effect=fake_run_cmd),
            patch("maelstrom.github.run_git", side_effect=fake_run_git),
            patch("maelstrom.github.update_local_main"),
        ):
            url, created = create_pr(cwd=tmp_path)
        return url, created, calls

    def _links(self, calls):
        return [c for c in calls if c[:3] == ["gh", "stack", "link"]]

    def test_a_stacked_branch_links_the_chain_bottom_to_top(self, tmp_path):
        _, _, calls = self._run(
            tmp_path, {"feat/child": "feat/parent", "feat/parent": "feat/grandparent"}
        )

        assert self._links(calls) == [
            ["gh", "stack", "link", "feat/grandparent", "feat/parent", "feat/child"]
        ]

    def test_a_two_branch_stack_links_both(self, tmp_path):
        _, _, calls = self._run(tmp_path, {"feat/child": "feat/parent"})

        assert self._links(calls) == [
            ["gh", "stack", "link", "feat/parent", "feat/child"]
        ]

    def test_an_unstacked_branch_never_calls_gh_stack(self, tmp_path):
        """No base, no stack — and no dependency on a public-preview extension."""
        _, _, calls = self._run(tmp_path, {}, branch="feat/solo")

        assert not [c for c in calls if c[:2] == ["gh", "stack"]]

    def test_no_base_flag_is_passed_to_gh_pr_create(self, tmp_path):
        """``link`` owns base chaining; a --base here would fight it.

        Everything still merges into main — the chained bases are review-time
        scaffolding that GitHub collapses as each PR lands.
        """
        _, _, calls = self._run(tmp_path, {"feat/child": "feat/parent"}, pr_open=False)

        creates = [c for c in calls if c[:3] == ["gh", "pr", "create"]]
        assert creates, "expected a PR to be created"
        assert all("--base" not in c for c in creates)

    def test_a_link_failure_warns_and_still_returns_the_pr_url(self, tmp_path, capsys):
        """Registration is decoration. The branch is pushed and the PR exists."""
        url, _, calls = self._run(
            tmp_path, {"feat/child": "feat/parent"}, link_fails=True
        )

        assert url == "https://example/pr"
        assert self._links(calls), "link was still attempted"
        assert "stack" in capsys.readouterr().out.lower()

    def test_a_missing_gh_stack_extension_warns_and_still_returns_the_url(
        self, tmp_path, capsys
    ):
        """``gh stack`` is a separate extension; not having it must not fail the PR."""
        store = InMemoryBaseStore()
        store.write("feat/child", BaseRef(branch="feat/parent"))

        def fake_run_cmd(cmd, *args, **kwargs):
            if cmd[:3] == ["gh", "stack", "link"]:
                raise FileNotFoundError("gh stack")
            if cmd[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="https://example/pr OPEN",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        with (
            patch(
                "maelstrom.github.sync_worktree",
                return_value=SyncResult(
                    success=True, branch="feat/child", message="ok"
                ),
            ),
            patch("maelstrom.github.GitConfigBaseStore", return_value=store),
            patch("maelstrom.github.get_current_branch", return_value="feat/child"),
            patch("maelstrom.github.run_cmd", side_effect=fake_run_cmd),
            patch("maelstrom.github.run_git"),
            patch("maelstrom.github.update_local_main"),
        ):
            url, _ = create_pr(cwd=tmp_path)

        assert url == "https://example/pr"
        assert "gh extension install github/gh-stack" in capsys.readouterr().out

    def test_no_local_gh_stack_subcommand_is_ever_invoked(self, tmp_path):
        """The guard that keeps us clear of the worktree state bug (issue #35)."""
        _, _, calls = self._run(tmp_path, {"feat/child": "feat/parent"})

        local = {
            "rebase",
            "sync",
            "push",
            "submit",
            "modify",
            "init",
            "add",
            "checkout",
            "unstack",
        }
        for call in calls:
            if call[:2] == ["gh", "stack"]:
                assert call[2] not in local, f"local gh stack command invoked: {call}"


class TestGetWorktreeCodeUsesTheBase:
    """A review must see this branch's own work, not the whole stack."""

    def _run(self, tmp_path, bases, branch="feat/child"):
        store = InMemoryBaseStore()
        for child, parent in bases.items():
            store.write(child, BaseRef(branch=parent))
        calls: list[list[str]] = []

        def fake_run_git(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="deadbeef",
                stderr="",
            )

        with (
            patch("maelstrom.github.GitConfigBaseStore", return_value=store),
            patch("maelstrom.github.get_current_branch", return_value=branch),
            patch("maelstrom.github.run_git", side_effect=fake_run_git),
        ):
            get_worktree_code(tmp_path)
        return calls

    def test_a_stacked_branch_diffs_against_its_base(self, tmp_path):
        """Diffing against main would show the parent's commits as this PR's work."""
        calls = self._run(tmp_path, {"feat/child": "feat/parent"})

        merge_bases = [c for c in calls if c[0] == "merge-base"]
        assert merge_bases == [["merge-base", "HEAD", "origin/feat/parent"]]

    def test_a_base_whose_ref_is_gone_falls_back_to_main(self, tmp_path):
        """A merged-and-pruned base must not leave the reviewer with no diff at all.

        ``merge-base`` against a ref that does not resolve raises, the caller
        swallows it, and ``commits_output`` comes back empty — so a reviewing agent
        silently receives no code rather than the branch's work.
        """
        store = InMemoryBaseStore()
        store.write("feat/child", BaseRef(branch="feat/gone"))
        calls: list[list[str]] = []

        def fake_run_git(cmd, *args, **kwargs):
            calls.append(list(cmd))
            if cmd[:1] == ["merge-base"] and "origin/feat/gone" in cmd:
                raise subprocess.CalledProcessError(128, cmd)
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="deadbeef",
                stderr="",
            )

        with (
            patch("maelstrom.github.GitConfigBaseStore", return_value=store),
            patch("maelstrom.github.get_current_branch", return_value="feat/child"),
            patch("maelstrom.github.run_git", side_effect=fake_run_git),
        ):
            get_worktree_code(tmp_path)

        merge_bases = [c for c in calls if c[0] == "merge-base"]
        assert ["merge-base", "HEAD", "origin/main"] in merge_bases

    def test_an_unstacked_branch_still_diffs_against_main(self, tmp_path):
        calls = self._run(tmp_path, {}, branch="feat/solo")

        merge_bases = [c for c in calls if c[0] == "merge-base"]
        assert merge_bases == [["merge-base", "HEAD", "origin/main"]]


class TestReadersDegradeOnUnparseableOutput:
    """gh printing something that is not JSON must not crash the reader.

    The parsers own ``json.loads`` now, so the transport layer has to keep
    catching ``JSONDecodeError`` around the parse call rather than only around
    ``run_cmd``. Nothing pinned that before this split.
    """

    def test_pr_comments_read_as_empty(self):
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="not json", stderr=""),
        ):
            assert get_pr_comments(Path("."), "o", "r", 7) == ([], None)

    def test_pr_checks_read_as_empty(self):
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="not json", stderr=""),
        ):
            assert get_pr_checks(Path(".")) == []

    def test_run_artifacts_read_as_empty(self):
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="not json", stderr=""),
        ):
            assert get_run_artifacts(Path("."), "12345") == []


class TestReadPrRepoInfoFailure:
    """`read_pr` degrades on a repo lookup that failed, but not on a broken gh.

    Without repo owner/name there is no GraphQL query to run, so the PR renders
    with no comments. That is the right answer for a repo gh could not read, and
    the wrong one for a gh that is not installed — an empty comment list would
    hide the real fault.
    """

    @staticmethod
    def _patches(repo_info_error):
        return (
            patch("maelstrom.github.get_pr_info", return_value=_pr()),
            patch("maelstrom.github.get_repo_info", side_effect=repo_info_error),
            patch("maelstrom.github.get_pr_checks", return_value=[]),
        )

    def test_a_failed_repo_lookup_leaves_the_comments_empty(self):
        err = GitHubCommandFailed("get repo info", "not a git repository")
        with contextlib.ExitStack() as stack:
            for p in self._patches(err):
                stack.enter_context(p)
            info = read_pr(Path("."))
        assert info.comments == []

    def test_a_missing_gh_propagates_rather_than_reading_as_no_comments(self):
        with contextlib.ExitStack() as stack:
            for p in self._patches(GitHubCliMissing("gh")):
                stack.enter_context(p)
            with pytest.raises(GitHubCliMissing):
                read_pr(Path("."))


class TestGetRepoInfoUnexpectedFormat:
    """gh answering in a shape we cannot read is not a command failure."""

    def test_it_keeps_the_message_it_always_had(self):
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="justaname", stderr=""),
        ):
            with pytest.raises(GitHubError, match="Unexpected repo format: justaname"):
                get_repo_info(Path("."))

    def test_it_is_not_reported_as_a_failed_command(self):
        """Nothing errored, so `.stderr` would be a string no subprocess wrote."""
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="justaname", stderr=""),
        ):
            with pytest.raises(GitHubError) as excinfo:
                get_repo_info(Path("."))
        assert not isinstance(excinfo.value, GitHubCommandFailed)


class TestTheAsyncPrReaders:
    """The two PR reads the orchestrator server makes, driven on a loop.

    The sync twins above are what ``mael list`` calls; these are what
    ``build_list_all_data`` calls, and until now nothing exercised them. Their
    degradation is the point: this runs on a 15-second poll, so a ``gh`` that
    is missing, unauthenticated or answering rubbish must cost the PR column
    rather than the whole read.
    """

    @staticmethod
    def _replies(**kwargs):
        return patch("maelstrom.github.run_cmd_async", **kwargs)

    def test_a_branch_with_a_pr_answers_its_number_and_commit_count(self):
        payload = json.dumps(
            {"number": 42, "commits": 5, "url": "https://x/pull/42", "isDraft": False}
        )
        with self._replies(return_value=_ok(payload)):
            pr = asyncio.run(get_pr_for_branch_async(Path("."), "feat/a"))
        assert pr is not None
        assert (pr.number, pr.commits, pr.url) == (42, 5, "https://x/pull/42")

    def test_a_branch_with_no_pr_answers_nothing(self):
        with self._replies(return_value=_ok("")):
            assert asyncio.run(get_pr_for_branch_async(Path("."), "feat/a")) is None

    def test_unparseable_output_answers_nothing(self):
        with self._replies(return_value=_ok("not json")):
            assert asyncio.run(get_pr_for_branch_async(Path("."), "feat/a")) is None

    def test_a_missing_gh_answers_nothing(self):
        """``gh`` absent raises from the spawn; the row degrades, it does not fail."""
        with self._replies(side_effect=FileNotFoundError("gh")):
            assert asyncio.run(get_pr_for_branch_async(Path("."), "feat/a")) is None

    def test_it_answers_every_branch_from_one_call(self):
        payload = json.dumps(
            {
                "data": {
                    "repository": {
                        "b0": {"nodes": [{"number": 1, "commits": {"totalCount": 2}}]},
                        "b1": {"nodes": [{"number": 3, "commits": {"totalCount": 4}}]},
                    }
                }
            }
        )
        with self._replies(return_value=_ok(payload)) as run:
            prs = asyncio.run(get_open_prs_async(Path("."), {"feat/a", "feat/b"}))
        assert run.call_count == 1
        assert prs is not None
        assert {b: p.number for b, p in prs.items()} == {"feat/a": 1, "feat/b": 3}

    def test_no_branches_costs_no_call(self):
        with self._replies() as run:
            assert asyncio.run(get_open_prs_async(Path("."), set())) == {}
        run.assert_not_called()

    def test_a_refused_query_is_told_apart_from_no_prs(self):
        """``None`` means "could not ask", which the caller retries per branch.

        An empty dict would claim every branch has no PR, blanking the column.
        """
        with self._replies(return_value=_ok("")):
            assert asyncio.run(get_open_prs_async(Path("."), {"feat/a"})) is None

    def test_unparseable_output_is_told_apart_from_no_prs(self):
        with self._replies(return_value=_ok("not json")):
            assert asyncio.run(get_open_prs_async(Path("."), {"feat/a"})) is None
