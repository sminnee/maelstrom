"""GitHub transport for maelstrom projects — the adapter over ``gh`` and ``git``.

Every function here shells out through ``run_cmd`` or ``run_git``, hands the raw
output to a parser in ``github_model``, and turns a failure into a typed
``GitHubError``. The domain logic — the dataclasses, the parsers, the stack walk,
the errors — lives in ``github_model`` and needs no subprocess to exercise.

``run_cmd`` is the mock seam these functions are tested through; nothing wraps it,
so a test can patch this module's attribute directly.
"""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, TypeVar

from .base_store import GitConfigBaseStore
from .github_model import (
    PASSING_STATES,
    TERMINAL_STATES,
    Artifact,
    CheckRun,
    GitHubCliMissing,
    GitHubCommandFailed,
    GitHubError,
    NoChecksFound,
    NoPullRequest,
    PRComment,
    PRInfo,
    PullRequestNotMergeable,
    SyncFailed,
    is_missing_pr_error,
    parse_artifacts,
    parse_check_runs,
    parse_open_prs_page,
    parse_pr_comments,
    parse_pr_info,
    stack_chain,
)
from .project_scaffold import scaffold_files
from .shell import run_cmd
from .worktree import (
    get_current_branch,
    run_git,
    sync_worktree,
    sync_worktree_with_autorepair,
    update_local_main,
)
from .worktree_model import MAIN_BRANCH, REPAIRED_MESSAGE, print_flushed


def get_repo_info(cwd: Path) -> tuple[str, str]:
    """Get the owner and repo name from the git remote.

    Args:
        cwd: Working directory (must be in a git repo).

    Returns:
        Tuple of (owner, repo).

    Raises:
        GitHubCommandFailed: If gh cannot read the repo.
        GitHubCliMissing: If gh is not installed.
    """
    try:
        result = run_cmd(
            [
                "gh",
                "repo",
                "view",
                "--json",
                "owner,name",
                "-q",
                '.owner.login + "/" + .name',
            ],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        parts = result.stdout.strip().split("/")
        if len(parts) != 2:
            # No command failed here — gh answered, in a shape we cannot read.
            raise GitHubError(f"Unexpected repo format: {result.stdout.strip()}")
        return parts[0], parts[1]
    except subprocess.CalledProcessError as e:
        raise GitHubCommandFailed("get repo info", e.stderr)
    except FileNotFoundError:
        raise GitHubCliMissing("gh")


def create_project_repo(
    name: str, *, private: bool = True, description: str | None = None
) -> str:
    """Create a GitHub repository seeded with maelstrom stub files.

    Builds the seed commit in a temporary directory, then creates and pushes the
    remote in one ``gh repo create`` call. Nothing exists remotely until the seed
    is ready, so a local failure leaves no empty repository behind. The seed
    commit is also what makes the repository usable by ``add_project``, which
    needs a commit on the default branch.

    Args:
        name: Repository name. ``owner/name`` passes through to gh unchanged.

    Returns:
        The HTTPS clone URL of the new repository.

    Raises:
        GitHubCommandFailed: If gh cannot create the repository.
        GitHubCliMissing: If gh is not installed.
    """
    local_name = name.split("/")[-1]

    try:
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td) / local_name
            repo_dir.mkdir()
            for filename, content in scaffold_files(local_name).items():
                (repo_dir / filename).write_text(content)

            run_cmd(["git", "init", "-b", "main"], cwd=repo_dir, quiet=True)
            run_cmd(["git", "add", "-A"], cwd=repo_dir, quiet=True)
            run_cmd(
                ["git", "commit", "-m", "chore: initial maelstrom project"],
                cwd=repo_dir,
                quiet=True,
            )

            create_cmd = [
                "gh",
                "repo",
                "create",
                name,
                "--source",
                str(repo_dir),
                "--push",
                "--private" if private else "--public",
            ]
            if description:
                create_cmd += ["--description", description]
            run_cmd(create_cmd, cwd=repo_dir)

            # Ask GitHub rather than reading back origin: gh writes that remote
            # using the ambient `git_protocol`, and agent pushes need HTTPS. The
            # `url` field is always the HTTPS form.
            result = run_cmd(
                ["gh", "repo", "view", name, "--json", "url", "-q", ".url"],
                quiet=True,
            )
            return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitHubCommandFailed("create GitHub repository", e.stderr)
    except FileNotFoundError:
        raise GitHubCliMissing("gh")


def get_pr_number_for_branch(cwd: Path, branch: str) -> int | None:
    """Get the PR number for a given branch, if one exists.

    Args:
        cwd: Working directory (must be in a git repo).
        branch: Branch name to look up.

    Returns:
        PR number if found, None otherwise.
    """
    try:
        result = run_cmd(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--json",
                "number",
                "-q",
                ".[0].number",
            ],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return int(result.stdout.strip())
    except (ValueError, FileNotFoundError):
        return None


def get_pr_number_and_commits(cwd: Path, branch: str) -> tuple[int | None, int | None]:
    """Get PR number and commit count for a given branch.

    Args:
        cwd: Working directory (must be in a git repo).
        branch: Branch name to look up.

    Returns:
        Tuple of (pr_number, commit_count). Both None if no PR exists.
    """
    try:
        result = run_cmd(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--json",
                "number,commits",
                "-q",
                ".[0] | [.number, (.commits | length)]",
            ],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return (None, None)
        # Parse the JSON array [number, commit_count]
        data = json.loads(result.stdout.strip())
        if isinstance(data, list) and len(data) == 2 and data[0] is not None:
            return (int(data[0]), int(data[1]))
        return (None, None)
    except (ValueError, FileNotFoundError, json.JSONDecodeError):
        return (None, None)


# Open PRs and their commit counts, for the whole repo, in one round trip.
#
# ``gh pr list --json commits`` cannot do this in bulk: its ``commits`` field
# expands every commit object (with authors), so asking for 100 PRs exceeds
# GitHub's 500,000-node budget and the call fails outright. Selecting
# ``commits { totalCount }`` asks for the count instead of the commits, which
# stays well inside the budget. Same number, one request rather than one per
# branch.
_OPEN_PRS_QUERY = """
query($owner: String!, $repo: String!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(states: OPEN, first: 100, after: $after) {
      nodes { number headRefName commits { totalCount } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

# Bound the paging loop. 100 pages is 10,000 open PRs — far past any real repo,
# and a backstop against a malformed cursor looping forever.
_OPEN_PRS_MAX_PAGES = 100


def get_open_prs(cwd: Path) -> dict[str, tuple[int, int]] | None:
    """Map branch -> (pr_number, commit_count) for every open PR in the repo.

    One GraphQL call for the whole repo, in place of one ``gh pr list`` per
    branch. On a project with seven worktrees that is ~0.7s instead of ~5.6s,
    which is most of what ``mael list`` spends.

    Args:
        cwd: Working directory (must be in a git repo).

    Returns:
        Branch name -> (PR number, commit count) for every open PR. ``{}`` when
        the repo genuinely has none. ``None`` when the lookup failed, so the
        caller can fall back per branch rather than render every PR as absent —
        an empty column and a broken ``gh`` must not look the same.
    """
    prs: dict[str, tuple[int, int]] = {}
    cursor: str | None = None
    try:
        for _ in range(_OPEN_PRS_MAX_PAGES):
            cmd = [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_OPEN_PRS_QUERY}",
                "-F",
                "owner=:owner",
                "-F",
                "repo=:repo",
            ]
            if cursor:
                # -f, not -F: the cursor is a declared String. -F coerces a
                # value that looks like a number, and GraphQL then rejects it.
                cmd += ["-f", f"after={cursor}"]
            result = run_cmd(cmd, cwd=cwd, quiet=True, check=False)
            if result.returncode != 0 or not result.stdout.strip():
                return None

            page = parse_open_prs_page(result.stdout.strip())
            prs.update(page.prs)
            if not page.has_next or not page.cursor:
                return prs
            cursor = page.cursor
        # Ran out of pages before the cursor did. Report failure rather than a
        # truncated map that would silently blank the overflow branches.
        return None
    except (ValueError, KeyError, TypeError, FileNotFoundError, json.JSONDecodeError):
        return None


def get_pr_url(cwd: Path) -> str:
    """Get the PR URL for the current branch.

    Args:
        cwd: Working directory (must be in a git repo with a PR).

    Returns:
        The PR URL.

    Raises:
        NoPullRequest: If the branch has no PR.
        GitHubCommandFailed: If gh fails for any other reason.
        GitHubCliMissing: If gh is not installed.
    """
    try:
        result = run_cmd(
            ["gh", "pr", "view", "--json", "url", "-q", ".url"],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        url = result.stdout.strip()
        if not url:
            raise NoPullRequest()
        return url
    except subprocess.CalledProcessError as e:
        if is_missing_pr_error(e.stderr):
            raise NoPullRequest()
        raise GitHubCommandFailed("get PR URL", e.stderr)
    except FileNotFoundError:
        raise GitHubCliMissing("gh")


def create_pr(
    cwd: Path | None = None,
    draft: bool = False,
    issue_id: str | None = None,
    progress: bool = False,
    squash: bool = False,
    autorepair: bool = False,
    announce: Callable[[str], None] = print_flushed,
) -> tuple[str, bool]:
    """Create a pull request for the current worktree branch, or push if PR exists.

    Syncs (rebases onto this branch's base) before pushing. A stacked chain is
    registered on GitHub with ``gh stack link`` once the PR exists.

    Args:
        cwd: Current working directory (default: actual cwd).
        draft: Create as draft PR (only if creating new PR).
        issue_id: Optional Linear issue ID to prepend to PR title (e.g., "ME-41").
        progress: If True, use "Progresses" instead of "Fixes" in PR title for
            multi-session tasks that aren't complete yet.
        squash: If True, autosquash ``fixup!`` commits during the pre-push sync.
        autorepair: If True, a conflict in the pre-push sync starts a headless
            Claude session to resolve it. Off by default: a PR push must not
            start an agent unasked.
        announce: Callable taking one line of progress text. Defaults to a
            flushed ``print``; the CLI passes ``click.echo``.

    Returns:
        Tuple of (PR URL, created) where created is True if new PR was created.

    Raises:
        SyncFailed: If the pre-push rebase fails.
        GitHubCommandFailed: If the push or the PR creation fails.
        GitHubCliMissing: If gh or git is not installed.
    """
    if cwd is None:
        cwd = Path.cwd()

    # Sync first (rebase onto origin/main)
    if autorepair:
        sync_result = sync_worktree_with_autorepair(
            cwd, squash=squash, announce=announce
        )
    else:
        sync_result = sync_worktree(cwd, squash=squash)
    if not sync_result.success:
        # An aborted rebase is restored, so the manual-resolution steps would
        # name a rebase that is no longer there. A repair that failed without
        # aborting — one that landed on the wrong branch — still needs them.
        if sync_result.had_conflicts and not sync_result.aborted:
            raise SyncFailed(
                "Sync failed due to conflicts. Resolve them first:\n"
                "  git status\n"
                "  # resolve conflicts\n"
                "  git add <files>\n"
                "  git rebase --continue"
            )
        raise SyncFailed(f"Sync failed: {sync_result.message}")

    # The push publishes commits the session rewrote, so say so before it lands
    # in a PR.
    if sync_result.repaired:
        announce(REPAIRED_MESSAGE)

    # Check if PR already exists (and is open)
    pr_exists = False
    existing_url = ""
    try:
        result = run_cmd(
            ["gh", "pr", "view", "--json", "url,state", "-q", '.url + " " + .state'],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().rsplit(" ", 1)
            if len(parts) == 2:
                url, state = parts
                if state == "OPEN":
                    pr_exists = True
                    existing_url = url
    except FileNotFoundError:
        raise GitHubCliMissing("gh")

    # Fetch current branch's remote tracking ref for --force-with-lease
    run_cmd(["git", "fetch", "origin"], cwd=cwd, check=False, quiet=True)

    # Fast-forward local main to match origin/main
    update_local_main(cwd.parent)

    # Push the branch
    try:
        result = run_cmd(
            ["git", "push", "--force-with-lease", "-u", "origin", "HEAD"],
            cwd=cwd,
            check=False,
        )
        if result.returncode != 0:
            raise GitHubCommandFailed("push branch", result.stderr)
        # Print push output for visibility
        if result.stderr:
            print(result.stderr.strip())
    except FileNotFoundError:
        raise GitHubCliMissing("git")

    # `get_current_branch` goes through `run_git` with check=True, so a
    # detached HEAD raises CalledProcessError. Convert it: this function
    # promises typed errors, and the CLI catches those rather than a traceback.
    try:
        branch_name = get_current_branch(cwd)
    except subprocess.CalledProcessError as e:
        raise GitHubCommandFailed("read the current branch", e.stderr)

    # If PR exists, just return the URL. Registration still runs: the stack may
    # have grown or collapsed since the PR was opened, and `link` is how GitHub
    # learns about it.
    if pr_exists:
        _register_stack(cwd, branch_name, announce=announce)
        return existing_url, False

    # Try to get the first commit message for title
    try:
        log_result = run_git(["log", "-1", "--format=%s"], cwd=cwd, quiet=True)
        title = log_result.stdout.strip()
    except subprocess.CalledProcessError:
        title = branch_name

    # Append issue ID to title if provided (enables Linear's GitHub auto-linking)
    if issue_id:
        verb = "Progresses" if progress else "Fixes"
        title = f"{title} ({verb} {issue_id.upper()})"

    # Create the PR with explicit title (--fill can fail if base branch not fetched)
    cmd = ["gh", "pr", "create", "--title", title, "--body", "", "--head", branch_name]
    if draft:
        cmd.append("--draft")

    result = run_cmd(cmd, cwd=cwd, check=False)

    if result.returncode != 0:
        raise GitHubCommandFailed("create PR", result.stderr)

    new_url = result.stdout.strip()
    _register_stack(cwd, branch_name, announce=announce)
    return new_url, True


def _base_refs_for_diff(cwd: Path) -> list[str]:
    """Refs to diff a review against, best first.

    A stacked branch diffs against its base, so a review sees only this branch's
    own work rather than the whole stack. ``origin/main`` follows as a fallback for
    a base that has merged and been pruned.
    """
    base = _resolve_base_branch(cwd)
    refs = [f"origin/{base}"]
    if base != MAIN_BRANCH:
        refs.append(f"origin/{MAIN_BRANCH}")
    return refs


def _resolve_base_branch(cwd: Path) -> str:
    """The branch ``cwd``'s work is stacked on, or ``main`` if it is not stacked.

    Never raises: a worktree whose branch or config cannot be read falls back to
    ``main``, which is what every branch used before stacking existed.
    """
    try:
        return GitConfigBaseStore(cwd).read(get_current_branch(cwd)).branch
    except Exception:
        return MAIN_BRANCH


def _register_stack(
    cwd: Path, branch: str, *, announce: Callable[[str], None] = print_flushed
) -> None:
    """Register this branch's stack on GitHub, so its PRs show as a stack.

    ``gh stack link`` is the only ``gh stack`` command used, and it runs only after
    the branch is pushed and its PR exists. Every *local* one is unusable from a
    maelstrom worktree; ``docs/dev/stacking.md`` has the reasoning.

    Never fatal: a failed registration costs the stack view and nothing else, so it
    warns and returns.
    """
    chain = stack_chain(branch, GitConfigBaseStore(cwd).all())
    if len(chain) < 2:
        return  # not stacked; nothing to register

    try:
        result = run_cmd(["gh", "stack", "link", *chain], cwd=cwd, check=False)
    except FileNotFoundError:
        announce(
            "Warning: could not register the stack on GitHub — the gh stack "
            "extension is not installed (gh extension install github/gh-stack). "
            "The PR is pushed; only the stack view is missing."
        )
        return
    if result.returncode != 0:
        announce(
            f"Warning: could not register the stack on GitHub "
            f"({result.stderr.strip() or 'gh stack link failed'}). "
            f"The PR is pushed; only the stack view is missing."
        )


def get_pr_info(cwd: Path) -> PRInfo:
    """Get basic PR information.

    Args:
        cwd: Working directory (must be in a git repo with a PR).

    Returns:
        PRInfo with basic fields populated.

    Raises:
        NoPullRequest: If the branch has no PR.
        GitHubCommandFailed: If gh fails for any other reason.
        GitHubCliMissing: If gh is not installed.
    """
    try:
        result = run_cmd(
            [
                "gh",
                "pr",
                "view",
                "--json",
                "number,title,url,state,mergedAt,headRefName",
            ],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        return parse_pr_info(result.stdout)
    except subprocess.CalledProcessError as e:
        if is_missing_pr_error(e.stderr):
            raise NoPullRequest()
        raise GitHubCommandFailed("get PR info", e.stderr)
    except FileNotFoundError:
        raise GitHubCliMissing("gh")


def get_pr_comments(
    cwd: Path, owner: str, repo: str, pr_number: int
) -> tuple[list[PRComment], str | None]:
    """Get all comments from a PR using GraphQL, plus the timestamp of the last push.

    Fetches three sources in a single round-trip:
    - Unresolved inline review threads (resolved threads are dropped).
    - Top-level PR (issue) comments.
    - Review submissions with non-empty bodies (approve/request-changes summaries).

    Args:
        cwd: Working directory.
        owner: Repository owner.
        repo: Repository name.
        pr_number: Pull request number.

    Returns:
        Tuple of (comments, last_push_at). last_push_at is the ISO 8601 timestamp
        of the most recent commit's pushedDate (falling back to committedDate), or
        None if unavailable.
    """
    query = """
    query($owner: String!, $repo: String!, $pr: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $pr) {
          reviewThreads(first: 100) {
            nodes {
              id
              isResolved
              path
              line
              comments(first: 50) {
                nodes {
                  body
                  author { login }
                  createdAt
                }
              }
            }
          }
          comments(first: 100) {
            nodes {
              body
              author { login }
              createdAt
            }
          }
          reviews(first: 100) {
            nodes {
              body
              author { login }
              submittedAt
            }
          }
          commits(last: 1) {
            nodes {
              commit {
                pushedDate
                committedDate
              }
            }
          }
        }
      }
    }
    """

    try:
        result = run_cmd(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={query}",
                "-f",
                f"owner={owner}",
                "-f",
                f"repo={repo}",
                "-F",
                f"pr={pr_number}",
            ],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        return parse_pr_comments(result.stdout)
    except subprocess.CalledProcessError:
        return [], None
    except (json.JSONDecodeError, KeyError):
        return [], None


def get_pr_checks(cwd: Path) -> list[CheckRun]:
    """Get CI check status for the current PR.

    Args:
        cwd: Working directory.

    Returns:
        List of CheckRun objects.
    """
    try:
        result = run_cmd(
            ["gh", "pr", "checks", "--json", "name,state,link"],
            cwd=cwd,
            quiet=True,
            check=False,  # Don't fail if no checks
        )
        if result.returncode != 0:
            return []

        return parse_check_runs(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return []


def get_run_artifacts(cwd: Path, run_id: str) -> list[Artifact]:
    """Get artifacts for a workflow run.

    Args:
        cwd: Working directory.
        run_id: GitHub Actions run ID.

    Returns:
        List of Artifact objects.
    """
    try:
        # Get artifacts via the API
        result = run_cmd(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/artifacts",
                "-q",
                ".artifacts",
            ],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode != 0:
            return []

        return parse_artifacts(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return []


def get_check_logs_truncated(cwd: Path, run_id: str, max_lines: int = 50) -> str:
    """Get truncated log output for failed steps in a workflow run.

    Args:
        cwd: Working directory.
        run_id: GitHub Actions run ID.
        max_lines: Maximum lines to return.

    Returns:
        Truncated log output string.
    """
    try:
        result = run_cmd(
            ["gh", "run", "view", run_id, "--log-failed"],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode != 0:
            return ""

        lines = result.stdout.strip().split("\n")
        if len(lines) > max_lines:
            return "\n".join(lines[-max_lines:])
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return ""


def get_full_check_log(cwd: Path, run_id: str, failed_only: bool = False) -> str:
    """Get full log output for a workflow run.

    Args:
        cwd: Working directory.
        run_id: GitHub Actions run ID.
        failed_only: If True, only show failed step logs.

    Returns:
        Full log output string.

    Raises:
        GitHubCommandFailed: If gh cannot fetch the logs.
        GitHubCliMissing: If gh is not installed.
    """
    try:
        cmd = ["gh", "run", "view", run_id]
        if failed_only:
            cmd.append("--log-failed")
        else:
            cmd.append("--log")

        result = run_cmd(cmd, cwd=cwd, quiet=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        raise GitHubCommandFailed(f"get logs for run {run_id}", e.stderr)
    except FileNotFoundError:
        raise GitHubCliMissing("gh")


def download_artifact(
    cwd: Path, run_id: str, artifact_name: str
) -> tuple[Path, list[str]]:
    """Download an artifact from a workflow run into $TMPDIR.

    Args:
        cwd: Working directory.
        run_id: GitHub Actions run ID.
        artifact_name: Name of the artifact to download.

    Returns:
        Tuple of (output directory path, list of relative file paths).

    Raises:
        GitHubCommandFailed: If the download fails.
        GitHubCliMissing: If gh is not installed.
    """
    tmp_base = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))
    output_dir = tmp_base / artifact_name
    if output_dir.exists():
        suffix = 2
        while (tmp_base / f"{artifact_name}-{suffix}").exists():
            suffix += 1
        output_dir = tmp_base / f"{artifact_name}-{suffix}"

    try:
        run_cmd(
            [
                "gh",
                "run",
                "download",
                run_id,
                "-n",
                artifact_name,
                "-D",
                str(output_dir),
            ],
            cwd=cwd,
            quiet=False,
            check=True,
        )

        files: list[str] = []
        for root, _dirs, filenames in os.walk(output_dir):
            for f in filenames:
                files.append(str(Path(root, f).relative_to(output_dir)))
        files.sort()

        return output_dir, files
    except subprocess.CalledProcessError as e:
        raise GitHubCommandFailed(f"download artifact '{artifact_name}'", e.stderr)
    except FileNotFoundError:
        raise GitHubCliMissing("gh")


def read_pr(cwd: Path | None = None) -> PRInfo:
    """Read comprehensive PR information including comments, checks, and artifacts.

    Args:
        cwd: Working directory (default: actual cwd).

    Returns:
        PRInfo with comments, checks and artifacts populated. A merged PR
        returns early with all three left empty: there is nothing left to act
        on, so the extra round trips would buy nothing.

    Raises:
        NoPullRequest: If the branch has no PR.
        GitHubCommandFailed: If reading the PR itself fails. A failed *repo*
            lookup is not fatal — the PR renders with no comments.
        GitHubCliMissing: If gh is not installed.
    """
    if cwd is None:
        cwd = Path.cwd()

    # Get basic PR info
    pr_info = get_pr_info(cwd)

    # If merged, no need to fetch more details
    if pr_info.merged:
        return pr_info

    # Get repo info for GraphQL queries
    try:
        owner, repo = get_repo_info(cwd)
    except GitHubCommandFailed:
        # A repo gh could not read has no comments to fetch, so render the PR
        # without them. Only a failed command degrades this way — anything else
        # propagates rather than reading as a PR with nothing said on it. In
        # practice `get_pr_info` above would already have raised on a missing
        # gh; this narrowing is defence in depth, so the degrade path cannot
        # widen as this function grows.
        owner, repo = None, None

    # Get PR comments (inline threads + top-level + review summaries) and last push time
    if owner and repo:
        pr_info.comments, pr_info.last_push_at = get_pr_comments(
            cwd, owner, repo, pr_info.number
        )

    # Get check status
    pr_info.checks = get_pr_checks(cwd)

    # Get artifacts for failed checks
    failed_run_ids = set()
    for check in pr_info.checks:
        if check.state == "FAILURE" and check.run_id:
            failed_run_ids.add(check.run_id)

    for run_id in failed_run_ids:
        artifacts = get_run_artifacts(cwd, run_id)
        if artifacts:
            pr_info.artifacts[run_id] = artifacts

    return pr_info


_T = TypeVar("_T")


def _poll_until(
    check: Callable[[], _T | None],
    *,
    timeout: int,
    poll_interval: int,
    progress: Callable[[], str],
    timeout_message: Callable[[], str],
) -> _T:
    """Poll ``check`` on a fixed interval until it yields a result.

    Shared base for the ``wait_for_*`` helpers. Each iteration calls ``check``:
    a non-``None`` return is the result and ends the loop. A ``None`` return
    means "keep waiting" — ``progress()`` is printed and we sleep for
    ``poll_interval`` before retrying. Any exception raised by ``check``
    propagates, which is how a probe aborts early on a terminal failure.

    Args:
        check: Probe run each iteration; returns the result or ``None`` to wait.
        timeout: Maximum seconds to wait before raising.
        poll_interval: Seconds to sleep between polls.
        progress: Builds the per-iteration progress line (called when waiting).
        timeout_message: Builds the ``TimeoutError`` message (called on timeout).

    Returns:
        The first non-``None`` value returned by ``check``.

    Raises:
        TimeoutError: If ``timeout`` elapses before ``check`` yields a result.
    """
    start = time.monotonic()

    while True:
        result = check()
        if result is not None:
            return result

        if time.monotonic() - start >= timeout:
            raise TimeoutError(timeout_message())

        print_flushed(progress())
        time.sleep(poll_interval)


def wait_for_checks(
    cwd: Path,
    timeout: int = 1800,
    poll_interval: int = 30,
) -> tuple[bool, list[CheckRun]]:
    """Poll PR checks until all reach a terminal state.

    Args:
        cwd: Working directory (must be in a git repo with a PR).
        timeout: Maximum seconds to wait (default 1800 = 30 min).
        poll_interval: Seconds between polls (default 30).

    Returns:
        Tuple of (passed, checks) where passed is True only if all checks
        are SUCCESS, SKIPPED, or NEUTRAL.

    Raises:
        TimeoutError: If timeout exceeded before all checks complete.
        NoChecksFound: If the PR has no checks.
    """
    start = time.monotonic()
    progress = {"complete": 0, "total": 0}

    def check() -> tuple[bool, list[CheckRun]] | None:
        checks = get_pr_checks(cwd)
        if not checks and time.monotonic() - start > poll_interval * 2:
            raise NoChecksFound()

        progress["complete"] = sum(1 for c in checks if c.state in TERMINAL_STATES)
        progress["total"] = len(checks)

        if progress["total"] > 0 and progress["complete"] == progress["total"]:
            passed = all(c.state in PASSING_STATES for c in checks)
            return passed, checks
        return None

    return _poll_until(
        check,
        timeout=timeout,
        poll_interval=poll_interval,
        progress=lambda: (
            f"Waiting... {progress['complete']}/{progress['total']} checks complete"
        ),
        timeout_message=lambda: (
            f"Timed out after {timeout}s waiting for checks "
            f"({progress['complete']}/{progress['total']} complete)"
        ),
    )


def wait_for_merge(
    cwd: Path,
    timeout: int = 3600,
    poll_interval: int = 30,
) -> PRInfo:
    """Poll the current PR until it merges.

    Args:
        cwd: Working directory (must be in a git repo with a PR).
        timeout: Maximum seconds to wait (default 3600 = 1 hour).
        poll_interval: Seconds between polls (default 30).

    Returns:
        The merged PRInfo.

    Raises:
        PullRequestNotMergeable: If the PR is closed without merging or its CI
            reaches a terminal failed state.
        TimeoutError: If timeout exceeded before the PR merges.
    """
    pr_number: int | None = None

    def check() -> PRInfo | None:
        nonlocal pr_number
        pr = get_pr_info(cwd)
        pr_number = pr.number

        if pr.merged:
            return pr
        if pr.state == "CLOSED":
            raise PullRequestNotMergeable(f"PR #{pr.number} was closed without merging")

        failed = [
            c.name
            for c in get_pr_checks(cwd)
            if c.state in TERMINAL_STATES and c.state not in PASSING_STATES
        ]
        if failed:
            raise PullRequestNotMergeable(
                f"PR #{pr.number} has failing checks: {', '.join(failed)}"
            )
        return None

    return _poll_until(
        check,
        timeout=timeout,
        poll_interval=poll_interval,
        progress=lambda: f"Waiting for PR #{pr_number} to merge...",
        timeout_message=lambda: (
            f"Timed out after {timeout}s waiting for PR #{pr_number} to merge"
        ),
    )


def wait_for_review(
    cwd: Path,
    timeout: int = 1800,
    poll_interval: int = 30,
) -> PRComment:
    """Poll PR until a review or inline-thread comment arrives after the last push.

    A "review" here is either a formal review submission (kind="review") or an
    unresolved inline thread comment (kind="thread"). Plain top-level issue
    comments (kind="issue") are ignored — they're often informational.

    Args:
        cwd: Working directory (must be in a git repo with a PR).
        timeout: Maximum seconds to wait (default 1800 = 30 min).
        poll_interval: Seconds between polls (default 30).

    Returns:
        The earliest qualifying PRComment created after the last push.

    Raises:
        TimeoutError: If timeout exceeded before a review arrives.
        NoPullRequest: If the branch has no PR.
        GitHubCommandFailed: If the repo or PR lookup fails.
        GitHubCliMissing: If gh is not installed.
    """
    owner, repo = get_repo_info(cwd)
    pr_number = get_pr_info(cwd).number

    def check() -> PRComment | None:
        comments, last_push_at = get_pr_comments(cwd, owner, repo, pr_number)

        candidates = [
            c
            for c in comments
            if c.kind in ("review", "thread")
            and c.created_at
            and (last_push_at is None or c.created_at > last_push_at)
        ]
        if candidates:
            candidates.sort(key=lambda c: c.created_at)
            return candidates[0]
        return None

    return _poll_until(
        check,
        timeout=timeout,
        poll_interval=poll_interval,
        progress=lambda: f"Waiting for review on PR #{pr_number}...",
        timeout_message=lambda: (
            f"Timed out after {timeout}s waiting for a review on PR #{pr_number}"
        ),
    )


def get_worktree_code(cwd: Path) -> tuple[str, str]:
    """Get commits and uncommitted changes for a worktree.

    Args:
        cwd: Working directory (must be in a git worktree).

    Returns:
        Tuple of (commits_output, uncommitted_output).
        commits_output: Combined log and diff of commits since diverging from main.
        uncommitted_output: Diff of uncommitted changes. Either half reads as
        empty when git cannot produce it — a worktree with no commits, or one
        not on a branch, is not an error here.
    """
    # Get commits since diverging from this branch's base. For a stacked branch
    # that is its parent, not main: diffing against main would present the
    # parent's commits as this PR's work, and a reviewing agent would report on
    # code that belongs to a different PR.
    commits_output = ""
    try:
        # A base that merged and was pruned no longer resolves. Falling back to
        # main keeps the reviewer seeing this branch's work; without it the
        # merge-base raises, the handler below swallows it, and the review is
        # handed no code at all.
        merge_base = ""
        for base_ref in _base_refs_for_diff(cwd):
            try:
                merge_base = run_git(
                    ["merge-base", "HEAD", base_ref],
                    cwd=cwd,
                    quiet=True,
                ).stdout.strip()
                break
            except subprocess.CalledProcessError:
                continue
        if not merge_base:
            raise subprocess.CalledProcessError(1, "merge-base")

        # Get log of commits
        log_result = run_git(
            ["log", f"{merge_base}..HEAD", "--oneline"],
            cwd=cwd,
            quiet=True,
        )
        log_output = log_result.stdout.strip()

        # Get diff of commits
        diff_result = run_git(
            ["diff", f"{merge_base}...HEAD"],
            cwd=cwd,
            quiet=True,
        )
        diff_output = diff_result.stdout.strip()

        if log_output:
            commits_output = f"Commits:\n{log_output}\n\nDiff:\n{diff_output}"
    except subprocess.CalledProcessError:
        # No commits or not on a branch - that's fine
        pass

    # Get uncommitted changes
    uncommitted_output = ""
    try:
        diff_result = run_git(
            ["diff", "HEAD"],
            cwd=cwd,
            quiet=True,
        )
        uncommitted_output = diff_result.stdout.strip()
    except subprocess.CalledProcessError:
        pass

    return commits_output, uncommitted_output
