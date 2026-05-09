"""
Layer 3 — LangGraph StateGraph
Defines the agent workflow and conditional routing between nodes.

Flow:
  START
    └─► classify_input
          ├─► (DATABASE_QUERY) discover_database ─► create_plan ─► execute_plan ─►┐
          └─► (chat)                                                                 ├─► generate_response ─► END
                                                                                   ┘
Schema discovery runs once per session; subsequent calls hit the cached
db_graph in ConversationState without re-running SQLAlchemy introspection.
"""
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END
from src.orchestration.state import ConversationState
from src.orchestration.agents.discovery import DiscoveryAgent
from src.orchestration.agents.supervisor import SupervisorAgent
from src.models.provider import ModelProvider
from src.config.settings import Settings
from src.observability.logger import get_logger
from src.observability.metrics import LLM_CALLS_TOTAL, LLM_LATENCY, LLM_ERRORS_TOTAL, SCHEMA_CACHE_HITS, SCHEMA_CACHE_MISSES
from src.observability.tracing import span

log = get_logger(__name__)

# ── Classification node (standalone, uses light LLM) ─────────────────────────
_CLASSIFY_SYSTEM = """\
Classify the user message into exactly one category:
  DATABASE_QUERY  — questions about data that need a database lookup
  GREETING        — hi, hello, how are you, etc.
  CHITCHAT        — general conversation, not database-related
  FAREWELL        — goodbye, bye, see you, etc.

Reply with ONLY the category name, nothing else."""


async def _classify(state: ConversationState, llm, provider: str) -> dict:
    with span("graph.classify_input", session_id=state.get("session_id", "")):
        messages = [
            SystemMessage(content=_CLASSIFY_SYSTEM),
            HumanMessage(content=state["question"]),
        ]
        LLM_CALLS_TOTAL.labels(agent="classify", provider=provider).inc()
        try:
            resp = await llm.ainvoke(messages)
            category = resp.content.strip().upper()
        except Exception:
            category = "DATABASE_QUERY"
        log.info("input_classified", input_type=category)
        return {"input_type": category}


# ── Discovery node ────────────────────────────────────────────────────────────
async def _discover(state: ConversationState, db_url: str) -> dict:
    with span("graph.discover_database", session_id=state.get("session_id", "")):
        if state.get("db_graph") is not None:
            SCHEMA_CACHE_HITS.inc()
            log.info("schema_cache_hit")
            return {"schema_cached": True}
        SCHEMA_CACHE_MISSES.inc()
        log.info("schema_cache_miss")
        agent = DiscoveryAgent(db_url)
        graph = await agent.discover()
        log.info("discovery_complete", node_count=graph.number_of_nodes())
        return {"db_graph": graph, "schema_cached": False}


# ── Graph factory ─────────────────────────────────────────────────────────────
def build_graph(settings: Settings):
    provider = ModelProvider(settings)
    llm_heavy = provider.heavy()
    llm_light = provider.light()
    supervisor = SupervisorAgent(
        llm_heavy, llm_light, settings.database_url,
        settings.max_result_rows, provider=settings.llm_provider,
    )

    # Bind runtime dependencies via closures
    async def classify_node(state):
        return await _classify(state, llm_light, settings.llm_provider)

    async def discover_node(state):
        return await _discover(state, settings.database_url)

    def route_after_classify(state: ConversationState) -> str:
        return "discover_database" if state.get("input_type") == "DATABASE_QUERY" else "generate_response"

    def route_after_plan(state: ConversationState) -> str:
        return "execute_plan" if state.get("plan") else "generate_response"

    builder = StateGraph(ConversationState)

    builder.add_node("classify_input",    classify_node)
    builder.add_node("discover_database", discover_node)
    builder.add_node("create_plan",       supervisor.create_plan)
    builder.add_node("execute_plan",      supervisor.execute_plan)
    builder.add_node("generate_response", supervisor.generate_response)

    builder.add_edge(START, "classify_input")
    builder.add_conditional_edges("classify_input",    route_after_classify)
    builder.add_edge("discover_database", "create_plan")
    builder.add_conditional_edges("create_plan",       route_after_plan)
    builder.add_edge("execute_plan",      "generate_response")
    builder.add_edge("generate_response", END)

    return builder.compile()
