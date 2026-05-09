"""Async client for Bale messenger Bot API.

Bale exposes a Telegram-clone Bot API at https://tapi.bale.ai/bot<TOKEN>/<method>.
Same payload shape and method names as Telegram for the endpoints we use.
"""

from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Literal

import httpx

logger = logging.getLogger(__name__)

BALE_API_BASE = "https://tapi.bale.ai/bot"

ParseMode = Literal["HTML", "Markdown", "MarkdownV2"]
MediaType = Literal["photo", "video", "document"]


class BaleClient:
    """Send text and media to a single Bale chat/channel."""

    def __init__(
        self,
        bot_token: str,
        chat_id: int | str,
        timeout: float = 60.0,
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=self._timeout)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("BaleClient not started — call start() first")
        return self._client

    def _url(self, method: str) -> str:
        return f"{BALE_API_BASE}{self._token}/{method}"

    async def get_me(self) -> dict[str, Any]:
        r = await self.http.get(self._url("getMe"))
        r.raise_for_status()
        return r.json()

    async def is_healthy(self) -> bool:
        try:
            r = await self.http.get(self._url("getMe"))
            return r.is_success
        except Exception:  # noqa: BLE001 — health check, swallow everything
            return False

    async def send_message(
        self,
        text: str,
        parse_mode: ParseMode | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": self._chat_id, "text": text}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        r = await self.http.post(self._url("sendMessage"), json=payload)
        r.raise_for_status()
        return r.json()

    async def send_photo(
        self, file_path: Path, caption: str | None = None
    ) -> dict[str, Any]:
        return await self._send_media("sendPhoto", "photo", file_path, caption)

    async def send_video(
        self, file_path: Path, caption: str | None = None
    ) -> dict[str, Any]:
        return await self._send_media("sendVideo", "video", file_path, caption)

    async def send_document(
        self, file_path: Path, caption: str | None = None
    ) -> dict[str, Any]:
        return await self._send_media("sendDocument", "document", file_path, caption)

    async def _send_media(
        self,
        method: str,
        field: str,
        file_path: Path,
        caption: str | None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"chat_id": str(self._chat_id)}
        if caption:
            data["caption"] = caption
        with file_path.open("rb") as fh:
            files = {field: (file_path.name, fh)}
            r = await self.http.post(self._url(method), data=data, files=files)
        r.raise_for_status()
        return r.json()

    async def send_media_group(
        self,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Send an album.

        Each item: ``{"type": "photo"|"video", "path": Path, "caption": str | None}``.
        The caption attached to the first item becomes the album caption (matches
        Telegram convention).
        """
        if not items:
            raise ValueError("send_media_group requires at least one item")

        with contextlib.ExitStack() as stack:
            media: list[dict[str, Any]] = []
            files: dict[str, tuple[str, Any]] = {}
            for idx, it in enumerate(items):
                attach_name = f"file{idx}"
                path = Path(it["path"])
                fh = stack.enter_context(path.open("rb"))
                files[attach_name] = (path.name, fh)
                entry: dict[str, Any] = {
                    "type": it["type"],
                    "media": f"attach://{attach_name}",
                }
                if idx == 0 and it.get("caption"):
                    entry["caption"] = it["caption"]
                media.append(entry)

            data = {
                "chat_id": str(self._chat_id),
                "media": json.dumps(media),
            }
            r = await self.http.post(
                self._url("sendMediaGroup"), data=data, files=files
            )
            r.raise_for_status()
            return r.json()
