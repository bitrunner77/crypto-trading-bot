"""
tests/test_logging.py — secret-redacting log filter.
"""
import logging

import pytest


def _make_record(msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="cryptobot.test", level=logging.INFO,
        pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


def test_anthropic_key_redacted():
    from utils.helpers import _SecretRedactingFilter
    f = _SecretRedactingFilter()
    rec = _make_record("Loaded ANTHROPIC_API_KEY=sk-ant-abcdef0123456789xyzABCDEFGHIJ")
    f.filter(rec)
    assert "sk-ant-" not in rec.getMessage()
    assert "[REDACTED]" in rec.getMessage()


def test_normal_message_passthrough():
    from utils.helpers import _SecretRedactingFilter
    f = _SecretRedactingFilter()
    rec = _make_record("Round 5 complete: balance $9,876.54")
    f.filter(rec)
    assert "Round 5" in rec.getMessage()


def test_filter_never_blocks_emission():
    from utils.helpers import _SecretRedactingFilter
    f = _SecretRedactingFilter()
    rec = _make_record("Anything")
    assert f.filter(rec) is True
