"""The voice port: speech to text and text to speech for **Voice mode**.

The routes speak to a :class:`Voice`; the composition root gives them
:class:`OpenAIVoice`, and a test gives them a fake. The calls block, so a
route runs them in a thread.
"""

from typing import Protocol

from mael_domain.integrations import openai
from mael_domain.integrations.errors import IntegrationError

#: How long one transcription or one synthesis may take.
VOICE_TIMEOUT_SECS = 60.0
#: The file extension OpenAI reads a clip's format from, per content type.
#: An unknown type is sent as WebM, the format most browsers record.
AUDIO_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
}


class VoiceUnavailable(Exception):
    """No speech provider is set up: no OpenAI key resolves."""


class VoiceRefused(Exception):
    """The speech provider refused the request, or could not be reached."""


class Voice(Protocol):
    """Speech to text and text to speech."""

    def available(self) -> bool:
        """Whether a provider is set up, so the other two calls can work."""
        ...

    def transcribe(self, audio: bytes, content_type: str) -> str:
        """The text of one recorded clip, in ``content_type``'s format.

        Raises:
            VoiceUnavailable: No provider is set up.
            VoiceRefused: The provider refused, or could not be reached.
        """
        ...

    def speak(self, text: str) -> bytes:
        """``text`` read aloud, as ``audio/mpeg`` bytes.

        Raises:
            VoiceUnavailable: No provider is set up.
            VoiceRefused: The provider refused, or could not be reached.
        """
        ...


class OpenAIVoice:
    """:class:`Voice` through OpenAI, with the key ``openai.api_key`` resolves.

    The key is read at each call, so a key added to the config works without
    a restart.
    """

    def available(self) -> bool:
        return bool(openai.get_openai_api_key())

    def _key(self) -> str:
        key = openai.get_openai_api_key()
        if not key:
            raise VoiceUnavailable("No OpenAI key: set openai.api_key")
        return key

    def transcribe(self, audio: bytes, content_type: str) -> str:
        key = self._key()
        if content_type not in AUDIO_EXTENSIONS:
            content_type = "audio/webm"
        filename = f"clip.{AUDIO_EXTENSIONS[content_type]}"
        try:
            return openai.transcribe(
                key, audio, filename, content_type, timeout=VOICE_TIMEOUT_SECS
            )
        except (IntegrationError, OSError, KeyError, ValueError) as exc:
            raise VoiceRefused(f"OpenAI transcription failed: {exc}") from exc

    def speak(self, text: str) -> bytes:
        key = self._key()
        try:
            return openai.speak(key, text, timeout=VOICE_TIMEOUT_SECS)
        except (IntegrationError, OSError) as exc:
            raise VoiceRefused(f"OpenAI speech failed: {exc}") from exc
