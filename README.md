# DataScribe Pro

A production-grade, read-only AI agent that lets you chat with any relational database in plain English.
Built on LangGraph and LangChain, cloud-agnostic across OpenAI, Azure OpenAI, and AWS Bedrock.

---

## What it does

Connect DataScribe Pro to any SQL database and ask questions like:

- *"Who are the top 5 artists by total track count?"*
- *"Show me monthly revenue trends for the last year"*
- *"Which customers have never placed an order?"*

The system decomposes your question into a plan, discovers the schema, generates a safe `SELECT` query, and streams the answer back — all through a browser chat UI.

**Hard read-only guarantee**: Three independent enforcement layers (system prompt, SQL AST validator, DB credentials) ensure no `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, or any other write command can ever reach your database.

---

## Architecture — 8 Layers

```
┌─────────────────────────────────────────────────────────┐
│  L1  Consumer       FastAPI · WebSocket · Session Mgmt  │
│  L2  AI Gateway     Provider Routing · Rate Limiting †  │
│  L3  Orchestration  LangGraph StateGraph · Agent Fleet  │
│  L4  Model          OpenAI · Azure OpenAI · AWS Bedrock │
│  L5  RAG            Schema Graph · BFS Context Retrieval│
│  L6  Security       SQL AST Validator · Audit Trail     │
│  L7  Infrastructure SQLAlchemy · Multi-DB Adapters      │
│  L8  Observability  OTel Tracing · Prometheus · structlog│
└─────────────────────────────────────────────────────────┘
† Layer 2 is documented as a production extension (see docs/HLD.md)
```

Full design: [docs/HLD.md](docs/HLD.md) · [docs/LLD.md](docs/LLD.md) · [docs/hld_architecture.drawio](docs/hld_architecture.drawio)

---

## Quick Start

### 1. Create and activate the virtual environment

```bash
cd datascribe-pro
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure your provider

```bash
cp .env.example .env
```

Edit `.env` — pick **one** provider block:

```env
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# — or Azure OpenAI —
# LLM_PROVIDER=azure_openai
# AZURE_OPENAI_API_KEY=...
# AZURE_OPENAI_ENDPOINT=https://YOUR_RESOURCE.openai.azure.com/
# AZURE_OPENAI_API_VERSION=2024-08-01-preview
# AZURE_DEPLOYMENT_HEAVY=gpt-4o
# AZURE_DEPLOYMENT_LIGHT=gpt-4o-mini

# — or AWS Bedrock (uses IAM role / ~/.aws/credentials) —
# LLM_PROVIDER=bedrock
# AWS_REGION=us-east-1
```

Set your database (any SQLAlchemy URI):

```env
DATABASE_URL=sqlite:///data/chinook.db
# DATABASE_URL=postgresql://readonly_user:pass@host:5432/mydb
# DATABASE_URL=mssql+pyodbc://readonly_user:pass@host/mydb?driver=ODBC+Driver+17+for+SQL+Server
```

> A sample SQLite database (`data/chinook.db`) is included — 11 tables covering artists,
> albums, tracks, customers, and invoices. No database setup needed to try it out.

### 3. Run

```bash
python main.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## Configuration Reference

All settings are read from `.env` (or environment variables):

| Variable | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` · `azure_openai` · `bedrock` |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `OPENAI_MODEL_HEAVY` | `gpt-4o` | Model for planning (heavier tasks) |
| `OPENAI_MODEL_LIGHT` | `gpt-4o-mini` | Model for inference and chat |
| `AZURE_OPENAI_API_KEY` | — | Azure OpenAI key |
| `AZURE_OPENAI_ENDPOINT` | — | `https://YOUR_RESOURCE.openai.azure.com/` |
| `AZURE_OPENAI_API_VERSION` | `2024-08-01-preview` | API version |
| `AZURE_DEPLOYMENT_HEAVY` | `gpt-4o` | Heavy deployment name |
| `AZURE_DEPLOYMENT_LIGHT` | `gpt-4o-mini` | Light deployment name |
| `AWS_REGION` | `us-east-1` | Bedrock region |
| `BEDROCK_MODEL_HEAVY` | `anthropic.claude-3-5-sonnet-…` | Heavy Bedrock model ID |
| `BEDROCK_MODEL_LIGHT` | `anthropic.claude-3-haiku-…` | Light Bedrock model ID |
| `DATABASE_URL` | `sqlite:///data/chinook.db` | Any SQLAlchemy URI |
| `APP_HOST` | `0.0.0.0` | Bind address |
| `APP_PORT` | `8000` | Listen port |
| `AGENT_MAX_ITERATIONS` | `10` | ReAct loop max steps |
| `MAX_RESULT_ROWS` | `500` | Auto-appended `LIMIT` |
| `OTLP_ENDPOINT` | — | Optional — send OTel spans to Jaeger/Tempo |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Chat frontend |
| `GET` | `/health` | `{"status":"ok","provider":"..."}` |
| `GET` | `/schema?session_id=<id>` | Table list for a session |
| `GET` | `/metrics` | Prometheus scrape endpoint |
| `WS` | `/ws/{session_id}` | Chat WebSocket |

### WebSocket protocol

**Client → Server**

```json
{ "type": "query", "question": "Who are the top artists?" }
{ "type": "reset" }
```

**Server → Client** (streamed in order)

```json
{ "type": "status",       "text": "Analyzing...",   "stage": "classify" }
{ "type": "schema_loaded","tables": ["artists",...], "cached": true }
{ "type": "plan",         "steps": ["Inference: ...", "General: ..."] }
{ "type": "step_start",   "index": 0, "text": "Inference: ..." }
{ "type": "chunk",        "text": "The top " }
{ "type": "done",         "latency_ms": 1420, "turns": 3, "trace_id": "abc123..." }
```

---

## Read-Only Enforcement

Three independent layers — bypassing one does not compromise the others:

| Layer | Mechanism | Blocks |
|---|---|---|
| Prompt Guard | System prompt in every agent | LLM generating write SQL by intent |
| SQL AST Validator | `sqlglot` parse + node type walk | INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE, GRANT, REVOKE, EXEC |
| Keyword Scanner | Token set intersection | Obfuscated or dialect-edge-case SQL |
| DB Connection | Read-only credentials / `?mode=ro` | Any write that passes the above |

Every blocked attempt increments `datascribe_sql_blocked_total` and writes an audit record.

---

## Observability

### Prometheus metrics (`GET /metrics`)

| Metric | Type | What it measures |
|---|---|---|
| `datascribe_requests_total` | Counter | Queries by input type |
| `datascribe_request_latency_seconds` | Histogram | End-to-end latency |
| `datascribe_llm_calls_total` | Counter | LLM invocations by agent + provider |
| `datascribe_llm_latency_seconds` | Histogram | Per-LLM-call latency |
| `datascribe_llm_errors_total` | Counter | LLM failures |
| `datascribe_schema_cache_hits_total` | Counter | Schema served from session cache |
| `datascribe_schema_cache_misses_total` | Counter | Schema rebuilt via introspection |
| `datascribe_sql_blocked_total` | Counter | Read-only violations caught |
| `datascribe_plan_steps_total` | Counter | Plan steps by type |
| `datascribe_agent_errors_total` | Counter | Unhandled agent exceptions |
| `datascribe_active_sessions` | Gauge | Live WebSocket connections |

### Tracing (OpenTelemetry)

Spans are written to stdout by default. To ship to Jaeger or Grafana Tempo:

```env
OTLP_ENDPOINT=http://localhost:4317
```

Span hierarchy per query:
```
graph.classify_input
graph.discover_database
graph.create_plan
  └── agent.planner.plan
graph.execute_plan
  └── agent.inference.query   (one per Inference step)
graph.generate_response
```

### Structured logs

Every log line is JSON (structlog), including `session_id`, `event`, `agent`, `provider`, and timing. Raw SQL and questions are never logged — only their SHA-256 prefix.

---

## Project Structure

```
datascribe-pro/
├── src/
│   ├── consumer/           # Layer 1 — FastAPI app, session manager
│   ├── models/             # Layer 4 — ModelProvider (OpenAI / Azure / Bedrock)
│   ├── orchestration/      # Layer 3 — LangGraph StateGraph + agent fleet
│   │   └── agents/         #   PlannerAgent, InferenceAgent, SupervisorAgent, DiscoveryAgent
│   ├── rag/                # Layer 5 — SchemaGraphBuilder, ContextRetriever
│   ├── security/           # Layer 6 — ReadOnlySQLValidator
│   ├── config/             # Pydantic BaseSettings
│   └── observability/      # Layer 8 — logger, metrics, tracing, audit
├── frontend/               # Browser chat UI (HTML + JS, no build step)
├── docs/                   # HLD, LLD, draw.io architecture diagram
├── data/                   # chinook.db sample SQLite database
├── main.py                 # Entry point
├── requirements.txt
└── .env.example
```

---

## Supported Databases

Any database with a SQLAlchemy driver works:

| Database | Example URI |
|---|---|
| SQLite (dev/demo) | `sqlite:///data/mydb.db` |
| PostgreSQL | `postgresql://user:pass@host:5432/db` |
| MySQL / MariaDB | `mysql+pymysql://user:pass@host:3306/db` |
| MS SQL Server | `mssql+pyodbc://user:pass@host/db?driver=ODBC+Driver+17+for+SQL+Server` |
| Snowflake | `snowflake://user:pass@account/db/schema` |

Use a **read-only database user** as an additional safeguard at the infrastructure level.

---

## Production Extensions

The following are designed and documented but not yet wired in the current release:

- **AI Gateway** (Layer 2) — per-tenant rate limiting, token cost tracking, provider fallback chain with circuit breaker
- **Redis session store** — replace in-memory `SessionManager` with Redis-backed store (TTL, horizontal scaling)
- **Secrets Manager** — AWS Secrets Manager / Azure Key Vault / HashiCorp Vault for credential rotation
- **Vector store** — embed schema descriptions for semantic table/column matching (ChromaDB / pgvector)
- **PII masking** — Microsoft Presidio to redact PII from LLM responses before returning to client
- **RBAC** — table-level access control lists per tenant
- **Kubernetes** — HPA-enabled deployment manifests (see [docs/LLD.md](docs/LLD.md))

---

## Dependencies

```
fastapi · uvicorn · langchain · langchain-openai · langchain-community
langgraph · langchain-aws · boto3 · sqlalchemy · sqlglot · networkx
pydantic-settings · structlog · prometheus-client
opentelemetry-api · opentelemetry-sdk · opentelemetry-exporter-otlp-proto-grpc
```
