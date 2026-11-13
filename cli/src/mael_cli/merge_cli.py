"""``mael merge``: finish a task's work, whichever way its project merges.

Each command reads the project's ``merge:`` key. In a ``github`` project it does
what the legacy ``mael gh`` command does. In a ``local`` project it works on the
task's **Merge request** through the orchestrator server, whose Build queue
gates each submit. See ``docs/dev/build-queue.md``.
"""

import sys
import time
from pathlib import Path
from typing import Any

import click

from mael_common.cli_async import AsyncGroup
from mael_domain.config import MaelstromConfig, load_config_or_default
from mael_domain.context import resolve_context
from mael_domain.github import push_head
from mael_domain.github_model import PR_DRAFT_PATH, GitHubError
from mael_domain.worktree import (
    FETCH_ERRORS,
    current_branch_or_none,
    ensure_fetched,
    get_worktree_dirty_files,
    is_based_on_origin_main,
    run_git,
)

from . import github_cli
from .orchestrator_client import (
    read_merge_request,
    retry_merge_request,
    submit_merge_request,
    withdraw_merge_request,
)
from .task_cli import _resolve_task_id

#: The statuses a Build has not settled yet.
_UNSETTLED = frozenset({"queued", "building"})


@click.group("merge", cls=AsyncGroup)
def merge() -> None:
    """Submit, read and withdraw the task's work, as the project's `merge:` says."""


def _where(target: str | None) -> tuple[str, Path, Path]:
    """The project, its path, and the worktree the command runs in."""
    try:
        ctx = resolve_context(target, require_project=True, require_worktree=False)
    except ValueError as e:
        raise click.ClickException(str(e))
    assert ctx.project is not None and ctx.project_path is not None
    cwd = (
        ctx.worktree_path
        if ctx.worktree_path and ctx.worktree_path.exists()
        else Path.cwd()
    )
    return ctx.project, ctx.project_path, cwd


def _config(cwd: Path) -> MaelstromConfig:
    try:
        return load_config_or_default(cwd)
    except ValueError as e:
        raise click.ClickException(f".maelstrom.yaml is invalid: {e}")


def _refuse_flags(mode: str, **flags: Any) -> None:
    """Refuse each set flag that ``mode`` has no use for."""
    named = [f"--{name.replace('_', '-')}" for name, value in flags.items() if value]
    if named:
        raise click.UsageError(f"{', '.join(named)}: not used with merge: {mode}")


@merge.command("submit")
@click.option("--draft", is_flag=True, help="GitHub: create the PR as a draft")
@click.option("--wait", is_flag=True, help="GitHub: wait for CI after the push")
@click.option(
    "--wait-for-review",
    "wait_for_review_flag",
    is_flag=True,
    help="GitHub: wait until a reviewer leaves feedback",
)
@click.option("--squash", is_flag=True, help="GitHub: autosquash fixup! commits first")
@click.option(
    "--autorepair",
    is_flag=True,
    help="GitHub: resolve a rebase conflict with a headless Claude session",
)
@click.option(
    "--skip-pre-push",
    is_flag=True,
    help="GitHub: push without the project's pre_push_cmd (emergency only)",
)
@click.option("--target", default=None, help="Project/worktree target")
async def merge_submit(
    draft, wait, wait_for_review_flag, squash, autorepair, skip_pre_push, target
):
    """Submit the task's work to merge.

    merge: github pushes and opens or updates the PR, as `mael gh create-pr`
    does. merge: local pushes the branch and submits its head commit to the
    task's Merge request, which queues a Build. It sends `.drafts/pr.md` as the
    request's description when the file exists, and leaves the file in place.
    """
    project, project_path, cwd = _where(target)
    mode = _config(cwd).merge
    if mode == "github":
        await github_cli.create_pr_command(
            draft=draft,
            wait=wait,
            wait_for_review_flag=wait_for_review_flag,
            squash=squash,
            autorepair=autorepair,
            skip_pre_push=skip_pre_push,
            target=target,
        )
        return
    _refuse_flags(
        mode,
        draft=draft,
        wait=wait,
        wait_for_review=wait_for_review_flag,
        squash=squash,
        autorepair=autorepair,
        skip_pre_push=skip_pre_push,
    )
    task_id = _resolve_task_id(None)
    if get_worktree_dirty_files(cwd):
        raise click.ClickException(
            "The worktree has uncommitted changes. A Queue build gates a commit: "
            "commit them, then submit."
        )
    branch = current_branch_or_none(cwd)
    if not branch:
        raise click.ClickException("HEAD is detached: check out the task branch.")
    try:
        ensure_fetched(project_path, max_age=0)
    except FETCH_ERRORS as e:
        raise click.ClickException(f"Could not fetch origin: {e}")
    base = is_based_on_origin_main(cwd)
    if base is None:
        raise click.ClickException(
            "The branch is not based on origin/main. Run `mael sync`, then submit."
        )
    try:
        push_head(cwd)
    except GitHubError as e:
        raise click.ClickException(str(e))
    sha = run_git(["rev-parse", "HEAD"], cwd=cwd, quiet=True).stdout.strip()
    draft = cwd / PR_DRAFT_PATH
    description = draft.read_text() if draft.is_file() else None
    reply = submit_merge_request(project, task_id, branch, sha, base, description)
    build = reply["builds"][0] if reply.get("builds") else {}
    click.echo(
        f"Merge request {reply['id']}: Queue build {build.get('id', '?')} of "
        f"{sha[:7]} is {reply['status']}."
    )


def _print_request(reply: dict[str, Any], *, every_build: bool) -> None:
    click.echo(f"Merge request {reply['id']}: {reply['status']}")
    click.echo(f"  Branch: {reply['branch']}")
    click.echo(f"  SHA:    {reply['sha']}")
    click.echo(f"  Base:   {reply['base']}")
    builds = reply.get("builds") or []
    shown = builds if every_build else [b for b in builds if b["id"] == reply["build"]]
    for build in shown:
        linked = " (linked)" if build["id"] == reply["build"] and every_build else ""
        slot = f" in {build['slot']}" if build["slot"] else ""
        job = f", gate job {build['job']}" if build["job"] else ""
        click.echo(
            f"  Queue build {build['id']}{linked}: {build['state']}{slot}{job}, "
            f"{build['sha'][:7]}"
        )
        for line in (build.get("detail") or "").splitlines():
            click.echo(f"    {line}")


@merge.command("status")
@click.option("--task", "task_id", default=None, help="Default: $MAEL_TASK_ID")
@click.option(
    "--builds", "every_build", is_flag=True, help="Local: list every Queue build"
)
@click.option(
    "--wait",
    is_flag=True,
    help="Wait until the status settles (exit 0=passed or merged, 1=any other, 2=timeout)",
)
@click.option("--timeout", default=3600, help="Local: --wait timeout in seconds")
@click.option("--interval", default=10, help="Local: --wait poll interval in seconds")
@click.option(
    "--wait-for-review",
    "wait_for_review_flag",
    is_flag=True,
    help="GitHub: wait until a reviewer leaves feedback",
)
@click.option(
    "--all-comments", is_flag=True, help="GitHub: include comments before the last push"
)
@click.option("--target", default=None, help="Project/worktree target")
def merge_status(
    task_id,
    every_build,
    wait,
    timeout,
    interval,
    wait_for_review_flag,
    all_comments,
    target,
):
    """Show the task's PR, or its Merge request and Queue builds."""
    project, _, cwd = _where(target)
    mode = _config(cwd).merge
    if mode == "github":
        _refuse_flags(mode, task=task_id, builds=every_build)
        github_cli.read_pr_command(
            target=target,
            wait=wait,
            wait_for_review_flag=wait_for_review_flag,
            all_comments=all_comments,
        )
        return
    _refuse_flags(mode, wait_for_review=wait_for_review_flag, all_comments=all_comments)
    task = _resolve_task_id(task_id)
    reply = read_merge_request(project, task)
    _print_request(reply, every_build=every_build)
    if not wait:
        return
    deadline = time.monotonic() + timeout
    while reply["status"] in _UNSETTLED:
        if time.monotonic() >= deadline:
            click.echo(f"Timed out after {timeout}s: still {reply['status']}", err=True)
            sys.exit(2)
        time.sleep(interval)
        reply = read_merge_request(project, task)
    click.echo(f"Merge request {reply['id']}: {reply['status']}")
    if reply["status"] not in ("passed", "merged"):
        sys.exit(1)


@merge.command("withdraw")
@click.option("--task", "task_id", default=None, help="Default: $MAEL_TASK_ID")
@click.option("--target", default=None, help="Project/worktree target")
async def merge_withdraw(task_id, target):
    """Withdraw the task's PR or Merge request while more work is done."""
    project, _, cwd = _where(target)
    if _config(cwd).merge == "github":
        await github_cli.withdraw_pr_command(task_id)
        return
    reply = withdraw_merge_request(project, _resolve_task_id(task_id))
    click.echo(f"Withdrew Merge request {reply['id']}")


@merge.command("retry")
@click.argument("task_id", required=False, default=None)
@click.option("--target", default=None, help="Project/worktree target")
def merge_retry(task_id, target):
    """Rebuild the task's failed Merge request at the same SHA (merge: local)."""
    project, _, cwd = _where(target)
    mode = _config(cwd).merge
    if mode != "local":
        raise click.UsageError(f"mael merge retry: not used with merge: {mode}")
    reply = retry_merge_request(project, _resolve_task_id(task_id))
    click.echo(
        f"Merge request {reply['id']}: Queue build {reply['build']} of "
        f"{reply['sha'][:7]} is {reply['status']}."
    )
