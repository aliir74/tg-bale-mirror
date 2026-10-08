"""Tests for the Telegram → Bale message id map."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from src.message_map import MessageMap


def test_record_pop_round_trips_through_disk(tmp_path: Path) -> None:
    f = tmp_path / ".map"
    mm = MessageMap(map_file=f)
    mm.record(1, [10, 11])
    mm.record(1, [12])
    mm.record(2, [])  # nothing sent → nothing stored

    reloaded = MessageMap(map_file=f)
    reloaded.load()

    assert reloaded.tg_ids() == [1]
    assert reloaded.pop(1) == [10, 11, 12]
    assert reloaded.pop(1) == []


def test_load_prunes_entries_older_than_48h(tmp_path: Path) -> None:
    f = tmp_path / ".map"
    old = (datetime.now() - timedelta(hours=49)).isoformat()
    fresh = datetime.now().isoformat()
    f.write_text(json.dumps({"messages": {
        "1": [{"bale_id": 10, "sent_at": old}],
        "2": [{"bale_id": 20, "sent_at": fresh}],
    }}))
    mm = MessageMap(map_file=f)

    mm.load()

    assert mm.tg_ids() == [2]


def test_load_tolerates_corrupt_file(tmp_path: Path) -> None:
    f = tmp_path / ".map"
    f.write_text("not json")
    mm = MessageMap(map_file=f)

    mm.load()

    assert mm.tg_ids() == []


def test_load_ignores_map_written_for_another_bale_chat(tmp_path: Path) -> None:
    f = tmp_path / ".map"
    MessageMap(map_file=f, bale_chat_id="@old").record(1, [10])

    mm = MessageMap(map_file=f, bale_chat_id="@new")
    mm.load()

    assert mm.tg_ids() == []
