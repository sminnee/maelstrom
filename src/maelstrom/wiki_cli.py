"""Thin CLI for the development-pattern wiki: ``mael wiki ...``.

Each command builds a :class:`~maelstrom.task_store.GitFileStore`, calls a single
model function from :mod:`maelstrom.wiki`, and renders the result.
"""

import click

from mael_common.util import read_content_file

from . import wiki as model
from .table_cli import draw_table
from .task_store import GitFileStore


def _store() -> GitFileStore:
    return GitFileStore()


def _read_content_file(content_file: str) -> str:
    """Read the ``--content-file`` argument, converting a missing path to a CLI error."""
    try:
        return read_content_file(content_file)
    except FileNotFoundError:
        raise click.ClickException(f"Content file not found: {content_file}")


@click.group("wiki")
def wiki() -> None:
    """Read and update the cross-project development-pattern wiki."""


@wiki.command("list")
def wiki_list() -> None:
    """Print the table of contents: every page path and its description."""
    pages = model.list_pages(_store())
    if not pages:
        click.echo("No wiki pages yet.")
        return
    draw_table(
        [{"PAGE": p.path, "DESCRIPTION": p.description} for p in pages],
        ["PAGE", "DESCRIPTION"],
    )


@wiki.command("read")
@click.argument("page")
def wiki_read(page: str) -> None:
    """Print the raw content of PAGE."""
    try:
        text = model.read_page(_store(), page)
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
def wiki_update(page: str, content_file: str) -> None:
    """Create or replace PAGE with the given content, and commit it.

    The content replaces the whole page — there is no partial edit. Read the page
    first, then write back the full body.
    """
    text = _read_content_file(content_file)
    store = _store()
    try:
        path = model.write_page(store, page, text)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"Wrote wiki page {path}.")
