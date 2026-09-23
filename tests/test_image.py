"""Tests for :mod:`maelstrom.image`: the bytes decide what an image is."""

import pytest

from maelstrom import image

PNG = b"\x89PNG\r\n\x1a\n\x00\x00fakepngdata"
JPG = b"\xff\xd8\xff\x00fakejpgdata"
GIF = b"GIF89a\x00fakegifdata"
NOT_AN_IMAGE = b"just some text, not an image at all"


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
        assert image.image_extension("shot.txt", data) == expected

    def test_falls_back_to_the_name(self):
        assert image.image_extension("diagram.svg", NOT_AN_IMAGE) == ".svg"

    def test_falls_back_to_bin(self):
        assert image.image_extension("", NOT_AN_IMAGE) == ".bin"


class TestIsImage:
    def test_accepts_a_known_format(self):
        assert image.is_image(PNG)

    def test_refuses_anything_else(self):
        assert not image.is_image(NOT_AN_IMAGE)
