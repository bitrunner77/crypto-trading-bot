"""
utils/helpers.py — Logging setup, retry decorators, and formatting helpers.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, TypeVar

from rich.console import Console
from rich.logging import RichHandler
from tenacity import retry, stop_after_attempt, wait_exponential

F = TypeVar("F", bound=Callable[..., Any])


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure Rich-based logging for the whole application."""
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True, console=Console(legacy_windows=False))],
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
