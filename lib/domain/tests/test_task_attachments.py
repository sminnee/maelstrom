"""The Task document store's contract, the store rules, and the media copy.

The table contract and the store rules run against both backends. ``prepare``
and the media copy run against a real temp worktree and a temp task repo,
because what they do is read and copy files.
"""

import asyncio
import json
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
from mael_domain.task_documents import NEEDS_BODY, DocumentRef, Presented, ReviewGroup

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

    def test_set_review_status_changes_one_version_and_keeps_the_rest(self, table):
        """A version's text stays; its review status and group survive a read."""
        grouped = replace(
            version(doc(), 2),
            agent_id="agent-1",
            group_id="g1",
            group_title="Task set",
            group_position=1,
        )

        async def scenario():
            await table.put(replace(doc(), role="tasks"))
            await table.add_version(version(doc(), 1))
            await table.add_version(grouped)
            await table.set_review_status(grouped.id, "approved")
            await table.set_review_status("no-such@v1", "approved")
            return await table.get(doc().id), await table.versions(doc().id)

        row, (first, second) = asyncio.run(scenario())

        assert row is not None and row.role == "tasks"
        assert first == version(doc(), 1)
        assert second == replace(grouped, review_status="approved")

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

    def test_a_version_carries_its_agent_and_group(self):
        table = InMemoryTaskAttachmentTable()
        group = docs.ReviewGroup("g1", "Task set", 2)

        document, first = add(table, role="tasks", agent_id="a1", group=group)

        assert document.role == "tasks"
        fields = (
            first.agent_id,
            first.group_id,
            first.group_title,
            first.group_position,
        )
        assert fields == ("a1", "g1", "Task set", 2)

    def test_the_same_text_presented_again_moves_its_agent_and_group(self):
        """No new version, and the status stays: only the server writes it."""
        table = InMemoryTaskAttachmentTable()
        add(table, agent_id="a1", group=docs.ReviewGroup("g1", "Old", 0))

        _, again = add(
            table,
            agent_id="a2",
            group=docs.ReviewGroup("g2", "New", 1),
            review_status="awaiting-review",
        )

        assert (again.version, again.review_status) == (1, "draft")
        assert (again.agent_id, again.group_id, again.group_position) == ("a2", "g2", 1)
        assert asyncio.run(table.versions(again.attachment_id)) == [again]

    def test_a_later_role_replaces_the_document_s_role(self):
        table = InMemoryTaskAttachmentTable()
        first, _ = add(table)

        second, _ = add(table, role="review")
        third, _ = add(table, body="v2")

        assert (first.role, second.role, third.role) == ("other", "review", "review")

    def test_an_unknown_role_is_refused(self):
        with pytest.raises(docs.InvalidDocument):
            add(InMemoryTaskAttachmentTable(), role="memo")

    def test_set_status_writes_the_latest_version_only(self):
        table = InMemoryTaskAttachmentTable()
        add(table)
        document, _ = add(table, body="v2")

        written = asyncio.run(docs.set_status(table, document, "approved"))
        versions = asyncio.run(table.versions(document.id))

        assert written is not None and written.review_status == "approved"
        assert [v.review_status for v in versions] == ["draft", "approved"]

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


#: Shared with the UI's heading-id test, so a link's fragment finds its heading.
SLUG_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "heading-slugs.json").read_text()
)["cases"]


class TestFragments:
    @pytest.mark.parametrize(
        "case", SLUG_CASES, ids=[case["name"] for case in SLUG_CASES]
    )
    def test_markdown_fragments_are_github_heading_slugs(self, case):
        assert docs.fragments("markdown", case["markdown"]) == case["slugs"]

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
    root = tmp_path / "attachments"
    monkeypatch.setattr("mael_domain.state_db.paths.attachments_root", lambda: root)
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


def bucket_names(tasks) -> list[str]:
    bucket = tasks / "northwind" / "images" / "2026-09-22.1"
    return sorted(p.name for p in bucket.iterdir()) if bucket.exists() else []


def write_verification(worktree, body: str) -> None:
    (worktree / ".drafts" / "verification.md").write_text(body)


def digest_of(worktree, body: str | None = None) -> str:
    prepared = docs.prepare(str(worktree), ".drafts/verification.md", body)
    assert prepared is not None
    return prepared.digest


def verification_text(worktree) -> str:
    return (worktree / ".drafts" / "verification.md").read_text()


def attached(worktree, body: str | None = None) -> str:
    """The verification file's text with its media copied into the bucket."""
    source = body if body is not None else verification_text(worktree)
    return docs.with_attached_media(source, "northwind", "2026-09-22.1", str(worktree))


class TestPrepare:
    """The digest takes the text and the bytes of each media file it names."""

    def test_the_same_file_again_has_the_same_digest(self, worktree):
        assert digest_of(worktree) == digest_of(worktree)

    def test_a_recording_made_again_under_the_same_name_changes_the_digest(
        self, worktree
    ):
        """The markdown did not change; the evidence did."""
        before = digest_of(worktree)
        (worktree / "docs" / "flow.webm").write_bytes(WEBM + b", recorded again")

        assert digest_of(worktree) != before

    def test_a_media_file_that_appears_later_changes_the_digest(self, worktree):
        write_verification(worktree, "![n](docs/new.png)\n")
        before = digest_of(worktree)
        (worktree / "docs" / "new.png").write_bytes(PNG)

        assert digest_of(worktree) != before

    def test_a_document_that_cannot_be_read_is_none(self, worktree):
        assert docs.prepare(str(worktree), ".drafts/missing.md") is None

    def test_a_given_body_is_used_without_reading_the_path(self, worktree):
        """A plan's file is outside the worktree; the document holds its text."""
        prepared = docs.prepare(
            str(worktree), "/Users/dev/.claude/plans/a.md", "# Plan\n"
        )

        assert prepared is not None and prepared.source == "# Plan\n"


class TestWithAttachedMedia:
    def test_media_lands_in_the_bucket_and_the_body_carries_tokens(
        self, tasks, worktree
    ):
        body = attached(worktree)

        assert body == (
            "# Login flow\n\n"
            f"![The dashboard]({{{{MAEL_TASK_DIR}}}}/images/2026-09-22.1/{SHOT})\n\n"
            f"![The flow]({{{{MAEL_TASK_DIR}}}}/images/2026-09-22.1/{FLOW})\n"
        )
        bucket = tasks / "northwind" / "images" / "2026-09-22.1"
        assert (bucket / SHOT).read_bytes() == PNG
        assert (bucket / FLOW).read_bytes() == WEBM

    def test_an_unchanged_file_is_stored_once(self, tasks, worktree):
        attached(worktree)
        attached(worktree, "Now with one picture.\n\n![The dashboard](docs/shot.png)\n")

        assert bucket_names(tasks) == [FLOW, SHOT]

    def test_two_files_of_one_name_are_each_stored_once(self, tasks, worktree):
        """Playwright names every recording ``video.webm``."""
        for name, data in (("a", WEBM + b"-a"), ("b", WEBM + b"-b")):
            (worktree / name).mkdir()
            (worktree / name / "video.webm").write_bytes(data)
        attached(worktree, "![a](a/video.webm) ![b](b/video.webm)\n")
        first = bucket_names(tasks)

        attached(worktree, "Again. ![a](a/video.webm) ![b](b/video.webm)\n")

        assert len(first) == 2
        assert bucket_names(tasks) == first

    @pytest.mark.parametrize("ext", [".mp4", ".mov"])
    def test_an_iso_media_file_keeps_its_own_extension(self, tasks, worktree, ext):
        (worktree / "docs" / f"clip{ext}").write_bytes(MP4)

        attached(worktree, f"![c](docs/clip{ext})\n")

        [name] = bucket_names(tasks)
        assert name.startswith("clip-") and name.endswith(ext)

    def test_a_file_that_is_not_media_is_refused(self, tasks, worktree):
        (worktree / "docs" / "notes.txt").write_text("just text")

        body = attached(worktree, "![n](docs/notes.txt)\n")

        assert body == "_`docs/notes.txt` could not be shown._\n"
        assert not (tasks / "northwind").exists()

    def test_an_iso_image_is_not_taken_for_a_video(self, tasks, worktree):
        """HEIC shares the ``ftyp`` box with MP4, and no ``<video>`` plays it."""
        (worktree / "docs" / "photo.heic").write_bytes(HEIC)

        body = attached(worktree, "![p](docs/photo.heic)\n")

        assert body == "_`docs/photo.heic` could not be shown._\n"

    def test_a_file_over_the_cap_is_refused(self, tasks, worktree, monkeypatch):
        monkeypatch.setattr(attachments, "MAX_MEDIA_BYTES", len(WEBM) - 1)
        assert len(PNG) < len(WEBM)

        body = attached(worktree)

        assert body == (
            "# Login flow\n\n"
            f"![The dashboard]({{{{MAEL_TASK_DIR}}}}/images/2026-09-22.1/{SHOT})\n\n"
            "_`docs/flow.webm` could not be shown._\n"
        )

    def test_a_path_outside_the_worktree_is_refused(self, tasks, worktree):
        (worktree.parent / "secret.png").write_bytes(PNG)

        body = attached(worktree, "![s](../secret.png)\n")

        assert body == "_`../secret.png` could not be shown._\n"

    def test_without_media_makes_every_ref_prose(self, worktree):
        assert docs.without_media(verification_text(worktree)) == (
            "# Login flow\n\n_`docs/shot.png` could not be shown._\n\n"
            "_`docs/flow.webm` could not be shown._\n"
        )


def presented(**over) -> Presented:
    fields = {
        "project": "northwind",
        "task_id": "2026-09-22.1",
        "name": ".drafts/verification.md",
        "role": "verification",
        "title": "Login flow",
        "status": "awaiting-review",
        "world_version": 1,
        "agent_id": "cobalt:ag1",
    }
    fields.update(over)
    return Presented(**fields)


def store(table, p: Presented, *, replay=False, verdict=False, now=NOW):
    return asyncio.run(
        docs.store_presented(table, p, replay=replay, verdict=verdict, now=now)
    )


def with_body(p: Presented, body: str = "# Login flow\n") -> Presented:
    return replace(p, body=body, digest=f"digest of {body}")


class TestStorePresented:
    """The table in ``docs/dev/orchestrator-server.md``, "Storing a Task document"."""

    def test_a_new_document_asks_for_its_body_then_is_version_one(self, table):
        assert store(table, presented()) is NEEDS_BODY

        row, version = store(table, with_body(presented()))

        assert (row.role, version.version) == ("verification", 1)
        assert (version.review_status, version.agent_id) == (
            "awaiting-review",
            "cobalt:ag1",
        )

    def test_a_new_presentation_with_a_new_body_is_the_next_version(self, table):
        store(table, with_body(presented()))
        again = presented(world_version=2)

        assert store(table, replace(again, digest="another")) is NEEDS_BODY
        _, version = store(table, with_body(again, "# Changed\n"), now=LATER)

        assert (version.version, version.body) == (2, "# Changed\n")

    def test_the_same_digest_again_needs_no_body_and_adds_no_version(self, table):
        store(table, with_body(presented()))
        group = ReviewGroup("g1", "The set", 2)
        again = replace(
            with_body(presented(world_version=2, agent_id="cobalt:ag2", group=group)),
            body=None,
        )

        _, version = store(table, again)

        assert version.version == 1
        assert (version.agent_id, version.group_id) == ("cobalt:ag2", "g1")

    def test_a_status_change_writes_the_status_on_the_latest(self, table):
        store(table, with_body(presented()))

        _, version = store(table, presented(status="approved"))

        assert (version.version, version.review_status) == (1, "approved")

    def test_a_replay_keeps_the_latest_and_its_status(self, table):
        store(table, with_body(presented()))

        _, version = store(
            table, presented(world_version=5, status="draft"), replay=True
        )

        assert (version.version, version.review_status) == (1, "awaiting-review")

    def test_a_replayed_plan_the_store_missed_asks_for_its_body(self, table):
        plan = presented(name="plan", role="plan", text_in_event=True)
        store(table, with_body(plan))

        held = store(table, with_body(plan), replay=True)
        missed = store(table, replace(plan, digest="a later plan"), replay=True)

        assert held is not NEEDS_BODY and held[1].version == 1
        assert missed is NEEDS_BODY

    def test_a_verdict_on_text_the_store_does_not_hold_writes_nothing(self, table):
        store(table, with_body(presented()))

        settled = store(
            table, presented(world_version=2, status="approved"), verdict=True
        )

        assert settled is None
        [row] = asyncio.run(table.all_documents())
        [version] = asyncio.run(table.versions(row.id))
        assert version.review_status == "awaiting-review"

    def test_an_unnumbered_presentation_is_told_new_by_its_digest(self, table):
        """A delegated agent's first presentation numbers from 1."""
        store(table, with_body(presented(status="approved")))
        store(table, with_body(presented(world_version=2), "# Second\n"))
        unnumbered = presented(world_version=None, agent_id="cobalt:ag2")

        same = store(table, replace(with_body(unnumbered, "# Second\n"), body=None))
        assert store(table, replace(unnumbered, digest="another")) is NEEDS_BODY
        _, new = store(table, with_body(unnumbered, "# Third\n"))

        assert same is not NEEDS_BODY and same[1].version == 2
        assert (new.version, new.review_status) == (3, "awaiting-review")

    def test_a_verdict_on_an_unnumbered_document_writes_nothing(self, table):
        store(table, with_body(presented()))

        settled = store(
            table, presented(world_version=None, status="approved"), verdict=True
        )

        assert settled is None

    def test_a_stored_source_adds_no_version(self, table):
        assert store(table, presented(has_path=False)) is None

    def test_the_wire_form_reads_back(self):
        p = with_body(presented(group=ReviewGroup("g1", "The set", 2)))

        assert Presented.from_json(p.to_json()) == p

    @pytest.mark.parametrize(
        "change",
        [
            {"name": None},
            {"status": "None"},
            {"hasPath": "false"},
            {"worldVersion": "one"},
        ],
    )
    def test_a_wire_form_of_the_wrong_shape_is_refused(self, change):
        wire = {**presented().to_json(), **change}
        if change.get("name", "") is None:
            del wire["name"]

        with pytest.raises(docs.InvalidDocument):
            Presented.from_json(wire)
