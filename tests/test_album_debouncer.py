"""Tests for the album debouncer."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from src.album_debouncer import AlbumDebouncer


@dataclass
class FakeMsg:
    id: int
    media_group_id: str | None = None


@pytest.fixture
def collected() -> list[list[FakeMsg]]:
    return []


@pytest.fixture
def debouncer(collected: list[list[FakeMsg]]) -> AlbumDebouncer:
    async def on_flush(msgs):
        collected.append(list(msgs))

    return AlbumDebouncer(on_flush=on_flush, delay=0.05)


async def test_lone_message_flushes_immediately(
    debouncer: AlbumDebouncer, collected: list[list[FakeMsg]]
) -> None:
    await debouncer.add(FakeMsg(id=1))
    assert collected == [[FakeMsg(id=1)]]


async def test_album_batches_same_group_id(
    debouncer: AlbumDebouncer, collected: list[list[FakeMsg]]
) -> None:
    await debouncer.add(FakeMsg(id=1, media_group_id="g1"))
    await debouncer.add(FakeMsg(id=2, media_group_id="g1"))
    await debouncer.add(FakeMsg(id=3, media_group_id="g1"))

    await asyncio.sleep(0.15)

    assert len(collected) == 1
    assert [m.id for m in collected[0]] == [1, 2, 3]


async def test_two_groups_flush_independently(
    debouncer: AlbumDebouncer, collected: list[list[FakeMsg]]
) -> None:
    await debouncer.add(FakeMsg(id=1, media_group_id="g1"))
    await debouncer.add(FakeMsg(id=10, media_group_id="g2"))
    await debouncer.add(FakeMsg(id=2, media_group_id="g1"))
    await debouncer.add(FakeMsg(id=11, media_group_id="g2"))

    await asyncio.sleep(0.15)

    assert len(collected) == 2
    groups = sorted([sorted(m.id for m in batch) for batch in collected])
    assert groups == [[1, 2], [10, 11]]


async def test_flush_all_drains_pending(
    debouncer: AlbumDebouncer, collected: list[list[FakeMsg]]
) -> None:
    await debouncer.add(FakeMsg(id=1, media_group_id="g1"))
    await debouncer.add(FakeMsg(id=2, media_group_id="g1"))

    await debouncer.flush_all()

    assert len(collected) == 1
    assert [m.id for m in collected[0]] == [1, 2]


async def test_callback_exception_is_swallowed(
    collected: list[list[FakeMsg]],
) -> None:
    async def boom(_msgs):
        raise RuntimeError("nope")

    deb = AlbumDebouncer(on_flush=boom, delay=0.05)
    await deb.add(FakeMsg(id=1))  # should not raise
    await deb.add(FakeMsg(id=2, media_group_id="g"))
    await asyncio.sleep(0.1)
