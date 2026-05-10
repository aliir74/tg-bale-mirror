"""Tests for the Config settings module."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import Config


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "abcdef0123456789abcdef0123456789")
    monkeypatch.setenv("TG_SOURCE_CHANNEL", "-1001234567890")
    monkeypatch.setenv("BALE_BOT_TOKEN", "999:test_token")
    monkeypatch.setenv("BALE_CHANNEL_ID", "@my_bale_channel")


def test_loads_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # avoid picking up a real .env
    _base_env(monkeypatch)

    cfg = Config()

    assert cfg.tg_api_id == 12345
    assert cfg.tg_api_hash == "abcdef0123456789abcdef0123456789"
    assert cfg.tg_source_channel == -1001234567890
    assert cfg.bale_bot_token == "999:test_token"
    assert cfg.bale_channel_id == "@my_bale_channel"
    assert cfg.tg_session_name == "tg-bale-mirror"
    assert cfg.log_level == "INFO"


def test_username_source_channel(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("TG_SOURCE_CHANNEL", "@some_channel")

    cfg = Config()

    assert cfg.tg_source_channel == "@some_channel"


def test_numeric_bale_channel(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("BALE_CHANNEL_ID", "987654321")

    cfg = Config()

    assert cfg.bale_channel_id == 987654321


def test_missing_required_raises(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TG_API_ID", "12345")
    # deliberately omit other required fields

    with pytest.raises(ValidationError):
        Config()


def test_tg_bot_token_defaults_to_none(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)

    cfg = Config()

    assert cfg.tg_bot_token is None


def test_tg_bot_token_loads_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("TG_BOT_TOKEN", "987654:bot_token_value")

    cfg = Config()

    assert cfg.tg_bot_token == "987654:bot_token_value"
