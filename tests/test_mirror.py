"""Tests for the Mirror service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.bale_client import BaleClient
from src.message_map import MessageMap
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


def _sent(*bale_ids: int) -> dict[str, Any]:
    """Bale-shaped send response: one message, or a list for sendMediaGroup."""
    msgs = [{"message_id": b} for b in bale_ids]
    return {"ok": True, "result": msgs[0] if len(msgs) == 1 else msgs}


def _counter_response() -> AsyncMock:
    ids = iter(range(100, 1000))
    return AsyncMock(side_effect=lambda *a, **k: _sent(next(ids)))


def _bale() -> BaleClient:
    b = BaleClient("X", "@c")
    b.is_healthy = AsyncMock(return_value=True)  # type: ignore[method-assign]
    b.send_message = _counter_response()  # type: ignore[method-assign]
    b.send_photo = _counter_response()  # type: ignore[method-assign]
    b.send_video = _counter_response()  # type: ignore[method-assign]
    b.send_document = _counter_response()  # type: ignore[method-assign]
    b.send_media_group = AsyncMock()  # type: ignore[method-assign]
    b.delete_message = AsyncMock(return_value={"ok": True, "result": True})  # type: ignore[method-assign]
    return b


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    d = tmp_path / "media"
    d.mkdir()
    return d


@pytest.fixture
def queue(tmp_path: Path) -> RetryQueue:
    return RetryQueue(_bale(), queue_file=tmp_path / ".q")


@pytest.fixture
def message_map(tmp_path: Path) -> MessageMap:
    return MessageMap(map_file=tmp_path / ".map")


def _make_mirror(
    temp_dir: Path,
    queue: RetryQueue,
    downloaded: list[Path],
    message_map: MessageMap | None = None,
) -> tuple[Mirror, BaleClient, MagicMock]:
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
    mm = message_map or MessageMap(map_file=temp_dir / ".map")
    return Mirror(tg, bale, queue, temp_dir, mm), bale, tg


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


def _album() -> list[FakeMessage]:
    return [
        FakeMessage(id=1, media_group_id="g", photo=object(), caption="album!"),
        FakeMessage(id=2, media_group_id="g", photo=object()),
        FakeMessage(id=3, media_group_id="g", video=object()),
    ]


async def test_delete_single_post_deletes_bale_copy(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    await mirror.handle([FakeMessage(id=7, photo=object(), caption="pic")])

    await mirror.handle_deleted([7])

    bale.delete_message.assert_awaited_once_with(100)  # type: ignore[attr-defined]
    assert message_map.tg_ids() == []


async def test_delete_post_with_long_caption_deletes_media_and_tail(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    await mirror.handle([FakeMessage(id=7, photo=object(), caption="x" * 2500)])

    await mirror.handle_deleted([7])

    deleted = [c.args[0] for c in bale.delete_message.await_args_list]  # type: ignore[attr-defined]
    assert deleted == [100, 100, 101]  # photo, then two tail text chunks


async def test_delete_one_album_item_deletes_only_its_copy(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    bale.send_media_group = AsyncMock(return_value=_sent(501, 502, 503))  # type: ignore[method-assign]
    await mirror.handle(_album())

    await mirror.handle_deleted([2])

    bale.delete_message.assert_awaited_once_with(502)  # type: ignore[attr-defined]
    assert message_map.tg_ids() == [1, 3]


async def test_delete_whole_album_deletes_every_copy(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    bale.send_media_group = AsyncMock(return_value=_sent(501, 502, 503))  # type: ignore[method-assign]
    await mirror.handle(_album())

    await mirror.handle_deleted([1, 2, 3])

    deleted = [c.args[0] for c in bale.delete_message.await_args_list]  # type: ignore[attr-defined]
    assert deleted == [501, 502, 503]


async def test_album_with_unexpected_response_is_not_mapped(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    bale.send_media_group = AsyncMock(return_value={"ok": True, "result": True})  # type: ignore[method-assign]
    await mirror.handle(_album())

    assert message_map.tg_ids() == []


async def test_delete_unknown_id_is_noop(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)

    await mirror.handle_deleted([999])

    bale.delete_message.assert_not_called()  # type: ignore[attr-defined]
    assert queue.size == 0


async def test_delete_of_queued_post_drops_it_from_queue(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    bale.send_photo = AsyncMock(side_effect=RuntimeError("network"))  # type: ignore[method-assign]
    await mirror.handle([FakeMessage(id=7, photo=object())])
    assert queue.size == 1

    await mirror.handle_deleted([7])

    assert queue.size == 0
    bale.delete_message.assert_not_called()  # type: ignore[attr-defined]


async def test_delete_failure_enqueues_delete(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    await mirror.handle([FakeMessage(id=7, text="hi")])
    bale.delete_message = AsyncMock(side_effect=RuntimeError("network"))  # type: ignore[method-assign]

    await mirror.handle_deleted([7])

    assert queue.size == 1
    assert queue._items[0] == {  # type: ignore[attr-defined]
        "kind": "delete", "bale_id": 100, "queued_at": queue._items[0]["queued_at"],  # type: ignore[attr-defined]
    }


async def test_delete_drops_queued_tail_of_mapped_post(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    bale.send_message = AsyncMock(side_effect=RuntimeError("network"))  # type: ignore[method-assign]
    await mirror.handle([FakeMessage(id=7, photo=object(), caption="x" * 1500)])
    assert queue.size == 1  # photo sent, tail queued

    await mirror.handle_deleted([7])

    bale.delete_message.assert_awaited_once_with(100)  # type: ignore[attr-defined]
    assert queue.size == 0


async def test_cancelled_delete_parks_remaining_ids_in_queue(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    message_map.record(7, [100, 101])
    bale.delete_message = AsyncMock(side_effect=asyncio.CancelledError)  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        await mirror.handle_deleted([7])

    assert [it["bale_id"] for it in queue._items] == [100, 101]  # type: ignore[attr-defined,typeddict-item]


async def test_delete_during_album_debounce_skips_deleted_items(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)
    bale.send_media_group = AsyncMock(return_value=_sent(501, 502))  # type: ignore[method-assign]

    await mirror.handle_deleted([2])  # arrives while the album is buffered
    await mirror.handle(_album())

    items = bale.send_media_group.await_args.args[0]  # type: ignore[attr-defined]
    assert [it["tg_id"] for it in items] == [1, 3]
    bale.delete_message.assert_not_called()  # type: ignore[attr-defined]


async def test_delete_of_whole_buffered_post_sends_nothing(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)

    await mirror.handle_deleted([7])
    await mirror.handle([FakeMessage(id=7, text="gone")])

    bale.send_message.assert_not_called()  # type: ignore[attr-defined]


async def test_delete_during_upload_deletes_copy_after_send(
    temp_dir: Path, queue: RetryQueue, message_map: MessageMap
) -> None:
    mirror, bale, _ = _make_mirror(temp_dir, queue, [], message_map)

    async def slow_photo(path, caption=None):
        await mirror.handle_deleted([7])  # delete lands mid-upload
        return _sent(100)

    bale.send_photo = AsyncMock(side_effect=slow_photo)  # type: ignore[method-assign]

    await mirror.handle([FakeMessage(id=7, photo=object())])

    bale.delete_message.assert_awaited_once_with(100)  # type: ignore[attr-defined]
    assert message_map.tg_ids() == []
