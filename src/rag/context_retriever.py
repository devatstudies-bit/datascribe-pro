"""
Layer 5 — RAG: Context Retriever
Extracts relevant tables, columns, and join paths from the schema graph
based on the user's question. This context is injected into the
InferenceAgent's system prompt to improve SQL generation accuracy.
"""
import networkx as nx


class ContextRetriever:
    """
    Keyword-match + BFS graph traversal retriever.
    Finds tables/columns relevant to the question and discovers join paths
    between matched tables (up to 3 hops via FK edges).
    """

    def retrieve(self, question: str, graph: nx.Graph) -> dict:
        if graph is None:
            return {"tables": [], "columns": [], "join_paths": [], "schema_summary": ""}

        q = question.lower()
        matched_table_nodes: list[int] = []
        tables_info: list[dict] = []

        for node, data in graph.nodes(data=True):
            if data.get("node_type") != "table":
                continue
            tname = data["tableName"].lower()
            # Match if table name (or its singular/plural) appears in question
            if tname in q or tname.rstrip("s") in q or f"{tname}s" in q:
                matched_table_nodes.append(node)
                cols = [
                    {"name": graph.nodes[n]["columnName"], "type": graph.nodes[n]["columnType"]}
                    for n in graph.neighbors(node)
                    if graph.nodes[n].get("node_type") == "column"
                ]
                tables_info.append({"name": data["tableName"], "columns": cols})

        join_paths: list[list[str]] = []
        for i in range(len(matched_table_nodes)):
            for j in range(i + 1, len(matched_table_nodes)):
                try:
                    path_nodes = nx.shortest_path(
                        graph, matched_table_nodes[i], matched_table_nodes[j]
                    )
                    if len(path_nodes) <= 7:  # max 3 hops
                        path_names = [
                            graph.nodes[n].get("tableName") or graph.nodes[n].get("columnName", "?")
                            for n in path_nodes
                        ]
                        join_paths.append(path_names)
                except nx.NetworkXNoPath:
                    pass

        schema_summary = self._build_summary(tables_info)
        return {
            "tables": tables_info,
            "join_paths": join_paths,
            "schema_summary": schema_summary,
        }

    def _build_summary(self, tables: list[dict]) -> str:
        if not tables:
            return "No specific tables identified — agent should use available tools to explore."
        lines = []
        for t in tables:
            col_str = ", ".join(f"{c['name']} ({c['type']})" for c in t["columns"][:10])
            lines.append(f"  {t['name']}: {col_str}")
        return "Relevant schema:\n" + "\n".join(lines)

    def full_schema_summary(self, graph: nx.Graph) -> str:
        """Return a compact summary of ALL tables for the system prompt."""
        if graph is None:
            return ""
        lines = []
        for node, data in graph.nodes(data=True):
            if data.get("node_type") != "table":
                continue
            cols = [
                graph.nodes[n]["columnName"]
                for n in graph.neighbors(node)
                if graph.nodes[n].get("node_type") == "column"
            ]
            lines.append(f"  {data['tableName']}({', '.join(cols[:8])}{'...' if len(cols) > 8 else ''})")
        return "Database tables:\n" + "\n".join(lines)
