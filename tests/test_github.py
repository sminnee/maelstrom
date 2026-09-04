"""Tests for GitHub polling helpers."""

import contextlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from maelstrom.base_store import InMemoryBaseStore
from maelstrom.github import (
    create_pr,
    create_project_repo,
    get_open_prs,
    get_pr_checks,
    get_pr_comments,
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
                written.update({p.name: p.read_text() for p in cwd.iterdir()})
            return subprocess.CompletedProcess(cmd, 0, stdout="url\n", stderr="")

        with patch("maelstrom.github.run_cmd", side_effect=_run):
            create_project_repo("proj")

        assert set(written) == {
            ".gitignore",
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


def _graphql_page(nodes, has_next=False, cursor=None):
    """Build one ``gh api graphql`` response page."""
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "nodes": nodes,
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    }
                }
            }
        }
    )


def _node(number, branch, commits):
    return {"number": number, "headRefName": branch, "commits": {"totalCount": commits}}


def _ok(stdout):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class TestGetOpenPrs:
    """One GraphQL call replaces one ``gh pr list`` per branch.

    The per-branch REST call cost ~0.8s each and dominated ``mael list``. The
    batch must return the same numbers, and must fail in a way callers can tell
    apart from "this repo has no open PRs".
    """

    def test_maps_every_open_pr_by_branch(self):
        page = _graphql_page(
            [
                _node(1837, "refactor/document-derivatives", 10),
                _node(1543, "feat/pgsql-users", 2),
            ]
        )
        with patch("maelstrom.github.run_cmd", return_value=_ok(page)):
            assert get_open_prs(Path(".")) == {
                "refactor/document-derivatives": (1837, 10),
                "feat/pgsql-users": (1543, 2),
            }

    def test_a_repo_with_no_open_prs_maps_to_nothing(self):
        with patch("maelstrom.github.run_cmd", return_value=_ok(_graphql_page([]))):
            assert get_open_prs(Path(".")) == {}

    def test_follows_pagination_to_the_last_page(self):
        """``first:`` is a page size, not a total. A repo with more open PRs
        than one page must not silently lose the overflow — a missing branch
        renders blank, which reads as "no PR"."""
        pages = [
            _ok(_graphql_page([_node(1, "one", 3)], has_next=True, cursor="CUR")),
            _ok(_graphql_page([_node(2, "two", 4)])),
        ]
        with patch("maelstrom.github.run_cmd", side_effect=pages) as run:
            assert get_open_prs(Path(".")) == {"one": (1, 3), "two": (2, 4)}
        assert run.call_count == 2
        # The cursor goes through -f, not -F. -F types the value, so a cursor
        # that looks like a number is sent as one and GraphQL rejects it
        # against the declared String.
        second = run.call_args_list[1][0][0]
        assert second[second.index("after=CUR") - 1] == "-f"

    def test_a_failed_call_is_distinct_from_an_empty_repo(self):
        """A batch failure must not blank the whole column silently. ``None``
        lets the caller fall back per branch; ``{}`` would claim no PRs exist."""
        with patch(
            "maelstrom.github.run_cmd",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom"),
        ):
            assert get_open_prs(Path(".")) is None

    def test_missing_gh_is_a_failure_not_an_empty_repo(self):
        with patch("maelstrom.github.run_cmd", side_effect=FileNotFoundError):
            assert get_open_prs(Path(".")) is None

    def test_unparseable_output_is_a_failure_not_an_empty_repo(self):
        with patch("maelstrom.github.run_cmd", return_value=_ok("not json")):
            assert get_open_prs(Path(".")) is None

    def test_a_graphql_error_payload_is_a_failure(self):
        """gh exits 0 on a GraphQL error payload, so returncode is not enough."""
        errors = json.dumps({"errors": [{"message": "rate limited"}]})
        with patch("maelstrom.github.run_cmd", return_value=_ok(errors)):
            assert get_open_prs(Path(".")) is None


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
