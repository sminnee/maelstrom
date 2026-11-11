"""Tests for :mod:`mael_domain.attachments` — the one way an image reaches a task.

The seam is the module's public functions. ``tests/test_linear.py`` covers the
same code from its other caller, and that pair is deliberate: these tests pin
what the shared mechanism does, those pin that ``mael linear plan`` still gets
it.
"""

from pathlib import Path

import pytest

from mael_common.image import MAX_BYTES
from mael_domain import attachments
from mael_domain.state_db import paths

PNG = b"\x89PNG\r\n\x1a\n\x00\x00fakepngdata"
NOT_AN_IMAGE = b"just some text, not an image at all"


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    """Point the attachments directory at a temp dir and hand back its root."""
    root = tmp_path / "attachments"
    monkeypatch.setattr("mael_domain.state_db.paths.attachments_root", lambda: root)
    return root


class TestSaveAttachment:
    def test_writes_the_file_and_returns_a_portable_token(self, tasks):
        token = attachments.save_attachment("proj", "t1", PNG, name="shot.png")

        assert token == "{{MAEL_TASK_DIR}}/images/t1/shot.png"
        written = tasks / "proj" / "images" / "t1" / "shot.png"
        assert written.read_bytes() == PNG

    def test_names_an_unnamed_paste(self, tasks):
        token = attachments.save_attachment("proj", "t1", PNG)

        assert token == "{{MAEL_TASK_DIR}}/images/t1/image.png"

    def test_a_second_image_of_one_name_does_not_overwrite_the_first(self, tasks):
        """The case the upload path introduces: one request at a time.

        Linear localizes a whole brief in one call, so an in-memory set of used
        names was enough for it. An upload arrives on its own request, so the
        de-dupe has to read what is already on disk.
        """
        other = PNG + b"-a-different-picture"
        first = attachments.save_attachment("proj", "t1", PNG, name="shot.png")
        second = attachments.save_attachment("proj", "t1", other, name="shot.png")

        assert first != second
        directory = tasks / "proj" / "images" / "t1"
        assert (directory / "shot.png").read_bytes() == PNG
        assert (directory / "shot-1.png").read_bytes() == other

    def test_buckets_keep_images_apart(self, tasks):
        one = attachments.save_attachment("proj", "t1", PNG, name="shot.png")
        two = attachments.save_attachment("proj", "agent-ab12", PNG, name="shot.png")

        # Same filename, different bucket: no collision, no de-dupe suffix.
        assert one == "{{MAEL_TASK_DIR}}/images/t1/shot.png"
        assert two == "{{MAEL_TASK_DIR}}/images/agent-ab12/shot.png"


class TestSaveFile:
    def test_an_image_goes_the_image_path(self, tasks):
        kind, token = attachments.save_file("proj", "t1", PNG, name="shot.png")

        assert (kind, token) == ("image", "{{MAEL_TASK_DIR}}/images/t1/shot.png")

    def test_a_json_file_keeps_its_name_and_extension(self, tasks):
        kind, token = attachments.save_file("proj", "t1", b'{"a": 1}', name="spec.JSON")

        assert (kind, token) == ("file", "{{MAEL_TASK_DIR}}/images/t1/spec.json")
        assert (tasks / "proj" / "images" / "t1" / "spec.json").read_bytes() == (
            b'{"a": 1}'
        )

    def test_an_odd_extension_becomes_bin(self, tasks):
        _, token = attachments.save_file("proj", "t1", NOT_AN_IMAGE, name="dump.x$y")

        assert token == "{{MAEL_TASK_DIR}}/images/t1/dump.bin"

    def test_a_stem_that_would_end_a_link_target_is_made_plain(self, tasks):
        _, token = attachments.save_file(
            "proj", "t1", NOT_AN_IMAGE, name="my (big) log.txt"
        )

        assert token == "{{MAEL_TASK_DIR}}/images/t1/my-big-log.txt"

    @pytest.mark.parametrize(
        ("name", "stored"),
        [
            # A path keeps its last segment only.
            ("../../x.txt", "x.txt"),
            ("..", "file.bin"),
            ("README", "README.bin"),
            ("$$$.txt", "file.txt"),
            ("a" * 300 + ".log", "a" * 100 + ".log"),
        ],
    )
    def test_a_name_that_cannot_be_kept_falls_back(self, tasks, name, stored):
        _, token = attachments.save_file("proj", "t1", NOT_AN_IMAGE, name=name)

        assert token == f"{{{{MAEL_TASK_DIR}}}}/images/t1/{stored}"

    def test_a_picked_screenshot_name_is_made_plain_too(self, tasks):
        """An image goes through the same stem rule: a space would end its ref."""
        _, token = attachments.save_file("proj", "t1", PNG, name="Screen Shot 1.png")

        assert token == "{{MAEL_TASK_DIR}}/images/t1/Screen-Shot-1.png"

    def test_a_second_file_of_one_name_is_de_duped(self, tasks):
        attachments.save_file("proj", "t1", b"one", name="note.txt")
        _, token = attachments.save_file("proj", "t1", b"two", name="note.txt")

        assert token == "{{MAEL_TASK_DIR}}/images/t1/note-1.txt"
        assert (tasks / "proj" / "images" / "t1" / "note.txt").read_bytes() == b"one"

    def test_refuses_a_file_over_the_media_cap_and_writes_nothing(self, tasks):
        big = b"x" * (attachments.MAX_MEDIA_BYTES + 1)

        with pytest.raises(ValueError, match="too large"):
            attachments.save_file("proj", "t1", big, name="big.log")
        assert not (tasks / "proj").exists()

    def test_a_file_over_the_image_cap_is_kept(self, tasks):
        """5 MB is the cap for an image only: the agent never sees a file's bytes."""
        kind, _ = attachments.save_file(
            "proj", "t1", b"x" * (MAX_BYTES + 1), name="a.log"
        )

        assert kind == "file"


class TestFileLink:
    def test_the_label_gives_the_name_and_a_human_size(self):
        assert attachments.file_label("spec.json", 412 * 1024) == (
            "File: spec.json, 412 KB"
        )
        assert attachments.file_label("a.txt", 12) == "File: a.txt, 12 B"
        assert attachments.file_label("b.log", 3 * 1024 * 1024) == "File: b.log, 3.0 MB"

    def test_a_bracket_in_the_label_cannot_close_the_link(self):
        link = attachments.markdown_link("File: a](x).txt, 1 B", "T")

        assert link == "[File: a\\]\\(x\\).txt, 1 B](T)"


class TestInlineMediaType:
    @pytest.mark.parametrize("name", ["page.html", "logo.svg", "tool.exe", "noext"])
    def test_a_type_that_can_run_script_or_is_unknown_is_a_download(self, name):
        assert attachments.inline_media_type(Path(name)) is None

    def test_a_json_file_is_inline_as_plain_text(self):
        assert attachments.inline_media_type(Path("spec.json")) == (
            "text/plain; charset=utf-8"
        )

    def test_an_image_is_inline_as_itself(self):
        assert attachments.inline_media_type(Path("shot.PNG")) == "image/png"


class TestMarkdownRef:
    def test_builds_a_markdown_image_ref(self):
        ref = attachments.markdown_ref("shot.png", "{{MAEL_TASK_DIR}}/images/t1/a.png")
        assert ref == "![shot.png]({{MAEL_TASK_DIR}}/images/t1/a.png)"

    def test_alt_text_cannot_close_the_ref_and_name_another_target(self):
        """The alt text is written by an agent or a client, so it is not trusted.

        A `]` that ended the alt would let the rest of the text name any URL,
        and the browser would fetch it: the registry would no longer be the
        only route to what the reader sees.
        """
        ref = attachments.markdown_ref(
            "x](https://evil.example/beacon.png)", "/api/files/ag1-2-shot.png"
        )
        assert ref.endswith("(/api/files/ag1-2-shot.png)")
        assert "https://evil.example" not in ref.split("](")[-1]
        assert ref.count("](") == 1

    def test_a_newline_in_alt_text_cannot_break_out_of_the_ref(self):
        """A ref is one line, so a newline would leave markdown behind it."""
        ref = attachments.markdown_ref("one\ntwo", "/api/files/ag1-2-shot.png")
        assert "\n" not in ref


class TestSizeAndFormatGuards:
    """One paste must not be able to fill the notebook repo."""

    def test_refuses_bytes_over_the_cap(self, tasks):
        too_big = PNG + b"\x00" * MAX_BYTES

        with pytest.raises(ValueError, match="too large"):
            attachments.save_attachment("proj", "t1", too_big, name="shot.png")

        assert not (tasks / "proj" / "images" / "t1").exists()

    def test_refuses_anything_that_is_not_an_image(self, tasks):
        with pytest.raises(ValueError, match="not an image"):
            attachments.save_attachment("proj", "t1", NOT_AN_IMAGE, name="x.png")

        assert not (tasks / "proj" / "images" / "t1").exists()


class TestResolveAttachment:
    """The read side, which serves bytes back to the browser."""

    def test_finds_a_saved_file(self, tasks):
        attachments.save_attachment("proj", "t1", PNG, name="shot.png")

        found = attachments.resolve_attachment("proj", "t1", "shot.png")

        assert found.read_bytes() == PNG

    def test_refuses_a_missing_file(self, tasks):
        with pytest.raises(KeyError):
            attachments.resolve_attachment("proj", "t1", "nope.png")

    @pytest.mark.parametrize(
        "escape",
        ["../../../etc/passwd", "..", "sub/dir.png", "/etc/passwd", ""],
    )
    def test_refuses_a_path_that_escapes_the_bucket(self, tasks, escape):
        """A name is a filename, never a path. The route takes it from a URL.

        Percent-encoded separators are not tested here: aiohttp decodes the
        path before the handler sees it, so ``..%2F`` arrives as ``../`` and is
        the first case. Testing the encoded form would assert against a string
        this function is never handed.
        """
        with pytest.raises(ValueError):
            attachments.resolve_attachment("proj", "t1", escape)


class TestBucketDirGuards:
    """Every segment of the path comes from a client, so every one is checked."""

    @pytest.mark.parametrize("escape", ["../../../etc", "..", "a/b", "/etc", ""])
    def test_refuses_a_project_that_escapes_the_repo(self, tasks, escape):
        with pytest.raises(ValueError):
            attachments.bucket_dir(escape, "t1")

    @pytest.mark.parametrize("escape", ["../../../etc", "..", "a/b", "/etc", ""])
    def test_refuses_a_bucket_that_escapes_the_repo(self, tasks, escape):
        with pytest.raises(ValueError):
            attachments.bucket_dir("proj", escape)

    def test_a_traversing_project_writes_nothing(self, tasks):
        """The guard is in the path builder, so the write path inherits it."""
        with pytest.raises(ValueError):
            attachments.save_attachment(
                "../../../../tmp/pwned", "t1", PNG, name="x.png"
            )


class TestAttachmentUrls:
    """A ref the agent can read becomes a ref the browser can fetch."""

    def test_the_token_form_names_the_given_project(self, tasks):
        markdown = "See ![shot]({{MAEL_TASK_DIR}}/images/t1/shot.png) here."

        assert (
            attachments.attachment_urls(markdown, "proj")
            == "See ![shot](/api/attachments/proj/t1/shot.png) here."
        )

    def test_the_absolute_form_names_the_project_in_the_path(self, tasks):
        """A first prompt names any project's root, not only the agent's own."""
        markdown = (
            f"![a]({tasks}/proj/images/t1/a.png) ![b]({tasks}/other/images/t2/b.png)"
        )

        assert attachments.attachment_urls(markdown, "proj") == (
            "![a](/api/attachments/proj/t1/a.png) ![b](/api/attachments/other/t2/b.png)"
        )

    def test_the_old_notebook_path_maps_to_the_route(self, tasks):
        """A transcript sent before the media moved keeps the old path."""
        old = paths.notebook_path(paths.notebook_root())
        markdown = f"![a]({old}/proj/images/t1/a.png)"

        assert attachments.attachment_urls(markdown, "proj") == (
            "![a](/api/attachments/proj/t1/a.png)"
        )

    def test_a_path_outside_the_attachments_directory_stays_unchanged(
        self, tasks, tmp_path
    ):
        markdown = (
            f"![x]({tmp_path}/elsewhere/images/t1/x.png) "
            f"![y]({tasks}/proj/notes/y.png) ![z](docs/images/z.png)"
        )

        assert attachments.attachment_urls(markdown, "proj") == markdown

    def test_a_path_in_prose_stays_as_written(self, tasks):
        """The reader sees what was sent, not a URL it never wrote."""
        markdown = (
            "The file is at `{{MAEL_TASK_DIR}}/images/t1/x.png` "
            f"and {tasks}/proj/images/t1/x.png."
        )

        assert attachments.attachment_urls(markdown, "proj") == markdown
