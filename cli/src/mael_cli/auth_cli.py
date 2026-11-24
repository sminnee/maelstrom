"""``mael auth``: admit, list and revoke the passkeys of the orchestrator server.

Each command carries the host key.
"""

from urllib.parse import quote

import click

from .orchestrator_client import OrchestratorClient, OrchestratorRefused
from .table_cli import draw_table


@click.group("auth")
def auth() -> None:
    """Manage the passkeys that may sign in to the orchestrator UI."""


@auth.command("verify")
@click.argument("code")
def auth_verify(code: str) -> None:
    """Admit the pending passkey whose sign-up screen shows CODE."""
    try:
        passkey = OrchestratorClient.from_config().post(
            "/api/auth/verify", {"code": code}
        )
    except OrchestratorRefused as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Verified {passkey['label']} ({passkey['id']}).")


@auth.command("list")
def auth_list() -> None:
    """List each passkey with its state and label."""
    passkeys = OrchestratorClient.from_config().get("/api/auth/passkeys")["passkeys"]
    if not passkeys:
        click.echo("No passkeys yet.")
        return
    draw_table(
        [
            {
                "ID": p["id"],
                "STATE": p["state"],
                "CODE": p.get("code", ""),
                "LABEL": p["label"],
                "CREATED": p["created"][:16],
            }
            for p in passkeys
        ],
        ["ID", "STATE", "CODE", "LABEL", "CREATED"],
    )


@auth.command("revoke")
@click.argument("passkey_id")
def auth_revoke(passkey_id: str) -> None:
    """Remove the passkey PASSKEY_ID and end each of its sign-ins."""
    try:
        OrchestratorClient.from_config().delete(
            f"/api/auth/passkeys/{quote(passkey_id, safe='')}"
        )
    except OrchestratorRefused as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Revoked {passkey_id}.")
