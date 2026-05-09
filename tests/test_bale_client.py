"""Tests for the BaleClient HTTP wrapper."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from src.bale_client import BALE_API_BASE, BaleClient


@pytest.fixture
async def client():
    c = BaleClient(bot_token="TESTTOKEN", chat_id="@channel")
    await c.start()
    try:
        yield c
    finally:
        await c.stop()


@respx.mock
async def test_get_me(client: BaleClient) -> None:
    route = respx.get(f"{BALE_API_BASE}TESTTOKEN/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"id": 1}})
    )

    result = await client.get_me()

    assert route.called
    assert result == {"ok": True, "result": {"id": 1}}


@respx.mock
async def test_is_healthy_true(client: BaleClient) -> None:
    respx.get(f"{BALE_API_BASE}TESTTOKEN/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    assert await client.is_healthy() is True


@respx.mock
async def test_is_healthy_false_on_error(client: BaleClient) -> None:
    respx.get(f"{BALE_API_BASE}TESTTOKEN/getMe").mock(
        return_value=httpx.Response(500)
    )
    assert await client.is_healthy() is False


@respx.mock
async def test_send_message_text_only(client: BaleClient) -> None:
    route = respx.post(f"{BALE_API_BASE}TESTTOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.send_message("hello world")

    body = json.loads(route.calls[0].request.content)
    assert body == {"chat_id": "@channel", "text": "hello world"}


@respx.mock
async def test_send_message_with_parse_mode(client: BaleClient) -> None:
    route = respx.post(f"{BALE_API_BASE}TESTTOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.send_message("<b>bold</b>", parse_mode="HTML")

    body = json.loads(route.calls[0].request.content)
    assert body["parse_mode"] == "HTML"


@respx.mock
async def test_send_photo_multipart(client: BaleClient, tmp_path) -> None:
    f = tmp_path / "pic.jpg"
    f.write_bytes(b"\xff\xd8\xff\xd9")
    route = respx.post(f"{BALE_API_BASE}TESTTOKEN/sendPhoto").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.send_photo(f, caption="a caption")

    assert route.called
    req = route.calls[0].request
    assert b"photo" in req.content
    assert b"pic.jpg" in req.content
    assert b"a caption" in req.content


@respx.mock
async def test_send_video_multipart(client: BaleClient, tmp_path) -> None:
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"\x00" * 10)
    route = respx.post(f"{BALE_API_BASE}TESTTOKEN/sendVideo").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.send_video(f)

    assert route.called
    assert b"video" in route.calls[0].request.content


@respx.mock
async def test_send_document_multipart(client: BaleClient, tmp_path) -> None:
    f = tmp_path / "thing.pdf"
    f.write_bytes(b"%PDF-1.4")
    route = respx.post(f"{BALE_API_BASE}TESTTOKEN/sendDocument").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.send_document(f, caption="doc")

    assert route.called
    assert b"document" in route.calls[0].request.content


@respx.mock
async def test_send_media_group(client: BaleClient, tmp_path) -> None:
    a = tmp_path / "a.jpg"
    a.write_bytes(b"a")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    route = respx.post(f"{BALE_API_BASE}TESTTOKEN/sendMediaGroup").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.send_media_group(
        [
            {"type": "photo", "path": a, "caption": "album cap"},
            {"type": "photo", "path": b, "caption": None},
        ]
    )

    body = route.calls[0].request.content
    # The media JSON descriptor should be in the form data
    assert b'"type": "photo"' in body or b'"type":"photo"' in body
    assert b"attach://file0" in body
    assert b"attach://file1" in body
    assert b"album cap" in body


async def test_unstarted_client_raises() -> None:
    c = BaleClient("X", "@c")
    with pytest.raises(RuntimeError, match="not started"):
        _ = c.http


async def test_send_media_group_empty_raises(client: BaleClient) -> None:
    with pytest.raises(ValueError):
        await client.send_media_group([])
