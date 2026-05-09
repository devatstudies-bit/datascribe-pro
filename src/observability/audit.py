"""
Layer 8 — Observability: Agent Decision Audit Trail
Writes an immutable, structured audit record for every query so the full
agent decision chain is explainable and compliant with data-governance requirements.

What is recorded (all fields are compliance-safe):
  - session_id, trace_id
  - question_hash  (SHA-256 prefix — never the raw question)
  - input_type     (classification result)
  - plan_steps     (list of Inference:/General: strings)
  - sql_hashes     (list of SHA-256 prefixes — never raw SQL)
  - sql_blocked    (bool — true if any SQL was blocked)
  - model_used
  - latency_ms
  - response_preview (first 200 chars of the response)
  - timestamp_utc

Raw questions and SQL are hashed so sensitive data is never in logs,
while the hash lets a DBA cross-reference against the DB query log.
"""
import hashlib
import time
from src.observability.logger import get_logger

log = get_logger("datascribe.audit")


class AuditLogger:
    """
    One instance per app (singleton via api.py app.state).
    Thread-safe: structlog + stdlib logging are both thread-safe.
    """

    def record(
        self,
        *,
        session_id: str,
        trace_id: str,
        question: str,
        input_type: str,
        plan_steps: list[str],
        sql_statements: list[str],    # raw SQL strings — hashed before logging
        sql_blocked: bool,
        model_used: str,
        latency_ms: int,
        response: str,
    ) -> None:
        log.info(
            "query_audit",
            session_id=session_id,
            trace_id=trace_id,
            question_hash=self._h(question),
            input_type=input_type,
            plan_steps=plan_steps,
            sql_hashes=[self._h(s) for s in sql_statements],
            sql_blocked=sql_blocked,
            model_used=model_used,
            latency_ms=latency_ms,
            response_preview=response[:200] if response else "",
            timestamp_utc=int(time.time()),
        )

    def record_sql_blocked(
        self,
        *,
        session_id: str,
        sql: str,
        reason: str,
    ) -> None:
        """Called by the SQL validator every time a statement is blocked."""
        log.warning(
            "sql_blocked",
            session_id=session_id,
            sql_hash=self._h(sql),
            reason=reason,
        )

    @staticmethod
    def _h(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]
