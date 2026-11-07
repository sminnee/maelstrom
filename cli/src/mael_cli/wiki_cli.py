"""Thin CLI for the development-pattern wiki: ``mael wiki ...``.

Each command opens the state database, calls a single model function from
:mod:`mael_domain.wiki`, and renders the result.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import click

from mael_common.cli_async import AsyncGroup
from mael_common.util import read_content_file
from mael_domain import wiki as model
from mael_domain.state_db.migrate import open_state_db
from mael_domain.state_db.paths import get_state_db_path
from mael_domain.state_db.types import StateDbError

from .orchestrator_client import refuse_with_parent
from .table_cli import draw_table


@asynccontextmanager
async def _table() -> AsyncGenerator[model.WikiTable]:
    """The wiki table in the state database, checked against this build's schema."""
    path = get_state_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = open_state_db(path)
    try:
        try:
            await db.check()
        except StateDbError as exc:
            raise click.ClickException(str(exc)) from exc
        yield model.SqliteWikiTable(db)
    finally:
        db.close()


def _read_content_file(content_file: str) -> str:
    """Read the ``--content-file`` argument, converting a missing path to a CLI error."""
    try:
        return read_content_file(content_file)
    except FileNotFoundError:
        raise click.ClickException(f"Content file not found: {content_file}")


@click.group("wiki", cls=AsyncGroup)
@click.pass_context
def wiki(ctx: click.Context) -> None:
    """Read and update the cross-project development-pattern wiki."""
    refuse_with_parent(f"wiki {ctx.invoked_subcommand}")


@wiki.command("list")
async def wiki_list() -> None:
    """Print the table of contents: every page path and its description."""
    async with _table() as table:
        pages = await model.list_pages(table)
    if not pages:
        click.echo("No wiki pages yet.")
        return
    draw_table(
        [{"PAGE": p.path, "DESCRIPTION": p.description} for p in pages],
        ["PAGE", "DESCRIPTION"],
    )


@wiki.command("read")
@click.argument("page")
async def wiki_read(page: str) -> None:
    """Print the raw content of PAGE."""
    async with _table() as table:
        try:
            text = await model.read_page(table, page)
        except KeyError:
            raise click.ClickException(f"Wiki page not found: {page}")
        except ValueError as exc:
            raise click.ClickException(str(exc))
    click.echo(text, nl=False)


@wiki.command("update")
@click.argument("page")
@click.option(
    "--content-file",
    required=True,
    help="File holding the whole page body ('-' reads stdin).",
)
async def wiki_update(page: str, content_file: str) -> None:
    """Create or replace PAGE with the given content.

    The content replaces the whole page — there is no partial edit. Read the page
    first, then write back the full body.
    """
    text = _read_content_file(content_file)
    async with _table() as table:
        try:
            path = await model.write_page(table, page, text)
        except ValueError as exc:
            raise click.ClickException(str(exc))
    click.echo(f"Wrote wiki page {path}.")
