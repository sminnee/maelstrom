"""The **Orchestrator client**: how a ``mael`` command calls the orchestrator server.

The URL is ``orchestrator_url:`` in the notebook root's ``config.yaml``. There
is no fallback to the database; ``docs/dev/cli-over-api.md`` gives the reason
and the plan.

A delegated agent's task is on the parent orchestrator, which ``MAEL_PARENT_URL``
names. Task commands call it with :meth:`OrchestratorClient.for_tasks`.
"""

import http.client
import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

import click

from mael_agent.host_ids import PARENT_URL_ENV
from mael_domain.context import load_global_config
from mael_domain.notebook_root import NOTEBOOK_ROOT_ENV

log = logging.getLogger(__name__)

#: How long a call waits. Long enough for a server that has just started to
#: finish its first reads, which it does before it answers.
TIMEOUT_SECS = 30.0

#: How long :func:`tell_orchestrator` waits. Short: the caller has finished its
#: work and holds the user's terminal open only for this.
NOTIFY_TIMEOUT_SECS = 2.0

#: TLS without verification. The server is on this machine and its dev
#: certificate names the dev host, and a uv-built Python may have no CA store.
#: Plain ``http://`` URLs ignore it.
_LOCAL_TLS = ssl.create_default_context()
_LOCAL_TLS.check_hostname = False
_LOCAL_TLS.verify_mode = ssl.CERT_NONE


def parent_url() -> str:
    """The parent orchestrator's URL from ``MAEL_PARENT_URL``, or "" when unset."""
    return os.environ.get(PARENT_URL_ENV, "").strip()


def refuse_with_parent(command: str) -> None:
    """Refuse ``mael <command>`` in a delegated agent: it uses the local state database.

    The agent's task is on the parent, so the local database is the wrong one.
    """
    url = parent_url()
    if url:
        raise click.ClickException(
            f"`mael {command}` uses the local state database, "
            f"and this agent's task is on {url}."
        )


class OrchestratorError(click.ClickException):
    """The server could not answer a call. Click prints the message and exits 1."""


class OrchestratorUnconfigured(OrchestratorError):
    def __init__(self) -> None:
        root = os.environ.get(NOTEBOOK_ROOT_ENV) or "$" + NOTEBOOK_ROOT_ENV
        super().__init__(
            f"No orchestrator_url in {root}/config.yaml. Set it to the URL "
            "of this notebook's orchestrator server."
        )


class OrchestratorUnreachable(OrchestratorError):
    def __init__(self, url: str, reason: object) -> None:
        super().__init__(
            f"No orchestrator answered at {url} ({reason}). "
            "Start it with `mael self restart orchestrator`."
        )
        self.url = url


class OrchestratorRefused(OrchestratorError):
    """The server answered ``{"error": {code, message}}``."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class OrchestratorClient:
    """JSON calls to one orchestrator server."""

    def __init__(self, base_url: str, *, timeout: float = TIMEOUT_SECS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_config(cls, *, timeout: float = TIMEOUT_SECS) -> "OrchestratorClient":
        """The client for ``orchestrator_url:``. Raises when the key is unset."""
        url = load_global_config().orchestrator_url
        if not url:
            raise OrchestratorUnconfigured()
        return cls(url, timeout=timeout)

    @classmethod
    def for_tasks(cls, *, timeout: float = TIMEOUT_SECS) -> "OrchestratorClient":
        """The client for task state: the parent's when ``MAEL_PARENT_URL`` is set."""
        url = parent_url()
        if url:
            return cls(url, timeout=timeout)
        return cls.from_config(timeout=timeout)

    def get(self, path: str) -> Any:
        return self._call("GET", path, None)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._call("POST", path, body or {})

    def put(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._call("PUT", path, body or {})

    def delete(self, path: str) -> Any:
        return self._call("DELETE", path, None)

    def _call(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        url = f"{self.base_url}{path}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout, context=_LOCAL_TLS
            ) as response:
                status, reply = response.status, response.read()
        except urllib.error.HTTPError as exc:
            raise _refusal(exc) from exc
        except urllib.error.URLError as exc:
            raise OrchestratorUnreachable(self.base_url, exc.reason) from exc
        # A dropped connection raises from ``http.client``, which is not an
        # ``OSError``; a socket timeout is an ``OSError`` but not a ``URLError``.
        except (OSError, http.client.HTTPException) as exc:
            raise OrchestratorUnreachable(self.base_url, exc) from exc
        try:
            return json.loads(reply or b"null")
        except ValueError as exc:
            raise OrchestratorRefused(
                "invalid", f"The server at {self.base_url} did not answer JSON.", status
            ) from exc


def _refusal(exc: urllib.error.HTTPError) -> OrchestratorRefused:
    """The server's ``{"error": …}`` reply, or the bare status when it sent none."""
    try:
        error = json.loads(exc.read())["error"]
        return OrchestratorRefused(str(error["code"]), str(error["message"]), exc.code)
    except (ValueError, KeyError, TypeError):
        return OrchestratorRefused("invalid", f"HTTP {exc.code}", exc.code)


def tell_orchestrator(path: str, body: dict[str, Any] | None = None) -> None:
    """POST ``body`` to ``path`` on the configured orchestrator, and ignore the answer.

    Raises nothing. Every caller has done its real work already, and a server
    that is not configured or not listening must not turn that into a failure.
    The miss is logged at debug: a plain CLI user often runs no server.
    """
    try:
        OrchestratorClient.from_config(timeout=NOTIFY_TIMEOUT_SECS).post(path, body)
    except (OrchestratorError, ValueError) as exc:
        log.debug("no orchestrator was told %s: %s", path, exc)


#: The route that asks an orchestrator to re-read its worktrees at once.
REFRESH_PATH = "/api/worktrees/refresh"


def tell_pr_changed(project: str | None, branch: str | None) -> None:
    """Tell the orchestrator that ``branch``'s pull request changed on GitHub.

    It reads that pull request again, then the worktrees. Without a project
    and a branch, it reads the worktrees only. Raises nothing; see
    :func:`tell_orchestrator`.
    """
    body = {"project": project, "branch": branch} if project and branch else None
    tell_orchestrator(REFRESH_PATH, body)


def _merge_request_path(project: str, task_id: str) -> str:
    return (
        f"/api/tasks/{quote(project, safe='')}/{quote(task_id, safe='')}/merge-request"
    )


def submit_merge_request(
    project: str, task_id: str, branch: str, sha: str, base: str
) -> dict[str, Any]:
    """Submit ``sha`` to the task's Merge request; the request as it now stands.

    On the parent's server for a delegated agent: the task is there.
    """
    return OrchestratorClient.for_tasks().put(
        _merge_request_path(project, task_id),
        {"branch": branch, "sha": sha, "base": base},
    )


def read_merge_request(project: str, task_id: str) -> dict[str, Any]:
    """The task's current Merge request, its status and its Builds, newest first."""
    return OrchestratorClient.for_tasks().get(_merge_request_path(project, task_id))


def withdraw_merge_request(project: str, task_id: str) -> dict[str, Any]:
    """Withdraw the task's open Merge request."""
    return OrchestratorClient.for_tasks().delete(_merge_request_path(project, task_id))
