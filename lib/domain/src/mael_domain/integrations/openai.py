"""OpenAI chat-completions integration for maelstrom."""

from ._auth import resolve_secret
from ._http import request_json

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


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
