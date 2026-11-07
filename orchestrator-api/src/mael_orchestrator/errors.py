"""The one error shape every route answers with, and the body every command reads.

Its own module so that each route module shares it, not only :mod:`.routes`.
"""

import json
from typing import Any

from aiohttp import web

#: The HTTP status for each error code the server answers with.
STATUS_FOR_CODE = {
    "unknown_id": 404,
    "invalid": 400,
    "agent_exited": 409,
    "not_waiting": 409,
    "stale_request": 409,
    "wrong_wait_kind": 409,
    "stale_version": 409,
    "cannot_update": 409,
    "not_implemented": 501,
    "language_server_failed": 502,
}


def error_response(code: str, message: str, **extra: Any) -> web.Response:
    """The one error shape: ``{"error": {"code", "message"}}`` at the code's status.

    ``extra`` adds fields beside those two, for the refusal that has to say
    more than why: a create whose launch failed names the task it wrote, so a
    retry does not write it twice.
    """
    return web.json_response(
        {"error": {"code": code, "message": message, **extra}},
        status=STATUS_FOR_CODE.get(code, 400),
    )


class BadBody(Exception):
    """A request body that is not a JSON object."""


async def read_body(request: web.Request) -> dict[str, Any]:
    """The JSON object a command carries; an empty body is an empty object."""
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise BadBody("Body is not JSON") from exc
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise BadBody("Body must be a JSON object")
    return body
