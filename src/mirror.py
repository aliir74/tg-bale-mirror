"""Translate Pyrogram Messages into Bale API calls.

The mirror is the only component that talks to both the Telegram client
(downloading media) and the Bale client (uploading). Failures land in
the retry queue with the temp file kept on disk so the retry can pick
up where this attempt left off. Every successful send is recorded in the
message map so a later Telegram delete can remove the Bale copy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any, Literal

from src.bale_client import BaleClient, result_message_ids
from src.message_map import MessageMap
from src.retry_queue import RetryQueue

logger = logging.getLogger(__name__)

MAX_CAPTION = 1000  # leave headroom under Bale's ~1024 cap

MediaKind = Literal["photo", "video", "document"]


class Mirror:
    def __init__(
        self,
        tg_client: Any,
        bale: BaleClient,
        retry_queue: RetryQueue,
        temp_dir: Path,
        message_map: MessageMap,
    ) -> None:
        self._tg = tg_client
        self._bale = bale
        self._queue = retry_queue
        self._map = message_map
        self._temp = temp_dir
        self._temp.mkdir(parents=True, exist_ok=True)

    async def handle(self, messages: list[Any]) -> None:
        if not messages:
            return
        if len(messages) > 1 or getattr(messages[0], "media_group_id", None):
            await self._handle_album(messages)
        else:
            await self._handle_single(messages[0])

    async def handle_deleted(self, tg_ids: list[int]) -> None:
        """Delete the Bale copies of deleted Telegram messages."""
        for tg_id in tg_ids:
            # A post can be part sent, part queued (e.g. caption tail), so
            # always drop queued sends as well as deleting mapped copies.
            dropped = self._queue.drop_pending(tg_id)
            bale_ids = self._map.pop(tg_id)
            if not bale_ids and not dropped:
                logger.debug("no bale copy for deleted tg message %d", tg_id)
            for i, bale_id in enumerate(bale_ids):
                try:
                    await self._bale.delete_message(bale_id)
                    logger.info("deleted bale message %d (tg %d)", bale_id, tg_id)
                except asyncio.CancelledError:
                    # Shutdown mid-delete: the map entry is already gone, so
                    # park the rest in the queue rather than lose them.
                    for rest in bale_ids[i:]:
                        self._queue.enqueue_delete(rest)
                    raise
                except Exception:  # noqa: BLE001 — any failure goes to queue
                    logger.exception("delete_message failed; enqueueing")
                    self._queue.enqueue_delete(bale_id)

    async def _handle_single(self, message: Any) -> None:
        kind = _media_kind(message)
        caption = message.caption or ""
        text = message.text or ""

        if kind is None:
            if text:
                await self._send_text(text, message.id)
            return

        path: Path | None = None
        head, tail = _split_caption(caption)
        try:
            path = await self._download(message)
            try:
                if kind == "photo":
                    resp = await self._bale.send_photo(path, caption=head or None)
                elif kind == "video":
                    resp = await self._bale.send_video(path, caption=head or None)
                else:
                    resp = await self._bale.send_document(path, caption=head or None)
            except Exception:  # noqa: BLE001 — any failure goes to queue
                logger.exception("send_%s failed; enqueueing", kind)
                self._queue.enqueue_media(kind, path, caption=head or None, tg_id=message.id)
                if tail:
                    self._queue.enqueue_text(tail, tg_id=message.id)
                path = None  # keep file on disk for retry
                return

            self._map.record(message.id, result_message_ids(resp))
            if tail:
                await self._send_text(tail, message.id)
        finally:
            _safe_unlink(path)

    async def _handle_album(self, messages: list[Any]) -> None:
        # Sort by message id so albums arrive in original order.
        messages = sorted(messages, key=lambda m: m.id)
        caption_msg = next((m for m in messages if m.caption), None)
        caption = caption_msg.caption if caption_msg else ""
        head, tail = _split_caption(caption)

        results = await asyncio.gather(
            *(self._download(m) for m in messages),
            return_exceptions=True,
        )

        downloaded: list[Path] = []
        items: list[dict[str, Any]] = []
        for m, r in zip(messages, results, strict=True):
            if isinstance(r, BaseException):
                logger.warning("album download failed for %s: %s", m.id, r)
                continue
            kind = _media_kind(m)
            if kind not in ("photo", "video"):
                # sendMediaGroup only accepts photo/video; skip anything else
                continue
            downloaded.append(r)
            items.append({
                "type": kind,
                "path": r,
                "caption": head if not items else None,
                "tg_id": m.id,
            })

        if not items:
            return

        # The caption tail is mapped to the message carrying the caption, so it
        # goes when that item (or the whole album) is deleted.
        tail_tg_id = caption_msg.id if caption_msg else None
        try:
            resp = await self._bale.send_media_group(items)
        except Exception:  # noqa: BLE001
            logger.exception("send_media_group failed; enqueueing")
            self._queue.enqueue_album(items)
            if tail:
                self._queue.enqueue_text(tail, tg_id=tail_tg_id)
            return  # leave files on disk for retry

        bale_ids = result_message_ids(resp)
        if len(bale_ids) == len(items):
            for it, bale_id in zip(items, bale_ids, strict=True):
                self._map.record(it["tg_id"], [bale_id])
        else:
            logger.warning(
                "sendMediaGroup returned %d ids for %d items; album not mapped",
                len(bale_ids), len(items),
            )
        for p in downloaded:
            _safe_unlink(p)
        if tail:
            await self._send_text(tail, tail_tg_id)

    async def _send_text(self, text: str, tg_id: int | None) -> None:
        for chunk in (text[i:i + MAX_CAPTION] for i in range(0, len(text), MAX_CAPTION)):
            try:
                resp = await self._bale.send_message(chunk)
            except Exception:  # noqa: BLE001
                logger.exception("send_message failed; enqueueing remainder")
                self._queue.enqueue_text(chunk, tg_id=tg_id)
                return
            if tg_id is not None:
                self._map.record(tg_id, result_message_ids(resp))

    async def _download(self, message: Any) -> Path:
        result = await self._tg.download_media(
            message,
            file_name=str(self._temp) + "/",
        )
        return Path(result)


def _media_kind(message: Any) -> MediaKind | None:
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if any(
        getattr(message, attr, None)
        for attr in ("document", "audio", "voice", "animation")
    ):
        return "document"
    return None


def _split_caption(caption: str) -> tuple[str, str]:
    if len(caption) <= MAX_CAPTION:
        return caption, ""
    return caption[:MAX_CAPTION], caption[MAX_CAPTION:]


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        os.unlink(path)
