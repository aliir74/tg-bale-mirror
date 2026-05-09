"""Tests for TgListener."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.album_debouncer import AlbumDebouncer
from src.tg_listener import TgListener


@dataclass
class FakeChat:
    id: int


async def test_resolve_source_passes_through_int() -> None:
    client = MagicMock()
    client.get_chat = AsyncMock()
    deb = AlbumDebouncer(on_flush=AsyncMock(), delay=0.01)
    listener = TgListener(client, -1001234567890, deb)

    chat_id = await listener.resolve_source()

    assert chat_id == -1001234567890
    client.get_chat.assert_not_called()


async def test_resolve_source_calls_get_chat_for_username() -> None:
    client = MagicMock()
    client.get_chat = AsyncMock(return_value=FakeChat(id=-1009876543210))
    deb = AlbumDebouncer(on_flush=AsyncMock(), delay=0.01)
    listener = TgListener(client, "@some_channel", deb)

    chat_id = await listener.resolve_source()

    assert chat_id == -1009876543210
    client.get_chat.assert_awaited_once_with("@some_channel")


async def test_register_requires_resolution() -> None:
    client = MagicMock()
    deb = AlbumDebouncer(on_flush=AsyncMock(), delay=0.01)
    listener = TgListener(client, "@x", deb)

    with pytest.raises(RuntimeError, match="resolve_source"):
        listener.register()


async def test_register_adds_handler_with_chat_filter() -> None:
    client = MagicMock()
    client.get_chat = AsyncMock(return_value=FakeChat(id=42))
    deb = AlbumDebouncer(on_flush=AsyncMock(), delay=0.01)
    listener = TgListener(client, "@x", deb)

    await listener.resolve_source()
    listener.register()

    client.add_handler.assert_called_once()


async def test_on_message_forwards_to_debouncer() -> None:
    client = MagicMock()
    flushed: list[list[object]] = []

    async def on_flush(msgs):
        flushed.append(list(msgs))

    deb = AlbumDebouncer(on_flush=on_flush, delay=0.01)
    listener = TgListener(client, 1, deb)

    fake_msg = MagicMock(spec=["id", "media_group_id"])
    fake_msg.id = 5
    fake_msg.media_group_id = None

    await listener._on_message(client, fake_msg)

    assert flushed == [[fake_msg]]
