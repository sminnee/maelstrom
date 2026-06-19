"""Tests for the shared secret-resolution chain in integrations._auth."""

from pathlib import Path
from unittest.mock import patch

from maelstrom.context import GlobalConfig
from maelstrom.integrations._auth import resolve_secret


def _config(**kwargs) -> GlobalConfig:
    return GlobalConfig(projects_dir=Path("/tmp"), **kwargs)


class TestResolveSecret:
    def test_env_var_hit(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MY_KEY", "from-env")
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "from-env"

    def test_env_file_walk_hit(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MY_KEY", raising=False)
        (tmp_path / ".env").write_text("MY_KEY=from-dotenv\n")
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "from-dotenv"

    def test_config_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # no .env here
        cfg = _config(linear_api_key="from-config")
        with patch("maelstrom.integrations._auth.load_global_config", return_value=cfg):
            assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "from-config"

    def test_none_when_absent(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        cfg = _config(linear_api_key=None)
        with patch("maelstrom.integrations._auth.load_global_config", return_value=cfg):
            assert resolve_secret("MY_KEY", config_attr="linear_api_key") is None
