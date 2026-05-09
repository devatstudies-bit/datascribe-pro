"""
Layer 8 — Observability: Structured Logger
Uses structlog to emit JSON log lines that can be shipped to ELK,
CloudWatch, Azure Monitor, or any log aggregator.

Every log record automatically includes:
  timestamp, level, logger, event, plus any bound context vars
  (session_id, agent, model are bound per-request in api.py)

Usage:
    from src.observability.logger import get_logger
    log = get_logger(__name__)
    log.info("plan_created", steps=plan, question_hash=h)
"""
import logging
import sys
import structlog


def setup_logging(level: str = "INFO") -> None:
    """
    Call once at startup. Configures both stdlib logging (so third-party
    libraries still work) and structlog with JSON rendering.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors pipeline
    shared_processors = [
        structlog.contextvars.merge_contextvars,          # pick up bound context
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Silence noisy libs
    for lib in ("httpx", "httpcore", "openai", "anthropic", "uvicorn.access"):
        logging.getLogger(lib).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)


def bind_request_context(session_id: str, **kwargs) -> None:
    """Bind values that appear on every log line for the current async task."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(session_id=session_id, **kwargs)


def unbind_request_context() -> None:
    structlog.contextvars.clear_contextvars()
