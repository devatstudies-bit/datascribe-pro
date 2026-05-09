"""
Layer 3 — SupervisorAgent (stateful via ConversationState)
Coordinates PlannerAgent and InferenceAgent.
Owns: create_plan, execute_plan, generate_response graph nodes.
"""
import time
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models import BaseChatModel
from src.orchestration.state import ConversationState
from src.orchestration.agents.planner import PlannerAgent
from src.orchestration.agents.inference import InferenceAgent
from src.observability.logger import get_logger
from src.observability.metrics import LLM_CALLS_TOTAL, LLM_LATENCY, LLM_ERRORS_TOTAL, AGENT_ERRORS_TOTAL
from src.observability.tracing import span

log = get_logger(__name__)


class SupervisorAgent:
    def __init__(
        self,
        llm_heavy: BaseChatModel,
        llm_light: BaseChatModel,
        db_url: str,
        max_rows: int = 500,
        provider: str = "unknown",
    ):
        self._llm_light = llm_light
        self._provider = provider
        self._planner = PlannerAgent(llm_heavy, provider=provider)
        self._inference = InferenceAgent(llm_light, db_url, max_rows, provider=provider)

    # ── Node: create_plan ─────────────────────────────────────────────────────
    async def create_plan(self, state: ConversationState) -> dict:
        with span("graph.create_plan", session_id=state.get("session_id", "")):
            plan = await self._planner.plan(state["question"])
            log.info("plan_created", step_count=len(plan))
            return {"plan": plan}

    # ── Node: execute_plan ────────────────────────────────────────────────────
    async def execute_plan(self, state: ConversationState) -> dict:
        with span("graph.execute_plan", session_id=state.get("session_id", ""), step_count=len(state.get("plan", []))):
            results: list[str] = []
            db_graph = state.get("db_graph")

            for step in state.get("plan", []):
                if ":" not in step:
                    continue
                step_type, content = step.split(":", 1)
                content = content.strip()

                if step_type.strip().lower() == "inference":
                    log.info("execute_inference_step", step=content)
                    result = await self._inference.query(content, db_graph)
                    results.append(f"**Step:** {step}\n**Result:** {result}")
                else:
                    results.append(f"**Step:** {step}")

            return {"db_results": "\n\n---\n\n".join(results) if results else "No results."}

    # ── Node: generate_response ───────────────────────────────────────────────
    async def generate_response(self, state: ConversationState) -> dict:
        with span("graph.generate_response", session_id=state.get("session_id", ""), provider=self._provider):
            t0 = time.monotonic()
            is_chat = state.get("input_type", "") in {"GREETING", "CHITCHAT", "FAREWELL"}

            if is_chat:
                messages = [
                    SystemMessage(content="You are a friendly AI assistant for a database exploration tool. Reply naturally and briefly."),
                    HumanMessage(content=state["question"]),
                ]
            else:
                messages = [
                    SystemMessage(content=(
                        "You are a response coordinator. Present the database results clearly and concisely.\n"
                        "Original question: " + state["question"] + "\n"
                        "Include ALL results. Use bullet points or tables for lists."
                    )),
                    HumanMessage(content=state.get("db_results", "No data retrieved.")),
                ]

            LLM_CALLS_TOTAL.labels(agent="supervisor", provider=self._provider).inc()
            try:
                resp = await self._llm_light.ainvoke(messages)
                LLM_LATENCY.labels(agent="supervisor", provider=self._provider).observe(time.monotonic() - t0)
                log.info("response_generated", input_type=state.get("input_type", ""))
                return {"response": resp.content, "plan": []}
            except Exception as exc:
                LLM_ERRORS_TOTAL.labels(agent="supervisor", provider=self._provider).inc()
                AGENT_ERRORS_TOTAL.labels(agent="supervisor").inc()
                log.error("generate_response_error", error=str(exc))
                return {"response": f"Error generating response: {exc}", "plan": []}
