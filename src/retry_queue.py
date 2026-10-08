"""Persistent JSON-on-disk retry queue for failed Bale sends.

Items can be text messages, single-media uploads, albums, or deletes. The queue is
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
from typing import Any, Literal, NotRequired, TypedDict

import httpx

from src.bale_client import BaleClient, result_message_ids
from src.message_map import MessageMap

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_FILE = Path(".bale_retry_queue")
DEFAULT_RETRY_INTERVAL_SECONDS = 300
MAX_AGE_HOURS = 24


class TextItem(TypedDict):
    kind: Literal["text"]
    text: str
    parse_mode: str | None
    queued_at: str
    tg_id: NotRequired[int | None]


class MediaItem(TypedDict):
    kind: Literal["media"]
    type: Literal["photo", "video", "document"]
    path: str
    caption: str | None
    queued_at: str
    tg_id: NotRequired[int | None]


class AlbumItem(TypedDict):
    kind: Literal["album"]
    items: list[dict[str, Any]]  # each may carry its own "tg_id"
    queued_at: str


class DeleteItem(TypedDict):
    kind: Literal["delete"]
    bale_id: int
    queued_at: str


QueueItem = TextItem | MediaItem | AlbumItem | DeleteItem


class RetryQueue:
    """Persistent queue of failed Bale sends, retried on a timer."""

    def __init__(
        self,
        bale: BaleClient,
        queue_file: Path = DEFAULT_QUEUE_FILE,
        retry_interval: float = DEFAULT_RETRY_INTERVAL_SECONDS,
        message_map: MessageMap | None = None,
    ) -> None:
        self._bale = bale
        self._map = message_map
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

    def enqueue_text(
        self,
        text: str,
        parse_mode: str | None = None,
        tg_id: int | None = None,
    ) -> None:
        self._items.append({
            "kind": "text",
            "text": text,
            "parse_mode": parse_mode,
            "queued_at": datetime.now().isoformat(),
            "tg_id": tg_id,
        })
        self._save()
        logger.info("queued text for retry (size=%d)", self.size)

    def enqueue_media(
        self,
        media_type: Literal["photo", "video", "document"],
        path: Path,
        caption: str | None,
        tg_id: int | None = None,
    ) -> None:
        self._items.append({
            "kind": "media",
            "type": media_type,
            "path": str(path),
            "caption": caption,
            "queued_at": datetime.now().isoformat(),
            "tg_id": tg_id,
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

    def enqueue_delete(self, bale_id: int) -> None:
        self._items.append({
            "kind": "delete",
            "bale_id": bale_id,
            "queued_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info("queued delete of bale message %d (size=%d)", bale_id, self.size)

    def drop_pending(self, tg_id: int) -> bool:
        """Remove queued sends for a Telegram message deleted before they went out.

        Album items lose only the matching entry; an album left empty is dropped.
        Returns ``True`` if anything was removed.
        """
        kept: list[QueueItem] = []
        removed = False
        for item in self._items:
            if item["kind"] == "album":
                rest = [it for it in item["items"] if it.get("tg_id") != tg_id]
                if len(rest) != len(item["items"]):
                    removed = True
                    if not rest:
                        continue
                    item = AlbumItem(kind="album", items=rest, queued_at=item["queued_at"])
            elif item["kind"] != "delete" and item.get("tg_id") == tg_id:
                removed = True
                continue
            kept.append(item)
        if removed:
            self._items = kept
            self._save()
            logger.info("dropped queued sends for deleted tg message %d", tg_id)
        return removed

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
            resp = await self._bale.send_message(item["text"], parse_mode=item.get("parse_mode"))  # type: ignore[arg-type]
            self._record(item.get("tg_id"), result_message_ids(resp))
        elif item["kind"] == "media":
            path = Path(item["path"])
            caption = item.get("caption")
            mt = item["type"]
            if mt == "photo":
                resp = await self._bale.send_photo(path, caption)
            elif mt == "video":
                resp = await self._bale.send_video(path, caption)
            else:
                resp = await self._bale.send_document(path, caption)
            self._record(item.get("tg_id"), result_message_ids(resp))
        elif item["kind"] == "album":
            album_items = [
                {**it, "path": Path(it["path"])}
                for it in item["items"]
            ]
            resp = await self._bale.send_media_group(album_items)
            bale_ids = result_message_ids(resp)
            if len(bale_ids) == len(album_items):
                for it, bale_id in zip(album_items, bale_ids, strict=True):
                    self._record(it.get("tg_id"), [bale_id])
        else:
            await self._delete(item["bale_id"])

    async def _delete(self, bale_id: int) -> None:
        # A 4xx means the message is gone, too old (>48h) or not ours to delete.
        # Retrying cannot fix that, and flush() stops at the first failure, so a
        # stuck delete would block every send queued behind it. Drop it instead.
        try:
            await self._bale.delete_message(bale_id)
        except httpx.HTTPStatusError as exc:
            if not exc.response.is_client_error:
                raise
            logger.warning(
                "dropping delete of bale message %d: HTTP %d",
                bale_id, exc.response.status_code,
            )

    def _record(self, tg_id: int | None, bale_ids: list[int]) -> None:
        if self._map is not None and tg_id is not None:
            self._map.record(tg_id, bale_ids)

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
