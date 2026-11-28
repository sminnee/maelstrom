"""``mael secret``: the **Secret store** of the orchestrator server.

Each command is a client of the server, which alone opens the store. No
command prints a value, and none takes one as an argument: an argument shows
in the process table and in the shell history.
"""

import sys
from urllib.parse import quote, urlencode

import click

from mael_domain.context import resolve_project

from .orchestrator_client import OrchestratorClient
from .table_cli import draw_table


@click.group("secret")
def secret() -> None:
    """Manage the secrets that dev environments use."""


def _read_hidden(label: str, *, confirm: bool = False) -> str:
    """One secret text: all of piped stdin less one final newline, else a hidden prompt."""
    if sys.stdin.isatty():
        return click.prompt(label, hide_input=True, confirmation_prompt=confirm)
    return sys.stdin.read().removesuffix("\n").removesuffix("\r")


def _scope(project: str | None, is_global: bool) -> str | None:
    """The project a command names, or ``None`` for the global scope."""
    if is_global:
        if project:
            raise click.UsageError("Give --project or --global, not both.")
        return None
    try:
        return resolve_project(project)
    except ValueError as exc:
        raise click.ClickException(
            f"{exc} Give --project, or --global for every project."
        ) from exc


def _label(project: str | None) -> str:
    return project if project is not None else "global"


@secret.command("unlock")
def secret_unlock() -> None:
    """Unlock the Secret store until the orchestrator server stops.

    The first run creates the store and sets its passphrase. The passphrase
    comes from a prompt, or from stdin when it is piped.
    """
    client = OrchestratorClient.from_config()
    create = not client.get("/api/secret-store")["exists"]
    label = "New passphrase for the Secret store" if create else "Passphrase"
    passphrase = _read_hidden(label, confirm=create)
    client.post(
        "/api/secret-store/unlock", {"passphrase": passphrase, "create": create}
    )
    click.echo("Created the Secret store." if create else "Unlocked the Secret store.")


@secret.command("set")
@click.argument("name")
@click.option("-p", "--project", default=None, help="Project (default: the cwd's).")
@click.option("--global", "is_global", is_flag=True, help="For every project.")
def secret_set(name: str, project: str | None, is_global: bool) -> None:
    """Store the Secret NAME. The value comes from a prompt, or from stdin.

    A second `set` replaces the value.
    """
    scope = _scope(project, is_global)
    value = _read_hidden(f"Value of {name}")
    OrchestratorClient.from_config().put(
        f"/api/secrets/{quote(name, safe='')}", {"value": value, "project": scope}
    )
    click.echo(f"Set {name} ({_label(scope)}).")


@secret.command("list")
def secret_list() -> None:
    """List each Secret's name, scope and set date. Never a value."""
    reply = OrchestratorClient.from_config().get("/api/secrets")
    if reply["locked"]:
        click.echo("The Secret store is locked.", err=True)
    if not reply["secrets"]:
        click.echo("No secrets yet.")
        return
    draw_table(
        [
            {"NAME": s["name"], "SCOPE": _label(s["project"]), "SET": s["setAt"][:16]}
            for s in reply["secrets"]
        ],
        ["NAME", "SCOPE", "SET"],
    )


@secret.command("rm")
@click.argument("name")
@click.option("-p", "--project", default=None, help="Project (default: the cwd's).")
@click.option("--global", "is_global", is_flag=True, help="For every project.")
def secret_rm(name: str, project: str | None, is_global: bool) -> None:
    """Remove the Secret NAME."""
    scope = _scope(project, is_global)
    query = f"?{urlencode({'project': scope})}" if scope is not None else ""
    OrchestratorClient.from_config().delete(
        f"/api/secrets/{quote(name, safe='')}{query}"
    )
    click.echo(f"Removed {name} ({_label(scope)}).")
