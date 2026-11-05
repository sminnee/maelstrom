"""One language server process, spoken to over stdio LSP.

The storage layer under :mod:`mael_orchestrator.language_servers`, over
``Content-Length`` JSON-RPC. See ``docs/dev/code-navigation.md``, "The
language server".
"""

import asyncio
import json
import logging
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from mael_common.shell import kill_process_group

log = logging.getLogger(__name__)

#: Bytes of stderr kept for an error message.
STDERR_TAIL_BYTES = 4096
#: How long a stop waits for ``shutdown`` and the exit before it kills the group.
STOP_SECS = 2.0


class LspError(RuntimeError):
    """The server did not start, did not answer, exited, or answered with an error."""


class LspClient:
    """One server process: start it, send it requests, stop it.

    A process that exits fails its pending requests. A later :meth:`start`
    spawns a new one, as the Codex bridge does.
    """

    def __init__(self, command: list[str], root: Path) -> None:
        self.command = command
        self.root = root
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stderr_reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._stderr = bytearray()
        self._write_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    def stderr_tail(self) -> str:
        return self._stderr.decode("utf-8", "replace").strip()

    async def start(self, timeout: float) -> None:
        """Spawn the server and run the ``initialize`` handshake."""
        await self.stop()
        self._stderr.clear()
        try:
            # Its own session, so a stop kills the node runtime under the wrapper too.
            self._process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=self.root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise LspError(f"{self.command[0]} did not start: {exc}") from exc
        self._stderr_reader = asyncio.create_task(self._read_stderr(self._process))
        self._reader = asyncio.create_task(self._read(self._process))
        try:
            await self._initialize(timeout)
        except BaseException:
            await self.stop()
            raise

    async def _initialize(self, timeout: float) -> None:
        uri = self.root.as_uri()
        await self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": uri,
                "workspaceFolders": [{"uri": uri, "name": self.root.name}],
                # Only definitions. No didChangeWatchedFiles: the server
                # watches the disk itself.
                "capabilities": {"textDocument": {"definition": {"linkSupport": True}}},
            },
            timeout,
        )
        await self.notify("initialized", {})

    async def request(self, method: str, params: Any, timeout: float) -> Any:
        """Send one request and wait for its result. Raises :class:`LspError`."""
        if not self.running:
            raise LspError(f"{self.command[0]} is not running")
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise LspError(
                f"{self.command[0]} did not answer {method} in {timeout:g}s"
            ) from exc
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Any) -> None:
        await self._send({"method": method, "params": params})

    async def stop(self) -> None:
        """Ask the server to shut down, then kill its process group."""
        process = self._process
        if process is not None and process.returncode is None:
            with suppress(LspError, OSError, ConnectionError):
                await self.request("shutdown", None, STOP_SECS)
                await self.notify("exit", None)
                await asyncio.wait_for(process.wait(), STOP_SECS)
            if process.returncode is None:
                kill_process_group(process)
                await process.wait()
        if process is not None:
            # The wrapper may be gone while the node runtime under it lives on.
            kill_process_group(process)
        tasks = [t for t in (self._reader, self._stderr_reader) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._reader = self._stderr_reader = None
        self._process = None

    async def _send(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise LspError(f"{self.command[0]} is not running")
        body = json.dumps({"jsonrpc": "2.0", **message}).encode()
        async with self._write_lock:
            try:
                process.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
                await process.stdin.drain()
            except (ConnectionError, OSError) as exc:
                raise LspError(f"{self.command[0]} closed its input") from exc

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while True:
                message = await _read_frame(process.stdout)
                if message is None:
                    break
                await self._dispatch(message)
        except Exception as exc:
            # Nothing reads its output now, so a live server would wedge.
            log.warning("language server sent a bad message: %r", exc)
            kill_process_group(process)
        finally:
            await process.wait()
            # The last stderr lines say why it exited.
            if self._stderr_reader is not None:
                with suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(self._stderr_reader), 1)
            error = LspError(
                f"{self.command[0]} exited with {process.returncode}: "
                f"{self.stderr_tail()}"
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if method is None:
            request_id = message.get("id")
            future = (
                self._pending.get(request_id) if isinstance(request_id, int) else None
            )
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(LspError(json.dumps(message["error"])))
            else:
                future.set_result(message.get("result"))
        elif "id" in message:
            # The server waits for an answer to each of its requests. None of
            # them needs more than null; a configuration item gets one each.
            result: Any = None
            if method == "workspace/configuration":
                result = [None] * len((message.get("params") or {}).get("items", []))
            with suppress(LspError):
                await self._send({"id": message["id"], "result": result})
        # A notification, such as diagnostics, is dropped.

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while chunk := await process.stderr.read(1024):
            self._stderr += chunk
            del self._stderr[:-STDERR_TAIL_BYTES]


async def _read_frame(stream: asyncio.StreamReader) -> dict[str, Any] | None:
    """The next message, or ``None`` at the end of the stream.

    Raises ``ValueError`` on a bad frame.
    """
    length = 0
    while True:
        line = await stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value)
    try:
        message = json.loads((await stream.readexactly(length)).decode("utf-8"))
    except asyncio.IncompleteReadError:
        return None
    if not isinstance(message, dict):
        raise ValueError(f"not a JSON object: {message!r:.80}")
    return message
