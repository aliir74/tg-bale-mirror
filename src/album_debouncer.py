"""Debounce buffer for Telegram album messages.

Pyrogram delivers the items of an album as separate ``Message`` updates,
all sharing the same ``media_group_id``. We buffer by group id and flush
after ``delay`` seconds of silence. Lone messages (no group id) bypass
the buffer and flush immediately.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

FlushCallback = Callable[[list[Any]], Awaitable[None]]


class AlbumDebouncer:
    def __init__(self, on_flush: FlushCallback, delay: float = 1.5) -> None:
        self._on_flush = on_flush
        self._delay = delay
        self._buffers: dict[str, list[Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def add(self, message: Any) -> None:
        group_id = getattr(message, "media_group_id", None)
        if group_id is None:
            await self._safe_flush([message])
            return

        gid = str(group_id)
        async with self._lock:
            self._buffers.setdefault(gid, []).append(message)
            prev = self._tasks.get(gid)
            if prev is not None and not prev.done():
                prev.cancel()
            self._tasks[gid] = asyncio.create_task(self._flush_after_delay(gid))

    async def _flush_after_delay(self, gid: str) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return
        async with self._lock:
            msgs = self._buffers.pop(gid, [])
            self._tasks.pop(gid, None)
        if msgs:
            await self._safe_flush(msgs)

    async def flush_all(self) -> None:
        """Cancel pending timers and deliver everything buffered."""
        async with self._lock:
            tasks = list(self._tasks.values())
            buffers = self._buffers.copy()
            self._tasks.clear()
            self._buffers.clear()
        for t in tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for msgs in buffers.values():
            if msgs:
                await self._safe_flush(msgs)

    async def _safe_flush(self, msgs: list[Any]) -> None:
        try:
            await self._on_flush(msgs)
        except Exception:
            logger.exception("album flush callback failed (n=%d)", len(msgs))
