"""
Layer 1 — Session Manager
In-memory session store keyed by session_id.
Persists the db_graph (nx.Graph) across WebSocket turns so schema
discovery only runs once per session.

Production upgrade path: replace _store with a Redis client that
pickle-serialises the graph with a configurable TTL.
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from typing import Optional
import networkx as nx


@dataclass
class Session:
    session_id: str
    db_graph: Optional[nx.Graph] = None
    turn_count: int = 0


class SessionManager:
    def __init__(self):
        self._store: dict[str, Session] = {}

    def create(self) -> Session:
        sid = str(uuid.uuid4())
        session = Session(session_id=sid)
        self._store[sid] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        return self._store.get(session_id)

    def get_or_create(self, session_id: Optional[str]) -> Session:
        if session_id and session_id in self._store:
            return self._store[session_id]
        return self.create()

    def update_graph(self, session_id: str, graph: nx.Graph) -> None:
        if session_id in self._store:
            self._store[session_id].db_graph = graph

    def increment_turn(self, session_id: str) -> None:
        if session_id in self._store:
            self._store[session_id].turn_count += 1

    def reset(self, session_id: str) -> None:
        """Clear the schema graph (forces re-discovery on next query)."""
        if session_id in self._store:
            self._store[session_id].db_graph = None
            self._store[session_id].turn_count = 0

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)
