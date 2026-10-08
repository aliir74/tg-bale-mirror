"""Tests for the periodic delete reconciler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.delete_reconciler import DeleteReconciler
from src.message_map import MessageMap


@dataclass
class FakeMsg:
    id: int
    empty: bool = False


def _setup(tmp_path: Path, tg_ids: list[int], empty: set[int]):
    mm = MessageMap(map_file=tmp_path / ".map")
    for i in tg_ids:
        mm.record(i, [i * 10])
    tg = MagicMock()
    tg.get_messages = AsyncMock(
        side_effect=lambda chat, ids: [FakeMsg(id=i, empty=i in empty) for i in ids]
    )
    on_deleted = AsyncMock()
    return DeleteReconciler(tg, -100, mm, on_deleted, interval=60), tg, on_deleted


async def test_reconcile_reports_empty_messages(tmp_path: Path) -> None:
    rec, tg, on_deleted = _setup(tmp_path, [1, 2, 3], empty={2})

    assert await rec.reconcile() == [2]

    tg.get_messages.assert_awaited_once_with(-100, [1, 2, 3])
    on_deleted.assert_awaited_once_with([2])


async def test_reconcile_with_nothing_deleted_does_not_call_back(tmp_path: Path) -> None:
    rec, _, on_deleted = _setup(tmp_path, [1, 2], empty=set())

    assert await rec.reconcile() == []
    on_deleted.assert_not_called()


async def test_reconcile_skips_batch_when_everything_is_empty(tmp_path: Path) -> None:
    rec, _, on_deleted = _setup(tmp_path, [1, 2, 3], empty={1, 2, 3})

    assert await rec.reconcile() == []
    on_deleted.assert_not_called()


async def test_zero_interval_disables_loop(tmp_path: Path) -> None:
    mm = MessageMap(map_file=tmp_path / ".map")
    rec = DeleteReconciler(MagicMock(), -100, mm, AsyncMock(), interval=0)

    rec.start()

    assert rec._task is None  # type: ignore[attr-defined]
