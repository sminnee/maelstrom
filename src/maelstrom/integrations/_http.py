"""Shared urllib JSON-request wrapper for service integrations.

Covers the three request shapes the integrations need — a JSON body, a
form-encoded body, and query params — behind one function. On an HTTP error it
raises ``click.ClickException`` with the exact ``HTTP Error <code>: <body>``
message the integrations used before, so observable behavior is unchanged.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import click


def _read_bytes(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    json_body: Any,
    form_body: dict | None,
    params: dict | None,
) -> bytes:
    """Build and send the request, returning the raw response body as bytes.

    The shared request-building core. Exactly one of ``json_body`` / ``form_body``
    should be supplied (or neither for a bodyless request). ``json_body`` sets
    ``Content-Type: application/json`` automatically; ``form_body`` is urlencoded
    and the caller is responsible for the form content-type header. ``params`` is
    urlencoded onto the URL.

    Raises:
        click.ClickException: On an HTTP error, with the response body inlined.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    request_headers: dict[str, str] = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode("utf-8")

    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)

    try:
        with urllib.request.urlopen(req) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise click.ClickException(f"HTTP Error {e.code}: {error_body}")


def _read_response(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    json_body: Any,
    form_body: dict | None,
    params: dict | None,
) -> str:
    """Build and send the request, returning the raw response body as text.

    Shared by :func:`request_json` and :func:`request_text` — they differ only
    in how they decode this body. See :func:`_read_bytes` for the body/params
    semantics.

    Raises:
        click.ClickException: On an HTTP error, with the response body inlined.
    """
    return _read_bytes(
        url,
        method=method,
        headers=headers,
        json_body=json_body,
        form_body=form_body,
        params=params,
    ).decode("utf-8")


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    form_body: dict | None = None,
    params: dict | None = None,
) -> Any:
    """Make an HTTP request and return the decoded JSON response.

    See :func:`_read_response` for the body/params semantics.

    Raises:
        click.ClickException: On an HTTP error, with the response body inlined.
    """
    body = _read_response(
        url,
        method=method,
        headers=headers,
        json_body=json_body,
        form_body=form_body,
        params=params,
    )
    return json.loads(body)


def request_text(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    form_body: dict | None = None,
    params: dict | None = None,
) -> str:
    """Make an HTTP request and return the raw response body as text.

    Like :func:`request_json` but skips JSON decoding — for endpoints that return
    a non-JSON body on success (e.g. Slack incoming webhooks reply with the
    literal string ``ok``).

    Raises:
        click.ClickException: On an HTTP error, with the response body inlined.
    """
    return _read_response(
        url,
        method=method,
        headers=headers,
        json_body=json_body,
        form_body=form_body,
        params=params,
    )


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> bytes:
    """Make an HTTP request and return the raw response body as bytes.

    Like :func:`request_text` but skips the UTF-8 decode — for binary assets
    (e.g. downloading an image) that must not be mangled into text.

    Raises:
        click.ClickException: On an HTTP error, with the response body inlined.
    """
    return _read_bytes(
        url,
        method=method,
        headers=headers,
        json_body=None,
        form_body=None,
        params=None,
    )
