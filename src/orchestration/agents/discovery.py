"""
Layer 3 — DiscoveryAgent (stateless)
Builds the schema graph via SQLAlchemy introspection.
No LLM call needed — SQLAlchemy reads metadata directly from the DB,
making this fast, accurate, and free of token cost.
"""
from src.rag.schema_graph import SchemaGraphBuilder
import networkx as nx


class DiscoveryAgent:
    def __init__(self, db_url: str):
        self._builder = SchemaGraphBuilder(db_url)

    async def discover(self) -> nx.Graph:
        return await self._builder.build()
