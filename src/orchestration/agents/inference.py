"""
Layer 3 — InferenceAgent (stateless, READ-ONLY enforced)
Executes natural-language database queries using the new LangChain 1.x
create_agent API (LangGraph-based ReAct loop).

Read-only is enforced at TWO levels:
  1. System prompt explicitly forbids DDL/DML
  2. The sql_db_query tool is wrapped — every SQL string passes through
     ReadOnlySQLValidator before reaching the database connection
"""
import asyncio
import time
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool as lc_tool
from langchain_community.utilities import SQLDatabase
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain.agents import create_agent
from src.security.sql_validator import ReadOnlySQLValidator, ReadOnlyViolationError
from src.rag.context_retriever import ContextRetriever
from src.observability.logger import get_logger
from src.observability.metrics import LLM_CALLS_TOTAL, LLM_LATENCY, LLM_ERRORS_TOTAL, AGENT_ERRORS_TOTAL
from src.observability.tracing import span
import networkx as nx

log = get_logger(__name__)

_SYSTEM = """\
You are a READ-ONLY database query expert.

{schema_context}

ABSOLUTE RULES (non-negotiable):
1. ONLY generate SELECT statements (CTEs with SELECT are fine)
2. NEVER generate: INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, GRANT, REVOKE, EXEC
3. Always add LIMIT {max_rows} unless the question asks for a specific number
4. If the user asks to modify data, reply: "I only have read-only access."

Response format:
Query Executed: <the SQL you ran>
Results: <raw results>
Summary: <1-2 sentence factual summary>
"""


class InferenceAgent:
    def __init__(self, llm: BaseChatModel, db_url: str, max_rows: int = 500, provider: str = "unknown"):
        self._llm = llm
        self._db_url = db_url
        self._max_rows = max_rows
        self._provider = provider
        self._validator = ReadOnlySQLValidator()
        self._retriever = ContextRetriever()
        self._db = SQLDatabase.from_uri(db_url)

    def _make_safe_tools(self, schema_context: str) -> list:
        """
        Return toolkit tools with sql_db_query replaced by a
        read-only validated wrapper.
        """
        toolkit = SQLDatabaseToolkit(db=self._db, llm=self._llm)
        base_tools = {t.name: t for t in toolkit.get_tools()}

        validator = self._validator
        db = self._db
        max_rows = self._max_rows

        @lc_tool
        def sql_db_query(query: str) -> str:
            """
            Execute a SQL SELECT query against the database.
            ONLY SELECT statements are permitted — all others are blocked.
            Input: a valid SQL SELECT string.
            """
            try:
                validator.validate(query)
            except ReadOnlyViolationError as e:
                return f"❌ READ-ONLY VIOLATION BLOCKED: {e}. Only SELECT queries are allowed."
            q = query.strip().rstrip(";")
            if "limit" not in q.lower():
                q += f" LIMIT {max_rows}"
            try:
                return db.run(q)
            except Exception as exc:
                return f"SQL error: {exc}"

        safe_tools = [sql_db_query]
        for name, t in base_tools.items():
            if name != "sql_db_query":
                safe_tools.append(t)

        return safe_tools

    async def query(self, question: str, db_graph: nx.Graph | None) -> str:
        with span("agent.inference.query", question_len=len(question), provider=self._provider):
            t0 = time.monotonic()
            ctx = self._retriever.retrieve(question, db_graph) if db_graph else {}
            schema_context = (
                ctx.get("schema_summary")
                or self._retriever.full_schema_summary(db_graph)
                or "Schema not yet loaded — use available tools to explore."
            )

            system_prompt = _SYSTEM.format(
                schema_context=schema_context,
                max_rows=self._max_rows,
            )

            safe_tools = self._make_safe_tools(schema_context)

            agent = create_agent(
                model=self._llm,
                tools=safe_tools,
                system_prompt=system_prompt,
            )

            LLM_CALLS_TOTAL.labels(agent="inference", provider=self._provider).inc()
            try:
                result = await agent.ainvoke({"messages": [("user", question)]})
                last_msg = result["messages"][-1]
                answer = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
                LLM_LATENCY.labels(agent="inference", provider=self._provider).observe(time.monotonic() - t0)
                log.info("inference_complete", question_len=len(question), answer_len=len(answer))
                return answer
            except Exception as exc:
                LLM_ERRORS_TOTAL.labels(agent="inference", provider=self._provider).inc()
                AGENT_ERRORS_TOTAL.labels(agent="inference").inc()
                log.error("inference_error", error=str(exc))
                return f"I encountered an error processing your query: {exc}"
