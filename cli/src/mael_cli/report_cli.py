"""``mael report ...``: the CLI over :mod:`mael_domain.reports`."""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import click

from mael_common.cli_async import AsyncGroup
from mael_domain import reports
from mael_domain import task as task_model
from mael_domain.context import resolve_project
from mael_domain.task import Task
from mael_domain.task_store import GitFileStore

from .table_cli import draw_table
from .task_cli import _table

_PROJECT = click.option(
    "-p", "--project", default=None, help="Project name (default: from cwd)."
)


def _store() -> GitFileStore:
    return GitFileStore()


@click.group("report", cls=AsyncGroup)
def report() -> None:
    """Keep scan output as dated reports, and read their findings."""


@report.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@_PROJECT
def report_import(path: Path, project: str | None) -> None:
    """Copy a SARIF file, or a directory that holds one, into a new report."""
    proj = resolve_project(project)
    try:
        click.echo(reports.import_report(_store(), proj, path, now=datetime.now(UTC)))
    except ValueError as e:
        raise click.ClickException(str(e))


@report.command("list")
@_PROJECT
def report_list(project: str | None) -> None:
    """List the project's reports, oldest first."""
    found = reports.list_reports(_store(), resolve_project(project))
    if not found:
        click.echo("No reports yet.")
        return
    draw_table(
        [{"report": r.name, "files": ", ".join(r.files)} for r in found],
        ["report", "files"],
    )


@report.command("show")
@click.argument("name")
@click.argument("finding", required=False, default=None)
@_PROJECT
async def report_show(name: str, finding: str | None, project: str | None) -> None:
    """List a report's findings, or show one finding by label or key."""
    proj = resolve_project(project)
    try:
        findings = reports.read_findings(_store(), proj, name)
    except ValueError as e:
        raise click.ClickException(str(e))
    by_ref = reports.tasks_by_finding(
        await task_model.list_tasks(await _table(), project=proj)
    )
    if finding is None:
        _echo_findings(findings, by_ref)
        return
    found = reports.find_finding(findings, finding)
    if found is None:
        raise click.ClickException(f"No finding {finding} in report {name}")
    _echo_finding(found, by_ref.get(found.ref, []))


def _location(f: reports.Finding) -> str:
    return f"{f.file}:{f.line}" if f.line is not None else f.file


def _echo_findings(
    findings: list[reports.Finding], by_ref: dict[str, list[Task]]
) -> None:
    counts = Counter(f.level for f in findings)
    summary = ", ".join(f"{n} {level}" for level, n in counts.items())
    click.echo(f"{len(findings)} findings: {summary}")
    draw_table(
        [
            {
                "id": f.label,
                "level": f.level,
                "cwe": f.cwe,
                "location": _location(f),
                "title": f.title,
                "tasks": ", ".join(t.id for t in by_ref.get(f.ref, [])),
            }
            for f in findings
        ],
        ["id", "level", "cwe", "location", "title", "tasks"],
    )


def _echo_finding(f: reports.Finding, tasks: list[Task]) -> None:
    click.echo(f"{f.label} — {f.title}")
    click.echo(f"level:    {f.level}")
    click.echo(f"rule:     {f.rule}")
    if f.cwe:
        click.echo(f"cwe:      {f.cwe}")
    click.echo(f"location: {_location(f)}")
    if f.symbol:
        click.echo(f"symbol:   {f.symbol}")
    click.echo(f"ref:      {f.ref}")
    if f.snippet:
        click.echo(f"\n    {f.snippet.strip()}")
    for section, text in f.detail.items():
        if section != "message":
            click.echo(f"\n## {section.capitalize()}\n\n{text}")
    click.echo("\n## Referenced by\n")
    for t in tasks:
        click.echo(f"{t.id}  {t.title}")
    if not tasks:
        click.echo("No task.")
