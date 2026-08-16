"""GitHub integration for maelstrom projects."""

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, TypeVar

from .base_store import GitConfigBaseStore
from .project_scaffold import scaffold_files
from .shell import run_cmd
from .worktree_model import MAIN_BRANCH, REPAIRED_MESSAGE, print_flushed
from .worktree import (
    get_current_branch,
    run_git,
    sync_worktree,
    sync_worktree_with_autorepair,
    update_local_main,
)


@dataclass
class PRComment:
    """A flat comment on a PR (inline thread reply, top-level issue comment, or review summary)."""

    author: str
    body: str
    created_at: str
    kind: str  # "thread" | "issue" | "review"
    path: str | None = None
    line: int | None = None
    thread_id: str | None = None


@dataclass
class CheckRun:
    """A CI check run."""

    name: str
    state: str  # "SUCCESS", "FAILURE", "PENDING", etc.
    run_id: str | None
    link: str


@dataclass
class Artifact:
    """A workflow run artifact."""

    name: str
    size: int  # bytes


@dataclass
class PRInfo:
    """Information about a pull request."""

    number: int
    title: str
    url: str
    state: str  # "OPEN", "MERGED", "CLOSED"
    merged: bool
    head_ref: str
    comments: list[PRComment] = field(default_factory=list)
    last_push_at: str | None = None
    checks: list[CheckRun] = field(default_factory=list)
    artifacts: dict[str, list[Artifact]] = field(default_factory=dict)  # run_id -> artifacts


def get_repo_info(cwd: Path) -> tuple[str, str]:
    """Get the owner and repo name from the git remote.

    Args:
        cwd: Working directory (must be in a git repo).

    Returns:
        Tuple of (owner, repo).

    Raises:
        RuntimeError: If unable to determine repo info.
    """
    try:
        result = run_cmd(
            ["gh", "repo", "view", "--json", "owner,name", "-q", ".owner.login + \"/\" + .name"],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        parts = result.stdout.strip().split("/")
        if len(parts) != 2:
            raise RuntimeError(f"Unexpected repo format: {result.stdout.strip()}")
        return parts[0], parts[1]
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get repo info: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")


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
        The clone URL of the new repository.

    Raises:
        RuntimeError: If the repository cannot be created.
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
                "gh", "repo", "create", name,
                "--source", str(repo_dir),
                "--push",
                "--private" if private else "--public",
            ]
            if description:
                create_cmd += ["--description", description]
            run_cmd(create_cmd, cwd=repo_dir)

            result = run_cmd(
                ["git", "remote", "get-url", "origin"], cwd=repo_dir, quiet=True
            )
            return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create GitHub repository: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")


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
            ["gh", "pr", "list", "--head", branch, "--json", "number", "-q", ".[0].number"],
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
                "gh", "pr", "list", "--head", branch,
                "--json", "number,commits",
                "-q", ".[0] | [.number, (.commits | length)]"
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
                "gh", "api", "graphql",
                "-f", f"query={_OPEN_PRS_QUERY}",
                "-F", "owner=:owner",
                "-F", "repo=:repo",
            ]
            if cursor:
                # -f, not -F: the cursor is a declared String. -F coerces a
                # value that looks like a number, and GraphQL then rejects it.
                cmd += ["-f", f"after={cursor}"]
            result = run_cmd(cmd, cwd=cwd, quiet=True, check=False)
            if result.returncode != 0 or not result.stdout.strip():
                return None

            payload = json.loads(result.stdout.strip())
            # gh exits 0 on a GraphQL error payload (rate limit, bad scope), so
            # the return code alone does not prove the data is there.
            if payload.get("errors"):
                return None
            connection = payload["data"]["repository"]["pullRequests"]

            for node in connection["nodes"]:
                prs[node["headRefName"]] = (
                    int(node["number"]),
                    int(node["commits"]["totalCount"]),
                )

            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return prs
            cursor = page_info.get("endCursor")
            if not cursor:
                return prs
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
        RuntimeError: If no PR exists or gh command fails.
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
            raise RuntimeError("No pull request found for current branch")
        return url
    except subprocess.CalledProcessError as e:
        if "no pull requests found" in e.stderr.lower():
            raise RuntimeError("No pull request found for current branch")
        raise RuntimeError(f"Failed to get PR URL: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")


def create_pr(cwd: Path | None = None, draft: bool = False, issue_id: str | None = None, progress: bool = False, squash: bool = False, autorepair: bool = False, announce: Callable[[str], None] = print_flushed) -> tuple[str, bool]:
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
        RuntimeError: If sync fails (conflicts) or PR creation/push fails.
    """
    if cwd is None:
        cwd = Path.cwd()

    # Sync first (rebase onto origin/main)
    if autorepair:
        sync_result = sync_worktree_with_autorepair(cwd, squash=squash, announce=announce)
    else:
        sync_result = sync_worktree(cwd, squash=squash)
    if not sync_result.success:
        # An aborted rebase is restored, so the manual-resolution steps would
        # name a rebase that is no longer there. A repair that failed without
        # aborting — one that landed on the wrong branch — still needs them.
        if sync_result.had_conflicts and not sync_result.aborted:
            raise RuntimeError(
                f"Sync failed due to conflicts. Resolve them first:\n"
                f"  git status\n"
                f"  # resolve conflicts\n"
                f"  git add <files>\n"
                f"  git rebase --continue"
            )
        raise RuntimeError(f"Sync failed: {sync_result.message}")

    # The push publishes commits the session rewrote, so say so before it lands
    # in a PR.
    if sync_result.repaired:
        announce(REPAIRED_MESSAGE)

    # Check if PR already exists (and is open)
    pr_exists = False
    existing_url = ""
    try:
        result = run_cmd(
            ["gh", "pr", "view", "--json", "url,state", "-q", ".url + \" \" + .state"],
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
        raise RuntimeError("GitHub CLI (gh) is not installed")

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
            raise RuntimeError(f"Failed to push branch: {result.stderr}")
        # Print push output for visibility
        if result.stderr:
            print(result.stderr.strip())
    except FileNotFoundError:
        raise RuntimeError("git is not installed")

    branch_name = get_current_branch(cwd)

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
        raise RuntimeError(f"Failed to create PR: {result.stderr}")

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


def stack_chain(branch: str, bases: dict[str, str]) -> list[str]:
    """The branches from the bottom of ``branch``'s stack up to ``branch`` itself.

    Walks the stored bases down to ``main`` and returns the result bottom-to-top,
    which is the order ``gh stack link`` wants. A branch with no base returns just
    itself, so callers can test the length to decide whether there is a stack at
    all.

    ``bases`` is validated against cycles when it is written, so the walk
    terminates; the visited set is a belt-and-braces stop rather than the guard.
    """
    chain = [branch]
    seen = {branch}
    current = bases.get(branch)
    while current and current != MAIN_BRANCH and current not in seen:
        chain.append(current)
        seen.add(current)
        current = bases.get(current)
    chain.reverse()
    return chain


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
        RuntimeError: If no PR exists or gh command fails.
    """
    try:
        result = run_cmd(
            ["gh", "pr", "view", "--json", "number,title,url,state,mergedAt,headRefName"],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return PRInfo(
            number=data["number"],
            title=data["title"],
            url=data["url"],
            state=data["state"],
            merged=data.get("mergedAt") is not None,
            head_ref=data["headRefName"],
        )
    except subprocess.CalledProcessError as e:
        if "no pull requests found" in e.stderr.lower():
            raise RuntimeError("No pull request found for current branch")
        raise RuntimeError(f"Failed to get PR info: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")


def get_pr_comments(cwd: Path, owner: str, repo: str, pr_number: int) -> tuple[list[PRComment], str | None]:
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
                "gh", "api", "graphql",
                "-f", f"query={query}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"pr={pr_number}",
            ],
            cwd=cwd,
            quiet=True,
            check=True,
        )
        data = json.loads(result.stdout)
        pr = data.get("data", {}).get("repository", {}).get("pullRequest", {}) or {}

        comments: list[PRComment] = []

        for node in pr.get("reviewThreads", {}).get("nodes", []) or []:
            if node.get("isResolved"):
                continue
            thread_id = node.get("id", "")
            path = node.get("path", "")
            line = node.get("line")
            for c in node.get("comments", {}).get("nodes", []) or []:
                author = c.get("author") or {}
                comments.append(PRComment(
                    author=author.get("login", "unknown") if author else "unknown",
                    body=c.get("body", ""),
                    created_at=c.get("createdAt", ""),
                    kind="thread",
                    path=path,
                    line=line,
                    thread_id=thread_id,
                ))

        for c in pr.get("comments", {}).get("nodes", []) or []:
            author = c.get("author") or {}
            comments.append(PRComment(
                author=author.get("login", "unknown") if author else "unknown",
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                kind="issue",
            ))

        for r in pr.get("reviews", {}).get("nodes", []) or []:
            body = r.get("body", "") or ""
            if not body.strip():
                continue
            author = r.get("author") or {}
            comments.append(PRComment(
                author=author.get("login", "unknown") if author else "unknown",
                body=body,
                created_at=r.get("submittedAt", "") or "",
                kind="review",
            ))

        last_push_at: str | None = None
        commit_nodes = pr.get("commits", {}).get("nodes", []) or []
        if commit_nodes:
            commit = commit_nodes[0].get("commit") or {}
            last_push_at = commit.get("pushedDate") or commit.get("committedDate") or None

        return comments, last_push_at
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

        data = json.loads(result.stdout)
        checks = []
        for check in data:
            # Extract run ID from link (e.g., https://github.com/owner/repo/actions/runs/12345678/job/...)
            link = check.get("link", "")
            run_id = None
            if "/runs/" in link:
                parts = link.split("/runs/")
                if len(parts) > 1:
                    run_id = parts[1].split("/")[0]

            checks.append(CheckRun(
                name=check.get("name", ""),
                state=check.get("state", ""),
                run_id=run_id,
                link=link,
            ))
        return checks
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
            ["gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/artifacts", "-q", ".artifacts"],
            cwd=cwd,
            quiet=True,
            check=False,
        )
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        artifacts = []
        for artifact in data:
            artifacts.append(Artifact(
                name=artifact.get("name", ""),
                size=artifact.get("size_in_bytes", 0),
            ))
        return artifacts
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
        RuntimeError: If unable to fetch logs.
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
        raise RuntimeError(f"Failed to get logs for run {run_id}: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")


def download_artifact(cwd: Path, run_id: str, artifact_name: str) -> tuple[Path, list[str]]:
    """Download an artifact from a workflow run into $TMPDIR.

    Args:
        cwd: Working directory.
        run_id: GitHub Actions run ID.
        artifact_name: Name of the artifact to download.

    Returns:
        Tuple of (output directory path, list of relative file paths).

    Raises:
        RuntimeError: If download fails.
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
            ["gh", "run", "download", run_id, "-n", artifact_name, "-D", str(output_dir)],
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
        raise RuntimeError(f"Failed to download artifact '{artifact_name}': {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("GitHub CLI (gh) is not installed")


def read_pr(cwd: Path | None = None) -> PRInfo:
    """Read comprehensive PR information including comments, checks, and artifacts.

    Args:
        cwd: Working directory (default: actual cwd).

    Returns:
        PRInfo with all fields populated.

    Raises:
        RuntimeError: If no PR exists or gh command fails.
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
    except RuntimeError:
        # If we can't get repo info, continue without review threads
        owner, repo = None, None

    # Get PR comments (inline threads + top-level + review summaries) and last push time
    if owner and repo:
        pr_info.comments, pr_info.last_push_at = get_pr_comments(cwd, owner, repo, pr_info.number)

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


TERMINAL_STATES = {
    "SUCCESS", "FAILURE", "STARTUP_FAILURE", "CANCELLED", "SKIPPED",
    "NEUTRAL", "TIMED_OUT", "STALE", "ACTION_REQUIRED",
}
PASSING_STATES = {"SUCCESS", "SKIPPED", "NEUTRAL"}


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
    ``poll_interval`` before retrying. ``check`` may raise ``RuntimeError`` to
    abort early (e.g. a terminal failure state).

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
        RuntimeError: Propagated from ``check`` on a terminal failure.
    """
    start = time.monotonic()

    while True:
        result = check()
        if result is not None:
            return result

        if time.monotonic() - start >= timeout:
            raise TimeoutError(timeout_message())

        print(progress())
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
        RuntimeError: If no PR or checks found.
    """
    start = time.monotonic()
    progress = {"complete": 0, "total": 0}

    def check() -> tuple[bool, list[CheckRun]] | None:
        checks = get_pr_checks(cwd)
        if not checks and time.monotonic() - start > poll_interval * 2:
            raise RuntimeError("No checks found for this PR")

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
        progress=lambda: f"Waiting... {progress['complete']}/{progress['total']} checks complete",
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
        RuntimeError: If the PR is closed without merging or its CI reaches a
            terminal failed state.
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
            raise RuntimeError(f"PR #{pr.number} was closed without merging")

        failed = [
            c.name for c in get_pr_checks(cwd)
            if c.state in TERMINAL_STATES and c.state not in PASSING_STATES
        ]
        if failed:
            raise RuntimeError(
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
        RuntimeError: If PR/repo info is unavailable.
    """
    owner, repo = get_repo_info(cwd)
    pr_number = get_pr_info(cwd).number

    def check() -> PRComment | None:
        comments, last_push_at = get_pr_comments(cwd, owner, repo, pr_number)

        candidates = [
            c for c in comments
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
        uncommitted_output: Diff of uncommitted changes.

    Raises:
        RuntimeError: If git commands fail.
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
                    ["merge-base", "HEAD", base_ref], cwd=cwd, quiet=True,
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
