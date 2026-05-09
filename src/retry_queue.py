"""Persistent JSON-on-disk retry queue for failed Bale sends.

Items can be text messages, single-media uploads, or albums. The queue is
flushed periodically by a background task; failures leave items in place
so the next tick retries them. Items older than ``MAX_AGE_HOURS`` are
pruned on load.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, TypedDict

from src.bale_client import BaleClient

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_FILE = Path(".bale_retry_queue")
DEFAULT_RETRY_INTERVAL_SECONDS = 300
MAX_AGE_HOURS = 24


class TextItem(TypedDict):
    kind: Literal["text"]
    text: str
    parse_mode: str | None
    queued_at: str


class MediaItem(TypedDict):
    kind: Literal["media"]
    type: Literal["photo", "video", "document"]
    path: str
    caption: str | None
    queued_at: str


class AlbumItem(TypedDict):
    kind: Literal["album"]
    items: list[dict[str, Any]]
    queued_at: str


QueueItem = TextItem | MediaItem | AlbumItem


class RetryQueue:
    """Persistent queue of failed Bale sends, retried on a timer."""

    def __init__(
        self,
        bale: BaleClient,
        queue_file: Path = DEFAULT_QUEUE_FILE,
        retry_interval: float = DEFAULT_RETRY_INTERVAL_SECONDS,
    ) -> None:
        self._bale = bale
        self._file = queue_file
        self._interval = retry_interval
        self._items: list[QueueItem] = []
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._load()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._save()

    @property
    def size(self) -> int:
        return len(self._items)

    def enqueue_text(self, text: str, parse_mode: str | None = None) -> None:
        self._items.append({
            "kind": "text",
            "text": text,
            "parse_mode": parse_mode,
            "queued_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info("queued text for retry (size=%d)", self.size)

    def enqueue_media(
        self,
        media_type: Literal["photo", "video", "document"],
        path: Path,
        caption: str | None,
    ) -> None:
        self._items.append({
            "kind": "media",
            "type": media_type,
            "path": str(path),
            "caption": caption,
            "queued_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info("queued %s for retry (size=%d)", media_type, self.size)

    def enqueue_album(self, items: list[dict[str, Any]]) -> None:
        # Persist paths as strings.
        normalized = [
            {**it, "path": str(it["path"])}
            for it in items
        ]
        self._items.append({
            "kind": "album",
            "items": normalized,
            "queued_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info("queued album for retry (size=%d)", self.size)

    async def flush(self) -> bool:
        """Try to send every queued item. Stops at the first failure.

        Returns ``True`` if the queue is empty after the attempt.
        """
        self._prune_expired()
        if not self._items:
            return True

        if not await self._bale.is_healthy():
            logger.warning("Bale health check failed; deferring flush")
            return False

        sent = 0
        try:
            for item in list(self._items):
                await self._send(item)
                self._items.pop(0)
                sent += 1
        except Exception as exc:  # noqa: BLE001 — we want to keep the queue intact on any error
            logger.error("retry flush failed after %d successes: %s", sent, exc)
            self._save()
            return False

        self._save()
        logger.info("flushed %d items from retry queue", sent)
        return True

    async def _send(self, item: QueueItem) -> None:
        if item["kind"] == "text":
            await self._bale.send_message(item["text"], parse_mode=item.get("parse_mode"))  # type: ignore[arg-type]
        elif item["kind"] == "media":
            path = Path(item["path"])
            caption = item.get("caption")
            mt = item["type"]
            if mt == "photo":
                await self._bale.send_photo(path, caption)
            elif mt == "video":
                await self._bale.send_video(path, caption)
            else:
                await self._bale.send_document(path, caption)
        else:
            album_items = [
                {**it, "path": Path(it["path"])}
                for it in item["items"]
            ]
            await self._bale.send_media_group(album_items)

    def _prune_expired(self) -> int:
        cutoff = datetime.now() - timedelta(hours=MAX_AGE_HOURS)
        before = len(self._items)
        self._items = [
            it for it in self._items
            if datetime.fromisoformat(it["queued_at"]) > cutoff
        ]
        removed = before - len(self._items)
        if removed > 0:
            logger.info("pruned %d expired items", removed)
        return removed

    def _load(self) -> None:
        if not self._file.exists():
            self._items = []
            return
        try:
            data = json.loads(self._file.read_text())
            self._items = data.get("items", [])
            self._prune_expired()
            logger.info("loaded %d items from %s", self.size, self._file)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("could not load %s: %s", self._file, exc)
            self._items = []

    def _save(self) -> None:
        try:
            self._file.write_text(
                json.dumps({"items": self._items}, ensure_ascii=False)
            )
        except OSError as exc:
            logger.warning("could not save %s: %s", self._file, exc)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            if self._items:
                await self.flush()
