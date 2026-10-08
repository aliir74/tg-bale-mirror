"""Pyrogram listener: new messages go to the album debouncer, deletes to a callback."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pyrogram import filters
from pyrogram.handlers import DeletedMessagesHandler, MessageHandler

from src.album_debouncer import AlbumDebouncer

if TYPE_CHECKING:
    from pyrogram.client import Client
    from pyrogram.types import Message

logger = logging.getLogger(__name__)

OnDeleted = Callable[[list[int]], Awaitable[None]]


class TgListener:
    def __init__(
        self,
        client: Client,
        source_channel: int | str,
        debouncer: AlbumDebouncer,
        on_deleted: OnDeleted | None = None,
    ) -> None:
        self._client = client
        self._source = source_channel
        self._debouncer = debouncer
        self._on_deleted_cb = on_deleted
        self._resolved_id: int | None = None

    async def resolve_source(self) -> int:
        """Resolve usernames to numeric chat ids so handler filters match."""
        if isinstance(self._source, int):
            self._resolved_id = self._source
            return self._source

        chat = await self._client.get_chat(self._source)
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            raise RuntimeError(f"could not resolve source {self._source!r}")
        self._resolved_id = int(chat_id)
        logger.info("resolved %s -> %d", self._source, self._resolved_id)
        return self._resolved_id

    def register(self) -> None:
        if self._resolved_id is None:
            raise RuntimeError("call resolve_source() before register()")
        chat_filter: list[int | str] = [self._resolved_id]
        self._client.add_handler(
            MessageHandler(self._on_message, filters.chat(chat_filter))
        )
        if self._on_deleted_cb is not None:
            self._client.add_handler(
                DeletedMessagesHandler(self._on_deleted, filters.chat(chat_filter))
            )
        logger.info("listener registered for chat %d", self._resolved_id)

    async def _on_message(self, _client: Client, message: Message) -> None:
        try:
            await self._debouncer.add(message)
        except Exception:
            logger.exception("dispatch failed for message %s", getattr(message, "id", "?"))

    async def _on_deleted(self, _client: Client, messages: list[Message]) -> None:
        # The handler fires if *any* message in the batch matches the filter,
        # so keep only ids that really belong to the source channel.
        ids = [
            m.id for m in messages
            if m.chat is not None and m.chat.id == self._resolved_id
        ]
        if not ids or self._on_deleted_cb is None:
            return
        logger.info("source deleted messages %s", ids)
        try:
            await self._on_deleted_cb(ids)
        except Exception:
            logger.exception("delete dispatch failed for %s", ids)
