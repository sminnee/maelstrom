"""GitHub integration for maelstrom projects."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .worktree import run_cmd, run_git, sync_worktree


@dataclass
class ReviewComment:
    """A comment in a review thread."""

    author: str
    body: str
    created_at: str


@dataclass
class ReviewThread:
    """An unresolved review thread on a PR."""

    id: str
    path: str
    line: int | None
    comments: list[ReviewComment] = field(default_factory=list)


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
    review_threads: list[ReviewThread] = field(default_factory=list)
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


def create_pr(cwd: Path | None = None, draft: bool = False) -> tuple[str, bool]:
    """Create a pull request for the current worktree branch, or push if PR exists.

    Syncs (rebases onto origin/main) before pushing.

    Args:
        cwd: Current working directory (default: actual cwd).
        draft: Create as draft PR (only if creating new PR).

    Returns:
        Tuple of (PR URL, created) where created is True if new PR was created.

    Raises:
        RuntimeError: If sync fails (conflicts) or PR creation/push fails.
    """
    if cwd is None:
        cwd = Path.cwd()

    # Sync first (rebase onto origin/main)
    sync_result = sync_worktree(cwd)
    if not sync_result.success:
        if sync_result.had_conflicts:
            raise RuntimeError(
                f"Sync failed due to conflicts. Resolve them first:\n"
                f"  git status\n"
                f"  # resolve conflicts\n"
                f"  git add <files>\n"
                f"  git rebase --continue"
            )
        raise RuntimeError(f"Sync failed: {sync_result.message}")

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

    # If PR exists, just return the URL
    if pr_exists:
        return existing_url, False

    # Get current branch name for --head flag
    branch_result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd, quiet=True)
    branch_name = branch_result.stdout.strip()

    # Try to get the first commit message for title
    try:
        log_result = run_git(["log", "-1", "--format=%s"], cwd=cwd, quiet=True)
        title = log_result.stdout.strip()
    except subprocess.CalledProcessError:
        title = branch_name

    # Create the PR with explicit title (--fill can fail if base branch not fetched)
    cmd = ["gh", "pr", "create", "--title", title, "--body", "", "--head", branch_name]
    if draft:
        cmd.append("--draft")

    result = run_cmd(cmd, cwd=cwd, check=False)

    if result.returncode != 0:
        raise RuntimeError(f"Failed to create PR: {result.stderr}")

    return result.stdout.strip(), True


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


def get_unresolved_review_threads(cwd: Path, owner: str, repo: str, pr_number: int) -> list[ReviewThread]:
    """Get unresolved review threads from a PR using GraphQL.

    Args:
        cwd: Working directory.
        owner: Repository owner.
        repo: Repository name.
        pr_number: Pull request number.

    Returns:
        List of unresolved ReviewThread objects.
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
              comments(first: 10) {
                nodes {
                  body
                  author { login }
                  createdAt
                }
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
        threads = []

        nodes = data.get("data", {}).get("repository", {}).get("pullRequest", {}).get("reviewThreads", {}).get("nodes", [])
        for node in nodes:
            if node.get("isResolved"):
                continue

            comments = []
            for comment in node.get("comments", {}).get("nodes", []):
                author = comment.get("author", {})
                comments.append(ReviewComment(
                    author=author.get("login", "unknown") if author else "unknown",
                    body=comment.get("body", ""),
                    created_at=comment.get("createdAt", ""),
                ))

            threads.append(ReviewThread(
                id=node.get("id", ""),
                path=node.get("path", ""),
                line=node.get("line"),
                comments=comments,
            ))

        return threads
    except subprocess.CalledProcessError:
        # GraphQL query failed - return empty list rather than failing
        return []
    except (json.JSONDecodeError, KeyError):
        return []


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


def download_artifact(cwd: Path, run_id: str, artifact_name: str, output_dir: Path | None = None) -> Path:
    """Download an artifact from a workflow run.

    Args:
        cwd: Working directory.
        run_id: GitHub Actions run ID.
        artifact_name: Name of the artifact to download.
        output_dir: Directory to download to (default: cwd).

    Returns:
        Path to the downloaded artifact directory.

    Raises:
        RuntimeError: If download fails.
    """
    if output_dir is None:
        output_dir = cwd

    try:
        run_cmd(
            ["gh", "run", "download", run_id, "-n", artifact_name, "-D", str(output_dir)],
            cwd=cwd,
            quiet=False,
            check=True,
        )
        return output_dir / artifact_name
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

    # Get unresolved review threads
    if owner and repo:
        pr_info.review_threads = get_unresolved_review_threads(cwd, owner, repo, pr_info.number)

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
    # Get commits since diverging from origin/main
    commits_output = ""
    try:
        # Get the merge-base
        merge_base_result = run_git(
            ["merge-base", "HEAD", "origin/main"],
            cwd=cwd,
            quiet=True,
        )
        merge_base = merge_base_result.stdout.strip()

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
