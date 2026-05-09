"""
Layer 8 — Observability: OpenTelemetry Distributed Tracing
Creates one root span per WebSocket query. Each LangGraph node and
agent call adds a child span so the full execution tree is visible
in Jaeger / Grafana Tempo / any OTLP-compatible backend.

By default (no OTLP_ENDPOINT set) spans are written to stdout as
structured JSON so they are always visible in logs even without
a tracing backend running.

Span hierarchy per query:
  ws.query
    ├── graph.classify_input
    ├── graph.discover_database          (or cache hit attr)
    ├── graph.create_plan
    │     └── agent.planner.plan
    ├── graph.execute_plan
    │     └── agent.inference.query  (one per Inference step)
    └── graph.generate_response
"""
import os
from contextlib import contextmanager
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace import Status, StatusCode


_tracer: trace.Tracer | None = None


def setup_tracing(service_name: str = "datascribe-pro", otlp_endpoint: str = "") -> None:
    """
    Call once at startup.
    If OTLP_ENDPOINT env var (or arg) is set, ships spans to that collector.
    Always attaches a ConsoleSpanExporter as a fallback so spans appear in logs.
    """
    global _tracer

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    # Console exporter — always on; useful for dev and for log-based tracing
    provider.add_span_processor(
        SimpleSpanProcessor(ConsoleSpanExporter())
    )

    endpoint = otlp_endpoint or os.getenv("OTLP_ENDPOINT", "")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
            )
        except Exception as exc:
            # Don't crash the app if the exporter isn't reachable at startup
            print(f"[tracing] OTLP exporter not available: {exc}")

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        # Lazy init with no-op tracer if setup_tracing was never called
        return trace.get_tracer("datascribe-pro")
    return _tracer


@contextmanager
def span(name: str, **attributes):
    """
    Convenience context manager that creates a child span, sets attributes,
    and records exceptions automatically.

    Usage:
        with span("agent.planner.plan", session_id=sid, question_len=len(q)):
            result = await planner.plan(question)
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as s:
        for k, v in attributes.items():
            s.set_attribute(k, str(v))
        try:
            yield s
        except Exception as exc:
            s.set_status(Status(StatusCode.ERROR, str(exc)))
            s.record_exception(exc)
            raise


def current_trace_id() -> str:
    """Return the hex trace-id of the active span, or empty string."""
    ctx = trace.get_current_span().get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return ""
