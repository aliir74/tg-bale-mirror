"""Persistent JSON-on-disk map of Telegram message ids to Bale message ids.

One Telegram post can produce several Bale messages (media + caption tail,
long text split into chunks), so each Telegram id maps to a list. Entries
older than ``MAX_AGE_HOURS`` are pruned: Bale's ``deleteMessage`` refuses
messages older than 48h, so there is no point remembering them longer.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

DEFAULT_MAP_FILE = Path(".bale_message_map")
MAX_AGE_HOURS = 48


class BaleRef(TypedDict):
    bale_id: int
    sent_at: str


class MessageMap:
    def __init__(self, map_file: Path = DEFAULT_MAP_FILE) -> None:
        self._file = map_file
        self._entries: dict[int, list[BaleRef]] = {}

    def load(self) -> None:
        if not self._file.exists():
            self._entries = {}
            return
        try:
            data = json.loads(self._file.read_text())
            self._entries = {int(k): v for k, v in data.get("messages", {}).items()}
            self._prune_expired()
            logger.info("loaded %d mapped messages from %s", len(self._entries), self._file)
        except (json.JSONDecodeError, ValueError, KeyError, AttributeError) as exc:
            logger.warning("could not load %s: %s", self._file, exc)
            self._entries = {}

    def save(self) -> None:
        try:
            self._file.write_text(json.dumps(
                {"messages": {str(k): v for k, v in self._entries.items()}},
                ensure_ascii=False,
            ))
        except OSError as exc:
            logger.warning("could not save %s: %s", self._file, exc)

    def record(self, tg_id: int, bale_ids: list[int]) -> None:
        if not bale_ids:
            return
        now = datetime.now().isoformat()
        self._entries.setdefault(tg_id, []).extend(
            {"bale_id": b, "sent_at": now} for b in bale_ids
        )
        self._prune_expired()
        self.save()

    def pop(self, tg_id: int) -> list[int]:
        refs = self._entries.pop(tg_id, [])
        if refs:
            self.save()
        return [r["bale_id"] for r in refs]

    def tg_ids(self) -> list[int]:
        self._prune_expired()
        return sorted(self._entries)

    def _prune_expired(self) -> None:
        cutoff = datetime.now() - timedelta(hours=MAX_AGE_HOURS)
        for tg_id in list(self._entries):
            refs = [
                r for r in self._entries[tg_id]
                if datetime.fromisoformat(r["sent_at"]) > cutoff
            ]
            if refs:
                self._entries[tg_id] = refs
            else:
                del self._entries[tg_id]
