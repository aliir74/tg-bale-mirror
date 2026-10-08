"""Tests that bot tokens never reach log output."""

from __future__ import annotations

import logging
import sys

import httpx

from src.main import RedactingFormatter

URL = "https://tapi.bale.ai/bot123456:SECRET-token_x/sendMessage"


def _format(record: logging.LogRecord) -> str:
    return RedactingFormatter("%(message)s").format(record)


def test_redacts_token_in_message() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "POST %s", (URL,), None)

    out = _format(record)

    assert "SECRET" not in out
    assert "/bot<redacted>/sendMessage" in out


def test_redacts_token_in_http_status_error_traceback() -> None:
    req = httpx.Request("POST", URL)
    try:
        httpx.Response(403, request=req).raise_for_status()
    except httpx.HTTPStatusError:
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "send failed", (), sys.exc_info()
        )

    out = _format(record)

    assert "SECRET" not in out
    assert "bot<redacted>" in out
