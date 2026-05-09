"""
Layer 3 — Orchestration: Shared Conversation State
Flows through every LangGraph node. Annotated reducers ensure safe
merging when nodes run concurrently or are re-entered.
"""
from __future__ import annotations
from typing import Annotated, Optional
from typing_extensions import TypedDict, NotRequired
import operator
import networkx as nx


def _keep_existing(old, new):
    """Once the graph is set, never overwrite it (it is cached for the session)."""
    return old if old is not None else new


def _replace(old, new):
    return new if new is not None else old


class ConversationState(TypedDict):
    # ── Input ────────────────────────────────────────────────────────────────
    question: str
    session_id: str

    # ── Layer 2: Classification ───────────────────────────────────────────────
    input_type: Annotated[str, _replace]          # DATABASE_QUERY | GREETING | CHITCHAT | FAREWELL

    # ── Layer 3: Plan ────────────────────────────────────────────────────────
    plan: Annotated[list[str], _replace]          # e.g. ["Inference: ...", "General: ..."]

    # ── Layer 3: Results ─────────────────────────────────────────────────────
    db_results: NotRequired[str]
    response: NotRequired[str]

    # ── Layer 5: Schema graph (cached across turns within a session) ──────────
    db_graph: Annotated[Optional[nx.Graph], _keep_existing]

    # ── Layer 4/8: Metadata ───────────────────────────────────────────────────
    model_used: NotRequired[str]
    schema_cached: NotRequired[bool]             # True if schema was served from cache
