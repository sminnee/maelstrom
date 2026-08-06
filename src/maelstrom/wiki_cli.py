"""Thin CLI for the development-pattern wiki: ``mael wiki ...``.

Each command builds a :class:`~maelstrom.task_store.GitFileStore`, calls a single
model function from :mod:`maelstrom.wiki`, and renders the result.
"""

import click

from . import wiki as model
from .table import draw_table
from .task_cli import open_index
from .task_store import GitFileStore
from .util import read_content_file


def _store() -> GitFileStore:
    return GitFileStore()


def _read_content_file(content_file: str) -> str:
    """Read the ``--content-file`` argument, converting a missing path to a CLI error."""
    try:
        return read_content_file(content_file)
    except FileNotFoundError:
        raise click.ClickException(f"Content file not found: {content_file}")


def _task_index_was_fresh(store: GitFileStore) -> bool:
    """Return whether the task index is complete at the store's current HEAD.

    Called *before* a wiki write, because the answer is unknowable afterwards: the
    write moves ``store.head()``, at which point a fresh index and a stale one look
    identical. Mirrors ``task_cli._mutate_index``.

    Best-effort: a cache we cannot read is treated as not fresh, which costs a
    scan rather than wrongly promoting a stale index.
    """
    try:
        return open_index(store).head() == store.head()
    except Exception:
        return False


def _carry_task_index_stamp(store: GitFileStore, *, was_fresh: bool) -> None:
    """Move the task index's HEAD stamp past a wiki commit.

    The wiki shares one git repo with the task notebook, so a wiki write advances
    ``store.head()``. The task index tracks freshness by comparing its own stamp
    to that HEAD, so without this a wiki write would leave a complete index
    reading as stale, and every later ``mael task`` read would fall back to a full
    filesystem scan until someone ran ``mael task reindex``.

    A wiki write touches no task file, so an index that was complete before the
    write is still complete after it — only the HEAD it is stamped against moved.
    Re-stamping is sound for exactly that reason, and only when ``was_fresh``:
    a stale index must stay stale rather than be promoted by a write that never
    rebuilt it. This mirrors ``task_cli._restamp``.

    Best-effort: the wiki must not fail because the cache could not be updated.
    A missed stamp costs a scan, which is the behaviour without this call at all.
    """
    if not was_fresh:
        return
    try:
        open_index(store).set_head(store.head())
    except Exception:
        pass


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
    # Capture index freshness before the write moves the store's HEAD.
    was_fresh = _task_index_was_fresh(store)
    try:
        path = model.write_page(store, page, text)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    _carry_task_index_stamp(store, was_fresh=was_fresh)
    click.echo(f"Wrote wiki page {path}.")
