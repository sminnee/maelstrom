"""CLI commands for GitHub operations."""

import sys
from pathlib import Path

import click

from .cmux import mael_layout
from .context import resolve_context
from .github import (
    create_pr,
    download_artifact,
    get_check_logs_truncated,
    get_full_check_log,
    get_worktree_code,
    read_pr,
    wait_for_checks,
    wait_for_review,
)


@click.group("gh")
def gh():
    """GitHub-related commands."""
    pass


def _handle_wait_for_review(cwd: Path) -> None:
    """Block until a reviewer comments, then print and exit appropriately.

    Exits 2 on timeout. Returns normally (exit 0) once a review arrives.
    """
    try:
        comment = wait_for_review(cwd)
    except TimeoutError as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    except RuntimeError as e:
        raise click.ClickException(str(e))

    location = ""
    if comment.kind == "thread" and comment.path:
        loc = f"{comment.path}:{comment.line}" if comment.line is not None else comment.path
        location = f" on {loc}"
    snippet = comment.body.strip().splitlines()[0] if comment.body.strip() else ""
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    click.echo(f"Review received from @{comment.author} ({comment.kind}){location}")
    if snippet:
        click.echo(f"  {snippet}")


def _open_pr_in_cmux(url: str) -> None:
    """Open a PR URL in a cmux browser, recycling any github.com browser. No-op outside cmux."""
    mael_layout.show_pr_browser(url)


@gh.command("create-pr")
@click.argument("issue_id", required=False, default=None)
@click.option("--draft", is_flag=True, help="Create as draft PR")
@click.option("--progress", is_flag=True, help="Mark as progress (not final). Uses 'Progresses' instead of 'Fixes' and skips setting status to 'In Review'")
@click.option("--wait", is_flag=True, help="Wait for CI checks to complete after creating PR")
@click.option("--wait-for-review", "wait_for_review_flag", is_flag=True, help="Wait until a reviewer leaves feedback (review or inline thread). Exits 0 on first review, 2 on timeout.")
@click.option("--squash", is_flag=True, help="Autosquash fixup! commits before pushing")
@click.option("--target", default=None, help="Project/worktree target for directory resolution")
def gh_create_pr(issue_id, draft, progress, wait, wait_for_review_flag, squash, target):
    """Create a PR for the current worktree (or push if PR exists).

    If ISSUE_ID is provided (e.g., ME-41), appends (Fixes ISSUE_ID) to the PR title
    for Linear auto-linking, and the task status is set to "In Review".

    With --progress, uses (Progresses ISSUE_ID) instead and does not change the task
    status to "In Review" (for multi-session tasks with remaining work).
    """
    if wait and wait_for_review_flag:
        raise click.UsageError("--wait and --wait-for-review are mutually exclusive")

    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Determine working directory for PR creation
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        url, created = create_pr(cwd=cwd, draft=draft, issue_id=issue_id, progress=progress, squash=squash)
        if created:
            click.echo(f"PR created: {url}")
        else:
            click.echo(f"Pushed to existing PR: {url}")
        _open_pr_in_cmux(url)
    except Exception as e:
        raise click.ClickException(str(e))

    # If issue_id provided, update Linear task status
    if issue_id:
        try:
            from .integrations.linear import (
                get_issue,
                get_labels,
                get_product_label,
                get_workflow_states,
                update_issue,
            )

            issue = get_issue(issue_id)
            states = get_workflow_states()

            if not progress:
                # Final PR: set status to "In Review"
                if "In Review" not in states:
                    click.echo("Warning: 'In Review' state not found in workflow", err=True)
                else:
                    # Build label list with product label
                    labels_map = get_labels()
                    current_labels = [
                        label["name"] for label in issue.get("labels", {}).get("nodes", [])
                    ]
                    product_label = get_product_label()
                    new_labels = list(current_labels)
                    if product_label and product_label not in new_labels and product_label in labels_map:
                        new_labels.append(product_label)

                    label_ids = [labels_map[name] for name in new_labels if name in labels_map]

                    update_issue(
                        issue["id"],
                        stateId=states["In Review"],
                        labelIds=label_ids,
                    )
                    click.echo(f"Updated {issue['identifier']} status to: In Review")

            # Promote parent from early states to In Progress
            if issue.get("parent"):
                parent = get_issue(issue["parent"]["id"])
                early_states = {"Todo", "Planned", "Backlog"}
                if parent["state"]["name"] in early_states:
                    if "In Progress" in states:
                        update_issue(parent["id"], stateId=states["In Progress"])
                        click.echo(f"Updated parent {parent['identifier']} status to: In Progress")
        except Exception as e:
            click.echo(f"Warning: Could not update Linear task: {e}", err=True)

    # Wait for CI checks if requested
    if wait:
        try:
            passed, checks = wait_for_checks(cwd)
            for check in checks:
                click.echo(f"  {check.state}: {check.name}")
            if passed:
                click.echo("Build passed")
            else:
                click.echo("Build failed")
                sys.exit(1)
        except TimeoutError as e:
            click.echo(str(e), err=True)
            sys.exit(2)
        except RuntimeError as e:
            raise click.ClickException(str(e))

    if wait_for_review_flag:
        _handle_wait_for_review(cwd)


@gh.command("wait-for-pr")
@click.argument("target", required=False, default=None)
@click.option("--timeout", default=1800, help="Timeout in seconds (default: 1800)")
@click.option("--interval", default=30, help="Poll interval in seconds (default: 30)")
def gh_wait_for_pr(target, timeout, interval):
    """Wait for CI checks to complete on the current PR.

    Polls GitHub checks every INTERVAL seconds until all checks reach a
    terminal state, then reports the results.

    Exit codes: 0 = passed, 1 = failed, 2 = timeout.
    """
    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        passed, checks = wait_for_checks(cwd, timeout=timeout, poll_interval=interval)
        for check in checks:
            click.echo(f"  {check.state}: {check.name}")
        if passed:
            click.echo("Build passed")
        else:
            click.echo("Build failed")
            sys.exit(1)
    except TimeoutError as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    except RuntimeError as e:
        raise click.ClickException(str(e))


def _format_size(size_bytes: int) -> str:
    """Format a byte size as a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _render_pr_comments(pr_info, all_comments: bool) -> None:
    """Render PR comments section with recency-based summarisation.

    Comments newer than the last pushed commit are shown in full; older
    comments are collapsed into a count line unless --all-comments is set.
    Inline-thread comments are grouped by thread for display.
    """
    comments = pr_info.comments
    if not comments:
        return

    last_push = pr_info.last_push_at
    # ISO 8601 Z-form sorts lexicographically — no parsing needed.
    def is_new(c) -> bool:
        if last_push is None:
            return True
        return c.created_at > last_push

    issue_comments = [c for c in comments if c.kind == "issue"]
    review_comments = [c for c in comments if c.kind == "review"]
    thread_comments = [c for c in comments if c.kind == "thread"]

    # Group thread comments by thread_id, preserving order of first appearance.
    threads: dict[str, list] = {}
    for c in thread_comments:
        tid = c.thread_id or ""
        threads.setdefault(tid, []).append(c)

    new_issue = [c for c in issue_comments if is_new(c)]
    old_issue = [c for c in issue_comments if not is_new(c)]
    new_review = [c for c in review_comments if is_new(c)]
    old_review = [c for c in review_comments if not is_new(c)]

    # Threads: entirely-old vs has-new-content
    threads_with_new: list[tuple[str, list]] = []
    threads_all_old: list[tuple[str, list]] = []
    for tid, tcomments in threads.items():
        if any(is_new(c) for c in tcomments):
            threads_with_new.append((tid, tcomments))
        else:
            threads_all_old.append((tid, tcomments))

    # Count total old comments hidden when not in all_comments mode.
    old_in_mixed_threads = sum(
        len([c for c in tc if not is_new(c)]) for _, tc in threads_with_new
    )
    all_old_thread_count = sum(len(tc) for _, tc in threads_all_old)
    total_old_hidden = (
        len(old_issue) + len(old_review) + all_old_thread_count + old_in_mixed_threads
    )

    if all_comments:
        show_issue = issue_comments
        show_review = review_comments
        show_threads = list(threads.items())
    else:
        show_issue = new_issue
        show_review = new_review
        show_threads = threads_with_new

    if not show_issue and not show_review and not show_threads and total_old_hidden == 0:
        return

    click.echo()
    click.echo("--- Comments ---")

    if show_issue:
        label_count = len(show_issue) if all_comments else len(new_issue)
        suffix = "" if all_comments else " new"
        click.echo()
        click.echo(f"Top-level ({label_count}{suffix}):")
        for c in show_issue:
            click.echo(f"  @{c.author} ({c.created_at}):")
            for line in c.body.splitlines():
                click.echo(f"    {line}")

    if show_review:
        label_count = len(show_review) if all_comments else len(new_review)
        suffix = "" if all_comments else " new"
        click.echo()
        click.echo(f"Review summaries ({label_count}{suffix}):")
        for c in show_review:
            click.echo(f"  @{c.author} ({c.created_at}):")
            for line in c.body.splitlines():
                click.echo(f"    {line}")

    if show_threads:
        new_thread_count = len(threads_with_new)
        label_count = len(show_threads) if all_comments else new_thread_count
        suffix = "" if all_comments else " new"
        click.echo()
        click.echo(f"Inline ({label_count}{suffix}):")
        for tid, tcomments in show_threads:
            head = tcomments[0]
            line_info = f":{head.line}" if head.line else ""
            click.echo(f"  {head.path}{line_info}")
            if all_comments:
                shown = tcomments
                hidden = 0
            else:
                shown = [c for c in tcomments if is_new(c)]
                hidden = len(tcomments) - len(shown)
            for c in shown:
                click.echo(f"    @{c.author}:")
                for line in c.body.splitlines():
                    click.echo(f"      {line}")
            if hidden:
                noun = "comment" if hidden == 1 else "comments"
                click.echo(f"    ... {hidden} earlier {noun} in this thread")

    if not all_comments and total_old_hidden > 0:
        noun = "comment" if total_old_hidden == 1 else "comments"
        click.echo()
        click.echo(f"{total_old_hidden} older {noun} hidden (use --all-comments to show)")


@gh.command("read-pr")
@click.argument("target", required=False, default=None)
@click.option("--wait", is_flag=True, help="Wait for CI checks to complete (exit 0=pass, 1=fail, 2=timeout)")
@click.option("--wait-for-review", "wait_for_review_flag", is_flag=True, help="Wait until a reviewer leaves feedback (review or inline thread). Exits 0 on first review, 2 on timeout.")
@click.option("--all-comments", is_flag=True, help="Include comments made before the last pushed commit")
def gh_read_pr(target, wait, wait_for_review_flag, all_comments):
    """Read PR status, comments, and check results."""
    if wait and wait_for_review_flag:
        raise click.UsageError("--wait and --wait-for-review are mutually exclusive")

    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Determine working directory
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        pr_info = read_pr(cwd=cwd)
    except RuntimeError as e:
        raise click.ClickException(str(e))

    # Print header
    click.echo(f"PR #{pr_info.number}: {pr_info.title}")
    click.echo(f"URL: {pr_info.url}")
    _open_pr_in_cmux(pr_info.url)

    # If merged, show simple message and exit
    if pr_info.merged:
        click.echo()
        click.echo("PR has been merged - no further action necessary")
        return

    click.echo(f"Status: {pr_info.state}")

    # Comments (inline threads + top-level + review summaries)
    _render_pr_comments(pr_info, all_comments=all_comments)

    # Checks
    failed_checks = [c for c in pr_info.checks if c.state == "FAILURE"]
    passing_checks = [c for c in pr_info.checks if c.state == "SUCCESS"]
    pending_checks = [c for c in pr_info.checks if c.state not in ("FAILURE", "SUCCESS")]

    if failed_checks:
        click.echo()
        click.echo("--- Failed Checks ---")
        for check in failed_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            click.echo(f"  X {check.name}{run_id_info}")

            # Show truncated logs
            if check.run_id:
                logs = get_check_logs_truncated(cwd, check.run_id, max_lines=30)
                if logs:
                    click.echo()
                    for line in logs.split("\n"):
                        click.echo(f"    {line}")
                    click.echo()
                click.echo(f"    -> Full log: mael gh check-log [--failed-only] {check.run_id}")

                # Show artifacts for this run
                if check.run_id in pr_info.artifacts:
                    click.echo()
                    click.echo("    Artifacts:")
                    for artifact in pr_info.artifacts[check.run_id]:
                        size_str = _format_size(artifact.size)
                        click.echo(f"      - {artifact.name} ({size_str})")
                        click.echo(f"        -> Download: mael gh download-artifact {check.run_id} {artifact.name}")

    if pending_checks:
        click.echo()
        click.echo("--- Pending Checks ---")
        for check in pending_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            click.echo(f"  ... {check.name}{run_id_info}")

    if passing_checks:
        click.echo()
        click.echo("--- Passing Checks ---")
        for check in passing_checks:
            run_id_info = f" (run {check.run_id})" if check.run_id else ""
            click.echo(f"  + {check.name}{run_id_info}")

    # Wait for CI checks if requested
    if wait:
        try:
            passed, checks = wait_for_checks(cwd)
            for check in checks:
                click.echo(f"  {check.state}: {check.name}")
            if passed:
                click.echo("Build passed")
            else:
                click.echo("Build failed")
                sys.exit(1)
        except TimeoutError as e:
            click.echo(str(e), err=True)
            sys.exit(2)
        except RuntimeError as e:
            raise click.ClickException(str(e))

    if wait_for_review_flag:
        _handle_wait_for_review(cwd)


@gh.command("download-artifact")
@click.argument("run_id")
@click.argument("artifact_name")
def gh_download_artifact(run_id, artifact_name):
    """Download an artifact from a PR's workflow run."""
    try:
        artifact_path, files = download_artifact(
            cwd=Path.cwd(),
            run_id=run_id,
            artifact_name=artifact_name,
        )
        click.echo(f"Downloaded to: {artifact_path}")
        if files:
            click.echo(f"\nFiles ({len(files)}):")
            for f in files:
                click.echo(f"  {f}")
        else:
            click.echo("\nNo files found in artifact.")
    except RuntimeError as e:
        raise click.ClickException(str(e))


@gh.command("check-log")
@click.argument("run_id")
@click.option("--failed-only", is_flag=True, help="Show only failed step logs")
def gh_check_log(run_id, failed_only):
    """Show full log output for a GitHub Actions run."""
    try:
        logs = get_full_check_log(
            cwd=Path.cwd(),
            run_id=run_id,
            failed_only=failed_only,
        )
        click.echo(logs)
    except RuntimeError as e:
        raise click.ClickException(str(e))


@gh.command("show-code")
@click.argument("target", required=False, default=None)
@click.option("--committed", is_flag=True, help="Show only committed changes")
@click.option("--uncommitted", is_flag=True, help="Show only uncommitted changes")
def gh_show_code(target, committed, uncommitted):
    """Show commits and uncommitted changes for a worktree."""
    try:
        ctx = resolve_context(target, require_project=False, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))

    # Determine working directory
    if ctx.worktree_path and ctx.worktree_path.exists():
        cwd = ctx.worktree_path
    else:
        cwd = Path.cwd()

    try:
        commits_output, uncommitted_output = get_worktree_code(cwd)
    except RuntimeError as e:
        raise click.ClickException(str(e))

    # Determine what to show based on flags
    show_committed = not uncommitted
    show_uncommitted = not committed

    if show_committed and commits_output:
        click.echo("=== Commits ===")
        click.echo(commits_output)

    if show_uncommitted and uncommitted_output:
        if show_committed and commits_output:
            click.echo()  # Separator between sections
        click.echo("=== Uncommitted Changes ===")
        click.echo(uncommitted_output)

    if not commits_output and not uncommitted_output:
        click.echo("No commits or uncommitted changes found.")
