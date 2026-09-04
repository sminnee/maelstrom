"""Pure GitHub domain logic — no subprocess, no printing, no Click.

This is the model layer for the GitHub subsystem, mirroring how
``worktree_model.py`` is the model for the worktree subsystem (see
``docs/dev/architecture-patterns.md``). It holds the dataclasses that describe a
pull request, the parsers that turn raw ``gh`` output into them, the stack walk,
and the typed errors the transport layer raises.

Everything here is a pure function of its arguments, so it is exercised by
calling it with a literal string. The IO adapter ``github.py`` imports from here;
this module must never import the adapter (that would create a circular
dependency).
"""

import json
from dataclasses import dataclass, field

from .worktree_model import MAIN_BRANCH


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
    artifacts: dict[str, list[Artifact]] = field(
        default_factory=dict
    )  # run_id -> artifacts


TERMINAL_STATES = {
    "SUCCESS",
    "FAILURE",
    "STARTUP_FAILURE",
    "CANCELLED",
    "SKIPPED",
    "NEUTRAL",
    "TIMED_OUT",
    "STALE",
    "ACTION_REQUIRED",
}
PASSING_STATES = {"SUCCESS", "SKIPPED", "NEUTRAL"}


class SyncFailed(RuntimeError):
    """A rebase onto the branch's base could not finish.

    Raised by ``github.create_pr``, which syncs before it pushes. Deliberately
    not a ``GitHubError``: the rebase is local git work, and calling it a GitHub
    failure would misname it. The CLI catches the two side by side.

    It subclasses ``RuntimeError`` because ``cli.py`` still catches that broadly
    for worktree work; see ``docs/dev/architecture-patterns.md`` §3.
    """


class GitHubError(RuntimeError):
    """Base for every error the GitHub subsystem raises.

    The CLI layer is the only place that catches these and turns them into
    ``click.ClickException`` (see ``docs/dev/architecture-patterns.md`` §3).

    It subclasses ``RuntimeError`` so that the ``except RuntimeError`` sites
    ``cli.py`` still keeps for worktree work carry on working while those
    modules get their own typed errors.
    """


class GitHubCliMissing(GitHubError):
    """A binary the subsystem shells out to is not on the PATH."""

    def __init__(self, binary: str) -> None:
        self.binary = binary
        message = (
            "GitHub CLI (gh) is not installed"
            if binary == "gh"
            else f"{binary} is not installed"
        )
        super().__init__(message)


class NoPullRequest(GitHubError):
    """The current branch has no pull request."""

    def __init__(self) -> None:
        super().__init__("No pull request found for current branch")


class GitHubCommandFailed(GitHubError):
    """A ``gh`` or ``git`` command exited non-zero.

    Not a dataclass: a dataclass generates an ``__init__`` that leaves
    ``BaseException.args`` empty, which breaks copying and pickling the error.
    Both arguments go into ``args`` for the same reason — ``copy`` rebuilds the
    error by calling the class with ``*args``.
    """

    def __init__(self, action: str, stderr: str) -> None:
        self.action = action
        self.stderr = stderr
        super().__init__(action, stderr)

    def __str__(self) -> str:
        return f"Failed to {self.action}: {self.stderr}"


class NoChecksFound(GitHubError):
    """The PR has no CI checks to wait for.

    Not a ``GitHubCommandFailed``: nothing errored — gh answered with an empty
    list. Synthesising an ``action``/``stderr`` pair for it would put a string
    no subprocess produced into ``.stderr``.
    """

    def __init__(self) -> None:
        super().__init__("No checks found for this PR")


class PullRequestNotMergeable(GitHubError):
    """The PR will not merge: it closed unmerged, or its CI failed terminally."""


def is_missing_pr_error(stderr: str) -> bool:
    """Whether ``stderr`` is gh saying the branch has no pull request.

    gh reports this as an ordinary command failure, so the message is the only
    way to tell it apart from a real error.
    """
    return "no pull requests found" in stderr.lower()


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


@dataclass(frozen=True)
class OpenPrsPage:
    """One page of the open-PR query: the branches it names and where to go next."""

    prs: dict[str, tuple[int, int]]
    has_next: bool
    cursor: str | None


def parse_pr_info(payload: str) -> PRInfo:
    """Read ``gh pr view --json number,title,url,state,mergedAt,headRefName``.

    Args:
        payload: The raw JSON object gh printed.

    Returns:
        The PR, with ``comments``, ``checks`` and ``artifacts`` left empty for
        the caller to fill.

    Raises:
        json.JSONDecodeError: If ``payload`` is not JSON.
        KeyError: If a required field is absent.
    """
    data = json.loads(payload)
    return PRInfo(
        number=data["number"],
        title=data["title"],
        url=data["url"],
        state=data["state"],
        merged=data.get("mergedAt") is not None,
        head_ref=data["headRefName"],
    )


def parse_pr_comments(payload: str) -> tuple[list[PRComment], str | None]:
    """Flatten the review GraphQL payload into comments, plus the last push time.

    Three sources become one flat list: unresolved inline review threads (one
    comment per reply, carrying the thread's path and line), top-level PR
    comments, and review submissions that said something. Resolved threads and
    bodiless reviews are dropped — neither is anything a reader must act on.

    Args:
        payload: The raw JSON object ``gh api graphql`` printed.

    Returns:
        Tuple of (comments, last_push_at). ``last_push_at`` is the ISO 8601
        ``pushedDate`` of the newest commit, falling back to its
        ``committedDate``, or ``None`` when the payload names no commit.

    Raises:
        json.JSONDecodeError: If ``payload`` is not JSON.
    """
    data = json.loads(payload)
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
            comments.append(
                PRComment(
                    author=author.get("login", "unknown") if author else "unknown",
                    body=c.get("body", ""),
                    created_at=c.get("createdAt", ""),
                    kind="thread",
                    path=path,
                    line=line,
                    thread_id=thread_id,
                )
            )

    for c in pr.get("comments", {}).get("nodes", []) or []:
        author = c.get("author") or {}
        comments.append(
            PRComment(
                author=author.get("login", "unknown") if author else "unknown",
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                kind="issue",
            )
        )

    for r in pr.get("reviews", {}).get("nodes", []) or []:
        body = r.get("body", "") or ""
        if not body.strip():
            continue
        author = r.get("author") or {}
        comments.append(
            PRComment(
                author=author.get("login", "unknown") if author else "unknown",
                body=body,
                created_at=r.get("submittedAt", "") or "",
                kind="review",
            )
        )

    last_push_at: str | None = None
    commit_nodes = pr.get("commits", {}).get("nodes", []) or []
    if commit_nodes:
        commit = commit_nodes[0].get("commit") or {}
        last_push_at = commit.get("pushedDate") or commit.get("committedDate") or None

    return comments, last_push_at


def run_id_from_link(link: str) -> str | None:
    """The Actions run id a check link points at, or ``None`` if it names none.

    A check link looks like
    ``https://github.com/owner/repo/actions/runs/12345678/job/9``. A third-party
    check links somewhere else entirely, which is why this can return nothing.
    """
    if "/runs/" not in link:
        return None
    return link.split("/runs/")[1].split("/")[0]


def parse_check_runs(payload: str) -> list[CheckRun]:
    """Read ``gh pr checks --json name,state,link`` into CheckRuns.

    Args:
        payload: The raw JSON array gh printed.

    Returns:
        One CheckRun per check, each carrying the run id its link names.

    Raises:
        json.JSONDecodeError: If ``payload`` is not JSON.
    """
    data = json.loads(payload)
    return [
        CheckRun(
            name=check.get("name", ""),
            state=check.get("state", ""),
            run_id=run_id_from_link(check.get("link", "")),
            link=check.get("link", ""),
        )
        for check in data
    ]


def parse_artifacts(payload: str) -> list[Artifact]:
    """Read the ``.artifacts`` array of the workflow-run artifacts API.

    Args:
        payload: The raw JSON array gh printed.

    Returns:
        One Artifact per entry.

    Raises:
        json.JSONDecodeError: If ``payload`` is not JSON.
    """
    data = json.loads(payload)
    return [
        Artifact(name=a.get("name", ""), size=a.get("size_in_bytes", 0)) for a in data
    ]


def parse_open_prs_page(payload: str) -> OpenPrsPage:
    """Read one page of the open-PR GraphQL query.

    Args:
        payload: The raw JSON object ``gh api graphql`` printed.

    Returns:
        The branches this page names, and whether another page follows.

    Raises:
        ValueError: If the payload carries GraphQL errors. gh exits 0 on a rate
            limit or a missing scope, so the payload is the only signal that the
            data is not there.
        json.JSONDecodeError: If ``payload`` is not JSON.
        KeyError, TypeError: If the payload has an unexpected shape.
    """
    data = json.loads(payload)
    if data.get("errors"):
        raise ValueError(f"GraphQL query failed: {data['errors']}")
    connection = data["data"]["repository"]["pullRequests"]

    prs = {
        node["headRefName"]: (int(node["number"]), int(node["commits"]["totalCount"]))
        for node in connection["nodes"]
    }
    page_info = connection.get("pageInfo") or {}
    return OpenPrsPage(
        prs=prs,
        has_next=bool(page_info.get("hasNextPage")),
        cursor=page_info.get("endCursor"),
    )
