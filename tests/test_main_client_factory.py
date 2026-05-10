"""Tests for build_tg_client_kwargs — the bot-vs-userbot mode selector."""

from __future__ import annotations

import logging

import pytest

from src.config import Config
from src.main import build_tg_client_kwargs


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TG_API_ID", "12345")
    monkeypatch.setenv("TG_API_HASH", "abcdef0123456789abcdef0123456789")
    monkeypatch.setenv("TG_SOURCE_CHANNEL", "-1001234567890")
    monkeypatch.setenv("BALE_BOT_TOKEN", "999:test_token")
    monkeypatch.setenv("BALE_CHANNEL_ID", "@my_bale_channel")


def test_bot_mode_kwargs(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("TG_BOT_TOKEN", "111:bot_token")

    cfg = Config()
    kwargs = build_tg_client_kwargs(cfg)

    assert kwargs["bot_token"] == "111:bot_token"
    assert kwargs["in_memory"] is True
    assert "session_string" not in kwargs
    assert kwargs["api_id"] == 12345
    assert kwargs["api_hash"] == "abcdef0123456789abcdef0123456789"
    assert kwargs["name"] == "tg-bale-mirror"


def test_userbot_session_string_mode_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("TG_SESSION_STRING", "deadbeef==")

    cfg = Config()
    kwargs = build_tg_client_kwargs(cfg)

    assert kwargs["session_string"] == "deadbeef=="
    assert kwargs["in_memory"] is True
    assert "bot_token" not in kwargs


def test_userbot_session_file_mode_kwargs(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)

    cfg = Config()
    kwargs = build_tg_client_kwargs(cfg)

    assert "bot_token" not in kwargs
    assert "session_string" not in kwargs
    assert "in_memory" not in kwargs
    assert kwargs["name"] == "tg-bale-mirror"


def test_bot_wins_when_both_set_and_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.chdir(tmp_path)
    _base_env(monkeypatch)
    monkeypatch.setenv("TG_BOT_TOKEN", "111:bot_token")
    monkeypatch.setenv("TG_SESSION_STRING", "deadbeef==")

    cfg = Config()
    with caplog.at_level(logging.WARNING):
        kwargs = build_tg_client_kwargs(cfg)

    assert kwargs["bot_token"] == "111:bot_token"
    assert "session_string" not in kwargs
    assert any(
        "TG_BOT_TOKEN" in rec.message and "TG_SESSION_STRING" in rec.message
        for rec in caplog.records
    )
