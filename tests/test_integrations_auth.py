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

    def test_value_with_inner_quote(self, monkeypatch, tmp_path):
        # The old regex truncated at the inner quote; parse_env_text keeps it.
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_KEY=\"ab'cd\"\n")
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "ab'cd"

    def test_unquoted_value_with_literal_quote(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_KEY=ab'cd\n")
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "ab'cd"

    def test_value_with_hash_is_kept(self, monkeypatch, tmp_path):
        # Single '#' with no leading double-space is part of the value.
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_KEY=abc#notacomment\n")
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "abc#notacomment"

    def test_double_space_hash_is_stripped(self, monkeypatch, tmp_path):
        # A double-space + '#' trailing comment is stripped by parse_env_text.
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("MY_KEY=abc  # trailing\n")
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "abc"

    def test_walks_past_closer_env_to_parent(self, monkeypatch, tmp_path):
        # Regression for the `break` bug: the closer .env lacks the key, so the
        # walk must continue up to the parent .env that defines it.
        monkeypatch.delenv("MY_KEY", raising=False)
        (tmp_path / ".env").write_text("MY_KEY=from-parent\n")
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / ".env").write_text("OTHER_KEY=irrelevant\n")
        sub = tmp_path / "a" / "b"
        sub.mkdir()
        monkeypatch.chdir(sub)
        assert resolve_secret("MY_KEY", config_attr="linear_api_key") == "from-parent"

    def test_export_prefix_not_supported(self, monkeypatch, tmp_path):
        # `export KEY=...` is deliberately out of scope: parse_env_text keys the
        # entry under "export MY_KEY", so MY_KEY does not resolve and we fall
        # through to the config fallback.
        monkeypatch.delenv("MY_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("export MY_KEY=from-dotenv\n")
        cfg = _config(linear_api_key=None)
        with patch("maelstrom.integrations._auth.load_global_config", return_value=cfg):
            assert resolve_secret("MY_KEY", config_attr="linear_api_key") is None
