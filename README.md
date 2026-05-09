# tg-bale-mirror

One-way mirror from a Telegram channel to a Bale messenger channel.
Every new post (text, photo, video, document, album) on the source
Telegram channel is re-published to the target Bale channel with the
caption preserved.

## How it works

1. A Pyrogram (pyrofork) **userbot** subscribes to the source Telegram
   channel using your personal Telegram account. Read access is enough
   — your account just needs to be a member.
2. New posts are dispatched to an in-memory **album debouncer** that
   batches items sharing the same `media_group_id` after 1.5s of idle.
3. The **mirror** downloads any media to a temp dir, then re-uploads it
   to Bale via the Telegram-clone Bot API at
   `https://tapi.bale.ai/bot<TOKEN>/<method>`. Captions over 1000 chars
   are split: head as the caption, tail as a follow-up text message.
4. Any send that fails (network blip, Bale rate limit, malformed
   request) lands in a JSON-on-disk **retry queue** that flushes every
   5 minutes once Bale's `getMe` says the API is healthy again. Items
   older than 24h are dropped.

## Stack

Python 3.11 · pyrofork (pyrogram fork with 64-bit channel ID support) ·
httpx · pydantic-settings · pytest · ruff · pyright.

## What's NOT mirrored

- Edits and deletes (Bale message IDs aren't tracked in v1)
- Polls, stickers, locations, contacts
- Forwarded-from headers (re-uploaded posts look authored by the bot)

## Quickstart

```bash
git clone <this repo>
cd tg-bale-mirror
make dev                                    # uv sync --extra dev
cp .env.example .env                        # fill in credentials
uv run python -m src.main                   # first run prompts for Telegram login
make run                                    # subsequent runs use the saved session
make test                                   # run the test suite (42 tests)
make check                                  # lint + typecheck + test
```

## Credentials

### Telegram (`TG_API_ID`, `TG_API_HASH`)

1. Visit <https://my.telegram.org> and log in with your phone number.
2. Click "API development tools" → fill in any app name → save.
3. Copy `api_id` (number) and `api_hash` (string) into `.env`.

The first time you run `python -m src.main`, pyrofork will ask for your
phone number and the SMS/Telegram code. It saves a `.session` file
locally — keep this safe; it's equivalent to your Telegram login.

### Telegram source channel (`TG_SOURCE_CHANNEL`)

Either the numeric chat id (recommended, e.g. `-1001234567890`) or
`@username`. To find the numeric id, forward a post from the channel to
[@RawDataBot](https://t.me/RawDataBot) and read `forward_from_chat.id`.

Your account just needs to be a member of this channel; no admin role
required.

### Bale (`BALE_BOT_TOKEN`, `BALE_CHANNEL_ID`)

1. Open Bale, message the bot manager (search for the BotFather
   equivalent on Bale, e.g. `@botfather` on Bale).
2. Create a new bot, copy the token into `.env`.
3. Add the bot as an **admin** of your target Bale channel with
   permission to post messages.
4. `BALE_CHANNEL_ID` can be `@your_channel_username` or the numeric id.

Verify the bot is reachable:

```bash
curl -s "https://tapi.bale.ai/bot$BALE_BOT_TOKEN/getMe" | jq
```

## Configuration

All settings come from `.env` (loaded via pydantic-settings). See
[`.env.example`](./.env.example) for the full list:

| Var | Required | Default | Notes |
|---|---|---|---|
| `TG_API_ID` | yes | — | from my.telegram.org |
| `TG_API_HASH` | yes | — | from my.telegram.org |
| `TG_SESSION_NAME` | no | `tg-bale-mirror` | filename prefix for the session file |
| `TG_SOURCE_CHANNEL` | yes | — | numeric id or `@username` |
| `BALE_BOT_TOKEN` | yes | — | from Bale's BotFather |
| `BALE_CHANNEL_ID` | yes | — | numeric id or `@username` |
| `TEMP_MEDIA_DIR` | no | `./tmp` | scratch dir for in-flight downloads |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` for verbose pyrogram traces |

## Operations

| Command | What it does |
|---|---|
| `make run` | Run the mirror in the foreground |
| `make test` | Run the pytest suite |
| `make lint` | `ruff check src tests` |
| `make format` | `ruff format src tests` |
| `make typecheck` | `pyright` |
| `make check` | Lint + typecheck + test (use before committing) |
| `make clean` | Remove caches |

The retry queue is at `.bale_retry_queue` in the working directory.
You can inspect it as plain JSON; the size is logged on every enqueue
and flush.

## Limits / known gotchas

- **Caption length:** Bale's caption cap is around 1024 chars. We
  truncate at 1000 and send the remainder as a separate message.
- **Video size:** Bale's upload limit is stricter than Telegram's.
  Large videos may fail with a 4xx — they'll land in the retry queue
  and keep failing. Workaround: catch & re-route to `send_document`
  manually if this becomes a problem (not implemented in v1).
- **Sticker / poll / location:** silently dropped. The mirror only
  forwards photos, videos, documents, audio, voice, animations, and
  text.
- **Edits / deletes:** not propagated. A second pass would need a
  `tg_msg_id ↔ bale_msg_id` cache (sqlite); see channel-ghost for the
  pattern.
- **Single source / single target:** v1 supports one source channel and
  one target. Fan-out would need a list of `(BaleClient, channel_id)`.

## Project layout

```
src/
  config.py            # pydantic-settings model
  bale_client.py       # async wrapper around tapi.bale.ai/bot<token>
  retry_queue.py       # JSON-on-disk queue with health-check'd flush loop
  album_debouncer.py   # buffers media_group_id, flushes after 1.5s
  mirror.py            # downloads from TG, uploads to Bale, handles failures
  tg_listener.py       # pyrofork client + handler that feeds the debouncer
  main.py              # entrypoint — wires everything, runs until SIGINT
scripts/
  run.sh
tests/                 # 42 tests covering everything except live API calls
```

## Reference

- Pattern for the Bale Bot API client: [`news-summarizer`](../news-summarizer)
- Pattern for the pyrofork userbot listener: [`channel-ghost`](../channel-ghost)
