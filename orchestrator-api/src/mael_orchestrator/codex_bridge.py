"""Connect Maelstrom to the machine-wide Codex app-server daemon."""

import asyncio
import base64
import hashlib
import json
import os
import re
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

from mael_orchestrator import __version__

MINIMUM_CODEX_VERSION = (0, 154, 0)
MINIMUM_CODEX_VERSION_TEXT = ".".join(str(part) for part in MINIMUM_CODEX_VERSION)
DEFAULT_COMMAND_TIMEOUT = 15.0
MAX_FRAME_BYTES = 64 * 1024 * 1024
_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_STABLE_VERSION = re.compile(r"^codex-cli (\d+)\.(\d+)\.(\d+)$")
_END = object()


class CodexBridgeError(RuntimeError):
    """A Codex app-server connection cannot serve a request."""


class CodexUnavailable(CodexBridgeError):
    """The Codex CLI or its app-server cannot be reached."""


class CodexIncompatible(CodexBridgeError):
    """The available Codex version cannot serve this bridge."""


@dataclass
class CodexBridge:
    """One JSON-RPC connection through the local Codex proxy."""

    executable: str = "codex"
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT
    handshake_timeout: float = DEFAULT_COMMAND_TIMEOUT
    _connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _process: asyncio.subprocess.Process | None = field(default=None, init=False)
    _reader: asyncio.Task[None] | None = field(default=None, init=False)
    _stderr_reader: asyncio.Task[None] | None = field(default=None, init=False)
    _stderr: bytearray = field(default_factory=bytearray, init=False)
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, init=False)
    _incoming: asyncio.Queue[dict[str, Any] | CodexBridgeError | object] = field(
        default_factory=asyncio.Queue, init=False
    )
    _next_id: int = field(default=1, init=False)
    _closed: bool = field(default=False, init=False)
    _upgraded: bool = field(default=False, init=False)

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        """Connect on demand and send one JSON-RPC request."""
        await self._connect()
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def incoming(self) -> AsyncIterator[dict[str, Any]]:
        """Yield notifications and server requests from this connection."""
        await self._connect()
        while True:
            message = await self._incoming.get()
            if message is _END:
                return
            if isinstance(message, CodexBridgeError):
                raise message
            yield cast(dict[str, Any], message)

    async def respond(self, request_id: str | int, result: Any) -> None:
        """Answer one server request."""
        await self._connect()
        await self._write({"id": request_id, "result": result})

    async def close(self) -> None:
        """Close this proxy connection without changing the daemon."""
        if self._closed:
            return
        self._closed = True
        self._incoming.put_nowait(_END)
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None
        await self._stop_proxy()

    async def _stop_proxy(self) -> None:
        process = self._process
        if process is None:
            return
        if self._upgraded and process.returncode is None:
            try:
                await self._write_frame(0x8, b"")
            except CodexBridgeError:
                pass
        self._upgraded = False
        self._process = None
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            process.terminate()
        await process.wait()
        if self._stderr_reader is not None:
            await asyncio.gather(self._stderr_reader, return_exceptions=True)
            self._stderr_reader = None

    async def _connect(self) -> None:
        if self._process is not None:
            return
        if self._closed:
            raise CodexUnavailable("The Codex app-server bridge is closed.")
        async with self._connect_lock:
            if self._process is not None:
                return
            cli_version = await self._check_cli()
            await self._start_daemon()
            await self._check_daemon_version(cli_version)
            await self._start_proxy()

    async def _start_proxy(self) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                "app-server",
                "proxy",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexUnavailable(
                f"Codex CLI is not available at {self.executable!r}."
            ) from error
        self._process = process
        self._stderr.clear()
        self._stderr_reader = asyncio.create_task(self._read_stderr(process))
        try:
            await asyncio.wait_for(
                self._upgrade_websocket(), timeout=self.handshake_timeout
            )
            await self._write(
                {
                    "id": self._next_id,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "maelstrom",
                            "title": "Maelstrom",
                            "version": __version__,
                        },
                        "capabilities": {"experimentalApi": False},
                    },
                }
            )
            self._next_id += 1
            response = await asyncio.wait_for(
                self._read_message(), timeout=self.handshake_timeout
            )
            if response.get("id") != 1 or "result" not in response:
                raise CodexIncompatible(
                    f"Codex app-server refused initialization: {response!r}"
                )
            await self._write({"method": "initialized"})
            self._reader = asyncio.create_task(self._read_messages())
        except TimeoutError as error:
            await self._stop_proxy()
            raise CodexUnavailable(
                "Timed out while initializing the Codex app-server proxy."
            ) from error
        except CodexBridgeError:
            await self._stop_proxy()
            raise

    async def _check_daemon_version(self, cli_version: str) -> None:
        result = await self._run("app-server", "daemon", "version")
        if result.returncode != 0:
            detail = result.stderr.decode().strip() or "Codex daemon version failed."
            raise CodexUnavailable(detail)
        try:
            versions = json.loads(result.stdout)
            daemon_version = str(versions["appServerVersion"])
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise CodexIncompatible(
                "Codex app-server returned an invalid version response."
            ) from error
        if daemon_version != cli_version:
            raise CodexIncompatible(
                f"Codex CLI {cli_version} does not match app-server {daemon_version}. "
                "Run `codex app-server daemon restart` to use the installed CLI."
            )

    async def _check_cli(self) -> str:
        result = await self._run("--version")
        if result.returncode != 0:
            detail = result.stderr.decode().strip() or "Codex CLI version failed."
            raise CodexUnavailable(detail)
        version_text = result.stdout.decode().strip()
        match = _STABLE_VERSION.fullmatch(version_text)
        if match is None:
            raise CodexIncompatible(
                f"Expected a stable Codex CLI version, got {version_text or 'no version'}."
            )
        version = tuple(int(part) for part in match.groups())
        if version < MINIMUM_CODEX_VERSION:
            raise CodexIncompatible(
                f"Codex CLI {version_text.removeprefix('codex-cli ')} is incompatible. "
                f"Maelstrom requires Codex CLI {MINIMUM_CODEX_VERSION_TEXT} or newer."
            )
        return version_text.removeprefix("codex-cli ")

    async def _start_daemon(self) -> None:
        result = await self._run("app-server", "daemon", "start")
        if result.returncode != 0:
            detail = (
                result.stderr.decode().strip()
                or "Codex app-server daemon start failed."
            )
            raise CodexUnavailable(detail)

    async def _run(self, *args: str) -> "_CompletedProcess":
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as error:
            raise CodexUnavailable(
                f"Codex CLI is not available at {self.executable!r}."
            ) from error
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.command_timeout
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            command = " ".join((self.executable, *args))
            raise CodexUnavailable(f"Timed out running `{command}`.") from error
        return _CompletedProcess(process.returncode or 0, stdout, stderr)

    async def _write(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, separators=(",", ":")).encode()
        await self._write_frame(0x1, payload)

    async def _write_frame(self, opcode: int, payload: bytes) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexUnavailable("The Codex app-server proxy is not connected.")
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        async with self._write_lock:
            process.stdin.write(header + mask + masked)
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise CodexUnavailable("The Codex app-server proxy closed.") from error

    async def _read_message(self) -> dict[str, Any]:
        payload = await self._read_text_frame()
        try:
            message = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CodexUnavailable(
                "The Codex app-server proxy returned malformed JSON."
            ) from error
        if not isinstance(message, dict):
            raise CodexUnavailable("The Codex app-server proxy returned a non-object.")
        return message

    async def _upgrade_websocket(self) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise CodexUnavailable("The Codex app-server proxy is not connected.")
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET /rpc HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
        async with self._write_lock:
            process.stdin.write(request)
            await process.stdin.drain()
        try:
            response = await process.stdout.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            raise await self._closed_error("during WebSocket upgrade") from error
        lines = response.decode(errors="replace").split("\r\n")
        headers = {
            name.strip().lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        expected = base64.b64encode(
            hashlib.sha1((key + _WEBSOCKET_GUID).encode()).digest()
        ).decode()
        if (
            not lines[0].startswith("HTTP/1.1 101 ")
            or headers.get("sec-websocket-accept") != expected
        ):
            raise CodexIncompatible(
                "Codex app-server refused the local WebSocket connection."
            )
        self._upgraded = True

    async def _read_text_frame(self) -> bytes:
        chunks: list[bytes] = []
        message_length = 0
        continuing = False
        while True:
            final, opcode, payload = await self._read_frame()
            if opcode == 0x8:
                raise await self._closed_error()
            if opcode == 0x9:
                await self._write_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1 and not continuing:
                chunks.append(payload)
                continuing = not final
            elif opcode == 0x0 and continuing:
                chunks.append(payload)
                continuing = not final
            else:
                raise CodexIncompatible(
                    f"Codex app-server sent unsupported WebSocket opcode {opcode}."
                )
            message_length += len(payload)
            if message_length > MAX_FRAME_BYTES:
                raise CodexIncompatible(
                    f"Codex app-server sent a {message_length}-byte WebSocket message."
                )
            if not continuing:
                return b"".join(chunks)

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        process = self._process
        if process is None or process.stdout is None:
            raise CodexUnavailable("The Codex app-server proxy is not connected.")
        try:
            first, second = await process.stdout.readexactly(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", await process.stdout.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await process.stdout.readexactly(8))[0]
            if length > MAX_FRAME_BYTES:
                raise CodexIncompatible(
                    f"Codex app-server sent a {length}-byte WebSocket frame."
                )
            mask = await process.stdout.readexactly(4) if second & 0x80 else b""
            payload = await process.stdout.readexactly(length)
        except asyncio.IncompleteReadError as error:
            raise await self._closed_error() from error
        if mask:
            payload = bytes(
                byte ^ mask[index % 4] for index, byte in enumerate(payload)
            )
        return bool(first & 0x80), first & 0x0F, payload

    async def _closed_error(self, context: str = "") -> CodexUnavailable:
        process = self._process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                process.terminate()
            await process.wait()
        if self._stderr_reader is not None:
            await asyncio.gather(self._stderr_reader, return_exceptions=True)
        detail = self._stderr.decode(errors="replace").strip()
        where = f" {context}" if context else ""
        suffix = f" {detail}" if detail else ""
        return CodexUnavailable(f"The Codex app-server proxy closed{where}.{suffix}")

    async def _read_messages(self) -> None:
        try:
            while True:
                message = await self._read_message()
                request_id = message.get("id")
                if request_id not in self._pending:
                    if "method" in message:
                        self._incoming.put_nowait(message)
                    continue
                future = self._pending[request_id]
                if future.done():
                    continue
                if "error" in message:
                    future.set_exception(
                        CodexBridgeError(
                            f"Codex app-server error: {message['error']!r}"
                        )
                    )
                else:
                    future.set_result(message.get("result"))
        except asyncio.CancelledError:
            raise
        except CodexBridgeError as error:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._incoming.put_nowait(error)

    async def _read_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        while chunk := await process.stderr.read(4096):
            self._stderr.extend(chunk)
            if len(self._stderr) > 16384:
                del self._stderr[:-16384]


@dataclass(frozen=True)
class _CompletedProcess:
    returncode: int
    stdout: bytes
    stderr: bytes
