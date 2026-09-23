"""The errors the service integrations raise.

``group_cli.IntegrationGroup`` reports them (see ``docs/dev/architecture-patterns.md`` §3).
"""


class IntegrationError(RuntimeError):
    """Base for every error a service integration raises."""


class IntegrationHTTPError(IntegrationError):
    """The service answered a request with an HTTP error status."""

    def __init__(self, code: int, body: str) -> None:
        self.code = code
        self.body = body
        super().__init__(code, body)

    def __str__(self) -> str:
        return f"HTTP Error {self.code}: {self.body}"
