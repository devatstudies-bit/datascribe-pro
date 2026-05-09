"""
Layer 3 — PlannerAgent (stateless)
Decomposes the user question into typed steps using the light LLM.
Returns a list like:
  ["Inference: Count employees by department",
   "General: Present the results clearly"]
"""
import time
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.language_models import BaseChatModel
from src.observability.logger import get_logger
from src.observability.metrics import LLM_CALLS_TOTAL, LLM_LATENCY, LLM_ERRORS_TOTAL, PLAN_STEPS_TOTAL
from src.observability.tracing import span

log = get_logger(__name__)

_SYSTEM = """You are a planning agent. Break the user's database question into the minimum steps needed.

Step types (prefix each line with one of these):
  Inference: <description>   — requires a database query
  General: <description>     — conversational response, no DB needed

Rules:
- One step per line, no blank lines
- Minimum steps to answer completely
- No duplicate steps
- Logical order

Examples:
  Inference: Get count of tracks per genre ordered descending
  General: Present the genre breakdown clearly"""


class PlannerAgent:
    def __init__(self, llm: BaseChatModel, provider: str = "unknown"):
        self._llm = llm
        self._provider = provider

    async def plan(self, question: str) -> list[str]:
        with span("agent.planner.plan", question_len=len(question), provider=self._provider):
            t0 = time.monotonic()
            try:
                messages = [
                    SystemMessage(content=_SYSTEM),
                    HumanMessage(content=f"Question: {question}"),
                ]
                LLM_CALLS_TOTAL.labels(agent="planner", provider=self._provider).inc()
                response = await self._llm.ainvoke(messages)
                LLM_LATENCY.labels(agent="planner", provider=self._provider).observe(time.monotonic() - t0)

                steps = [
                    line.strip()
                    for line in response.content.splitlines()
                    if line.strip() and ":" in line
                ]
                steps = steps or ["General: I can help with database questions. What would you like to know?"]

                # Metric: count each step type
                for s in steps:
                    step_type = s.split(":")[0].strip().capitalize()
                    PLAN_STEPS_TOTAL.labels(step_type=step_type).inc()

                log.info("plan_created", step_count=len(steps), steps=steps)
                return steps

            except Exception as exc:
                LLM_ERRORS_TOTAL.labels(agent="planner", provider=self._provider).inc()
                log.error("planner_error", error=str(exc))
                return ["General: Sorry, I had trouble creating a plan. Please try again."]
