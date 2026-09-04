"""Claude's own session transcripts, through both backends.

The only durable record of a session that has stopped. Shaped like
``tests/test_agent_spec_store.py``: the same assertions run against the
in-memory and the on-disk backend, because the Protocol is the contract.

No test reads the real ``~/.claude/projects`` — the on-disk backend takes its
root, and every fixture is built under ``tmp_path``.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

from maelstrom.agent_model import KIND_CLI, KIND_MAEL, TranscriptMeta
from maelstrom.transcript_store import (
    ClaudeTranscriptStore,
    InMemoryTranscriptStore,
    write_transcript,
)
from maelstrom.worktree_model import sanitise_path_for_claude

CWD = Path("/w/alpha")
OTHER = Path("/w/bravo")


def lines(
    *,
    cwd: Path = CWD,
    branch: str = "feat/x",
    entrypoint: str = "cli",
    title: str | None = "Improve plan mode",
    prompt: str = "do a code review here",
) -> list[dict]:
    """The head of a transcript, in the order Claude writes one."""
    head: list[dict] = [{"type": "last-prompt", "leafUuid": "u1"}]
    head.append(
        {
            "type": "user",
            "cwd": str(cwd),
            "gitBranch": branch,
            "entrypoint": entrypoint,
            "isMeta": True,
            "message": {"role": "user", "content": "<local-command-caveat>ignore me"},
        }
    )
    head.append(
        {
            "type": "user",
            "cwd": str(cwd),
            "gitBranch": branch,
            "entrypoint": entrypoint,
            "message": {"role": "user", "content": prompt},
        }
    )
    if title is not None:
        head.append({"type": "ai-title", "aiTitle": title})
    return head


class Backend(NamedTuple):
    """One backend, plus the way a test puts a transcript into it.

    The two backends are seeded differently — one takes entries, the other
    writes a file — so the fixture pairs each store with its own ``add``
    rather than assuming one shape.
    """

    store: object
    add: Callable[..., None]

    def list(self, cwds):
        return self.store.list(cwds)


@pytest.fixture(params=["memory", "claude"])
def store(request, tmp_path):
    """Both backends, behind one ``add`` the assertions share."""
    if request.param == "memory":
        backing = InMemoryTranscriptStore()
        return Backend(backing, backing.add)

    root = tmp_path / "projects"

    def add(session_id: str, entries: list[dict]) -> None:
        cwd = Path(entries[1]["cwd"]) if len(entries) > 1 else CWD
        write_transcript(
            root / sanitise_path_for_claude(cwd) / f"{session_id}.jsonl", entries
        )

    return Backend(ClaudeTranscriptStore(root), add)


def test_a_transcript_reports_its_session_cwd_and_branch(store):
    store.add("s1", lines())
    (meta,) = store.list(None)
    assert meta.session_id == "s1"
    assert meta.cwd == CWD
    assert meta.branch == "feat/x"


def test_an_interactive_session_is_labelled_by_its_ai_title(store):
    store.add("s1", lines(title="Improve plan mode"))
    (meta,) = store.list(None)
    assert meta.kind == KIND_CLI
    assert meta.label == "Improve plan mode"


def test_a_driven_agent_falls_back_to_its_first_prompt(store):
    """A daemon-driven agent writes no ``ai-title``, so its prompt is the label."""
    store.add(
        "s1", lines(entrypoint="sdk-cli", title=None, prompt="do a code review here")
    )
    (meta,) = store.list(None)
    assert meta.kind == KIND_MAEL
    assert meta.label == "do a code review here"


def test_the_label_skips_a_meta_entry(store):
    """The first ``user`` entry is often a local-command caveat, not a prompt."""
    store.add("s1", lines(title=None))
    (meta,) = store.list(None)
    assert meta.label == "do a code review here"


def test_a_filter_returns_only_the_transcripts_of_the_cwds_given(store):
    store.add("s1", lines(cwd=CWD))
    store.add("s2", lines(cwd=OTHER))
    assert [m.session_id for m in store.list([CWD])] == ["s1"]


def test_no_filter_returns_every_transcript(store):
    store.add("s1", lines(cwd=CWD))
    store.add("s2", lines(cwd=OTHER))
    assert {m.session_id for m in store.list(None)} == {"s1", "s2"}


def test_listing_is_empty_when_there_are_no_transcripts(store):
    assert store.list(None) == []


def test_a_filter_on_a_cwd_with_no_transcripts_is_empty(store):
    store.add("s1", lines(cwd=CWD))
    assert store.list([OTHER]) == []


def test_a_transcript_that_will_not_parse_is_skipped(tmp_path):
    """One truncated file must not hide every other resumable session."""
    root = tmp_path / "projects"
    store = ClaudeTranscriptStore(root)
    write_transcript(root / sanitise_path_for_claude(CWD) / "s1.jsonl", lines())
    (root / sanitise_path_for_claude(CWD) / "broken.jsonl").write_text("{not json")
    assert [m.session_id for m in store.list(None)] == ["s1"]


def test_a_transcript_with_no_readable_head_is_skipped(tmp_path):
    """A file with no ``cwd`` names no worktree, so it cannot be resumed."""
    root = tmp_path / "projects"
    store = ClaudeTranscriptStore(root)
    (root / sanitise_path_for_claude(CWD)).mkdir(parents=True)
    (root / sanitise_path_for_claude(CWD) / "s1.jsonl").write_text(
        json.dumps({"type": "last-prompt"}) + "\n"
    )
    assert store.list(None) == []


def test_the_read_stops_before_the_body_of_a_long_transcript(tmp_path):
    """1.8 GB of transcripts on this machine: a whole-file read is not viable."""
    root = tmp_path / "projects"
    path = root / sanitise_path_for_claude(CWD) / "s1.jsonl"
    tail = [{"type": "assistant", "message": {"content": []}}] * 5000
    write_transcript(path, lines() + tail)
    store = ClaudeTranscriptStore(root)
    (meta,) = store.list(None)
    assert meta.label == "Improve plan mode"
    assert meta.lines_read < 100


def test_a_meta_carries_the_files_size_and_modification_time(tmp_path):
    """``age`` is a column, and the size says which sessions are substantial."""
    root = tmp_path / "projects"
    path = root / sanitise_path_for_claude(CWD) / "s1.jsonl"
    write_transcript(path, lines())
    (meta,) = ClaudeTranscriptStore(root).list(None)
    assert meta.size == path.stat().st_size
    assert meta.modified_at == pytest.approx(path.stat().st_mtime)


def test_a_meta_is_comparable_by_value():
    """The row builder is handed these, so equality must not be identity."""
    fields = dict(
        session_id="s1",
        cwd=CWD,
        branch="feat/x",
        kind=KIND_CLI,
        label="hi",
        modified_at=1.0,
        size=2,
    )
    assert TranscriptMeta(**fields) == TranscriptMeta(**fields)
