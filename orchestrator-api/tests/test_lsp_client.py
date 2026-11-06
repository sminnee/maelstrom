"""The stdio LSP client, observed through a stand-in language server."""

import json
import sys
from pathlib import Path

import pytest

from mael_orchestrator.lsp_client import LspClient, LspError

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_lsp_server.py"


class FakeServer:
    """The fake's command, and what it logged."""

    def __init__(self, tmp_path: Path, definition) -> None:
        self.log = tmp_path / "log.jsonl"
        replies = tmp_path / "replies.json"
        replies.write_text(json.dumps({"definition": definition}))
        self.command = [sys.executable, str(FAKE_SERVER), str(self.log), str(replies)]
        self.root = tmp_path / "worktree"
        self.root.mkdir()

    def messages(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]


@pytest.fixture
def fake(tmp_path):
    return FakeServer(tmp_path, definition=[])


async def test_the_handshake_answers_the_servers_requests_then_says_initialized(
    fake,
):
    client = LspClient(fake.command, fake.root)
    try:
        await client.start(timeout=10)
    finally:
        await client.stop()

    messages = fake.messages()
    initialize = messages[0]
    assert initialize["method"] == "initialize"
    assert initialize["params"]["rootUri"] == fake.root.as_uri()
    assert initialize["params"]["workspaceFolders"] == [
        {"uri": fake.root.as_uri(), "name": "worktree"}
    ]
    # The symbol tree, not the flat list: a Fragment's dotted name needs it.
    assert initialize["params"]["capabilities"] == {
        "textDocument": {
            "definition": {"linkSupport": True},
            "references": {},
            "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
        }
    }
    # The server's three requests, answered in order: a configuration item
    # each gets null, and the others get null.
    assert [(m.get("id"), m.get("result", "absent")) for m in messages[1:4]] == [
        ("s1", [None, None]),
        ("s2", None),
        ("s3", None),
    ]
    assert [m.get("method") for m in messages[4:]] == [
        "initialized",
        "shutdown",
        "exit",
    ]


async def test_a_definition_round_trip_reads_split_frames_with_multibyte_bodies(
    tmp_path,
):
    location = {
        "uri": "{root}/café.py",
        "range": {
            "start": {"line": 4, "character": 4},
            "end": {"line": 4, "character": 9},
        },
    }
    fake = FakeServer(tmp_path, definition=[location])
    client = LspClient(fake.command, fake.root)
    try:
        await client.start(timeout=10)
        result = await client.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": (fake.root / "a.py").as_uri()},
                "position": {"line": 0, "character": 0},
            },
            timeout=10,
        )
    finally:
        await client.stop()

    assert result == [{**location, "uri": f"{fake.root.as_uri()}/café.py"}]


@pytest.mark.parametrize(
    ("method", "error"),
    [
        ("fake/crash", "the fake server fell over"),
        # A server whose output makes no sense is killed, not left to wedge.
        ("fake/garbage", "exited with"),
    ],
)
async def test_a_crashed_server_fails_its_request_and_starts_again(fake, method, error):
    client = LspClient(fake.command, fake.root)
    try:
        await client.start(timeout=10)
        with pytest.raises(LspError, match=error):
            await client.request(method, {}, timeout=10)
        assert not client.running

        await client.start(timeout=10)
        assert client.running
        assert await client.request("textDocument/definition", {}, timeout=10) == []
    finally:
        await client.stop()

    assert [m.get("method") for m in fake.messages()].count("initialize") == 2
