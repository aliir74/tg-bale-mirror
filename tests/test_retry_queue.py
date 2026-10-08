"""Tests for the retry queue."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from src.bale_client import BaleClient
from src.message_map import MessageMap
from src.retry_queue import RetryQueue


def _stub_bale(healthy: bool = True) -> BaleClient:
    bale = BaleClient("X", "@c")
    bale.is_healthy = AsyncMock(return_value=healthy)  # type: ignore[method-assign]
    bale.send_message = AsyncMock()  # type: ignore[method-assign]
    bale.send_photo = AsyncMock()  # type: ignore[method-assign]
    bale.send_video = AsyncMock()  # type: ignore[method-assign]
    bale.send_document = AsyncMock()  # type: ignore[method-assign]
    bale.send_media_group = AsyncMock()  # type: ignore[method-assign]
    return bale


@pytest.fixture
def queue_file(tmp_path: Path) -> Path:
    return tmp_path / ".bale_retry_queue"


async def test_enqueue_persists_to_disk(queue_file: Path) -> None:
    bale = _stub_bale()
    q = RetryQueue(bale, queue_file=queue_file)

    q.enqueue_text("hello")
    q.enqueue_media("photo", Path("/tmp/x.jpg"), "cap")

    on_disk = json.loads(queue_file.read_text())
    assert len(on_disk["items"]) == 2
    assert on_disk["items"][0]["text"] == "hello"
    assert on_disk["items"][1]["type"] == "photo"


async def test_load_restores_items(queue_file: Path) -> None:
    queue_file.write_text(json.dumps({
        "items": [
            {
                "kind": "text",
                "text": "persisted",
                "parse_mode": None,
                "queued_at": datetime.now().isoformat(),
            }
        ]
    }))
    bale = _stub_bale()
    q = RetryQueue(bale, queue_file=queue_file)

    await q.start()
    try:
        assert q.size == 1
    finally:
        await q.stop()


async def test_load_prunes_expired(queue_file: Path) -> None:
    old = (datetime.now() - timedelta(hours=48)).isoformat()
    fresh = datetime.now().isoformat()
    queue_file.write_text(json.dumps({
        "items": [
            {"kind": "text", "text": "old", "parse_mode": None, "queued_at": old},
            {"kind": "text", "text": "fresh", "parse_mode": None, "queued_at": fresh},
        ]
    }))
    bale = _stub_bale()
    q = RetryQueue(bale, queue_file=queue_file)

    await q.start()
    try:
        assert q.size == 1
    finally:
        await q.stop()


async def test_flush_skipped_when_unhealthy(queue_file: Path) -> None:
    bale = _stub_bale(healthy=False)
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_text("a")

    ok = await q.flush()

    assert ok is False
    assert q.size == 1
    bale.send_message.assert_not_called()  # type: ignore[attr-defined]


async def test_flush_drains_text_items(queue_file: Path) -> None:
    bale = _stub_bale()
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_text("one")
    q.enqueue_text("two")

    ok = await q.flush()

    assert ok is True
    assert q.size == 0
    assert bale.send_message.await_count == 2  # type: ignore[attr-defined]


async def test_flush_routes_media_to_correct_method(queue_file: Path) -> None:
    bale = _stub_bale()
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_media("photo", Path("/tmp/p.jpg"), "p")
    q.enqueue_media("video", Path("/tmp/v.mp4"), "v")
    q.enqueue_media("document", Path("/tmp/d.pdf"), None)

    ok = await q.flush()

    assert ok is True
    bale.send_photo.assert_awaited_once()  # type: ignore[attr-defined]
    bale.send_video.assert_awaited_once()  # type: ignore[attr-defined]
    bale.send_document.assert_awaited_once()  # type: ignore[attr-defined]


async def test_flush_failure_keeps_remaining(queue_file: Path) -> None:
    bale = _stub_bale()
    bale.send_message = AsyncMock(side_effect=[None, RuntimeError("boom")])  # type: ignore[method-assign]
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_text("first")
    q.enqueue_text("second")
    q.enqueue_text("third")

    ok = await q.flush()

    assert ok is False
    # First sent, second failed, third still queued → 2 remain
    assert q.size == 2


async def test_flush_album_routes_to_send_media_group(queue_file: Path) -> None:
    bale = _stub_bale()
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_album([
        {"type": "photo", "path": Path("/tmp/a.jpg"), "caption": "x"},
        {"type": "photo", "path": Path("/tmp/b.jpg"), "caption": None},
    ])

    ok = await q.flush()

    assert ok is True
    bale.send_media_group.assert_awaited_once()  # type: ignore[attr-defined]


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://tapi.bale.ai/botX/deleteMessage")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, request=req))


async def test_queued_delete_4xx_is_dropped_and_does_not_block(queue_file: Path) -> None:
    bale = _stub_bale()
    bale.delete_message = AsyncMock(side_effect=_http_error(400))  # type: ignore[method-assign]
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_delete(55)
    q.enqueue_text("after")

    assert await q.flush() is True

    assert q.size == 0
    bale.send_message.assert_awaited_once()  # type: ignore[attr-defined]


async def test_queued_delete_5xx_stays_queued(queue_file: Path) -> None:
    bale = _stub_bale()
    bale.delete_message = AsyncMock(side_effect=_http_error(502))  # type: ignore[method-assign]
    q = RetryQueue(bale, queue_file=queue_file)
    q.enqueue_delete(55)

    assert await q.flush() is False
    assert q.size == 1


async def test_flush_records_mapping(tmp_path: Path, queue_file: Path) -> None:
    bale = _stub_bale()
    bale.send_message = AsyncMock(return_value={"ok": True, "result": {"message_id": 9}})  # type: ignore[method-assign]
    bale.send_media_group = AsyncMock(  # type: ignore[method-assign]
        return_value={"ok": True, "result": [{"message_id": 10}, {"message_id": 11}]}
    )
    mm = MessageMap(map_file=tmp_path / ".map")
    q = RetryQueue(bale, queue_file=queue_file, message_map=mm)
    q.enqueue_text("hi", tg_id=1)
    q.enqueue_album([
        {"type": "photo", "path": Path("/tmp/a"), "caption": None, "tg_id": 2},
        {"type": "photo", "path": Path("/tmp/b"), "caption": None, "tg_id": 3},
    ])

    assert await q.flush() is True

    assert mm.pop(1) == [9]
    assert mm.pop(2) == [10]
    assert mm.pop(3) == [11]


async def test_drop_pending_removes_album_item_only(queue_file: Path) -> None:
    q = RetryQueue(_stub_bale(), queue_file=queue_file)
    q.enqueue_album([
        {"type": "photo", "path": Path("/tmp/a"), "caption": "c", "tg_id": 2},
        {"type": "photo", "path": Path("/tmp/b"), "caption": None, "tg_id": 3},
    ])
    q.enqueue_text("other", tg_id=4)

    assert q.drop_pending(2) is True
    assert q.drop_pending(99) is False

    on_disk = json.loads(queue_file.read_text())["items"]
    assert [it["tg_id"] for it in on_disk[0]["items"]] == [3]
    assert on_disk[1]["text"] == "other"
