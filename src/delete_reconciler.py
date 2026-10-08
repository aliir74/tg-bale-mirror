"""Periodic fallback that finds Telegram deletes no update was pushed for.

Telegram does not promise delete updates (and may not send them to bot
accounts at all), so every ``interval`` seconds this asks Telegram for every
mapped message still inside Bale's 48h delete window. Messages that come back
empty were deleted and go through the same path as a pushed delete.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from src.message_map import MessageMap
from src.tg_listener import OnDeleted

logger = logging.getLogger(__name__)

BATCH_SIZE = 200  # get_messages limit


class DeleteReconciler:
    def __init__(
        self,
        tg_client: Any,
        chat_id: int,
        message_map: MessageMap,
        on_deleted: OnDeleted,
        interval: float,
    ) -> None:
        self._tg = tg_client
        self._chat_id = chat_id
        self._map = message_map
        self._on_deleted = on_deleted
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._interval > 0:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def reconcile(self) -> list[int]:
        """Check mapped messages once; return the ids found deleted."""
        ids = self._map.tg_ids()
        deleted: list[int] = []
        for i in range(0, len(ids), BATCH_SIZE):
            batch = ids[i:i + BATCH_SIZE]
            messages = await self._tg.get_messages(self._chat_id, batch, replies=0)
            gone = [m.id for m in messages if getattr(m, "empty", False)]
            if len(batch) > 1 and len(gone) == len(batch) and not await self._can_see_chat():
                # Every message "missing" plus no channel access means we lost
                # access, not that everything was deleted. Don't wipe Bale.
                logger.warning(
                    "all %d checked messages came back empty and the source "
                    "channel is unreachable; skipping batch", len(batch),
                )
                continue
            deleted.extend(gone)
        if deleted:
            logger.info("reconcile found deleted tg messages %s", deleted)
            await self._on_deleted(deleted)
        return deleted

    async def _can_see_chat(self) -> bool:
        try:
            await self._tg.get_chat(self._chat_id)
        except Exception:  # noqa: BLE001 — any failure means "can't confirm access"
            logger.exception("get_chat failed for %d", self._chat_id)
            return False
        return True

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.reconcile()
            except Exception:  # noqa: BLE001 — keep checking on the next tick
                logger.exception("delete reconcile failed")
