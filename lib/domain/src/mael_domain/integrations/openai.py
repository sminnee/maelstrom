"""OpenAI integration for maelstrom: chat completions, transcription, and speech."""

import json

from ._auth import resolve_secret
from ._http import multipart_body, request_bytes, request_json

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
#: The model that turns the user's speech into a message.
TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
#: The model, and its voice, that reads an agent's reply aloud.
SPEECH_MODEL = "gpt-4o-mini-tts"
SPEECH_VOICE = "alloy"


def get_openai_api_key() -> str | None:
    """The OpenAI key from env var, ``.env`` file, or global config, else ``None``."""
    return resolve_secret("OPENAI_API_KEY", config_attr="openai_api_key")


def chat_complete(
    api_key: str, *, model: str, system: str, prompt: str, timeout: float
) -> str:
    """One system + user exchange; returns the reply's text, stripped.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
    """
    reply = request_json(
        OPENAI_CHAT_URL,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=timeout,
    )
    return reply["choices"][0]["message"]["content"].strip()


def transcribe(
    api_key: str, audio: bytes, filename: str, content_type: str, *, timeout: float
) -> str:
    """The text of one recorded clip, stripped.

    ``filename``'s extension tells OpenAI the audio format.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
    """
    body, multipart_type = multipart_body(
        {"model": TRANSCRIBE_MODEL, "response_format": "json"},
        {"file": (filename, audio, content_type)},
    )
    reply = request_bytes(
        OPENAI_TRANSCRIPTION_URL,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": multipart_type},
        raw_body=body,
        timeout=timeout,
    )
    return json.loads(reply)["text"].strip()


def speak(api_key: str, text: str, *, timeout: float) -> bytes:
    """``text`` read aloud, as MP3 bytes.

    Raises:
        IntegrationHTTPError: On an HTTP error, with the response body inlined.
    """
    return request_bytes(
        OPENAI_SPEECH_URL,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
        json_body={
            "model": SPEECH_MODEL,
            "voice": SPEECH_VOICE,
            "input": text,
            "response_format": "mp3",
        },
        timeout=timeout,
    )
