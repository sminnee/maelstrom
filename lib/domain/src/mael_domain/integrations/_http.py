"""Shared urllib JSON-request wrapper for service integrations.

Covers the three request shapes the integrations need — a JSON body, a
form-encoded body, and query params — behind one function. On an HTTP error it
raises ``IntegrationHTTPError``, whose message is ``HTTP Error <code>: <body>``.
"""

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .errors import IntegrationHTTPError


def _read_bytes(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    json_body: Any,
    form_body: dict | None,
    params: dict | None,
    timeout: float | None = None,
    raw_body: bytes | None = None,
) -> bytes:
    """Build and send the request, returning the raw response body as bytes.

    The shared request-building core. Exactly one of ``json_body`` / ``form_body``
    should be supplied (or neither for a bodyless request). ``json_body`` sets
    ``Content-Type: application/json`` automatically; ``form_body`` is urlencoded
    and the caller is responsible for the form content-type header. ``raw_body``
    is sent as it is, under the caller's content-type header. ``params`` is
    urlencoded onto the URL. ``timeout`` bounds the wait in seconds; ``None``
    waits as long as the socket default allows.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
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
    elif raw_body is not None:
        data = raw_body

    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        raise IntegrationHTTPError(e.code, error_body) from e


def _read_response(
    url: str,
    *,
    method: str,
    headers: dict[str, str] | None,
    json_body: Any,
    form_body: dict | None,
    params: dict | None,
    timeout: float | None = None,
) -> str:
    """Build and send the request, returning the raw response body as text.

    Shared by :func:`request_json` and :func:`request_text` — they differ only
    in how they decode this body. See :func:`_read_bytes` for the body/params
    semantics.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
    """
    return _read_bytes(
        url,
        method=method,
        headers=headers,
        json_body=json_body,
        form_body=form_body,
        params=params,
        timeout=timeout,
    ).decode("utf-8")


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    form_body: dict | None = None,
    params: dict | None = None,
    timeout: float | None = None,
) -> Any:
    """Make an HTTP request and return the decoded JSON response.

    See :func:`_read_response` for the body/params semantics.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
    """
    body = _read_response(
        url,
        method=method,
        headers=headers,
        json_body=json_body,
        form_body=form_body,
        params=params,
        timeout=timeout,
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
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
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
    json_body: Any = None,
    raw_body: bytes | None = None,
    timeout: float | None = None,
) -> bytes:
    """Make an HTTP request and return the raw response body as bytes.

    Like :func:`request_text` but skips the UTF-8 decode — for binary assets
    (e.g. downloading an image, or synthesized speech) that must not be mangled
    into text. ``raw_body`` carries a body already encoded, such as the output
    of :func:`multipart_body`.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
    """
    return _read_bytes(
        url,
        method=method,
        headers=headers,
        json_body=json_body,
        form_body=None,
        params=None,
        timeout=timeout,
        raw_body=raw_body,
    )


def multipart_body(
    fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
) -> tuple[bytes, str]:
    """A ``multipart/form-data`` body and its content-type header value.

    ``files`` maps each field name to ``(filename, data, content_type)``.

    Raises:
        ValueError: A name holds ``"``, CR or LF, which would break the part header.
    """
    for name in [*fields, *files, *(f[0] for f in files.values())]:
        if any(c in name for c in '"\r\n'):
            raise ValueError(f"multipart name holds a quote or a line break: {name!r}")
    boundary = f"mael-{secrets.token_hex(16)}"
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode("utf-8") + b"\r\n"
    for name, (filename, data, content_type) in files.items():
        out += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
