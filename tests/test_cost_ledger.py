"""Cost-ledger poisoning protection — glc.db.log_call validation (Leak 10).

The exploit: any in-process code can call log_call with absurd token counts
and poison /v1/cost/by_agent for any agent_id.

    import glc.db
    glc.db.log_call(provider="gemini", model="x",
                    input_tokens=999_999_999, agent="victim", status="ok")

After the fix all numeric fields are clamped to sane bounds before writing.
Negative values become 0; values above the cap become the cap. Both produce
a WARNING log so operators can detect anomalies. Invariant 8.
"""

from __future__ import annotations

import glc.db as db


def test_normal_tokens_stored_unchanged():
    db.log_call(provider="gemini", model="flash", input_tokens=100, output_tokens=50)
    row = db.recent(limit=1)[0]
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 50


def test_absurd_token_count_clamped(caplog):
    """999_999_999 input tokens must be clamped to _MAX_TOKENS, not stored raw."""
    import logging

    with caplog.at_level(logging.WARNING, logger="glc.db"):
        db.log_call(provider="gemini", model="flash", input_tokens=999_999_999)
    row = db.recent(limit=1)[0]
    assert row["input_tokens"] == db._MAX_TOKENS
    assert any("input_tokens" in m for m in caplog.messages)


def test_negative_tokens_clamped_to_zero(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="glc.db"):
        db.log_call(provider="gemini", model="flash", input_tokens=-500, output_tokens=-1)
    row = db.recent(limit=1)[0]
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0


def test_absurd_latency_clamped():
    db.log_call(provider="gemini", model="flash", latency_ms=99_999_999)
    row = db.recent(limit=1)[0]
    assert row["latency_ms"] == db._MAX_LATENCY_MS


def test_absurd_tool_calls_clamped():
    db.log_call(provider="gemini", model="flash", tool_calls=99_999)
    row = db.recent(limit=1)[0]
    assert row["tool_calls"] == db._MAX_TOOL_CALLS


def test_absurd_retries_clamped():
    db.log_call(provider="gemini", model="flash", retries=99_999)
    row = db.recent(limit=1)[0]
    assert row["retries"] == db._MAX_RETRIES


def test_non_numeric_token_value_recorded_as_zero(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="glc.db"):
        db.log_call(provider="gemini", model="flash", input_tokens="not-a-number")
    row = db.recent(limit=1)[0]
    assert row["input_tokens"] == 0
