"""One-time Telegram session-string generator.

Run locally and paste your phone number + login code when prompted. The
resulting session string is printed to stdout — copy it into .env as
TG_SESSION_STRING.

Usage:
    uv run python scripts/generate_session.py

Env vars required: TG_API_ID, TG_API_HASH.
Get both from https://my.telegram.org/apps — one-time step per user account.

Do NOT commit the generated session string. It grants full access to your
Telegram account.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from pyrogram.client import Client


async def main() -> None:
    load_dotenv()
    api_id_raw = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id_raw or not api_hash:
        print("ERROR: set TG_API_ID and TG_API_HASH in .env first.", file=sys.stderr)
        sys.exit(1)

    async with Client(
        name="tg_bale_mirror_session_gen",
        api_id=int(api_id_raw),
        api_hash=api_hash,
        in_memory=True,
    ) as client:
        session_string = await client.export_session_string()

    print()
    print("=" * 70)
    print("Copy the line below into .env as TG_SESSION_STRING=...")
    print("(do NOT commit it — it grants full access to your Telegram account)")
    print("=" * 70)
    print(session_string)
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
