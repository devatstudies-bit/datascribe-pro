# DataScribe Pro — Low-Level Design (LLD)
### Component Specifications, Class Designs & Implementation Details

> **Scope note**: This LLD covers the current working implementation. Sections marked
> **[Production Extension]** describe components documented in the HLD that are not yet
> implemented — they are the natural next step for a hardened deployment.

---

## 1. Project Structure (Actual)

```
datascribe-pro/
├── src/
│   ├── consumer/                   # Layer 1
│   │   ├── api.py                  # FastAPI app, WebSocket endpoint, /metrics
│   │   └── session_manager.py      # In-memory session state (db_graph per session)
│   │
│   ├── orchestration/              # Layer 3
│   │   ├── graph.py                # LangGraph StateGraph — nodes + routing
│   │   ├── state.py                # ConversationState TypedDict with reducers
│   │   └── agents/
│   │       ├── supervisor.py       # SupervisorAgent — create_plan, execute_plan, generate_response
│   │       ├── planner.py          # PlannerAgent — decomposes question into typed steps
│   │       ├── discovery.py        # DiscoveryAgent — SQLAlchemy schema introspection
│   │       └── inference.py        # InferenceAgent — read-only SQL execution
│   │
│   ├── models/                     # Layer 4
│   │   └── provider.py             # ModelProvider — heavy/light LLMs, all 3 cloud providers
│   │
│   ├── rag/                        # Layer 5
│   │   ├── schema_graph.py         # SchemaGraphBuilder (SQLAlchemy → NetworkX)
│   │   └── context_retriever.py    # ContextRetriever — keyword match + BFS join paths
│   │
│   ├── security/                   # Layer 6
│   │   └── sql_validator.py        # ReadOnlySQLValidator — AST + keyword scan
│   │
│   ├── config/                     # Cross-cutting
│   │   └── settings.py             # Pydantic BaseSettings, lru_cache singleton
│   │
│   └── observability/              # Layer 8
│       ├── logger.py               # structlog JSON pipeline, bind_request_context
│       ├── metrics.py              # 10 Prometheus metrics + metrics_response()
│       ├── tracing.py              # OTel setup, span() ctx manager, current_trace_id()
│       └── audit.py                # AuditLogger — SHA-256 hashed query audit trail
│
├── frontend/
│   ├── index.html                  # Dark-theme chat UI
│   └── app.js                      # WebSocket client, markdown rendering
│
├── docs/                           # This directory
│   ├── HLD.md
│   ├── LLD.md
│   └── hld_architecture.drawio
│
├── data/
│   └── chinook.db                  # SQLite sample DB (11 tables, dev only)
│
├── main.py                         # Entry point — setup_logging/tracing, uvicorn
├── requirements.txt
└── .env.example
```

**Production extensions not yet implemented:**
```
src/
├── gateway/            # Layer 2 — rate limiter, cost tracker, fallback chain
├── security/
│   ├── pii_masker.py  # Layer 6 — Presidio PII detection
│   └── rbac.py        # Layer 6 — table-level ACLs
└── infrastructure/     # Layer 7 — Redis cache, Secrets Manager, DB adapter factory
```

---

## 2. Layer 1 — Consumer Layer

### 2.1 WebSocket Message Protocol

**Server → Client:**

| Event | Fields | When sent |
|---|---|---|
| `session_info` | `session_id`, `provider` | On connect |
| `status` | `text`, `stage` | Each graph node transition |
| `schema_loaded` | `tables[]`, `cached`, `text` | After discovery node |
| `plan` | `steps[]` | After planner node |
| `step_start` | `index`, `text` | For each Inference step |
| `chunk` | `text` | Word-by-word response streaming |
| `done` | `model`, `latency_ms`, `turns`, `trace_id` | Query complete |
| `error` | `message` | On exception |

**Client → Server:**

| Message | Fields |
|---|---|
| `query` | `question` |
| `reset` | (none) — clears session graph, resets turn count |

### 2.2 Session Manager

```python
# src/consumer/session_manager.py
@dataclass
class Session:
    session_id: str
    db_graph: Optional[nx.Graph] = None   # persisted across turns
    turn_count: int = 0

class SessionManager:
    """In-memory store. Production upgrade: replace with Redis + pickle TTL."""
    def get_or_create(self, session_id: Optional[str]) -> Session: ...
    def update_graph(self, session_id: str, graph: nx.Graph) -> None: ...
    def increment_turn(self, session_id: str) -> None: ...
    def reset(self, session_id: str) -> None: ...   # clears graph, resets turns
```

### 2.3 FastAPI App (api.py)

Key wiring in the lifespan:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging()          # structlog JSON pipeline
    setup_tracing()          # OTel ConsoleSpanExporter + optional OTLP
    app.state.session_mgr = SessionManager()
    app.state.audit = AuditLogger()
    app.state.graph = build_graph(settings)
    yield
```

Routes:
- `GET /` — serves `frontend/index.html`
- `GET /health` — `{"status": "ok", "provider": "<llm_provider>"}`
- `GET /schema?session_id=<id>` — table list for a session
- `GET /metrics` — Prometheus scrape endpoint
- `WS /ws/{session_id}` — primary chat interface

Per-query instrumentation in `_handle_query()`:
```python
bind_request_context(session.session_id)   # attaches session_id to all log lines
# ... stream graph nodes ...
REQUESTS_TOTAL.labels(input_type=final_input_type).inc()
REQUEST_LATENCY.labels(input_type=final_input_type).observe(latency_ms / 1000)
audit.record(session_id=..., trace_id=..., question=..., ...)
# done event includes trace_id for cross-referencing with OTel
```

**[Production Extension]** Add `schemas.py` with `QueryRequest` (JWT, tenant_id, db_alias, input sanitization) and migrate session store to Redis with TTL.

---

## 3. Layer 2 — AI Gateway Layer

**[Production Extension — not in current release]**

The current implementation connects directly to the configured provider via `ModelProvider`. A production AI Gateway would add:

- **Token-bucket rate limiter** per tenant (requests/min, tokens/day) backed by Redis
- **Cost tracker** accumulating token spend by tenant/provider/task
- **Fallback chain**: AzureOAI → OpenAI → Bedrock on provider error
- **Circuit breaker**: opens after N consecutive failures; half-opens after cooldown
- **Credential rotation**: Secrets Manager lookup without service restart

See HLD §Layer 2 for the full design.

---

## 4. Layer 3 — Orchestration Layer

### 4.1 ConversationState

```python
# src/orchestration/state.py
class ConversationState(TypedDict):
    # Input
    question:      str
    session_id:    str

    # Classification
    input_type:    Annotated[str, _replace]           # DATABASE_QUERY | GREETING | CHITCHAT | FAREWELL

    # Planning
    plan:          Annotated[list[str], _replace]     # ["Inference: ...", "General: ..."]

    # Results
    db_results:    NotRequired[str]
    response:      NotRequired[str]

    # RAG — cached across turns via _keep_existing reducer
    db_graph:      Annotated[Optional[nx.Graph], _keep_existing]

    # Metadata
    model_used:    NotRequired[str]
    schema_cached: NotRequired[bool]
```

`_keep_existing` ensures `db_graph` is never overwritten once set — the schema discovery only runs once per session.

### 4.2 StateGraph

```
START
  └─► classify_input
        ├─► (DATABASE_QUERY) discover_database ─► create_plan ─► execute_plan ─►┐
        └─► (GREETING/CHITCHAT/FAREWELL)                                          ├─► generate_response ─► END
```

```python
# src/orchestration/graph.py
def build_graph(settings: Settings):
    provider = ModelProvider(settings)
    supervisor = SupervisorAgent(
        llm_heavy, llm_light,
        settings.database_url,
        settings.max_result_rows,
        provider=settings.llm_provider,
    )

    builder = StateGraph(ConversationState)
    builder.add_node("classify_input",    classify_node)
    builder.add_node("discover_database", discover_node)
    builder.add_node("create_plan",       supervisor.create_plan)
    builder.add_node("execute_plan",      supervisor.execute_plan)
    builder.add_node("generate_response", supervisor.generate_response)
    # ... edges ...
    return builder.compile()
```

Conditional routing:
- After `classify_input`: `DATABASE_QUERY → discover_database`, else `→ generate_response`
- After `create_plan`: has plan `→ execute_plan`, else `→ generate_response`

### 4.3 Agent Responsibilities

| Agent | Class | Key method | Read-only? |
|---|---|---|---|
| SupervisorAgent | `supervisor.py` | `create_plan`, `execute_plan`, `generate_response` | N/A |
| PlannerAgent | `planner.py` | `plan(question) → list[str]` | N/A |
| DiscoveryAgent | `discovery.py` | `discover() → nx.Graph` | Yes — `information_schema` only |
| InferenceAgent | `inference.py` | `query(question, db_graph) → str` | Yes — validator + prompt guard |

### 4.4 InferenceAgent (Read-Only Enforced)

```python
# src/orchestration/agents/inference.py
class InferenceAgent:
    async def query(self, question: str, db_graph: nx.Graph | None) -> str:
        with span("agent.inference.query", provider=self._provider):
            # 1. Retrieve schema context from graph
            ctx = self._retriever.retrieve(question, db_graph)

            # 2. Build safe tool set — sql_db_query is wrapped with validator
            safe_tools = self._make_safe_tools(schema_context)

            # 3. LangChain 1.x ReAct agent (LangGraph-based, no AgentExecutor)
            agent = create_agent(model=self._llm, tools=safe_tools, system_prompt=system_prompt)

            # 4. Invoke — validator fires inside the tool before any DB call
            result = await agent.ainvoke({"messages": [("user", question)]})
```

The `sql_db_query` tool wrapper:
```python
@lc_tool
def sql_db_query(query: str) -> str:
    try:
        validator.validate(query)       # raises ReadOnlyViolationError if not SELECT
    except ReadOnlyViolationError as e:
        return f"❌ READ-ONLY VIOLATION BLOCKED: {e}"
    q = query.strip().rstrip(";")
    if "limit" not in q.lower():
        q += f" LIMIT {max_rows}"
    return db.run(q)
```

---

## 5. Layer 4 — Model Layer

### 5.1 ModelProvider

```python
# src/models/provider.py
class ModelProvider:
    """
    Returns cached LLM instances. Provider selected via LLM_PROVIDER env var.
    heavy() → used for planning (GPT-4o class)
    light() → used for inference, classification, response (GPT-4o-mini class)
    """
    def heavy(self) -> BaseChatModel: ...
    def light(self) -> BaseChatModel: ...
```

Provider selection:

| `LLM_PROVIDER` value | `heavy()` | `light()` |
|---|---|---|
| `openai` | `ChatOpenAI(gpt-4o)` | `ChatOpenAI(gpt-4o-mini)` |
| `azure_openai` | `AzureChatOpenAI(azure_deployment_heavy)` | `AzureChatOpenAI(azure_deployment_light)` |
| `bedrock` | `ChatBedrock(claude-3-5-sonnet)` | `ChatBedrock(claude-3-haiku)` |

**[Production Extension]** Upgrade to `ModelRegistry` with a 3-tier system (HEAVY / MEDIUM / LIGHT), provider health checks (`is_healthy()`), and automatic fallback chain (Azure → OpenAI → Bedrock) wired through the AI Gateway layer.

---

## 6. Layer 5 — RAG Layer

### 6.1 Schema Graph Builder

```python
# src/rag/schema_graph.py
class SchemaGraphBuilder:
    """
    Uses SQLAlchemy inspect() — not an LLM call. Instant, accurate, free.
    Replaces the expensive LLM-based discovery in the original notebook.
    """
    def build(self, db_url: str) -> nx.Graph:
        engine = create_engine(db_url)
        inspector = inspect(engine)
        G = nx.Graph()
        for table in inspector.get_table_names():
            G.add_node(table, node_type="table")
            for col in inspector.get_columns(table):
                col_key = f"{table}.{col['name']}"
                G.add_node(col_key, node_type="column", ...)
                G.add_edge(table, col_key)
            for fk in inspector.get_foreign_keys(table):
                G.add_edge(f"{table}.{fk['constrained_columns'][0]}",
                           f"{fk['referred_table']}.{fk['referred_columns'][0]}",
                           edge_type="foreign_key")
        return G

    @staticmethod
    def table_names(graph: nx.Graph) -> list[str]:
        return [n for n, d in graph.nodes(data=True) if d.get("node_type") == "table"]
```

### 6.2 Context Retriever

```python
# src/rag/context_retriever.py
class ContextRetriever:
    def retrieve(self, question: str, graph: nx.Graph) -> dict:
        # 1. Keyword match — table names (singular + plural) against question tokens
        # 2. BFS join path discovery between matched tables (max 3 hops)
        # 3. Returns schema_summary string injected into InferenceAgent system prompt
        ...

    def full_schema_summary(self, graph: nx.Graph) -> str:
        # Fallback: full schema dump when no tables matched by keyword
        ...
```

**[Production Extension]** Add `vector_store.py`: embed table/column descriptions with `text-embedding-3-small`, store in ChromaDB or pgvector, replace keyword matching with cosine similarity search for better recall on paraphrase queries.

---

## 7. Layer 6 — Security Layer

### 7.1 Read-Only SQL Validator

```python
# src/security/sql_validator.py
_BLOCKED_NODE_TYPES = (
    exp.Insert, exp.Update, exp.Delete,
    exp.Create, exp.Drop, exp.Alter, exp.TruncateTable,
    exp.Grant, exp.Revoke,
    exp.Command,      # catches EXEC / EXECUTE
    exp.Transaction,  # prevents DML wrapped in BEGIN
)

_BLOCKED_KEYWORDS = frozenset({
    "insert", "update", "delete", "drop", "create",
    "alter", "truncate", "grant", "revoke", "exec", "execute",
})
```

> **Note:** `"into"` and `"set"` were considered but excluded — they appear in valid SELECT
> contexts (`INSERT INTO` is already caught by the AST pass; `SET` in `SET TRANSACTION` is
> caught by `exp.Transaction`). Broad keyword matching would produce false positives.

Two-pass validation:
1. **AST pass** (`_check_ast`): `sqlglot.parse()` → walk all nodes → block any `_BLOCKED_NODE_TYPES`
2. **Keyword pass** (`_check_keywords`): token set intersection as secondary catch for obfuscated SQL

Both passes fire for every SQL string. A `ReadOnlyViolationError` stops execution immediately — the SQL never reaches the DB connection.

Each block increments `SQL_BLOCKED_TOTAL.labels(reason="ast_node"|"keyword")`.

### 7.2 Audit Logger

```python
# src/observability/audit.py  (placed in observability — audit records are telemetry)
class AuditLogger:
    def record(self, *, session_id, trace_id, question, input_type,
               plan_steps, sql_statements, sql_blocked, model_used,
               latency_ms, response) -> None:
        log.info("query_audit",
            question_hash=self._h(question),      # SHA-256[:16] — never raw question
            sql_hashes=[self._h(s) for s in sql_statements],
            plan_steps=plan_steps,
            ...)

    @staticmethod
    def _h(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:16]
```

The SHA-256 prefix lets a DBA cross-reference the audit log against the DB query log without exposing sensitive data in application logs.

**[Production Extension]** Add `src/security/pii_masker.py` (Microsoft Presidio — masks EMAIL, PHONE, PERSON, CREDIT_CARD from LLM responses before returning to client) and `src/security/rbac.py` (table-level ACLs per tenant).

---

## 8. Layer 7 — Infrastructure Layer

### 8.1 Current: SQLAlchemy Direct Connection

The current implementation connects directly to the `DATABASE_URL` from settings. SQLAlchemy provides implicit connection pooling (`StaticPool` for SQLite, `QueuePool` for others).

### 8.2 Configuration (settings.py)

```python
# src/config/settings.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"         # openai | azure_openai | bedrock

    # OpenAI
    openai_api_key: str = ""
    openai_model_heavy: str = "gpt-4o"
    openai_model_light: str = "gpt-4o-mini"

    # Azure OpenAI
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-08-01-preview"
    azure_deployment_heavy: str = "gpt-4o"
    azure_deployment_light: str = "gpt-4o-mini"

    # AWS Bedrock
    aws_region: str = "us-east-1"
    bedrock_model_heavy: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_model_light: str = "anthropic.claude-3-haiku-20240307-v1:0"

    # Database (any SQLAlchemy URI)
    database_url: str = "sqlite:///data/chinook.db"

    # App
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    agent_max_iterations: int = 10
    max_result_rows: int = 500

@lru_cache
def get_settings() -> Settings: ...
```

**[Production Extension]** Add to Settings: `redis_url`, `secrets_provider` (env/vault/aws_sm/azure_kv), `enable_pii_masking`, `audit_retention_days`, `otlp_endpoint`. Replace in-memory `SessionManager` with Redis-backed store (session TTL, pickle-serialised `nx.Graph`). Add `DBAdapterFactory` with `_assert_read_only()` startup check and per-tenant connection pools.

**[Production Extension]** Kubernetes deployment:

```yaml
# k8s/deployment.yaml (target)
containers:
- name: api
  image: datascribe-pro:latest
  ports:
  - containerPort: 8000    # FastAPI / WebSocket
  readinessProbe:
    httpGet: { path: /health, port: 8000 }
  livenessProbe:
    httpGet: { path: /health, port: 8000 }
  resources:
    requests: { memory: "512Mi", cpu: "250m" }
    limits:   { memory: "2Gi",  cpu: "1000m" }
---
kind: HorizontalPodAutoscaler
spec:
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: Resource
    resource: { name: cpu, target: { type: Utilization, averageUtilization: 70 } }
```

---

## 9. Layer 8 — Observability Layer

### 9.1 Structured Logger

```python
# src/observability/logger.py
def setup_logging(level: str = "INFO") -> None:
    """structlog JSON pipeline — call once at startup in main.py and api.py lifespan."""

def get_logger(name: str) -> structlog.BoundLogger:
    """Returns a bound logger; use instead of logging.getLogger everywhere."""

def bind_request_context(session_id: str, **kwargs) -> None:
    """Binds session_id to every log line for the current async task (contextvars)."""
```

Every log line emits JSON including `timestamp`, `level`, `logger`, `event`, `session_id` (when bound), plus any keyword args passed to the log call.

### 9.2 Prometheus Metrics

```python
# src/observability/metrics.py
```

| Metric | Type | Labels | Incremented by |
|---|---|---|---|
| `datascribe_requests_total` | Counter | `input_type` | `api.py` — per completed query |
| `datascribe_request_latency_seconds` | Histogram | `input_type` | `api.py` — end-to-end |
| `datascribe_llm_calls_total` | Counter | `agent`, `provider` | planner, inference, supervisor, classify nodes |
| `datascribe_llm_latency_seconds` | Histogram | `agent`, `provider` | same |
| `datascribe_llm_errors_total` | Counter | `agent`, `provider` | on LLM exception |
| `datascribe_schema_cache_hits_total` | Counter | — | `graph.py` `_discover` on cache hit |
| `datascribe_schema_cache_misses_total` | Counter | — | `graph.py` `_discover` on cache miss |
| `datascribe_sql_blocked_total` | Counter | `reason` (`ast_node`\|`keyword`) | `sql_validator.py` |
| `datascribe_plan_steps_total` | Counter | `step_type` (`Inference`\|`General`) | `planner.py` |
| `datascribe_agent_errors_total` | Counter | `agent` | inference, supervisor on exception |
| `datascribe_active_sessions` | Gauge | — | `api.py` WebSocket connect/disconnect |

Scraped at `GET /metrics` (Prometheus text format, `prometheus_client` default registry).

**[Production Extension]** Add `datascribe_llm_tokens_total` (Counter, labels: agent, provider) once token metadata is reliably available from LangChain response callbacks. Add `datascribe_provider_healthy` (Gauge, label: provider) driven by the AI Gateway health checker.

### 9.3 OpenTelemetry Tracing

```python
# src/observability/tracing.py
def setup_tracing(service_name: str = "datascribe-pro", otlp_endpoint: str = "") -> None:
    """
    Always attaches ConsoleSpanExporter (spans visible in logs without a backend).
    If OTLP_ENDPOINT env var is set, also ships to Jaeger / Grafana Tempo via gRPC.
    """

@contextmanager
def span(name: str, **attributes):
    """Convenience wrapper — auto-records exceptions, sets ERROR status."""

def current_trace_id() -> str:
    """Returns hex trace-id of the active span; included in the 'done' WS event."""
```

Span hierarchy per query:
```
ws.query (implicit — each _handle_query call)
  ├── graph.classify_input
  ├── graph.discover_database
  ├── graph.create_plan
  │     └── agent.planner.plan
  ├── graph.execute_plan
  │     └── agent.inference.query  (one per Inference step)
  └── graph.generate_response
```

### 9.4 Audit Logger

```python
# src/observability/audit.py
class AuditLogger:
    def record(self, *, session_id, trace_id, question, input_type,
               plan_steps, sql_statements, sql_blocked,
               model_used, latency_ms, response) -> None:
        """Writes one audit record per query. Questions and SQL are SHA-256 hashed."""

    def record_sql_blocked(self, *, session_id, sql, reason) -> None:
        """Called whenever ReadOnlySQLValidator blocks a statement."""
```

Fields logged (all compliance-safe, no raw user data):

| Field | Value |
|---|---|
| `session_id` | session UUID |
| `trace_id` | OTel trace hex (links to spans) |
| `question_hash` | SHA-256[:16] of raw question |
| `input_type` | classification result |
| `plan_steps` | list of Inference:/General: strings |
| `sql_hashes` | SHA-256[:16] per SQL statement |
| `sql_blocked` | bool |
| `model_used` | provider name |
| `latency_ms` | integer |
| `response_preview` | first 200 chars |
| `timestamp_utc` | Unix timestamp |

---

## 10. How All 8 Layers Fire on a Single Query

```
User: "Who are the top 3 artists by track count?"

LAYER 1  Consumer:       WebSocket receives query; bind_request_context(session_id);
                         ACTIVE_SESSIONS gauge already tracking this connection

LAYER 2  AI Gateway:     [Production Extension] — currently ModelProvider selects
                         provider directly from LLM_PROVIDER env var

LAYER 3  Orchestration:  StateGraph: classify_input → discover_database (CACHE HIT,
                         SCHEMA_CACHE_HITS++) → create_plan → execute_plan → generate_response

LAYER 4  Model:          classify & generate_response use light() (gpt-4o-mini);
                         create_plan uses heavy() (gpt-4o);
                         inference uses light() (gpt-4o-mini)

LAYER 5  RAG:            ContextRetriever: "artist" + "track" keywords →
                         artists + tracks + albums nodes extracted from nx.Graph →
                         BFS finds join paths → schema_summary injected into system prompt

LAYER 6  Security:       Prompt guard (system prompt forbids DDL) + AST validator passes
                         "SELECT a.Name, COUNT(*) ... GROUP BY ..." → PASS;
                         audit.record() writes hashed question + SQL

LAYER 7  Infrastructure: SQLAlchemy connection pool acquires read-only connection →
                         query executes → connection returned to pool

LAYER 8  Observability:  span("agent.inference.query", latency=1.1s);
                         LLM_CALLS_TOTAL["inference","openai"]++;
                         REQUEST_LATENCY["DATABASE_QUERY"].observe(1.4);
                         done event includes trace_id for cross-referencing spans
```

---

## 11. Read-Only Guarantee Summary

| Enforcement Point | Mechanism | What It Blocks |
|---|---|---|
| Prompt Guard | System prompt in `InferenceAgent` | LLM generating DDL/DML by design |
| AST Validator (pass 1) | `sqlglot.parse()` node type walk | INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, GRANT, REVOKE, EXEC, BEGIN |
| Keyword Scanner (pass 2) | Token set intersection | Edge cases / obfuscated SQL not caught by AST |
| DB Connection | Read-only credentials / SQLite `?mode=ro` | Any write that passes passes 1–2 |

If any pass blocks: `ReadOnlyViolationError` raised → SQL never reaches DB → `SQL_BLOCKED_TOTAL` incremented → audit record written → user receives a safe error message.
