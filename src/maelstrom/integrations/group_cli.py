"""The base class of every integration group (see ``docs/dev/architecture-patterns.md`` §3)."""

from typing import Any

import click

from mael_common.cli_async import AsyncGroup

from .errors import IntegrationError


class IntegrationGroup(AsyncGroup):
    """An ``AsyncGroup`` that reports an ``IntegrationError`` as a CLI error."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except IntegrationError as e:
            raise click.ClickException(str(e)) from e
