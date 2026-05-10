# tg-bale-mirror

One-way mirror from a Telegram channel to a Bale messenger channel.
Every new post (text, photo, video, document, album) on the source
Telegram channel is re-published to the target Bale channel with the
caption preserved.

## How it works

1. A Pyrogram (pyrofork) listener subscribes to the source Telegram
   channel. Two listener modes are supported:
   - **Bot mode (default):** a Telegram bot you created via @BotFather
     and added as **admin** of the source channel.
   - **Userbot mode (legacy):** your personal Telegram account; needs
     to be a member of the channel. Use this only when you can't add a
     bot as admin of the source.
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
cp .env.example .env                        # fill in credentials (bot mode by default)
make run                                    # bot mode needs no interactive login
make test                                   # run the test suite
make check                                  # lint + typecheck + test
```

## Credentials

### Telegram (`TG_API_ID`, `TG_API_HASH`)

These identify the *app*, not the user — required in both bot and
userbot mode.

1. Visit <https://my.telegram.org> and log in with your phone number.
2. Click "API development tools" → fill in any app name → save.
3. Copy `api_id` (number) and `api_hash` (string) into `.env`.

### Listener mode

Pick one of three modes. Precedence at startup:

1. `TG_BOT_TOKEN` set → **bot mode** (recommended). Wins over
   `TG_SESSION_STRING` with a logged warning if both are configured.
2. `TG_SESSION_STRING` set → userbot in-memory mode (VPS/headless).
3. Neither set → userbot interactive `.session` file (local dev only).

#### Bot mode (`TG_BOT_TOKEN`) — default

1. Talk to [@BotFather](https://t.me/BotFather), create a bot, copy the
   token into `TG_BOT_TOKEN`.
2. Add the bot to the source channel as **admin** with at least "Post
   Messages" / read access. Without admin status the bot will not
   receive `channel_post` updates and the mirror will sit idle.
3. Run `make run` — no interactive login needed.

Caveat: bot accounts have a stricter per-file download cap than
userbots. Pyrofork uses MTProto so this is usually fine, but very
large videos may fail and end up in the retry queue. Switch to
userbot mode if that becomes a problem.

#### Userbot mode (`TG_SESSION_STRING` or `.session` file) — legacy

Use this only when you can't make the bot an admin (e.g. you're
mirroring someone else's channel and you're just a member).

The first time you run `python -m src.main` without a session string,
pyrofork will ask for your phone number and the SMS/Telegram code. It
saves a `.session` file locally — keep this safe; it's equivalent to
your Telegram login. For VPS/headless deploys, run `make session` once
locally and paste the resulting string into `TG_SESSION_STRING`.

### Telegram source channel (`TG_SOURCE_CHANNEL`)

Either the numeric chat id (recommended, e.g. `-1001234567890`) or
`@username`. To find the numeric id, forward a post from the channel to
[@RawDataBot](https://t.me/RawDataBot) and read `forward_from_chat.id`.

In bot mode the bot must be a channel admin; in userbot mode your
account just needs to be a member.

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
| `TG_API_ID` | yes | — | from my.telegram.org (both modes) |
| `TG_API_HASH` | yes | — | from my.telegram.org (both modes) |
| `TG_SESSION_NAME` | no | `tg-bale-mirror` | filename prefix for the userbot session file |
| `TG_BOT_TOKEN` | no* | — | bot listener mode (preferred). *One of `TG_BOT_TOKEN` / `TG_SESSION_STRING` / interactive `.session` is required at runtime. |
| `TG_SESSION_STRING` | no* | — | userbot listener mode (in-memory). |
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

## VPS deploy (your-vps, systemd)

Same pattern as `news-summarizer`. The repo lives at `/opt/tg-bale-mirror`,
runs under a systemd unit, deploys via `git pull` + `uv sync` + restart.

**One-time bootstrap:**

```bash
# 1. Generate a session string locally (interactive — phone + login code)
make session
# Copy the output into your local .env as TG_SESSION_STRING=...

# 2. Push code to GitHub origin/main first (deploy refuses if local is ahead)
git push -u origin main

# 3. SSH in once, clone the repo, push .env, install + enable the systemd unit
ssh your-vps 'git clone https://github.com/aliir74/tg-bale-mirror /opt/tg-bale-mirror'
make push-env
make install-systemd
make start
make logs-follow
```

**Day-to-day:**

```bash
make deploy        # git pull + uv sync + restart on the VPS
make restart       # restart only
make logs          # last 100 lines
make logs-follow   # tail -f journal
make status        # systemctl status
make ssh           # interactive shell on the VPS in /opt/tg-bale-mirror
make push-env      # sync local .env to VPS (3s abort window)
make pull-state    # download .bale_retry_queue to ./state-backup/
```

## Reference

- Pattern for the Bale Bot API client: [`news-summarizer`](../news-summarizer)
- Pattern for the pyrofork userbot listener + session-string flow: [`channel-ghost`](../channel-ghost)
