"""V9-compatible per-call ledger. Same schema as llm_gatewayV9/db.py, but
the database lives under ~/.glc/ so the gateway is installable as a daemon
without writing into the source tree.

Note: this is the *worker call* ledger, used by /v1/cost/by_agent. The
audit log (every channel message, policy verdict, tool dispatch) is a
separate append-only store under glc/audit/store.py.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

_log = logging.getLogger(__name__)

# Upper bounds for numeric ledger fields.
# Values above these are clamped, not rejected, so a legitimate large-context
# call (e.g. Gemini 2.5 with a 2M-token context window) still records correctly
# while an in-process attacker cannot inflate counts into the billions.
_MAX_TOKENS = 2_000_000      # 2 M tokens — covers today's largest context windows
_MAX_LATENCY_MS = 300_000    # 5 minutes
_MAX_CHARS = 10_000_000      # 10 M characters (~2.5 M tokens worth of text)
_MAX_TOOL_CALLS = 1_000
_MAX_RETRIES = 100
_MAX_EMBED_DIM = 100_000


def _clamp(value: int | float | None, lo: int, hi: int, field: str) -> int:
    """Return *value* clamped to [lo, hi].

    Negative values are raised to *lo* and values above *hi* are reduced to
    *hi*. Both cases are logged at WARNING level so operators can detect
    attempted ledger poisoning or genuine provider anomalies.
    """
    if value is None:
        return lo
    try:
        v = int(value)
    except (TypeError, ValueError):
        _log.warning("db.log_call: non-numeric value for %s=%r; recording 0", field, value)
        return lo
    if v < lo:
        _log.warning("db.log_call: %s=%d is negative; clamping to %d", field, v, lo)
        return lo
    if v > hi:
        _log.warning(
            "db.log_call: %s=%d exceeds limit %d — possible ledger poisoning; clamping",
            field, v, hi,
        )
        return hi
    return v

DEFAULT_DIR = Path(os.path.expanduser("~/.glc"))
DB_PATH = os.getenv("GLC_GATEWAY_DB", str(DEFAULT_DIR / "gateway.sqlite"))


def _ensure_parent() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def conn():
    _ensure_parent()
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_create_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                status TEXT,
                error TEXT,
                prompt_chars INTEGER DEFAULT 0,
                response_chars INTEGER DEFAULT 0,
                override TEXT,
                attempted TEXT,
                tool_calls INTEGER DEFAULT 0,
                reasoning_applied INTEGER DEFAULT 0,
                tool_dialect TEXT,
                call_role TEXT DEFAULT 'worker',
                router_decision TEXT,
                embed_dim INTEGER,
                agent TEXT,
                session TEXT,
                retries INTEGER DEFAULT 0
            )"""
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON calls(ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prov_ts ON calls(provider, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_role_ts ON calls(call_role, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_agent_ts ON calls(agent, ts DESC)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_session_ts ON calls(session, ts DESC)")


def log_call(
    provider,
    model,
    input_tokens=0,
    output_tokens=0,
    latency_ms=0,
    status="ok",
    error=None,
    prompt_chars=0,
    response_chars=0,
    override=None,
    attempted=None,
    cache_create_tokens=0,
    cache_read_tokens=0,
    tool_calls=0,
    reasoning_applied=False,
    tool_dialect=None,
    call_role="worker",
    router_decision=None,
    embed_dim=None,
    agent=None,
    session=None,
    retries=0,
) -> None:
    # Clamp all numeric fields before writing. Without this any in-process code
    # (e.g. a hostile adapter sharing the gateway process) can call log_call with
    # input_tokens=999_999_999 and inflate the cost ledger for any agent_id —
    # poisoning /v1/cost/by_agent and potentially triggering fake budget alerts.
    # This is Leak 10 / invariant 8. Clamping (not rejection) is used so a
    # legitimate large-context call still records; the WARNING log makes
    # anomalous values visible to operators.
    with conn() as c:
        c.execute(
            """INSERT INTO calls (ts, provider, model, input_tokens, output_tokens,
                                  cache_create_tokens, cache_read_tokens,
                                  latency_ms, status, error, prompt_chars, response_chars,
                                  override, attempted, tool_calls, reasoning_applied, tool_dialect,
                                  call_role, router_decision, embed_dim,
                                  agent, session, retries)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                time.time(),
                str(provider or "unknown"),
                str(model or "unknown"),
                _clamp(input_tokens, 0, _MAX_TOKENS, "input_tokens"),
                _clamp(output_tokens, 0, _MAX_TOKENS, "output_tokens"),
                _clamp(cache_create_tokens, 0, _MAX_TOKENS, "cache_create_tokens"),
                _clamp(cache_read_tokens, 0, _MAX_TOKENS, "cache_read_tokens"),
                _clamp(latency_ms, 0, _MAX_LATENCY_MS, "latency_ms"),
                status,
                error,
                _clamp(prompt_chars, 0, _MAX_CHARS, "prompt_chars"),
                _clamp(response_chars, 0, _MAX_CHARS, "response_chars"),
                override,
                attempted,
                _clamp(tool_calls, 0, _MAX_TOOL_CALLS, "tool_calls"),
                1 if reasoning_applied else 0,
                tool_dialect,
                call_role,
                router_decision,
                _clamp(embed_dim, 0, _MAX_EMBED_DIM, "embed_dim") if embed_dim is not None else None,
                agent,
                session,
                _clamp(retries, 0, _MAX_RETRIES, "retries"),
            ),
        )


def by_agent(session=None, since=None):
    where = ["ts >= ?"]
    # Day-rollover fix: bucket by calendar day, not by 24h window.
    args = [since if since is not None else (time.time() - (time.time() % 86400))]
    if session:
        where.append("session=?")
        args.append(session)
    q = (
        "SELECT agent, provider, COUNT(*) AS calls, "
        "SUM(input_tokens) AS in_tok, SUM(output_tokens) AS out_tok, "
        "SUM(latency_ms) AS total_latency_ms, "
        "SUM(retries) AS total_retries, "
        "SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok, "
        "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
        "FROM calls WHERE " + " AND ".join(where) + " AND agent IS NOT NULL "
        "GROUP BY agent, provider"
    )
    with conn() as c:
        rows = c.execute(q, args).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["agent"], []).append(dict(r))
        return out


def recent(limit=100, provider=None, status=None):
    q = "SELECT * FROM calls"
    where, args = [], []
    if provider:
        where.append("provider=?")
        args.append(provider)
    if status:
        where.append("status=?")
        args.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY ts DESC LIMIT ?"
    args.append(limit)
    with conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def aggregate(call_role=None):
    now = time.time()
    day_start = now - (now % 86400)
    q = """SELECT provider,
                  COUNT(*) AS calls,
                  SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok_calls,
                  SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors,
                  SUM(input_tokens) AS in_tok,
                  SUM(output_tokens) AS out_tok,
                  SUM(cache_read_tokens) AS cache_reads,
                  SUM(cache_create_tokens) AS cache_creates,
                  SUM(tool_calls) AS tool_calls,
                  AVG(latency_ms) AS avg_latency,
                  MAX(ts) AS last_ts
             FROM calls WHERE ts >= ?"""
    args = [day_start]
    if call_role == "worker":
        q += " AND (call_role='worker' OR call_role IS NULL)"
    elif call_role == "router":
        q += " AND call_role LIKE 'router%'"
    elif call_role:
        q += " AND call_role=?"
        args.append(call_role)
    q += " GROUP BY provider"
    with conn() as c:
        rows = c.execute(q, args).fetchall()
        return {r["provider"]: dict(r) for r in rows}
