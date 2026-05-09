"""
Layer 1 — Consumer: FastAPI Application
Provides:
  GET  /          → serves the chat frontend
  GET  /health    → health check
  GET  /schema    → list discovered tables for a session
  WS   /ws/{session_id}  → WebSocket chat endpoint (primary interface)

WebSocket message protocol (server → client):
  {"type": "session_info",   "session_id": str}
  {"type": "status",         "text": str, "stage": str}
  {"type": "schema_loaded",  "tables": list[str], "cached": bool}
  {"type": "plan",           "steps": list[str]}
  {"type": "step_start",     "index": int, "text": str}
  {"type": "chunk",          "text": str}
  {"type": "done",           "model": str, "latency_ms": int, "turns": int}
  {"type": "error",          "message": str}

WebSocket message protocol (client → server):
  {"type": "query",  "question": str}
  {"type": "reset"}
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.config.settings import get_settings
from src.consumer.session_manager import SessionManager
from src.orchestration.graph import build_graph
from src.rag.schema_graph import SchemaGraphBuilder
from src.observability.logger import get_logger, setup_logging, bind_request_context
from src.observability.metrics import (
    REQUESTS_TOTAL, REQUEST_LATENCY, ACTIVE_SESSIONS, metrics_response,
)
from src.observability.tracing import setup_tracing, current_trace_id
from src.observability.audit import AuditLogger

log = get_logger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"

# ── App lifecycle ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging()
    setup_tracing()
    app.state.settings = settings
    app.state.session_mgr = SessionManager()
    app.state.audit = AuditLogger()
    app.state.graph = build_graph(settings)
    log.info("startup", provider=settings.llm_provider, database=settings.database_url)
    yield
    log.info("shutdown")


app = FastAPI(title="DataScribe Pro", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend static files
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── HTTP endpoints ─────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    html = FRONTEND_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return JSONResponse({"status": "DataScribe Pro API is running. Open /docs for API details."})


@app.get("/health")
async def health():
    return {"status": "ok", "provider": app.state.settings.llm_provider}


@app.get("/schema")
async def schema(session_id: str | None = None):
    mgr: SessionManager = app.state.session_mgr
    session = mgr.get(session_id) if session_id else None
    if session and session.db_graph:
        tables = SchemaGraphBuilder.table_names(session.db_graph)
        return {"tables": tables, "cached": True}
    return {"tables": [], "cached": False}


@app.get("/metrics")
async def metrics():
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


# ── WebSocket endpoint ────────────────────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()

    mgr: SessionManager = app.state.session_mgr
    langgraph = app.state.graph
    settings = app.state.settings
    audit: AuditLogger = app.state.audit

    session = mgr.get_or_create(session_id)
    ACTIVE_SESSIONS.inc()

    await _send(websocket, {
        "type": "session_info",
        "session_id": session.session_id,
        "provider": settings.llm_provider,
    })

    # If we already have a schema, tell the client immediately
    if session.db_graph:
        await _send(websocket, {
            "type": "schema_loaded",
            "tables": SchemaGraphBuilder.table_names(session.db_graph),
            "cached": True,
        })

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, {"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get("type") == "reset":
                mgr.reset(session.session_id)
                await _send(websocket, {"type": "status", "text": "Session reset.", "stage": "reset"})
                continue

            question = (msg.get("question") or "").strip()
            if not question:
                continue

            await _handle_query(websocket, question, session, langgraph, mgr, settings, audit)

    except WebSocketDisconnect:
        log.info("ws_disconnected", session_id=session_id)
    finally:
        ACTIVE_SESSIONS.dec()


async def _handle_query(websocket, question, session, langgraph, mgr, settings, audit: AuditLogger):
    t0 = time.monotonic()
    bind_request_context(session.session_id)

    await _send(websocket, {"type": "status", "text": "Analyzing your question…", "stage": "classify"})

    initial_state = {
        "question": question,
        "session_id": session.session_id,
        "db_graph": session.db_graph,
        "input_type": "",
        "plan": [],
    }

    final_input_type = "UNKNOWN"
    final_plan: list[str] = []
    final_response = ""
    sql_blocked = False

    try:
        async for chunk in langgraph.astream(initial_state, stream_mode="updates"):
            node_name = next(iter(chunk))
            state_update = chunk[node_name]

            if node_name == "classify_input":
                final_input_type = state_update.get("input_type", "UNKNOWN")
                label = "Looking up database…" if final_input_type == "DATABASE_QUERY" else "Preparing response…"
                await _send(websocket, {"type": "status", "text": label, "stage": "classify"})

            elif node_name == "discover_database":
                cached = state_update.get("schema_cached", False)
                new_graph = state_update.get("db_graph")
                if new_graph:
                    mgr.update_graph(session.session_id, new_graph)
                    session.db_graph = new_graph

                graph = session.db_graph
                tables = SchemaGraphBuilder.table_names(graph) if graph else []
                await _send(websocket, {
                    "type": "schema_loaded",
                    "tables": tables,
                    "cached": cached,
                    "text": "Schema loaded from cache." if cached else f"Schema discovered: {len(tables)} tables.",
                })

            elif node_name == "create_plan":
                final_plan = state_update.get("plan", [])
                if final_plan:
                    await _send(websocket, {"type": "plan", "steps": final_plan})
                    for i, step in enumerate(final_plan):
                        if step.lower().startswith("inference:"):
                            await _send(websocket, {"type": "step_start", "index": i, "text": step})

            elif node_name == "execute_plan":
                await _send(websocket, {"type": "status", "text": "Executing query…", "stage": "execute"})

            elif node_name == "generate_response":
                final_response = state_update.get("response", "")
                if final_response:
                    words = final_response.split(" ")
                    for word in words:
                        await _send(websocket, {"type": "chunk", "text": word + " "})
                        await asyncio.sleep(0.012)

        mgr.increment_turn(session.session_id)
        latency_ms = int((time.monotonic() - t0) * 1000)
        trace_id = current_trace_id()

        REQUESTS_TOTAL.labels(input_type=final_input_type).inc()
        REQUEST_LATENCY.labels(input_type=final_input_type).observe(latency_ms / 1000)

        audit.record(
            session_id=session.session_id,
            trace_id=trace_id,
            question=question,
            input_type=final_input_type,
            plan_steps=final_plan,
            sql_statements=[],
            sql_blocked=sql_blocked,
            model_used=settings.llm_provider,
            latency_ms=latency_ms,
            response=final_response,
        )

        await _send(websocket, {
            "type": "done",
            "model": settings.llm_provider,
            "latency_ms": latency_ms,
            "turns": session.turn_count,
            "trace_id": trace_id,
        })

    except Exception as exc:
        log.error("query_error", error=str(exc), session_id=session.session_id)
        await _send(websocket, {"type": "error", "message": str(exc)})


async def _send(ws: WebSocket, data: dict):
    try:
        await ws.send_json(data)
    except Exception:
        pass
