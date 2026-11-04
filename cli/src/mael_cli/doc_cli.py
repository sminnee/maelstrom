"""``mael doc``: add, list, show, link and archive Task documents.

A thin client over the orchestrator server. See ``CONTEXT.md``, "Task document",
and ``docs/adr/0001-task-documents.md``. A task argument defaults to
``$MAEL_TASK_ID``, and ``--project`` to the cwd's project.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import click

from mael_domain.context import resolve_project
from mael_domain.task_documents import TaskDocumentError, parse_ref

from .orchestrator_client import OrchestratorClient, OrchestratorRefused

_SARIF_SUFFIXES = (".sarif", ".sarif.json")

_project_option = click.option(
    "-p", "--project", help="The project. Defaults to the cwd's project."
)


def _project(project: str | None) -> str:
    try:
        return resolve_project(project)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _session_project(project: str | None) -> str:
    """The project of the session's task: the cwd's, else ``--project``.

    The cwd wins because ``--project`` may name the document's project, not the
    session's.
    """
    try:
        return resolve_project(None)
    except ValueError:
        return _project(project)


def _task(task_id: str | None) -> str:
    found = task_id or os.environ.get("MAEL_TASK_ID")
    if not found:
        raise click.ClickException("No task id given and MAEL_TASK_ID is not set.")
    return found


def _ref(text: str, project: str | None) -> str:
    """``text`` as the canonical URI, its blanks filled from the cwd and the task."""
    try:
        parsed = parse_ref(
            text,
            project=lambda: _project(project),
            task_id=os.environ.get("MAEL_TASK_ID"),
        )
    except TaskDocumentError as exc:
        raise click.ClickException(str(exc)) from exc
    return str(parsed)


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    client = OrchestratorClient.from_config()
    try:
        return client.post(path, body) if method == "POST" else client.get(path)
    except OrchestratorRefused as exc:
        raise click.ClickException(exc.message) from exc


def _task_path(project: str, task_id: str) -> str:
    return f"/api/tasks/{quote(project, safe='')}/{quote(task_id, safe='')}/documents"


def _default_name(path: Path) -> str:
    """``path`` relative to its worktree root, or its basename outside one."""
    found = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode == 0:
        root = Path(found.stdout.strip()).resolve()
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return path.name


def _default_title(kind: str, body: str, name: str) -> str:
    """A markdown file's first heading, else the name."""
    heading = re.search(r"^#{1,6}\s+(.+?)\s*#*\s*$", body, re.MULTILINE)
    return heading[1] if kind == "markdown" and heading else name


@click.group("doc")
def doc() -> None:
    """Task documents: files a task owns, versioned, that other tasks can link."""


@doc.command("add")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", help="The document name. Defaults to the worktree path.")
@click.option(
    "--kind",
    type=click.Choice(["markdown", "sarif"]),
    help="Defaults from the suffix: .sarif and .sarif.json are sarif.",
)
@click.option("--title", help="Defaults to the first heading, else the name.")
@click.option("--task", "task_id", help="The owning task. Defaults to $MAEL_TASK_ID.")
@_project_option
def add(
    path: Path,
    name: str | None,
    kind: str | None,
    title: str | None,
    task_id: str | None,
    project: str | None,
) -> None:
    """Add PATH as the next version of a document of the task."""
    path = path.resolve()
    body = path.read_text()
    kind = kind or ("sarif" if path.name.endswith(_SARIF_SUFFIXES) else "markdown")
    name = name or _default_name(path)
    reply = _call(
        "POST",
        _task_path(_project(project), _task(task_id)),
        {
            "name": name,
            "kind": kind,
            "title": title or _default_title(kind, body, name),
            "body": body,
        },
    )
    click.echo(reply["uri"])


@doc.command("list")
@click.argument("task_id", required=False)
@click.option("--archived", is_flag=True, help="Include archived documents.")
@_project_option
def list_documents(task_id: str | None, archived: bool, project: str | None) -> None:
    """The documents TASK owns, then those it links."""
    reply = _call("GET", _task_path(_project(project), _task(task_id)))
    for heading in ("owned", "linked"):
        shown = [d for d in reply[heading] if archived or not d["archived"]]
        if not shown:
            continue
        click.echo(f"{heading.capitalize()}:")
        for found in shown:
            note = "  (archived)" if found["archived"] else ""
            click.echo(
                f"  {found['uri']}@v{found['version']}  {found['kind']}  "
                f"{found['title']}{note}"
            )


@doc.command("show")
@click.argument("ref")
@click.option("--body", "body_only", is_flag=True, help="Print only the body.")
@_project_option
def show(ref: str, body_only: bool, project: str | None) -> None:
    """A document: its versions and the tasks that link it, by fragment."""
    reply = _call("GET", f"/api/task-documents?ref={quote(_ref(ref, project))}")
    if body_only:
        click.echo(reply["version"]["body"], nl=False)
        return
    found = reply["document"]
    click.echo(f"uri:      {found['uri']}")
    click.echo(f"title:    {found['title']}")
    click.echo(f"kind:     {found['kind']}")
    click.echo(f"owner:    {found['taskId']}")
    click.echo(f"archived: {'yes' if found['archived'] else 'no'}")
    click.echo("\nVersions:")
    for version in reply["versions"]:
        click.echo(
            f"  v{version['version']}  {version['createdAt']}  "
            f"{version['reviewStatus']}  {version['title']}"
        )
    if reply["findings"]:
        click.echo("\nFindings:")
        for finding in reply["findings"]:
            where = finding["file"] + (f":{finding['line']}" if finding["line"] else "")
            click.echo(
                f"  {finding['label']}  {finding['level']}  {finding['title']}  {where}\n"
                f"    #{finding['key']}"
            )
    if reply["links"]:
        click.echo("\nLinked by:")
        for fragment, tasks in reply["links"].items():
            click.echo(
                f"  {'#' + fragment if fragment else '(whole)'}: {', '.join(tasks)}"
            )


def _link(action: str, ref: str, task_id: str | None, project: str | None) -> None:
    task = f"{_project(project)}/{_task(task_id)}"
    _call(
        "POST",
        f"/api/task-documents/{action}",
        {"ref": _ref(ref, project), "taskId": task},
    )


@doc.command("link")
@click.argument("ref")
@click.argument("task_id", required=False)
@_project_option
def link(ref: str, task_id: str | None, project: str | None) -> None:
    """Link TASK to the document, or to the fragment REF names."""
    _link("link", ref, task_id, project)


@doc.command("unlink")
@click.argument("ref")
@click.argument("task_id", required=False)
@_project_option
def unlink(ref: str, task_id: str | None, project: str | None) -> None:
    """Remove TASK's link to the document, or to the fragment REF names."""
    _link("unlink", ref, task_id, project)


def _archive(action: str, ref: str, project: str | None) -> None:
    body: dict[str, Any] = {"ref": _ref(ref, project)}
    if by := os.environ.get("MAEL_TASK_ID"):
        body["by"] = f"{_session_project(project)}/{by}"
    _call("POST", f"/api/task-documents/{action}", body)


@doc.command("archive")
@click.argument("ref")
@_project_option
def archive(ref: str, project: str | None) -> None:
    """Archive the document. Inside a task session, only its owner may."""
    _archive("archive", ref, project)


@doc.command("unarchive")
@click.argument("ref")
@_project_option
def unarchive(ref: str, project: str | None) -> None:
    """Unarchive the document. Inside a task session, only its owner may."""
    _archive("unarchive", ref, project)
