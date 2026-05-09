"""
Layer 5 — RAG: Schema Graph Builder
Uses SQLAlchemy introspection (no LLM call needed) to build a NetworkX
graph of tables, columns, and foreign-key relationships.
This replaces the expensive LLM-based discovery from the notebook.
"""
import asyncio
import networkx as nx
from sqlalchemy import create_engine, inspect as sa_inspect


class SchemaGraphBuilder:
    """
    Introspects a database via SQLAlchemy and returns an nx.Graph where:
      - TABLE nodes carry: tableName, node_type="table"
      - COLUMN nodes carry: columnName, columnType, isOptional, tableName, node_type="column"
      - TABLE→COLUMN edges: membership
      - COLUMN→COLUMN edges: foreign-key relationships (edge_type="foreign_key")
    """

    def __init__(self, db_url: str):
        self._db_url = db_url

    async def build(self) -> nx.Graph:
        schema = await asyncio.to_thread(self._introspect)
        return self._to_graph(schema)

    def _introspect(self) -> list[dict]:
        engine = create_engine(self._db_url)
        inspector = sa_inspect(engine)
        tables = []

        for table_name in sorted(inspector.get_table_names()):
            fk_map: dict[str, dict] = {}
            for fk in inspector.get_foreign_keys(table_name):
                referred_cols = fk.get("referred_columns", [])
                for col_name in fk.get("constrained_columns", []):
                    fk_map[col_name] = {
                        "table": fk.get("referred_table", ""),
                        "column": referred_cols[0] if referred_cols else "",
                    }

            columns = []
            for col in inspector.get_columns(table_name):
                columns.append({
                    "columnName": col["name"],
                    "columnType": str(col["type"]),
                    "isOptional": bool(col.get("nullable", True)),
                    "foreignKeyReference": fk_map.get(col["name"]),
                })

            tables.append({"tableName": table_name, "columns": columns})

        engine.dispose()
        return tables

    def _to_graph(self, schema: list[dict]) -> nx.Graph:
        G = nx.Graph()
        table_node: dict[str, int] = {}
        column_node: dict[str, int] = {}
        nid = 0

        for table in schema:
            nid += 1
            G.add_node(nid, tableName=table["tableName"], node_type="table")
            table_node[table["tableName"]] = nid

            for col in table["columns"]:
                nid += 1
                key = f"{table['tableName']}.{col['columnName']}"
                G.add_node(
                    nid,
                    columnName=col["columnName"],
                    columnType=col["columnType"],
                    isOptional=col["isOptional"],
                    tableName=table["tableName"],
                    node_type="column",
                )
                column_node[key] = nid
                G.add_edge(table_node[table["tableName"]], nid)

        # Second pass: FK edges
        for table in schema:
            for col in table["columns"]:
                fk = col.get("foreignKeyReference")
                if not fk:
                    continue
                src = f"{table['tableName']}.{col['columnName']}"
                dst = f"{fk['table']}.{fk['column']}"
                if src in column_node and dst in column_node:
                    G.add_edge(column_node[src], column_node[dst], edge_type="foreign_key")

        return G

    @staticmethod
    def table_names(graph: nx.Graph) -> list[str]:
        return [
            d["tableName"]
            for _, d in graph.nodes(data=True)
            if d.get("node_type") == "table"
        ]
