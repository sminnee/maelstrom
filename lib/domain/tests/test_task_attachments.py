"""The Task document store's contract, and what ``attach`` copies where.

The table contract runs against both backends. ``attach`` runs against a real
temp worktree and a temp task repo, because what it does is copy files.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from mael_domain import attachments, sarif
from mael_domain import task_documents as docs
from mael_domain.state_db.migrate import open_state_db
from mael_domain.task_attachments import (
    InMemoryTaskAttachmentTable,
    Link,
    SqliteTaskAttachmentTable,
    TaskAttachment,
    Version,
    attachment_id,
)
from mael_domain.task_documents import DocumentRef, attach

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
KEY = "northwind/2026-09-22.1"


@pytest.fixture(params=["memory", "sqlite"])
def table(request, tmp_path):
    if request.param == "memory":
        yield InMemoryTaskAttachmentTable()
        return
    db = open_state_db(tmp_path / "state.db")
    asyncio.run(db.migrate())
    yield SqliteTaskAttachmentTable(db)
    db.close()


def doc(name: str = ".drafts/verification.md", key: str = KEY) -> TaskAttachment:
    return TaskAttachment(
        id=attachment_id(key, name),
        task_key=key,
        kind="markdown",
        name=name,
        archived=False,
        attached_at=NOW,
    )


def version(document: TaskAttachment, n: int, body: str = "It works.\n") -> Version:
    return Version(
        id=f"{document.id}@v{n}",
        attachment_id=document.id,
        version=n,
        title="Login flow",
        body=body,
        digest=f"d{n}",
        created_at=NOW,
        review_status="draft",
    )


def a_link(document: TaskAttachment, task_id: str, fragment: str = "") -> Link:
    return Link(
        id=f"{document.id}-{task_id}-{fragment}",
        attachment_id=document.id,
        project="northwind",
        task_id=task_id,
        fragment=fragment,
    )


class TestTable:
    def test_read_answers_one_document_or_none(self, table):
        async def scenario():
            await table.put(doc())
            return (
                await table.read(KEY, ".drafts/verification.md"),
                await table.read(KEY, "missing.md"),
            )

        found, missing = asyncio.run(scenario())

        assert found == doc()
        assert missing is None

    def test_put_replaces_the_row_with_its_id(self, table):
        async def scenario():
            await table.put(doc())
            await table.put(replace(doc(), archived=True))
            return await table.all_documents()

        assert asyncio.run(scenario()) == [replace(doc(), archived=True)]

    def test_owned_lists_one_task_s_documents_by_name(self, table):
        async def scenario():
            for name in ("zap.sarif", "notes/a.md"):
                await table.put(doc(name))
            await table.put(doc("other.md", key="northwind/2026-09-22.2"))
            return await table.owned(KEY)

        assert [d.name for d in asyncio.run(scenario())] == ["notes/a.md", "zap.sarif"]

    def test_versions_come_oldest_first(self, table):
        async def scenario():
            await table.put(doc())
            for n in (2, 1, 3):
                await table.add_version(version(doc(), n))
            return await table.versions(doc().id)

        assert [v.version for v in asyncio.run(scenario())] == [1, 2, 3]

    def test_links_are_read_by_document_and_by_task(self, table):
        first, second = doc("a.md"), doc("b.md")

        async def scenario():
            for d in (first, second):
                await table.put(d)
            await table.add_link(a_link(first, "2026-09-22.5", "intro"))
            await table.add_link(a_link(first, "2026-09-22.5", "intro"))
            await table.add_link(a_link(first, "2026-09-22.6"))
            await table.add_link(a_link(second, "2026-09-22.5"))
            return (
                await table.links(first.id),
                await table.linked("northwind", "2026-09-22.5"),
                await table.linked("elsewhere", "2026-09-22.5"),
            )

        links, linked, elsewhere = asyncio.run(scenario())

        assert sorted((x.task_id, x.fragment) for x in links) == [
            ("2026-09-22.5", "intro"),
            ("2026-09-22.6", ""),
        ]
        assert sorted(d.name for d in linked) == ["a.md", "b.md"]
        assert elsewhere == []

    def test_remove_link_takes_one_away(self, table):
        kept, gone = a_link(doc(), "2026-09-22.5"), a_link(doc(), "2026-09-22.6")

        async def scenario():
            await table.put(doc())
            await table.add_link(kept)
            await table.add_link(gone)
            await table.remove_link(gone.id)
            await table.remove_link(gone.id)
            return await table.links(doc().id)

        assert asyncio.run(scenario()) == [kept]


def test_sqlite_rows_survive_a_reopen(tmp_path):
    async def scenario():
        first = open_state_db(tmp_path / "state.db")
        await first.migrate()
        table = SqliteTaskAttachmentTable(first)
        await table.put(doc())
        await table.add_version(version(doc(), 1))
        await table.add_link(a_link(doc(), "2026-09-22.5"))
        first.close()
        second = open_state_db(tmp_path / "state.db")
        await second.check()
        table = SqliteTaskAttachmentTable(second)
        stored = (
            await table.all_documents(),
            await table.versions(doc().id),
            await table.links(doc().id),
        )
        second.close()
        return stored

    assert asyncio.run(scenario()) == (
        [doc()],
        [version(doc(), 1)],
        [a_link(doc(), "2026-09-22.5")],
    )


ZAP = (Path(__file__).parents[1] / "fixtures" / "sarif" / "zap.sarif.json").read_text()


ADD = {
    "project": "northwind",
    "task_id": "2026-09-22.1",
    "name": "notes.md",
    "kind": "markdown",
    "title": "Notes",
    "now": NOW,
}


def add(table, name="notes.md", body="# Notes\n", **over):
    args = {
        "project": "northwind",
        "task_id": "2026-09-22.1",
        "name": name,
        "kind": "markdown",
        "title": "Notes",
        "body": body,
        "now": NOW,
    }
    args.update(over)
    return asyncio.run(docs.add(table, **args))


def ref(text: str) -> DocumentRef:
    return docs.parse_ref(text, project="northwind", task_id="2026-09-22.1")


class TestRefs:
    @pytest.mark.parametrize(
        ("text", "parsed"),
        [
            ("notes.md", ("northwind", "2026-09-22.1", "notes.md", None, None)),
            ("t.2:a/b.md", ("northwind", "t.2", "a/b.md", None, None)),
            ("p:t.2:a.md@v3", ("p", "t.2", "a.md", 3, None)),
            # A finding key holds a `:`, so the fragment splits off first.
            ("p:t.2:zap@v1#v3:abc", ("p", "t.2", "zap", 1, "v3:abc")),
            ("a.md#setup", ("northwind", "2026-09-22.1", "a.md", None, "setup")),
        ],
    )
    def test_a_ref_fills_what_it_leaves_out(self, text, parsed):
        found = ref(text)
        assert (
            found.project,
            found.task_id,
            found.name,
            found.version,
            found.fragment,
        ) == parsed

    def test_a_ref_prints_its_canonical_uri(self):
        assert str(ref("a.md@v2#setup")) == "northwind:2026-09-22.1:a.md@v2#setup"

    @pytest.mark.parametrize("text", ["", "p:t:u:a.md", "a.md#", " a.md", "a\x07.md"])
    def test_a_ref_the_grammar_refuses(self, text):
        with pytest.raises(docs.InvalidName):
            ref(text)

    def test_a_ref_with_no_task_is_refused(self):
        with pytest.raises(docs.InvalidName, match="task"):
            docs.parse_ref("a.md", project="p", task_id=None)


class TestAdd:
    def test_each_new_body_is_the_next_version(self):
        table = InMemoryTaskAttachmentTable()
        add(table)

        document, second = add(table, body="# Notes\n\nMore.\n", now=LATER)

        assert (document.name, document.kind, document.attached_at) == (
            "notes.md",
            "markdown",
            NOW,
        )
        assert (second.version, second.created_at) == (2, LATER)

    def test_the_latest_body_again_adds_nothing(self):
        table = InMemoryTaskAttachmentTable()
        _, first = add(table)

        _, again = add(table, now=LATER)

        assert again == first

    def test_another_kind_under_the_same_name_is_refused(self):
        table = InMemoryTaskAttachmentTable()
        add(table, name="scan")

        with pytest.raises(docs.KindMismatch):
            add(table, name="scan", kind="sarif", body=ZAP)

    @pytest.mark.parametrize(("kind", "body"), [("sarif", "# not sarif"), ("pdf", "x")])
    def test_a_body_its_kind_cannot_read_is_refused(self, kind, body):
        with pytest.raises(docs.InvalidDocument):
            add(InMemoryTaskAttachmentTable(), kind=kind, body=body)

    def test_two_adds_at_once_take_two_versions(self):
        """The next number is read and written as one step."""

        class Slow(InMemoryTaskAttachmentTable):
            async def versions(self, attachment):
                read = await super().versions(attachment)
                await asyncio.sleep(0)  # yield between the read and the write
                return read

        table = Slow()

        async def both():
            await docs.add(table, **ADD, body="zero")
            await asyncio.gather(
                docs.add(table, **ADD, body="one"), docs.add(table, **ADD, body="two")
            )
            return await table.versions(attachment_id(KEY, "notes.md"))

        assert [v.version for v in asyncio.run(both())] == [1, 2, 3]

    def test_an_archived_document_still_takes_versions(self):
        table = InMemoryTaskAttachmentTable()
        add(table)
        asyncio.run(docs.archive(table, ref("notes.md"), True))

        document, version = add(table, body="v2")

        assert (document.archived, version.version) == (True, 2)


class TestResolve:
    def test_a_ref_resolves_to_the_latest_or_the_version_it_names(self):
        table = InMemoryTaskAttachmentTable()
        add(table, body="one")
        add(table, body="two")

        _, latest = asyncio.run(docs.resolve(table, ref("notes.md")))
        _, first = asyncio.run(docs.resolve(table, ref("notes.md@v1")))

        assert (latest.body, first.body) == ("two", "one")

    @pytest.mark.parametrize("text", ["missing.md", "notes.md@v9"])
    def test_what_is_not_there_is_not_found(self, text):
        table = InMemoryTaskAttachmentTable()
        add(table)

        with pytest.raises(docs.DocumentNotFound):
            asyncio.run(docs.resolve(table, ref(text)))


class TestFragments:
    def test_markdown_fragments_are_github_heading_slugs(self):
        body = (
            "# Set up\n\nText.\n\n## What's new?\n\n"
            "```\n# not a heading\n~~~\n# still code\n```\n\n"
            "~~~\n# tilde code\n~~~\n\n## Set up\n### Set up ###\n"
        )

        assert docs.fragments("markdown", body) == [
            "set-up",
            "whats-new",
            "set-up-1",
            "set-up-2",
        ]

    def test_sarif_fragments_are_finding_keys(self):
        keys = docs.fragments("sarif", ZAP)

        assert keys == [f.key for f in sarif.parse(ZAP).findings]
        assert keys != ["F1", "F2"]


class TestLinks:
    def test_a_link_to_a_fragment_is_kept_once(self):
        table = InMemoryTaskAttachmentTable()
        add(table, body="# Setup\n")

        for _ in range(2):
            asyncio.run(
                docs.link(
                    table, ref("notes.md#setup"), project="northwind", task_id="t.5"
                )
            )

        [made] = asyncio.run(table.links(attachment_id(KEY, "notes.md")))
        assert (made.task_id, made.fragment) == ("t.5", "setup")

    def test_a_fragment_the_latest_version_lacks_is_refused(self):
        table = InMemoryTaskAttachmentTable()
        add(table, body="# Setup\n")
        add(table, body="# Install\n")

        with pytest.raises(docs.UnknownFragment):
            asyncio.run(
                docs.link(
                    table, ref("notes.md#setup"), project="northwind", task_id="t.5"
                )
            )

    def test_a_link_to_one_version_is_refused(self):
        """A link is to the document; its fragment is checked on the latest version."""
        table = InMemoryTaskAttachmentTable()
        add(table, body="# Setup\n")

        with pytest.raises(docs.InvalidName, match="@v1"):
            asyncio.run(
                docs.link(
                    table, ref("notes.md@v1#setup"), project="northwind", task_id="t.5"
                )
            )

    def test_the_owner_cannot_link_its_own_document(self):
        table = InMemoryTaskAttachmentTable()
        add(table)

        with pytest.raises(docs.TaskDocumentError, match="owns"):
            asyncio.run(
                docs.link(
                    table, ref("notes.md"), project="northwind", task_id="2026-09-22.1"
                )
            )

    def test_unlink_removes_only_the_fragment_it_names(self):
        table = InMemoryTaskAttachmentTable()
        add(table, body="# Setup\n")
        for text in ("notes.md", "notes.md#setup"):
            asyncio.run(docs.link(table, ref(text), project="northwind", task_id="t.5"))

        asyncio.run(
            docs.unlink(
                table, ref("notes.md#setup"), project="northwind", task_id="t.5"
            )
        )

        [kept] = asyncio.run(table.links(attachment_id(KEY, "notes.md")))
        assert kept.fragment == ""


class TestArchive:
    def test_the_owner_archives_and_unarchives(self):
        table = InMemoryTaskAttachmentTable()
        add(table)

        asyncio.run(docs.archive(table, ref("notes.md"), True, by=KEY))
        archived = asyncio.run(table.read(KEY, "notes.md"))
        asyncio.run(docs.archive(table, ref("notes.md"), False, by=KEY))

        assert archived is not None and archived.archived
        assert not asyncio.run(table.read(KEY, "notes.md")).archived

    def test_another_task_cannot_archive_it(self):
        table = InMemoryTaskAttachmentTable()
        add(table)

        with pytest.raises(docs.NotOwner):
            asyncio.run(docs.archive(table, ref("notes.md"), True, by="northwind/t.5"))


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
        "title": "Login flow",
        "now": now,
    }
    args.update(over)
    attached = asyncio.run(attach(table, **args))
    return attached[1] if attached else None


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
        assert attached.attachment_id == attachment_id(KEY, ".drafts/verification.md")
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
        assert (again.version, again.created_at) == (1, NOW)

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

    def test_an_earlier_body_again_is_the_next_version(self, tasks, worktree):
        table = InMemoryTaskAttachmentTable()
        first = attach_verification(table, worktree)
        attach_verification(table, worktree, body="Reworked.\n")

        again = attach_verification(table, worktree)

        assert again is not None and again.version == 3
        assert again.body == first.body

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
        assert asyncio.run(table.all_documents()) == []

    def test_a_given_body_is_attached_without_reading_the_path(self, tasks, worktree):
        """A plan's file is outside the worktree; the document holds its text."""
        attached = attach_verification(
            InMemoryTaskAttachmentTable(),
            worktree,
            path="/Users/dev/.claude/plans/a.md",
            name="plan",
            title="Plan",
            body="# Plan\n\nDo the thing.\n",
        )

        assert attached is not None
        assert attached.body == "# Plan\n\nDo the thing.\n"
