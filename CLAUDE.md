# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

One-way mirror: every new post in a Telegram channel gets re-published to a Bale messenger channel. A pyrofork `Client` reads the source — by default in **bot mode** (a @BotFather bot added as admin of the source channel), with **userbot mode** (your personal Telegram account, session string or interactive `.session`) supported as a legacy fallback. A Bale **bot** writes to the target via Telegram-clone Bot API at `https://tapi.bale.ai/bot<TOKEN>/<method>`. v1 mirrors text + photo/video/document/album. Edits, deletes, polls, stickers, locations, contacts, and forward-from headers are NOT propagated.

## Common commands

```bash
make dev          # uv sync --extra dev (install with test/lint deps)
make run          # uv run python -m src.main (foreground)
make test         # pytest
make lint         # ruff check src tests
make format       # ruff format src tests
make typecheck    # pyright
make check        # lint + typecheck + test (run before commit)
make session      # one-time: generate TG_SESSION_STRING for headless deploy

# Run a single test
uv run pytest tests/test_mirror.py::test_handle_album_caption_split -v
```

VPS deploy targets (`SSH_HOST=your-vps`, `REMOTE_DIR=/opt/tg-bale-mirror`, systemd unit `tg-bale-mirror`): `make deploy` (refuses if local HEAD is ahead of `origin/main`), `make restart`, `make logs`, `make logs-follow`, `make status`, `make ssh`, `make push-env`, `make pull-state`, `make install-systemd`. macOS LaunchAgent variants: `make install-agent`, `make uninstall-agent`, `make agent-status`.

## Architecture (start here)

The runtime is a single async process (`src/main.py`) wiring five components in this exact order — read these together to understand the flow:

```
TgListener  ──msg──▶  AlbumDebouncer  ──flush──▶  Mirror  ──upload──▶  BaleClient
   (pyrofork)          (1.5s window per             │ on failure
                        media_group_id)             ▼
                                              RetryQueue (JSON on disk)
                                              flushes every 5min
                                              once BaleClient.is_healthy()
```

- `tg_listener.py` — pyrofork `Client` with a `MessageHandler` filtered to one resolved chat id. **Resolve usernames to numeric ids before registering** (`resolve_source()` then `register()`); the filter compares ids, not strings. The listener is mode-agnostic — `MessageHandler` dispatches both `message` and `channel_post` updates through the same callback, so bot mode and userbot mode share this code path.
- `album_debouncer.py` — buffers messages by `media_group_id`, flushes after `delay=1.5s` of silence. Lone messages (no group id) bypass the buffer. `flush_all()` is called on shutdown.
- `mirror.py` — the only component that touches both clients. Downloads media to `temp_media_dir` via `tg.download_media`, then uploads. **Caption split rule:** captions over `MAX_CAPTION=1000` chars are sent as `head` (caption on the media) + `tail` (follow-up `send_message`). Albums use the first-non-empty caption. Only `photo|video` go into `sendMediaGroup`; documents in an album are dropped.
- `bale_client.py` — thin `httpx.AsyncClient` wrapper. `send_media_group` uses the `attach://fileN` multipart pattern. `is_healthy()` is `getMe` returning success — used by the retry queue to decide whether to attempt a flush.
- `retry_queue.py` — JSON-on-disk queue at `.bale_retry_queue`. Three item kinds: `text`, `media`, `album`. `flush()` stops at the first failure (preserves order); items older than `MAX_AGE_HOURS=24` are pruned on load. **On send failure in mirror, the temp media file is intentionally NOT deleted** — the retry queue references the path on disk.

## Conventions specific to this repo

- **`pyrofork`, not `pyrogram`.** Upstream pyrogram 2.0.106 rejects 64-bit channel IDs that Telegram now issues. The package is `pyrofork` but imports as `pyrogram` (drop-in fork).
- **Listener has three modes**, decided by `build_tg_client_kwargs(config)` in `main.py`. Precedence top-down (first match wins):
  - `TG_BOT_TOKEN` set → **bot mode** (in-memory, no `.session` file). The bot must be added as **admin** of the source channel to receive `channel_post` updates. **This is the recommended/default path.** If `TG_SESSION_STRING` is also set, bot mode wins and a warning is logged.
  - `TG_SESSION_STRING` set → userbot in-memory mode. **VPS/systemd path for userbot deploys** — generate locally with `make session`, copy into `.env`, deploy.
  - Neither set → pyrofork creates `<TG_SESSION_NAME>.session` on disk and prompts for phone+code on first run. **Local dev path only** — first run is interactive.
- **`TG_API_ID` / `TG_API_HASH` are required in all three modes** — they identify the pyrofork app, not the user.
- **`Config()` typing.** `pydantic-settings` populates fields from env, but pyright sees no kwargs being passed → use `# type: ignore[call-arg]` at the construction site (already done in `main.py`).
- **`tg_source_channel` and `bale_channel_id`** accept `int | str` and have `field_validator(mode="before")` that coerces numeric strings to int and leaves `@username` as str. When passing `chat_id` to Bale Bot API, always `str(...)` it (see `_send_media`).
- **Captions over 1000 chars** must always go through `_split_caption()` — don't pass long captions directly to `send_photo`/etc. or Bale will 4xx them. The retry queue stores the head and tail as separate items.
- **Media downloads go to `temp_media_dir`** via `tg.download_media(message, file_name=str(temp_dir) + "/")` — note the trailing slash; pyrogram interprets a path ending in `/` as a directory.
- **All `except Exception` in send paths** funnel into the retry queue. Don't add narrower handlers — Bale's error surface is unstable and we want every failure to retry, not crash the listener.
- **Tests use `pytest-asyncio` with `asyncio_mode = "auto"`** — async tests don't need the `@pytest.mark.asyncio` decorator. `respx` mocks the Bale API; pyrogram is mocked via `Mock`/`AsyncMock` in test fixtures. There is no live-API test — everything is unit-level.
- **Ruff config:** line-length 100, `target-version = py311`. `E501` (line too long) is intentionally ignored. `PLC0415` (import not at top) is on — keep imports at module top.
- **pyright `typeCheckingMode = "basic"`**, `reportGeneralTypeIssues = "warning"`. `tests/` is excluded from typecheck.

## Operational notes

- `.bale_retry_queue` (JSON) and `<TG_SESSION_NAME>.session` (binary) are runtime state in the working dir. Both are gitignored. The session file is equivalent to your Telegram login — never commit, never paste in chat.
- `make push-env` overwrites `/opt/tg-bale-mirror/.env` on the VPS via scp with a 3-second abort window. `make pull-state` downloads `.bale_retry_queue` into `state-backup/` for inspection.
- The systemd unit and LaunchAgent plist live in `ops/`. The plist is a template (`${REPO_PATH}` / `${UV_PATH}` / `${HOME}` placeholders) rendered by `make install-agent`.

## Reference patterns

The README points at sibling repos for the patterns this one borrows:

- `personal/news-summarizer/` — Bale Bot API client style + VPS-systemd deploy targets (Makefile pattern is mirrored here).
- `personal/channel-ghost/` — pyrofork userbot + session-string flow.

If you need to extend this mirror (e.g. message-id mapping for edit/delete propagation, multi-target fan-out), check those repos first for the established pattern.
