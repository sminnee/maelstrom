"""A stand-in language server: ``Content-Length`` JSON-RPC on stdio.

Usage: ``python fake_lsp_server.py <log> <replies>``.

It appends each message it gets to ``<log>``, one JSON line each. Before it
answers ``initialize`` it sends the three requests Pyright sends, and waits for
the answers. It writes each frame in two halves, which splits a multibyte
character, and sends a notification with one before each definition answer.

``<replies>`` is a JSON object: ``definition`` is the answer to every
``textDocument/definition``, with ``{root}`` replaced by the ``rootUri``.
``references`` is the answer to every ``textDocument/references``, the same
way, or a JSON-RPC error with ``references_error``. ``document_symbol`` maps a file name to its ``textDocument/documentSymbol``
answer, ``[]`` by default; a file named in ``symbol_error`` gets a JSON-RPC
error instead. With ``crash_on: n``, the n-th definition request crashes the server, as the
method ``fake/crash`` does: it writes to stderr and exits. The method
``fake/garbage`` answers with a frame that holds no JSON object, and the
server stays up.
"""

import json
import sys
import time

reader = sys.stdin.buffer
writer = sys.stdout.buffer
log_path, replies_path = sys.argv[1], sys.argv[2]
root = ""
next_id = 0
definitions = 0


def read_message():
    length = 0
    while True:
        line = reader.readline()
        if not line:
            sys.exit(0)
        line = line.strip()
        if not line:
            break
        name, _, value = line.partition(b":")
        if name.lower() == b"content-length":
            length = int(value)
    message = json.loads(reader.read(length).decode("utf-8"))
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(json.dumps(message) + "\n")
    return message


def send(message):
    body = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode()
    frame = b"Content-Length: %d\r\n\r\n" % len(body) + body
    # Cut inside the first multibyte character, else in the middle of the body.
    wide = next((i for i, b in enumerate(frame) if b >= 0x80), None)
    half = wide + 1 if wide is not None else len(frame) - len(body) // 2 - 1
    writer.write(frame[:half])
    writer.flush()
    time.sleep(0.01)
    writer.write(frame[half:])
    writer.flush()


def ask(method, params):
    """Send a request to the client and wait for its answer."""
    global next_id
    next_id += 1
    asked = f"s{next_id}"
    send({"id": asked, "method": method, "params": params})
    while True:
        message = read_message()
        if message.get("id") == asked and "method" not in message:
            return


def crash():
    sys.stderr.write("the fake server fell over\n")
    sys.stderr.flush()
    sys.exit(3)


def replies():
    with open(replies_path, encoding="utf-8") as file:
        return json.load(file)


while True:
    message = read_message()
    method = message.get("method")
    if method == "initialize":
        root = message["params"]["rootUri"]
        ask("workspace/configuration", {"items": [{"section": "python"}, {}]})
        ask("client/registerCapability", {"registrations": []})
        ask("window/workDoneProgress/create", {"token": "t"})
        send(
            {
                "id": message["id"],
                "result": {"capabilities": {"definitionProvider": True}},
            }
        )
    elif method == "textDocument/definition":
        definitions += 1
        if replies().get("crash_on") == definitions:
            crash()
        send(
            {
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": root, "diagnostics": [{"message": "naïve — ☃"}]},
            }
        )
        answer = json.dumps(replies().get("definition")).replace("{root}", root)
        send({"id": message["id"], "result": json.loads(answer)})
    elif method == "textDocument/references":
        if replies().get("references_error"):
            send({"id": message["id"], "error": {"code": -32603, "message": "no"}})
            continue
        answer = json.dumps(replies().get("references")).replace("{root}", root)
        send({"id": message["id"], "result": json.loads(answer)})
    elif method == "textDocument/documentSymbol":
        name = message["params"]["textDocument"]["uri"].rsplit("/", 1)[-1]
        if name in (replies().get("symbol_error") or []):
            send({"id": message["id"], "error": {"code": -32603, "message": "no"}})
        else:
            symbols = (replies().get("document_symbol") or {}).get(name, [])
            send({"id": message["id"], "result": symbols})
    elif method == "fake/crash":
        crash()
    elif method == "fake/garbage":
        writer.write(b"Content-Length: 5\r\n\r\n[1,2]")
        writer.flush()
    elif method == "shutdown":
        send({"id": message["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
    elif "id" in message:
        send({"id": message["id"], "result": None})
