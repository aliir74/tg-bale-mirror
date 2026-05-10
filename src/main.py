"""Entrypoint: wire Pyrogram + Bale + retry queue and run until SIGINT/SIGTERM."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from dotenv import load_dotenv
from pyrogram.client import Client

from src.album_debouncer import AlbumDebouncer
from src.bale_client import BaleClient
from src.config import Config
from src.mirror import Mirror
from src.retry_queue import RetryQueue
from src.tg_listener import TgListener

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def build_tg_client_kwargs(config: Config) -> dict[str, object]:
    """Pick pyrofork Client kwargs based on config.

    Precedence:
      1. tg_bot_token set        → bot mode (in-memory). Wins over session_string
         with a warning if both are configured.
      2. tg_session_string set   → userbot in-memory mode.
      3. neither set             → userbot interactive (.session file on disk).
    """
    kwargs: dict[str, object] = {
        "name": config.tg_session_name,
        "api_id": config.tg_api_id,
        "api_hash": config.tg_api_hash,
    }
    if config.tg_bot_token:
        if config.tg_session_string:
            logger.warning(
                "both TG_BOT_TOKEN and TG_SESSION_STRING are set; "
                "bot mode wins, ignoring TG_SESSION_STRING"
            )
        kwargs["bot_token"] = config.tg_bot_token
        kwargs["in_memory"] = True
        logger.info("using bot mode (in-memory)")
    elif config.tg_session_string:
        kwargs["session_string"] = config.tg_session_string
        kwargs["in_memory"] = True
        logger.info("using session string from env (in-memory mode)")
    else:
        logger.info("using session file %s.session", config.tg_session_name)
    return kwargs


async def main() -> None:
    load_dotenv()
    config = Config()  # type: ignore[call-arg]  # populated from env
    _configure_logging(config.log_level)
    logger.info("starting tg-bale-mirror")

    bale = BaleClient(config.bale_bot_token, config.bale_channel_id)
    queue = RetryQueue(bale)
    tg = Client(**build_tg_client_kwargs(config))  # type: ignore[arg-type]
    mirror = Mirror(tg, bale, queue, config.temp_media_dir)
    debouncer = AlbumDebouncer(on_flush=mirror.handle)
    listener = TgListener(tg, config.tg_source_channel, debouncer)

    await bale.start()
    await queue.start()
    await tg.start()

    shutdown = asyncio.Event()
    try:
        await listener.resolve_source()
        listener.register()
        logger.info("listening for posts")

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, shutdown.set)
        await shutdown.wait()
    finally:
        logger.info("shutting down")
        await debouncer.flush_all()
        await tg.stop()
        await queue.stop()
        await bale.stop()


if __name__ == "__main__":
    asyncio.run(main())
