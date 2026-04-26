"""
utils/helpers.py — Logging setup, retry decorators, and formatting helpers.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import re
import time
from typing import Any, Callable, TypeVar

from rich.console import Console
from rich.logging import RichHandler
from tenacity import retry, stop_after_attempt, wait_exponential

F = TypeVar("F", bound=Callable[..., Any])


_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    # Generic bearer-ish secrets that contain at least 24 base64-ish chars.
    re.compile(r"\b[A-Za-z0-9+/=_-]{40,}\b"),
]


class _SecretRedactingFilter(logging.Filter):
    """Replaces anything matching common API-key/secret shapes with [REDACTED].
    Cheap defense-in-depth against accidental traceback leaks. The third
    pattern is broad on purpose; it errs on the side of redacting too much
    rather than leaking creds.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = msg
        for pat in _SECRET_PATTERNS:
            redacted = pat.sub("[REDACTED]", redacted)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure Rich-based logging for the whole application."""
    handler = RichHandler(
        rich_tracebacks=True,
        markup=True,
        console=Console(legacy_windows=False),
    )
    handler.addFilter(_SecretRedactingFilter())
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
    )
    # Silence noisy third-party loggers
    for name in ("ccxt", "websockets", "aiohttp", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return logging.getLogger("cryptobot")


logger = setup_logging()


def retry_async(attempts: int = 3, wait_min: float = 1.0, wait_max: float = 10.0):
    """Decorator: retry an async function on exception with exponential backoff."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        @retry(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=1, min=wait_min, max=wait_max),
            reraise=True,
        )
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper  # type: ignore[return-value]
    return decorator


def pct_change(old: float, new: float) -> float:
    """Return percentage change from old to new."""
    if old == 0:
        return 0.0
    return (new - old) / old


def format_usdt(value: float) -> str:
    return f"${value:,.2f}"


def format_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"


def now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
