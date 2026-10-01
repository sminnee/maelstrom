"""Attached documents: the table's contract, and what ``attach`` copies where.

The table contract runs against both backends. ``attach`` runs against a real
temp worktree and a temp task repo, because what it does is copy files.
"""

import asyncio

import pytest

from mael_domain import attachments
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_attachments import (
    InMemoryTaskAttachmentTable,
    SqliteTaskAttachmentTable,
    TaskAttachment,
    attach,
    task_key,
)

PNG = b"\x89PNG\r\n\x1a\n\x00\x00fakepngdata"
WEBM = b"\x1a\x45\xdf\xa3\x00\x00fakewebmdata, longer than the picture"
#: An ISO-BMFF header: the ``ftyp`` box, then the major brand.
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00fakemp4data"
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00fakeheicdata"
#: A bucket filename carries the first twelve hex digits of its bytes' SHA-256.
SHOT = "shot-ae4a7e43881f.png"
FLOW = "flow-e2c867114833.webm"
NOW = "2026-10-01T10:00:00Z"
LATER = "2026-10-01T11:00:00Z"


@pytest.fixture(params=["memory", "sqlite"])
def table(request, tmp_path):
    if request.param == "memory":
        yield InMemoryTaskAttachmentTable()
        return
    db = open_state_db(tmp_path / "state.db")
    asyncio.run(db.migrate())
    yield SqliteTaskAttachmentTable(db)
    db.close()


def document(**over) -> dict:
    """The fields of one upsert, as ``attach`` passes them."""
    fields = {
        "task_key": "northwind/2026-09-22.1",
        "kind": "verification",
        "path": ".drafts/verification.md",
        "title": "Login flow",
        "body": "It works.\n",
        "digest": "d1",
        "attached_at": NOW,
    }
    fields.update(over)
    return fields


def summary(doc: TaskAttachment) -> tuple:
    return (doc.task_key, doc.path, doc.version, doc.body)


class TestTable:
    def test_a_first_upsert_is_version_one(self, table):
        stored = asyncio.run(table.upsert(**document()))

        assert stored.version == 1
        assert stored.id

    def test_the_same_path_again_replaces_the_body_and_raises_the_version(self, table):
        async def scenario():
            await table.upsert(**document())
            await table.upsert(**document(body="It still works.\n", digest="d2"))
            return await table.list()

        [stored] = asyncio.run(scenario())

        assert summary(stored) == (
            "northwind/2026-09-22.1",
            ".drafts/verification.md",
            2,
            "It still works.\n",
        )

    def test_a_revision_keeps_its_id(self, table):
        async def scenario():
            first = await table.upsert(**document())
            second = await table.upsert(**document(digest="d2"))
            return first.id, second.id

        first, second = asyncio.run(scenario())

        assert first == second

    def test_another_path_and_another_task_are_other_documents(self, table):
        async def scenario():
            await table.upsert(**document())
            await table.upsert(**document(path="/plans/a.md", kind="plan"))
            await table.upsert(**document(task_key="northwind/2026-09-22.2"))
            return await table.list()

        stored = asyncio.run(scenario())

        assert sorted((d.task_key, d.path, d.version) for d in stored) == [
            ("northwind/2026-09-22.1", ".drafts/verification.md", 1),
            ("northwind/2026-09-22.1", "/plans/a.md", 1),
            ("northwind/2026-09-22.2", ".drafts/verification.md", 1),
        ]

    def test_read_answers_one_document_or_none(self, table):
        async def scenario():
            await table.upsert(**document())
            key = "northwind/2026-09-22.1"
            return (
                await table.read(key, ".drafts/verification.md"),
                await table.read(key, "missing.md"),
            )

        found, missing = asyncio.run(scenario())

        assert found is not None and found.title == "Login flow"
        assert missing is None


def test_sqlite_rows_survive_a_reopen(tmp_path):
    async def scenario():
        first = open_state_db(tmp_path / "state.db")
        await first.migrate()
        await SqliteTaskAttachmentTable(first).upsert(**document())
        first.close()
        second = open_state_db(tmp_path / "state.db")
        await second.check()
        stored = await SqliteTaskAttachmentTable(second).list()
        second.close()
        return stored

    [stored] = asyncio.run(scenario())

    assert summary(stored) == (
        "northwind/2026-09-22.1",
        ".drafts/verification.md",
        1,
        "It works.\n",
    )


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    """Point the task repo at a temp dir and hand back its root."""
    root = tmp_path / "tasks"
    monkeypatch.setattr("mael_domain.task_store.tasks_root", lambda: root)
    return root


@pytest.fixture
def worktree(tmp_path):
    root = tmp_path / "northwind-alpha"
    (root / ".drafts").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "docs" / "shot.png").write_bytes(PNG)
    (root / "docs" / "flow.webm").write_bytes(WEBM)
    (root / ".drafts" / "verification.md").write_text(
        "# Login flow\n\n![The dashboard](docs/shot.png)\n\n![The flow](docs/flow.webm)\n"
    )
    return root


def attach_verification(table, worktree, now=NOW, **over):
    args = {
        "project": "northwind",
        "task_id": "2026-09-22.1",
        "cwd": str(worktree),
        "path": ".drafts/verification.md",
        "kind": "verification",
        "title": "Login flow",
        "now": now,
    }
    args.update(over)
    return asyncio.run(attach(table, **args))


def bucket_names(tasks) -> list[str]:
    bucket = tasks / "northwind" / "images" / "2026-09-22.1"
    return sorted(p.name for p in bucket.iterdir()) if bucket.exists() else []


def write_verification(worktree, body: str) -> None:
    (worktree / ".drafts" / "verification.md").write_text(body)


class TestAttach:
    def test_media_lands_in_the_bucket_and_the_body_carries_tokens(
        self, tasks, worktree
    ):
        attached = attach_verification(InMemoryTaskAttachmentTable(), worktree)

        assert attached is not None
        assert attached.task_key == task_key("northwind", "2026-09-22.1")
        assert attached.body == (
            "# Login flow\n\n"
            f"![The dashboard]({{{{MAEL_TASK_DIR}}}}/images/2026-09-22.1/{SHOT})\n\n"
            f"![The flow]({{{{MAEL_TASK_DIR}}}}/images/2026-09-22.1/{FLOW})\n"
        )
        bucket = tasks / "northwind" / "images" / "2026-09-22.1"
        assert (bucket / SHOT).read_bytes() == PNG
        assert (bucket / FLOW).read_bytes() == WEBM

    def test_the_same_document_again_writes_nothing(self, tasks, worktree):
        """A stream replay shows the path again, so the version must hold."""
        table = InMemoryTaskAttachmentTable()
        first = attach_verification(table, worktree)

        again = attach_verification(table, worktree, now=LATER)

        assert again == first
        assert (again.version, again.attached_at) == (1, NOW)

    def test_a_changed_body_is_the_next_version(self, tasks, worktree):
        table = InMemoryTaskAttachmentTable()
        attach_verification(table, worktree)
        write_verification(
            worktree, "Now with one picture.\n\n![The dashboard](docs/shot.png)\n"
        )

        attached = attach_verification(table, worktree, now=LATER)

        assert attached is not None and attached.version == 2
        # The picture did not change, so the bucket holds it once.
        assert bucket_names(tasks) == [FLOW, SHOT]

    def test_a_changed_title_is_the_next_version(self, tasks, worktree):
        table = InMemoryTaskAttachmentTable()
        attach_verification(table, worktree)

        attached = attach_verification(table, worktree, title="Sign-in flow", now=LATER)

        assert attached is not None
        assert (attached.version, attached.title) == (2, "Sign-in flow")

    def test_a_recording_made_again_under_the_same_name_is_the_next_version(
        self, tasks, worktree
    ):
        """The markdown did not change; the evidence did."""
        table = InMemoryTaskAttachmentTable()
        attach_verification(table, worktree)
        again = WEBM + b", recorded again"
        (worktree / "docs" / "flow.webm").write_bytes(again)

        attached = attach_verification(table, worktree, now=LATER)

        assert attached is not None and attached.version == 2
        [added] = set(bucket_names(tasks)) - {FLOW, SHOT}
        assert (
            tasks / "northwind" / "images" / "2026-09-22.1" / added
        ).read_bytes() == (again)
        assert f"/images/2026-09-22.1/{added})" in attached.body

    def test_two_files_of_one_name_are_each_stored_once(self, tasks, worktree):
        """Playwright names every recording ``video.webm``."""
        for name, data in (("a", WEBM + b"-a"), ("b", WEBM + b"-b")):
            (worktree / name).mkdir()
            (worktree / name / "video.webm").write_bytes(data)
        table = InMemoryTaskAttachmentTable()
        write_verification(worktree, "![a](a/video.webm) ![b](b/video.webm)\n")
        attach_verification(table, worktree)
        first = bucket_names(tasks)
        write_verification(worktree, "Again. ![a](a/video.webm) ![b](b/video.webm)\n")

        attach_verification(table, worktree, now=LATER)

        assert len(first) == 2
        assert bucket_names(tasks) == first

    @pytest.mark.parametrize("ext", [".mp4", ".mov"])
    def test_an_iso_media_file_keeps_its_own_extension(self, tasks, worktree, ext):
        (worktree / "docs" / f"clip{ext}").write_bytes(MP4)
        write_verification(worktree, f"![c](docs/clip{ext})\n")

        attach_verification(InMemoryTaskAttachmentTable(), worktree)

        [name] = bucket_names(tasks)
        assert name.startswith("clip-") and name.endswith(ext)

    def test_a_file_that_is_not_media_is_refused(self, tasks, worktree):
        (worktree / "docs" / "notes.txt").write_text("just text")
        write_verification(worktree, "![n](docs/notes.txt)\n")

        attached = attach_verification(InMemoryTaskAttachmentTable(), worktree)

        assert attached is not None
        assert attached.body == "_`docs/notes.txt` could not be shown._\n"
        assert not (tasks / "northwind").exists()

    def test_an_iso_image_is_not_taken_for_a_video(self, tasks, worktree):
        """HEIC shares the ``ftyp`` box with MP4, and no ``<video>`` plays it."""
        (worktree / "docs" / "photo.heic").write_bytes(HEIC)
        write_verification(worktree, "![p](docs/photo.heic)\n")

        attached = attach_verification(InMemoryTaskAttachmentTable(), worktree)

        assert attached is not None
        assert attached.body == "_`docs/photo.heic` could not be shown._\n"

    def test_a_file_over_the_cap_is_refused(self, tasks, worktree, monkeypatch):
        monkeypatch.setattr(attachments, "MAX_MEDIA_BYTES", len(WEBM) - 1)
        assert len(PNG) < len(WEBM)

        attached = attach_verification(InMemoryTaskAttachmentTable(), worktree)

        assert attached is not None
        assert attached.body == (
            "# Login flow\n\n"
            f"![The dashboard]({{{{MAEL_TASK_DIR}}}}/images/2026-09-22.1/{SHOT})\n\n"
            "_`docs/flow.webm` could not be shown._\n"
        )

    def test_a_path_outside_the_worktree_is_refused(self, tasks, worktree):
        (worktree.parent / "secret.png").write_bytes(PNG)
        write_verification(worktree, "![s](../secret.png)\n")

        attached = attach_verification(InMemoryTaskAttachmentTable(), worktree)

        assert attached is not None
        assert attached.body == "_`../secret.png` could not be shown._\n"

    def test_a_document_that_cannot_be_read_is_not_attached(self, tasks, worktree):
        table = InMemoryTaskAttachmentTable()

        attached = attach_verification(table, worktree, path=".drafts/missing.md")

        assert attached is None
        assert asyncio.run(table.list()) == []

    def test_a_given_body_is_attached_without_reading_the_path(self, tasks, worktree):
        """A plan's file is outside the worktree; the document holds its text."""
        attached = attach_verification(
            InMemoryTaskAttachmentTable(),
            worktree,
            path="/Users/dev/.claude/plans/a.md",
            kind="plan",
            title="Plan",
            body="# Plan\n\nDo the thing.\n",
        )

        assert attached is not None
        assert (attached.kind, attached.body) == ("plan", "# Plan\n\nDo the thing.\n")
