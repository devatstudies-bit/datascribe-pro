"""
Layer 8 — Observability: Prometheus Metrics
All counters and histograms are defined here and imported wherever
instrumentation is needed.  The /metrics HTTP endpoint is registered
in api.py so Prometheus can scrape it.

Metrics catalogue:
  datascribe_requests_total          — every WS query, labelled by input_type
  datascribe_request_latency_seconds — end-to-end latency histogram
  datascribe_llm_calls_total         — LLM invocations by agent + provider
  datascribe_llm_latency_seconds     — per-LLM-call latency
  datascribe_schema_cache_hits_total — schema served from session cache
  datascribe_schema_cache_misses_total — schema rebuilt via SQLAlchemy
  datascribe_sql_blocked_total       — read-only violations caught
  datascribe_plan_steps_total        — steps executed, by type (Inference/General)
  datascribe_agent_errors_total      — unhandled exceptions per agent
  datascribe_active_sessions         — current live sessions (gauge)
"""
from prometheus_client import Counter, Histogram, Gauge, REGISTRY, generate_latest, CONTENT_TYPE_LATEST

# ── Request metrics ──────────────────────────────────────────────────────────
REQUESTS_TOTAL = Counter(
    "datascribe_requests_total",
    "Total WebSocket queries processed",
    ["input_type"],           # DATABASE_QUERY | GREETING | CHITCHAT | FAREWELL
)

REQUEST_LATENCY = Histogram(
    "datascribe_request_latency_seconds",
    "End-to-end query latency (classify → response)",
    ["input_type"],
    buckets=[0.5, 1, 2, 5, 10, 15, 30, 60, 120],
)

# ── LLM metrics ──────────────────────────────────────────────────────────────
LLM_CALLS_TOTAL = Counter(
    "datascribe_llm_calls_total",
    "LLM invocations",
    ["agent", "provider"],    # agent: planner|inference|supervisor|classify
)

LLM_LATENCY = Histogram(
    "datascribe_llm_latency_seconds",
    "Per-LLM-call latency",
    ["agent", "provider"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
)

LLM_ERRORS_TOTAL = Counter(
    "datascribe_llm_errors_total",
    "LLM call failures",
    ["agent", "provider"],
)

# ── Schema / RAG metrics ─────────────────────────────────────────────────────
SCHEMA_CACHE_HITS = Counter(
    "datascribe_schema_cache_hits_total",
    "Schema graph served from session cache (no SQLAlchemy call)",
)

SCHEMA_CACHE_MISSES = Counter(
    "datascribe_schema_cache_misses_total",
    "Schema rebuilt via SQLAlchemy introspection",
)

# ── Security metrics (Layer 6) ───────────────────────────────────────────────
SQL_BLOCKED_TOTAL = Counter(
    "datascribe_sql_blocked_total",
    "SQL statements blocked by read-only enforcer",
    ["reason"],               # ast_node | keyword
)

# ── Orchestration metrics ────────────────────────────────────────────────────
PLAN_STEPS_TOTAL = Counter(
    "datascribe_plan_steps_total",
    "Plan steps executed",
    ["step_type"],            # Inference | General
)

AGENT_ERRORS_TOTAL = Counter(
    "datascribe_agent_errors_total",
    "Unhandled exceptions per agent",
    ["agent"],                # planner | inference | supervisor | discovery
)

# ── Infrastructure metrics (Layer 7) ────────────────────────────────────────
ACTIVE_SESSIONS = Gauge(
    "datascribe_active_sessions",
    "Number of live WebSocket sessions",
)


def metrics_response() -> tuple[bytes, str]:
    """Return (body, content_type) for a Prometheus /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
