# DataScribe Pro — High-Level Design (HLD)
### Production-Grade, Cloud-Agnostic AI Database Discovery & Query System

> **Implementation status**: Layers 1, 3, 4, 5, 6, and 8 are implemented in the current
> release. Layer 2 (AI Gateway) and Layer 7 production features (Redis, Secrets Manager,
> Kubernetes) are the production hardening path — documented here, detailed with
> **[Production Extension]** markers in the [LLD](LLD.md).

---

## 1. Executive Summary

DataScribe Pro is a **read-only, multi-agent AI system** that enables non-technical users to explore, understand, and query relational databases using natural language. It is built on **LangChain** and **LangGraph**, supports multiple cloud AI providers (Azure OpenAI, OpenAI, AWS Bedrock), and is structured across **8 architectural layers** to ensure security, reliability, scalability, and observability in production environments.

**Core guarantee**: The system can never issue DDL (CREATE, ALTER, DROP, TRUNCATE) or DML write commands (INSERT, UPDATE, DELETE). All SQL execution paths are enforced as read-only at the security layer — not just at the prompt level.

---

## 2. System Context

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL CONSUMERS                              │
│   Web App │ Mobile App │ Internal Tools │ BI Tools │ Slack Bot │ CLI    │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │  HTTPS / WebSocket / gRPC
┌─────────────────────────▼───────────────────────────────────────────────┐
│                      DataScribe Pro Platform                             │
│                    (8-Layer Agentic Architecture)                        │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    Target DBs       AI Providers    Telemetry Stack
  (SQL/NoSQL)    (Azure/OpenAI/AWS)  (OTEL/Grafana)
```

---

## 3. The 8-Layer Architecture

```
╔══════════════════════════════════════════════════════════════════════════╗
║  LAYER 1 │ CONSUMER LAYER                                               ║
║  REST API · WebSocket · gRPC · SDK · CLI · Multi-Tenant Session Mgmt   ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 2 │ AI GATEWAY LAYER                                             ║
║  Rate Limiting · API Key Mgmt · Provider Routing · Cost Tracking        ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 3 │ ORCHESTRATION LAYER                                          ║
║  LangGraph StateGraph · SupervisorAgent · PlannerAgent · AgentFleet     ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 4 │ MODEL LAYER                                                  ║
║  ModelRegistry · AzureOpenAI · OpenAI · AWS Bedrock · Fallback Chain   ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 5 │ RAG LAYER                                                    ║
║  Schema Graph · Vector Store · Context Retrieval · Embedding Cache      ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 6 │ SECURITY LAYER                                               ║
║  Read-Only SQL Enforcer · PII Masking · RBAC · Audit Trail              ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 7 │ INFRASTRUCTURE LAYER                                         ║
║  K8s · Connection Pooling · Multi-DB Adapters · Redis Cache · Secrets   ║
╠══════════════════════════════════════════════════════════════════════════╣
║  LAYER 8 │ OBSERVABILITY / EXPLAINABILITY LAYER                         ║
║  OpenTelemetry · Prometheus · Grafana · Agent Decision Audit · Alerts   ║
╚══════════════════════════════════════════════════════════════════════════╝
```

---

## 4. Layer-by-Layer Description

---

### Layer 1 — Consumer Layer

**Purpose**: The single entry point for all clients. Handles protocol translation, authentication, session lifecycle, and multi-tenancy.

```
┌─────────────────────────────────────────────────────────────────┐
│                       CONSUMER LAYER                            │
│                                                                 │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │  REST API    │  │  WebSocket     │  │   gRPC / SDK     │   │
│  │  (FastAPI)   │  │  (streaming)   │  │  (internal use)  │   │
│  └──────┬───────┘  └───────┬────────┘  └────────┬─────────┘   │
│         └──────────────────┴───────────────────-─┘             │
│                             │                                   │
│                  ┌──────────▼──────────┐                       │
│                  │  Session Manager    │                       │
│                  │  (per-tenant state) │                       │
│                  └──────────┬──────────┘                       │
│                             │                                   │
│                  ┌──────────▼──────────┐                       │
│                  │  Request Validator  │                       │
│                  │  + Payload Sanitise │                       │
│                  └─────────────────────┘                       │
└─────────────────────────────────────────────────────────────────┘
```

**Key Responsibilities**:
- JWT / API-key authentication per tenant
- Input sanitisation (max token length, character filtering)
- Session state management — persists `db_graph` across turns per session
- Streaming responses via WebSocket/SSE for long-running discoveries
- Request queuing when downstream is at capacity

**How the notebook maps here**: The `graph.invoke({...state})` call is wrapped by this layer; the stateful `state` object per session lives here.

---

### Layer 2 — AI Gateway Layer

**Purpose**: A cloud-agnostic routing, rate-limiting, and cost-management layer sitting between the orchestration layer and external AI providers.

```
┌─────────────────────────────────────────────────────────────────┐
│                      AI GATEWAY LAYER                           │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Unified Gateway Router                     │   │
│  │                                                         │   │
│  │  ┌───────────────┐  ┌────────────────┐                 │   │
│  │  │ Rate Limiter  │  │ Cost Tracker   │                 │   │
│  │  │ (per tenant)  │  │ (tokens/$/day) │                 │   │
│  │  └───────────────┘  └────────────────┘                 │   │
│  │                                                         │   │
│  │  ┌────────────────────────────────────────────────┐    │   │
│  │  │         Provider Selection Policy              │    │   │
│  │  │  Primary → Fallback → Emergency                │    │   │
│  │  │  (e.g. AzureOAI → OpenAI → AWS Bedrock)       │    │   │
│  │  └────────────────────────────────────────────────┘    │   │
│  │                                                         │   │
│  │  ┌────────────────┐  ┌─────────────────────────────┐  │   │
│  │  │ Retry + Circuit│  │ API Key Rotation &           │  │   │
│  │  │ Breaker        │  │ Credential Manager           │  │   │
│  │  └────────────────┘  └─────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Key Responsibilities**:
- Provider routing: selects Azure OpenAI / OpenAI / AWS Bedrock based on policy (cost, latency, availability)
- Per-tenant rate limiting (requests/min and tokens/day)
- Exponential backoff + circuit breaker for provider failures
- Credential rotation without service restart (via Secrets Manager)
- Token usage metering → emitted to Observability Layer

**How the notebook maps here**: The `ChatOpenAI(temperature=0)` calls in `Config` are replaced by a `ModelRouter` that selects the provider transparently.

---

### Layer 3 — Orchestration Layer

**Purpose**: The brain of the system. Implements the LangGraph `StateGraph` that coordinates the agent fleet, manages state transitions, and enforces workflow logic.

```
┌─────────────────────────────────────────────────────────────────────┐
│                       ORCHESTRATION LAYER                           │
│                                                                     │
│   User Request                                                      │
│        │                                                            │
│        ▼                                                            │
│   ┌────────────────────────────────────────────────────────────┐   │
│   │              LangGraph StateGraph                          │   │
│   │                                                            │   │
│   │  START ──► [classify_input]                                │   │
│   │                   │                                        │   │
│   │          DATABASE_QUERY?                                   │   │
│   │            ├── YES ──► [discover_database] ──►            │   │
│   │            │           (cached after 1st run)             │   │
│   │            │                   │                          │   │
│   │            │           [create_plan]                      │   │
│   │            │                   │                          │   │
│   │            │           [execute_plan]                     │   │
│   │            │                   │                          │   │
│   │            └── NO  ──────────► │                          │   │
│   │                        [generate_response] ──► END        │   │
│   └────────────────────────────────────────────────────────────┘   │
│                                                                     │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐    │
│   │ Supervisor   │  │  Planner     │  │  Discovery           │    │
│   │ Agent        │  │  Agent       │  │  Agent               │    │
│   └──────────────┘  └──────────────┘  └──────────────────────┘    │
│                      ┌──────────────┐                              │
│                      │  Inference   │                              │
│                      │  Agent       │                              │
│                      └──────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

**Agent Roles**:

| Agent | Stateful? | Role | Read-Only? |
|---|---|---|---|
| SupervisorAgent | Yes (via State) | Orchestrates all agents, coordinates delegation | N/A |
| PlannerAgent | No | Decomposes user query into typed steps | N/A |
| DiscoveryAgent | No | Builds schema graph from DB metadata | Yes — uses `information_schema` only |
| InferenceAgent | No | Executes SELECT queries, returns results | Yes — enforced by Security Layer |

**How the notebook maps here**: Directly — `create_graph()`, `SupervisorAgent`, `PlannerAgent`, `DiscoveryAgent`, `InferenceAgent`. Production version adds caching, retry, and security wrapping around each node.

---

### Layer 4 — Model Layer

**Purpose**: Abstracts all LLM provider interactions behind a unified interface. Enables cloud-agnostic model selection without changing orchestration code.

```
┌─────────────────────────────────────────────────────────────────┐
│                         MODEL LAYER                             │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                   ModelRegistry                         │   │
│  │                                                         │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │   │
│  │  │ Azure OpenAI │  │   OpenAI     │  │ AWS Bedrock │  │   │
│  │  │ Adapter      │  │   Adapter    │  │ Adapter     │  │   │
│  │  │              │  │              │  │ (Claude 3,  │  │   │
│  │  │ gpt-4o       │  │ gpt-4o       │  │  Llama 3,  │  │   │
│  │  │ gpt-4o-mini  │  │ gpt-4-turbo  │  │  Titan)    │  │   │
│  │  └──────────────┘  └──────────────┘  └─────────────┘  │   │
│  │                                                         │   │
│  │  ┌─────────────────────────────────────────────────┐   │   │
│  │  │         ModelSelector (selection policy)        │   │   │
│  │  │  - Task type (discovery → heavy, chat → light)  │   │   │
│  │  │  - Cost budget per tenant                       │   │   │
│  │  │  - Provider health (circuit breaker state)      │   │   │
│  │  └─────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**LangChain Model Bindings**:

| Provider | LangChain Class | Task Assignment |
|---|---|---|
| Azure OpenAI | `AzureChatOpenAI` | Discovery (GPT-4o), Inference (GPT-4o-mini) |
| OpenAI | `ChatOpenAI` | Fallback for all tasks |
| AWS Bedrock | `ChatBedrock` (Claude 3.5) | Emergency fallback / cost-optimised path |

**How the notebook maps here**: `Config.llm` and `Config.llm_gpt4` become provider-agnostic instances returned by `ModelRegistry.get(task_type, tenant_policy)`.

---

### Layer 5 — RAG Layer

**Purpose**: Provides schema-aware context injection. The schema graph (NetworkX in prototype → Neo4j in production) acts as structured RAG, ensuring agents query with full relationship awareness.

```
┌─────────────────────────────────────────────────────────────────────┐
│                           RAG LAYER                                 │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                   Schema Knowledge Store                     │  │
│  │                                                              │  │
│  │  ┌───────────────────┐    ┌──────────────────────────────┐  │  │
│  │  │  Schema Graph     │    │  Vector Store                │  │  │
│  │  │  (Neo4j / NX)     │    │  (schema embeddings)         │  │  │
│  │  │                   │    │                              │  │  │
│  │  │  Tables ──► Cols  │    │  "invoice total" ──►         │  │  │
│  │  │  FK relationships │    │  [invoices.Total,            │  │  │
│  │  │  Column metadata  │    │   invoice_items.UnitPrice]   │  │  │
│  │  └────────┬──────────┘    └───────────────┬──────────────┘  │  │
│  │           └──────────────────┬────────────┘                 │  │
│  │                              ▼                               │  │
│  │              ┌───────────────────────────┐                  │  │
│  │              │  Context Retriever        │                  │  │
│  │              │  (graph traversal +       │                  │  │
│  │              │   semantic similarity)    │                  │  │
│  │              └───────────────────────────┘                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌────────────────────────────────┐                                │
│  │  Schema Cache (Redis)          │                                │
│  │  TTL: configurable per DB      │                                │
│  │  Invalidated on DDL detected   │                                │
│  └────────────────────────────────┘                                │
└─────────────────────────────────────────────────────────────────────┘
```

**How the notebook maps here**: `DiscoveryAgent.discover()` → `jsonToGraph()` → `nx.Graph` is the schema graph. Production adds vector embeddings of schema descriptions, semantic retrieval, and Redis caching of the graph so discovery runs only when the schema changes.

---

### Layer 6 — Security Layer

**Purpose**: The most critical layer for production. Enforces read-only access at multiple levels — LLM prompt, SQL parsing, and DB connection — so that no path can ever issue a write or schema-mutating command.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SECURITY LAYER                              │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │               Read-Only Enforcement Stack                    │  │
│  │                                                              │  │
│  │  Level 1: Prompt Guard                                       │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │  System prompt injection: "You MUST ONLY use SELECT.   │  │  │
│  │  │  DDL and DML commands are strictly forbidden."         │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                              │  │
│  │  Level 2: SQL AST Parser (sqlglot / sqlparse)               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │  Parse every SQL string before execution:              │  │  │
│  │  │  BLOCKED: INSERT, UPDATE, DELETE, DROP, CREATE,        │  │  │
│  │  │           ALTER, TRUNCATE, EXEC, GRANT, REVOKE         │  │  │
│  │  │  ALLOWED: SELECT, WITH (CTEs), EXPLAIN                 │  │  │
│  │  │  Raises ReadOnlyViolationError → logged + alerted      │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                              │  │
│  │  Level 3: Database Connection (read-only credentials)       │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │  DB user has GRANT SELECT only                         │  │  │
│  │  │  Connection string: ?mode=ro (SQLite) / pg read role   │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐ │
│  │ PII Detector │  │ RBAC         │  │ Audit Trail              │ │
│  │ (Presidio)   │  │ (per-tenant  │  │ (all queries logged with │ │
│  │ masks output │  │  table ACLs) │  │  user/session/timestamp) │ │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

**Read-Only Enforcement (3 layers of defence)**:
1. **Prompt Guard** — System prompts explicitly forbid non-SELECT SQL
2. **SQL AST Parser** — Every generated SQL is parsed with `sqlglot` before execution; blocked commands raise an exception and are never sent to the DB
3. **DB Connection** — The database user has `SELECT` grants only; even if layers 1 and 2 were bypassed, the DB would reject the command

**How the notebook maps here**: The notebook's disclaimer says "it is possible for it to attempt to do INSERT, UPDATE and DELETE." Production eliminates this risk entirely via the above 3-layer enforcement.

---

### Layer 7 — Infrastructure Layer

**Purpose**: Platform-level concerns: deployment, database connectivity, caching, secrets, and horizontal scalability.

```
┌─────────────────────────────────────────────────────────────────────┐
│                      INFRASTRUCTURE LAYER                           │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                Container Platform (Kubernetes)                │ │
│  │                                                               │ │
│  │  ┌─────────────────┐  ┌───────────────────┐                  │ │
│  │  │  App Pods        │  │  Worker Pods       │                  │ │
│  │  │  (FastAPI)       │  │  (LangGraph tasks) │                  │ │
│  │  │  HPA: 2-20       │  │  HPA: 1-50         │                  │ │
│  │  └─────────────────┘  └───────────────────┘                  │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌────────────────────────────┐  ┌──────────────────────────────┐  │
│  │   Multi-DB Adapter Layer   │  │   Cache Layer (Redis)        │  │
│  │                            │  │                              │  │
│  │  SQLite  (dev)             │  │  Schema graphs (per-DB)      │  │
│  │  PostgreSQL (prod)         │  │  Query result cache          │  │
│  │  MySQL / MariaDB           │  │  Session state               │  │
│  │  MS SQL Server             │  │  LLM response cache          │  │
│  │  Snowflake / BigQuery      │  │                              │  │
│  │  (read-only URI per env)   │  │                              │  │
│  └────────────────────────────┘  └──────────────────────────────┘  │
│                                                                     │
│  ┌────────────────────────────┐  ┌──────────────────────────────┐  │
│  │   Secrets Management       │  │   Connection Pool Manager    │  │
│  │   (Vault / AWS SM /        │  │   (SQLAlchemy pool per DB)   │  │
│  │    Azure Key Vault)        │  │   Max connections per tenant │  │
│  └────────────────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**How the notebook maps here**: `sqlite:///` in `Config.db_engine` becomes a multi-DB adapter. The environment variable `DATABASE` is replaced by a Secrets Manager lookup per tenant. Redis caches the `db_graph` so `discover_database` skips the expensive LLM call on repeat queries.

---

### Layer 8 — Observability / Explainability Layer

**Purpose**: Full visibility into agent decisions, LLM calls, SQL executions, and system health. Enables debugging, compliance, and cost optimisation.

```
┌─────────────────────────────────────────────────────────────────────┐
│                 OBSERVABILITY / EXPLAINABILITY LAYER                │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                    Tracing (OpenTelemetry)                    │ │
│  │   Every agent invocation creates a span:                     │ │
│  │   [session] → [classify] → [discover] → [plan] →            │ │
│  │   [execute:step1] → [execute:step2] → [respond]              │ │
│  │   Spans include: model used, tokens, latency, SQL executed   │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌────────────────────────────┐  ┌──────────────────────────────┐  │
│  │  Metrics (Prometheus)      │  │  Structured Logging          │  │
│  │  GET /metrics              │  │  (structlog JSON pipeline)   │  │
│  │                            │  │                              │  │
│  │  - requests_total          │  │  Every log line includes:    │  │
│  │  - request_latency_seconds │  │  session_id, event name,     │  │
│  │  - llm_calls_total         │  │  agent, provider, latency    │  │
│  │  - llm_latency_seconds     │  │  sql_hash (not raw SQL)      │  │
│  │  - schema_cache_hits_total │  │                              │  │
│  │  - sql_blocked_total       │  │                              │  │
│  │  - plan_steps_total        │  │                              │  │
│  │  - agent_errors_total      │  │                              │  │
│  │  - active_sessions (gauge) │  │                              │  │
│  └────────────────────────────┘  └──────────────────────────────┘  │
│                                                                     │
│  ┌────────────────────────────┐  ┌──────────────────────────────┐  │
│  │  Agent Decision Audit      │  │  Alerts (PagerDuty / SNS)    │  │
│  │  (src/observability/       │  │  [Production Extension]      │  │
│  │   audit.py)                │  │                              │  │
│  │  For each query, stores:   │  │  - sql_blocked > 0/min       │  │
│  │  - question_hash (SHA-256) │  │  - LLM error rate > 1%       │  │
│  │  - plan_steps              │  │  - p99 latency > 30s         │  │
│  │  - sql_hashes (SHA-256)    │  │  - Schema cache miss > 50%   │  │
│  │  - model_used, latency_ms  │  │  - Provider circuit open     │  │
│  │  - trace_id (OTel link)    │  │                              │  │
│  │  - response_preview        │  │                              │  │
│  └────────────────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**How the notebook maps here**: `logging.basicConfig` in the notebook is replaced by structured JSON logs, OTel spans wrapping each `StateGraph` node, and Prometheus metrics collected from each agent method call.

---

## 5. End-to-End Request Flow

```
User: "Who are the top 3 artists by track count?"

[L1] Consumer Layer
  → WebSocket receives JSON {"type":"query","question":"..."}
  → bind_request_context(session_id) attaches session to all log lines
  → ACTIVE_SESSIONS gauge already tracking this connection
  → Routes to Orchestration Layer via graph.astream()

[L2] AI Gateway Layer  [Production Extension — currently direct provider call]
  → Checks rate limit for session (OK)
  → Selects primary provider via LLM_PROVIDER env var

[L3] Orchestration Layer — StateGraph
  → Node: classify_input → "DATABASE_QUERY"
        span("graph.classify_input"), LLM_CALLS_TOTAL["classify","openai"]++
  → Node: discover_database → CACHE HIT (db_graph in session state)
        SCHEMA_CACHE_HITS++; WebSocket sends {"type":"schema_loaded","cached":true}
  → Node: create_plan (PlannerAgent)
        span("agent.planner.plan"), PLAN_STEPS_TOTAL["Inference"]++
        Plan: ["Inference: Get track count per artist ordered desc limit 3",
               "General: Present results clearly"]
  → Node: execute_plan (SupervisorAgent → InferenceAgent)

[L5] RAG Layer
  → ContextRetriever: "artist"+"track" keywords → matches artists, tracks, albums
  → BFS finds join path: artists ─AlbumId─ albums ─TrackId─ tracks
  → schema_summary injected into InferenceAgent system prompt

[L6] Security Layer
  → Prompt Guard: system prompt forbids DDL/DML
  → SQL AST Validator: "SELECT a.Name, COUNT(*) ... GROUP BY ..." → PASS
  → DB Connection: read-only SQLAlchemy session, no write grants

[L7] Infrastructure Layer
  → SQLAlchemy pool acquires connection → query executes → connection returned

[L8] Observability Layer
  → span("agent.inference.query", latency=1.1s) recorded
  → LLM_CALLS_TOTAL["inference","openai"]++; LLM_LATENCY.observe(1.1)
  → REQUEST_LATENCY["DATABASE_QUERY"].observe(1.4)
  → audit.record(question_hash=..., plan_steps=[...], sql_hashes=[...])

[L3] → Node: generate_response (SupervisorAgent)
[L1] → Response streamed word-by-word via WebSocket {"type":"chunk","text":"..."}
     → {"type":"done","latency_ms":1400,"trace_id":"<otel-hex>"}
```

---

## 6. Technology Stack

| Concern | Technology | Status |
|---|---|---|
| API Framework | FastAPI + Uvicorn | Implemented |
| Chat Frontend | WebSocket + dark-theme HTML/JS UI | Implemented |
| Agent Framework | LangGraph `StateGraph` + LangChain 1.x `create_agent` | Implemented |
| LLM: OpenAI | `langchain-openai` (`ChatOpenAI`) | Implemented |
| LLM: Azure OpenAI | `langchain-openai` (`AzureChatOpenAI`) | Implemented |
| LLM: AWS Bedrock | `langchain-aws` (`ChatBedrock`) | Implemented |
| Schema Introspection | SQLAlchemy `inspect()` → NetworkX graph | Implemented |
| Context Retrieval | Keyword match + BFS join path traversal | Implemented |
| SQL Validation | `sqlglot` AST parser + keyword scan | Implemented |
| Tracing | OpenTelemetry SDK → ConsoleSpanExporter / OTLP | Implemented |
| Metrics | `prometheus-client` → `GET /metrics` | Implemented |
| Logging | `structlog` JSON pipeline + context vars | Implemented |
| Audit Trail | SHA-256 hashed queries in structured logs | Implemented |
| Schema Graph (prod) | Neo4j + `langchain-community` Neo4jGraph | Production Extension |
| Vector Store | ChromaDB / Pinecone / pgvector | Production Extension |
| PII Detection | Microsoft Presidio | Production Extension |
| Session Cache | Redis (schema graph + session state TTL) | Production Extension |
| Secrets | AWS Secrets Manager / Azure Key Vault / HashiCorp Vault | Production Extension |
| Containers | Docker + Kubernetes (EKS / AKS / GKE) | Production Extension |
| AI Gateway | Rate limiting, cost tracking, fallback chain | Production Extension |
| CI/CD | GitHub Actions | Production Extension |

---

## 7. Key Non-Functional Requirements

| Property | Target |
|---|---|
| Read-only guarantee | 100% — 3 enforcement layers |
| Schema discovery latency | < 30s (cached < 100ms) |
| Query response latency (p95) | < 15s |
| System availability | 99.9% |
| Multi-tenancy | Isolated per tenant (schema cache, rate limits, DB creds) |
| Cloud portability | Provider changed via config, no code change required |
| Audit retention | 90 days minimum |
