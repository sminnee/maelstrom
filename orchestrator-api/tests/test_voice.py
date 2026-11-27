"""``OpenAIVoice``: the format it sends, and how it maps OpenAI's failures."""

from unittest.mock import patch

import pytest

from mael_orchestrator.voice import OpenAIVoice, VoiceRefused, VoiceUnavailable

URLOPEN = "mael_domain.integrations._http.urllib.request.urlopen"
KEY = "mael_domain.integrations.openai.get_openai_api_key"


def _replying(mock_urlopen, body: bytes):
    mock_urlopen.return_value.__enter__.return_value.read.return_value = body


@patch(KEY, return_value="sk-test")
@patch(URLOPEN)
def test_an_unknown_clip_type_is_sent_as_webm(mock_urlopen, _key):
    _replying(mock_urlopen, b'{"text": " ship it "}')

    text = OpenAIVoice().transcribe(b"clip", "audio/x-unknown")

    assert text == "ship it"
    sent = mock_urlopen.call_args[0][0].data
    assert b'filename="clip.webm"' in sent
    assert b"Content-Type: audio/webm" in sent


@patch(KEY, return_value="sk-test")
@patch(URLOPEN)
def test_a_reply_with_no_text_is_refused(mock_urlopen, _key):
    _replying(mock_urlopen, b"{}")

    with pytest.raises(VoiceRefused):
        OpenAIVoice().transcribe(b"clip", "audio/mp4")


@patch(KEY, return_value=None)
def test_no_key_is_unavailable(_key):
    voice = OpenAIVoice()

    assert voice.available() is False
    with pytest.raises(VoiceUnavailable):
        voice.speak("hello")
