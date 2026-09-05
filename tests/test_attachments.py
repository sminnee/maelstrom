"""Tests for :mod:`maelstrom.attachments` — the one way an image reaches a task.

The seam is the module's public functions. ``tests/test_linear.py`` covers the
same code from its other caller, and that pair is deliberate: these tests pin
what the shared mechanism does, those pin that ``mael linear plan`` still gets
it.
"""

import pytest

from maelstrom import attachments

PNG = b"\x89PNG\r\n\x1a\n\x00\x00fakepngdata"
JPG = b"\xff\xd8\xff\x00fakejpgdata"
GIF = b"GIF89a\x00fakegifdata"
NOT_AN_IMAGE = b"just some text, not an image at all"


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    """Point the task repo at a temp dir and hand back its root."""
    root = tmp_path / "tasks"
    monkeypatch.setattr("maelstrom.task_store.tasks_root", lambda: root)
    return root


class TestImageExtension:
    """The extension comes from the bytes, then the name, then nothing."""

    @pytest.mark.parametrize(
        "data,expected",
        [
            (PNG, ".png"),
            (JPG, ".jpg"),
            (GIF, ".gif"),
            (b"RIFF\x00\x00\x00\x00WEBP", ".webp"),
        ],
    )
    def test_sniffs_magic_bytes(self, data, expected):
        # The name says otherwise on purpose: the bytes win.
        assert attachments.image_extension("shot.txt", data) == expected

    def test_falls_back_to_the_name(self):
        assert attachments.image_extension("diagram.svg", NOT_AN_IMAGE) == ".svg"

    def test_falls_back_to_bin(self):
        assert attachments.image_extension("", NOT_AN_IMAGE) == ".bin"


class TestIsImage:
    def test_accepts_a_known_format(self):
        assert attachments.is_image(PNG)

    def test_refuses_anything_else(self):
        assert not attachments.is_image(NOT_AN_IMAGE)


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


class TestMarkdownRef:
    def test_builds_a_markdown_image_ref(self):
        ref = attachments.markdown_ref("shot.png", "{{MAEL_TASK_DIR}}/images/t1/a.png")
        assert ref == "![shot.png]({{MAEL_TASK_DIR}}/images/t1/a.png)"


class TestSizeAndFormatGuards:
    """One paste must not be able to fill the notebook repo."""

    def test_refuses_bytes_over_the_cap(self, tasks):
        too_big = PNG + b"\x00" * attachments.MAX_BYTES

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
            attachments.save_attachment("../../../../tmp/pwned", "t1", PNG, name="x.png")
