"""The Codex app-server connection, observed through a stand-in Codex CLI."""

import asyncio
import json
import shlex
from pathlib import Path

import pytest

import maelstrom.orchestrator.codex_bridge as codex_bridge
from maelstrom.orchestrator.codex_bridge import (
    CodexBridge,
    CodexIncompatible,
    CodexUnavailable,
)


def _codex(tmp_path: Path, body: str) -> Path:
    executable = tmp_path / "codex"
    executable.write_text(f"#!/bin/sh\n{body}\n")
    executable.chmod(0o755)
    return executable


def _proxy_codex(
    tmp_path: Path,
    *,
    cli_version: str = "0.154.0",
    daemon_version: str = "0.154.0",
    initial_line: str = "",
    initial_error: str = "",
    initial_delay: float = 0,
    fragment_initial: bool = False,
    exit_before_initialize: bool = False,
) -> tuple[Path, Path, Path]:
    calls = tmp_path / "calls"
    traffic = tmp_path / "traffic"
    proxy = tmp_path / "proxy.py"
    if not initial_line:
        initial_line = json.dumps(
            {
                "id": 1,
                "result": {
                    "userAgent": "codex/test",
                    "codexHome": "/tmp/codex",
                    "platformFamily": "unix",
                    "platformOs": "macos",
                },
            }
        )
    proxy.write_text(
        f"INITIAL_LINE = {initial_line!r}\n"
        f"INITIAL_ERROR = {initial_error!r}\n"
        f"INITIAL_DELAY = {initial_delay!r}\n"
        f"FRAGMENT_INITIAL = {fragment_initial!r}\n"
        f"EXIT_BEFORE_INITIALIZE = {exit_before_initialize!r}\n"
        + """import base64
import hashlib
import json
import struct
import sys
import time

reader = sys.stdin.buffer
writer = sys.stdout.buffer
traffic = sys.argv[1]


def read_exact(size):
    value = reader.read(size)
    if len(value) != size:
        raise EOFError
    return value


def read_frame():
    first, second = read_exact(2)
    length = second & 0x7f
    if length == 126:
        length = struct.unpack("!H", read_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(8))[0]
    mask = read_exact(4) if second & 0x80 else b""
    payload = read_exact(length)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return first & 0x0f, payload


def write_text(message):
    payload = message.encode()
    if len(payload) < 126:
        header = bytes((0x81, len(payload)))
    else:
        header = bytes((0x81, 126)) + struct.pack("!H", len(payload))
    writer.write(header + payload)
    writer.flush()


def write_fragmented_text(message):
    payload = message.encode()
    for index in range(0, len(payload), 4):
        final = index + 4 >= len(payload)
        opcode = 1 if index == 0 else 0
        writer.write(bytes(((0x80 if final else 0) | opcode, len(payload[index:index + 4]))) + payload[index:index + 4])
    writer.flush()


headers = bytearray()
while not headers.endswith(b"\\r\\n\\r\\n"):
    headers.extend(read_exact(1))
if not headers.startswith(b"GET /rpc HTTP/1.1\\r\\n"):
    sys.exit(24)
key_line = next(line for line in headers.decode().split("\\r\\n") if line.lower().startswith("sec-websocket-key:"))
key = key_line.split(":", 1)[1].strip()
accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
writer.write(("HTTP/1.1 101 Switching Protocols\\r\\nUpgrade: websocket\\r\\nConnection: Upgrade\\r\\nSec-WebSocket-Accept: " + accept + "\\r\\n\\r\\n").encode())
writer.flush()

slow_id = None
while True:
    try:
        opcode, payload = read_frame()
    except EOFError:
        break
    if opcode == 8:
        break
    if opcode != 1:
        continue
    line = payload.decode()
    with open(traffic, "a") as output:
        output.write(line + "\\n")
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        time.sleep(INITIAL_DELAY)
        if INITIAL_ERROR:
            print(INITIAL_ERROR, file=sys.stderr, flush=True)
        if EXIT_BEFORE_INITIALIZE:
            sys.exit(23)
        if FRAGMENT_INITIAL:
            write_fragmented_text(INITIAL_LINE)
        else:
            write_text(INITIAL_LINE)
    elif method == "model/list":
        write_text(json.dumps({"id": message["id"], "result": {"models": []}}))
    elif method == "slow":
        slow_id = message["id"]
    elif method == "fast":
        write_text(json.dumps({"method": "thread/started", "params": {"thread": "t1"}}))
        write_text(json.dumps({"id": "approval-1", "method": "item/fileChange/requestApproval", "params": {"itemId": "i1"}}))
        write_text(json.dumps({"id": message["id"], "result": "fast-result"}))
        if slow_id is not None:
            write_text(json.dumps({"id": slow_id, "result": "slow-result"}))
"""
    )
    executable = _codex(
        tmp_path,
        f"""
printf '%s\\n' "$*" >> {shlex.quote(str(calls))}
if [ "$1" = "--version" ]; then
  printf 'codex-cli {cli_version}\\n'
elif [ "$1 $2 $3" = "app-server daemon start" ]; then
  exit 0
elif [ "$1 $2 $3" = "app-server daemon version" ]; then
  printf '{{"cliVersion":"{cli_version}","appServerVersion":"{daemon_version}"}}\\n'
elif [ "$1 $2" = "app-server proxy" ]; then
  exec python3 {shlex.quote(str(proxy))} {shlex.quote(str(traffic))}
else
  printf 'unexpected command: %s\\n' "$*" >&2
  exit 90
fi
""",
    )
    return executable, calls, traffic


async def test_missing_codex_is_reported_as_unavailable(tmp_path):
    bridge = CodexBridge(executable=str(tmp_path / "missing-codex"))

    with pytest.raises(CodexUnavailable, match="Codex CLI is not available"):
        await bridge.request("model/list", {})


async def test_a_codex_before_app_server_daemon_support_is_incompatible(tmp_path):
    calls = tmp_path / "calls"
    executable = _codex(
        tmp_path,
        f"""
printf '%s\\n' "$*" >> {calls}
if [ "$1" = "--version" ]; then
  printf 'codex-cli 0.153.9\\n'
  exit 0
fi
exit 91
""",
    )
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexIncompatible, match=r"0\.153\.9.*0\.154\.0"):
        await bridge.request("model/list", {})

    assert calls.read_text().splitlines() == ["--version"]


async def test_a_newer_stable_codex_reaches_daemon_start(tmp_path):
    executable = _codex(
        tmp_path,
        """
if [ "$1" = "--version" ]; then
  printf 'codex-cli 0.155.0\\n'
  exit 0
fi
printf 'start refused for test\\n' >&2
exit 17
""",
    )
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexUnavailable, match="start refused for test"):
        await bridge.request("model/list", {})


async def test_a_prerelease_codex_is_incompatible(tmp_path):
    executable = _codex(
        tmp_path,
        """
if [ "$1" = "--version" ]; then
  printf 'codex-cli 0.155.0-beta.1\\n'
  exit 0
fi
exit 91
""",
    )
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexIncompatible, match="stable Codex CLI"):
        await bridge.request("model/list", {})


async def test_a_matching_daemon_connects_through_the_proxy(tmp_path):
    executable, calls, traffic = _proxy_codex(tmp_path)
    bridge = CodexBridge(executable=str(executable))

    result = await bridge.request("model/list", {})
    await bridge.close()

    assert result == {"models": []}
    assert calls.read_text().splitlines() == [
        "--version",
        "app-server daemon start",
        "app-server daemon version",
        "app-server proxy",
    ]
    messages = [json.loads(line) for line in traffic.read_text().splitlines()]
    assert messages[0]["method"] == "initialize"
    assert messages[0]["params"]["clientInfo"]["name"] == "maelstrom"
    assert messages[0]["params"]["capabilities"] == {"experimentalApi": False}
    assert messages[1] == {"method": "initialized"}
    assert messages[2] == {"id": 2, "method": "model/list", "params": {}}


async def test_a_daemon_from_another_codex_version_is_incompatible(tmp_path):
    executable, calls, _traffic = _proxy_codex(
        tmp_path, cli_version="0.155.0", daemon_version="0.154.0"
    )
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(
        CodexIncompatible,
        match=r"Codex CLI 0\.155\.0.*app-server 0\.154\.0.*daemon restart",
    ):
        await bridge.request("model/list", {})

    assert calls.read_text().splitlines() == [
        "--version",
        "app-server daemon start",
        "app-server daemon version",
    ]


async def test_concurrent_requests_are_correlated_by_id(tmp_path):
    executable, _calls, _traffic = _proxy_codex(tmp_path)
    bridge = CodexBridge(executable=str(executable))

    slow = asyncio.create_task(bridge.request("slow", {}))
    await asyncio.sleep(0)
    fast = asyncio.create_task(bridge.request("fast", {}))

    assert await asyncio.gather(slow, fast) == ["slow-result", "fast-result"]
    await bridge.close()


async def test_incoming_messages_can_be_read_and_answered(tmp_path):
    executable, _calls, traffic = _proxy_codex(tmp_path)
    bridge = CodexBridge(executable=str(executable))
    incoming = bridge.incoming()

    await bridge.request("fast", {})
    notification = await anext(incoming)
    request = await anext(incoming)
    await bridge.respond(request["id"], {"decision": "accept"})
    await bridge.close()

    assert notification == {
        "method": "thread/started",
        "params": {"thread": "t1"},
    }
    assert request == {
        "id": "approval-1",
        "method": "item/fileChange/requestApproval",
        "params": {"itemId": "i1"},
    }
    assert json.loads(traffic.read_text().splitlines()[-1]) == {
        "id": "approval-1",
        "result": {"decision": "accept"},
    }


async def test_an_initialize_error_is_reported_as_incompatible(tmp_path):
    executable, _calls, _traffic = _proxy_codex(
        tmp_path,
        initial_line=json.dumps(
            {"id": 1, "error": {"code": -32601, "message": "initialize changed"}}
        ),
    )
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexIncompatible, match="initialize changed"):
        await bridge.request("model/list", {})


async def test_malformed_proxy_output_is_reported_and_cleaned_up(tmp_path):
    executable, _calls, _traffic = _proxy_codex(tmp_path, initial_line="not-json")
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexUnavailable, match="malformed JSON"):
        await bridge.request("model/list", {})
    await bridge.close()


async def test_a_fragmented_message_has_a_total_size_limit(tmp_path, monkeypatch):
    executable, _calls, _traffic = _proxy_codex(tmp_path, fragment_initial=True)
    monkeypatch.setattr(codex_bridge, "MAX_FRAME_BYTES", 8)
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexIncompatible, match="WebSocket message"):
        await bridge.request("model/list", {})


async def test_proxy_stderr_explains_an_early_exit(tmp_path):
    executable, _calls, _traffic = _proxy_codex(
        tmp_path,
        initial_error="proxy socket refused",
        exit_before_initialize=True,
    )
    bridge = CodexBridge(executable=str(executable))

    with pytest.raises(CodexUnavailable, match="proxy socket refused"):
        await bridge.request("model/list", {})


async def test_initialize_timeout_is_reported(tmp_path):
    executable, _calls, _traffic = _proxy_codex(tmp_path, initial_delay=1)
    bridge = CodexBridge(executable=str(executable), handshake_timeout=0.05)

    with pytest.raises(CodexUnavailable, match="Timed out.*initializing"):
        await bridge.request("model/list", {})


async def test_cli_command_timeout_is_reported(tmp_path):
    executable = _codex(tmp_path, "sleep 1")
    bridge = CodexBridge(executable=str(executable), command_timeout=0.05)

    with pytest.raises(CodexUnavailable, match=r"Timed out running .*--version"):
        await bridge.request("model/list", {})
