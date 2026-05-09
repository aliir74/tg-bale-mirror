"""Tests for the Mirror service."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bale_client import BaleClient
from src.mirror import Mirror
from src.retry_queue import RetryQueue


@dataclass
class FakeMessage:
    id: int = 1
    text: str | None = None
    caption: str | None = None
    photo: Any = None
    video: Any = None
    document: Any = None
    audio: Any = None
    voice: Any = None
    animation: Any = None
    media_group_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def _bale() -> BaleClient:
    b = BaleClient("X", "@c")
    b.is_healthy = AsyncMock(return_value=True)  # type: ignore[method-assign]
    b.send_message = AsyncMock()  # type: ignore[method-assign]
    b.send_photo = AsyncMock()  # type: ignore[method-assign]
    b.send_video = AsyncMock()  # type: ignore[method-assign]
    b.send_document = AsyncMock()  # type: ignore[method-assign]
    b.send_media_group = AsyncMock()  # type: ignore[method-assign]
    return b


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    d = tmp_path / "media"
    d.mkdir()
    return d


@pytest.fixture
def queue(tmp_path: Path) -> RetryQueue:
    return RetryQueue(_bale(), queue_file=tmp_path / ".q")


def _make_mirror(temp_dir: Path, queue: RetryQueue, downloaded: list[Path]) -> tuple[Mirror, BaleClient, MagicMock]:
    bale = _bale()
    tg = MagicMock()

    async def fake_download(message, file_name):
        idx = len(downloaded)
        p = temp_dir / f"file_{idx}.bin"
        p.write_bytes(b"data")
        downloaded.append(p)
        return str(p)

    tg.download_media = AsyncMock(side_effect=fake_download)

    # Reuse the same bale on the queue for consistency
    queue._bale = bale  # type: ignore[attr-defined]
    return Mirror(tg, bale, queue, temp_dir), bale, tg


async def test_text_only_sends_message(temp_dir: Path, queue: RetryQueue) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [])
    await mirror.handle([FakeMessage(text="hello world")])

    bale.send_message.assert_awaited_once_with("hello world")  # type: ignore[attr-defined]


async def test_empty_text_message_is_skipped(temp_dir: Path, queue: RetryQueue) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [])
    await mirror.handle([FakeMessage()])

    bale.send_message.assert_not_called()  # type: ignore[attr-defined]


async def test_photo_with_caption_sends_photo(temp_dir: Path, queue: RetryQueue) -> None:
    downloaded: list[Path] = []
    mirror, bale, tg = _make_mirror(temp_dir, queue, downloaded)

    msg = FakeMessage(photo=object(), caption="a pic")
    await mirror.handle([msg])

    bale.send_photo.assert_awaited_once()  # type: ignore[attr-defined]
    args, kwargs = bale.send_photo.await_args  # type: ignore[attr-defined]
    assert kwargs.get("caption") == "a pic"
    # The downloaded file should be cleaned up
    assert not downloaded[0].exists()


async def test_video_routes_to_send_video(temp_dir: Path, queue: RetryQueue) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [])
    await mirror.handle([FakeMessage(video=object())])
    bale.send_video.assert_awaited_once()  # type: ignore[attr-defined]


async def test_document_audio_voice_animation_route_to_document(
    temp_dir: Path, queue: RetryQueue
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [])
    await mirror.handle([FakeMessage(document=object())])
    await mirror.handle([FakeMessage(audio=object())])
    await mirror.handle([FakeMessage(voice=object())])
    await mirror.handle([FakeMessage(animation=object())])

    assert bale.send_document.await_count == 4  # type: ignore[attr-defined]


async def test_album_calls_send_media_group(temp_dir: Path, queue: RetryQueue) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [])
    msgs = [
        FakeMessage(id=1, media_group_id="g", photo=object(), caption="album!"),
        FakeMessage(id=2, media_group_id="g", photo=object()),
        FakeMessage(id=3, media_group_id="g", video=object()),
    ]
    await mirror.handle(msgs)

    bale.send_media_group.assert_awaited_once()  # type: ignore[attr-defined]
    items = bale.send_media_group.await_args.args[0]  # type: ignore[attr-defined]
    assert len(items) == 3
    assert items[0]["caption"] == "album!"
    assert items[1]["caption"] is None
    assert items[2]["type"] == "video"


async def test_send_failure_enqueues_media(
    temp_dir: Path, queue: RetryQueue
) -> None:
    downloaded: list[Path] = []
    mirror, bale, _ = _make_mirror(temp_dir, queue, downloaded)
    bale.send_photo = AsyncMock(side_effect=RuntimeError("network"))  # type: ignore[method-assign]

    await mirror.handle([FakeMessage(photo=object(), caption="cap")])

    assert queue.size == 1
    # The downloaded file should still be on disk for retry
    assert downloaded[0].exists()


async def test_album_failure_enqueues_album(
    temp_dir: Path, queue: RetryQueue
) -> None:
    downloaded: list[Path] = []
    mirror, bale, _ = _make_mirror(temp_dir, queue, downloaded)
    bale.send_media_group = AsyncMock(side_effect=RuntimeError("nope"))  # type: ignore[method-assign]

    msgs = [
        FakeMessage(id=1, media_group_id="g", photo=object(), caption="x"),
        FakeMessage(id=2, media_group_id="g", photo=object()),
    ]
    await mirror.handle(msgs)

    assert queue.size == 1
    # Files preserved for retry
    for p in downloaded:
        assert p.exists()


async def test_long_caption_is_chunked(temp_dir: Path, queue: RetryQueue) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [])
    long_cap = "x" * 2500
    await mirror.handle([FakeMessage(photo=object(), caption=long_cap)])

    bale.send_photo.assert_awaited_once()  # type: ignore[attr-defined]
    sent_caption = bale.send_photo.await_args.kwargs["caption"]  # type: ignore[attr-defined]
    assert len(sent_caption) == 1000
    # The remainder should land as follow-up send_message calls
    assert bale.send_message.await_count >= 1  # type: ignore[attr-defined]
