# Observability

Documents the observability stack: Prometheus metrics, request tracing, structured logging, and debug trace export.

## Source of Truth

| Concern | File |
|---------|------|
| Metrics definitions | `app/observability/metrics.py` |
| Trace context propagation | `app/observability/trace_context.py` |
| Prometheus multiprocess support | `app/observability/prometheus.py` |
| Debug trace export | `app/services/agentscope/registry.py` (`_write_debug_trace`) |
| Logging configuration | `app/core/logging.py` |

---

## Prometheus Metrics

All metrics are defined in `app/observability/metrics.py` using the `prometheus_client` library (Counter and Histogram types).

### Analysis Runs

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `analysis_runs_total` | Counter | `status` | Number of analysis runs |

### Orchestrator

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `orchestrator_step_duration_seconds` | Histogram | `agent` | Agent step latency |

### Agentic Runtime

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `agentic_runtime_runs_total` | Counter | `status`, `mode`, `resumed` | Agentic runtime runs |
| `agentic_runtime_tool_selections_total` | Counter | `tool`, `source`, `degraded` | Planner selections by runtime tool |
| `agentic_runtime_planner_calls_total` | Counter | `status`, `source` | Planner call outcomes |
| `agentic_runtime_planner_duration_seconds` | Histogram | `status`, `source` | Planner latency (buckets: 1ms - 5s) |
| `agentic_runtime_tool_calls_total` | Counter | `tool`, `status` | Runtime tool invocations |
| `agentic_runtime_tool_duration_seconds` | Histogram | `tool`, `status` | Runtime tool duration (buckets: 10ms - 60s) |
| `agentic_runtime_subagent_sessions_total` | Counter | `source_tool`, `session_mode`, `status`, `resumed` | Subagent session lifecycle events |
| `agentic_runtime_final_decisions_total` | Counter | `decision`, `mode` | Final trading decisions produced |
| `agentic_runtime_execution_outcomes_total` | Counter | `status`, `mode` | Execution outcomes |
| `agentic_runtime_session_messages_total` | Counter | `resume_requested` | Messages sent to runtime sessions |

### LLM

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_calls_total` | Counter | `provider`, `status` | Total LLM calls |
| `llm_prompt_tokens_total` | Counter | `provider`, `model` | Prompt tokens consumed |
| `llm_completion_tokens_total` | Counter | `provider`, `model` | Completion tokens consumed |
| `llm_cost_usd_total` | Counter | `provider`, `model` | Estimated LLM cost in USD |
| `llm_latency_seconds` | Histogram | `provider`, `model`, `status` | LLM end-to-end latency |

### HTTP

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `backend_http_requests_total` | Counter | `method`, `route`, `status` | Total backend HTTP requests |
| `backend_http_request_duration_seconds` | Histogram | `method`, `route` | Request duration (buckets: 5ms - 20s) |

### MCP Tool Layer

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mcp_tool_calls_total` | Counter | `tool`, `status` | MCP tool invocations via MCPClientAdapter |
| `mcp_tool_duration_seconds` | Histogram | `tool`, `status` | MCP tool execution duration (buckets: 1ms - 5s) |

### Risk Engine and Decision Quality

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `risk_evaluation_total` | Counter | `accepted`, `asset_class`, `mode` | Risk engine evaluations |
| `decision_gate_blocks_total` | Counter | `gate` | Decision gate blocks that forced HOLD |
| `debate_impact_abs` | Histogram | `decision`, `strong_conflict` | Absolute debate score contribution to combined score |
| `contradiction_detection_total` | Counter | `level` | Trend/momentum contradiction detections |

### Cache

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `metaapi_cache_hits_total` | Counter | `resource` | MetaAPI Redis cache hits |
| `metaapi_cache_misses_total` | Counter | `resource` | MetaAPI Redis cache misses |
| `yfinance_cache_hits_total` | Counter | `resource` | Yahoo Finance Redis cache hits |
| `yfinance_cache_misses_total` | Counter | `resource` | Yahoo Finance Redis cache misses |

### External Providers

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `external_provider_failures_total` | Counter | `provider` | External provider failures |
| `metaapi_sdk_circuit_open_total` | Counter | `region`, `operation` | MetaAPI SDK circuit breaker openings |

---

## Trace Context

Defined in `app/observability/trace_context.py`. Provides async-safe correlation and causation ID propagation using Python `contextvars`.

### How It Works

- Every request gets a unique `correlation_id` (16-char hex from UUID4).
- The `causation_id` tracks parent-child relationships via a stack.
- Uses `contextvars.ContextVar` instead of `threading.local`, so each async task gets its own isolated trace state.
- Copy-on-write stack semantics prevent mutation of parent task state.

### API

```python
from app.observability.trace_context import trace_ctx

# At request boundary
trace_ctx.set(correlation_id="run-42", causation_id="api-request")

# Read current IDs
cid = trace_ctx.correlation_id
cause = trace_ctx.causation_id

# Push/pop for child operations
trace_ctx.push_causation("agent-step-technical")
# ... child work ...
trace_ctx.pop_causation()

# Export as dict for log injection
trace_ctx.as_dict()  # {"correlation_id": "...", "causation_id": "..."}
```

### Propagation

The `correlation_id` ties all events in a single user-facing request. The causation stack tracks which operation triggered which child operation, enabling parent-to-child tracing within a single process.

---

## Run Trace

Each analysis run stores a `trace` JSON field in the `analysis_runs` table. The agentic runtime populates this with:

- **sessions**: Agent session metadata (phase, role, depth, status, llm_enabled).
- **events**: Lifecycle, data, and agent events with timestamps.
- **session_history**: Per-agent message history (system, user, assistant messages).

This trace is queryable via the API and provides a complete record of what happened during a run.

---

## Agent Step Audit

The `agent_steps` table provides a per-agent, per-run audit trail.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer (PK) | Auto-increment primary key |
| `run_id` | Integer (FK) | References `analysis_runs.id` |
| `agent_name` | String(100) | Agent identifier |
| `status` | String(20) | Step status (default: `completed`) |
| `input_payload` | JSON | What the agent received |
| `output_payload` | JSON | What the agent produced |
| `error` | Text (nullable) | Error message if the step failed |
| `created_at` | DateTime | UTC timestamp |

Every agent execution is recorded with its full input and output payloads, providing a complete audit trail for debugging and compliance.

---

## Debug Trace Export

Optional JSON file export for offline analysis. Controlled by `debug_trade_json_enabled` in settings.

### Schema (v2)

Written by `_write_debug_trace` in `app/services/agentscope/registry.py`. Files are named `run-{id}-{timestamp}.json` and written to the directory configured by `debug_trade_json_dir` (default: `./debug-traces`).

Top-level structure:

```
schema_version: 2
generated_at: ISO 8601 timestamp
runtime_engine: "agentscope_v1"
run:
  id, pair, timeframe, mode, status, risk_percent, created_at, updated_at
context:
  market_snapshot: current price data
  price_history: list of OHLCV candles (configurable limit)
  news_context: news data
workflow: ordered list of agent names executed
agent_steps: per-agent input/output/status/llm_enabled
analysis_bundle:
  analysis_outputs: technical, news, market-context
  bullish / bearish: researcher outputs
  trader_decision: trader agent output
  risk: risk manager output
  execution_manager: execution manager output
final_decision: the run's trading decision
execution: execution details
elapsed_seconds: total pipeline time
```

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `debug_trade_json_enabled` | `false` | Enable debug trace file export |
| `debug_trade_json_dir` | `./debug-traces` | Output directory |
| `debug_trade_json_include_price_history` | (configurable) | Include OHLCV candles in export |
| `debug_trade_json_price_history_limit` | (configurable) | Max candles to include |

---

## Logging

Configured in `app/core/logging.py`:

```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
```

- INFO-level structured logging to stdout.
- Uses the Python standard `logging` module.
- No built-in log aggregation; relies on container or orchestrator log collection (e.g., Docker log driver, Kubernetes stdout capture).

---

## OpenTelemetry

Optional FastAPI auto-instrumentation. Controlled by the `OPEN_TELEMETRY_ENABLED` environment variable (default: `false`).

When enabled, `FastAPIInstrumentor.instrument_app(app)` is called in `app/main.py`, which provides automatic span creation for all HTTP endpoints.

Dependencies (from `requirements.txt`):
- `opentelemetry-api >= 1.39.0`
- `opentelemetry-sdk >= 1.39.0`
- `opentelemetry-instrumentation-fastapi >= 0.48b0`

Exporter configuration is handled via standard OpenTelemetry environment variables (e.g., `OTEL_EXPORTER_OTLP_ENDPOINT`).

---

## Infrastructure

### Prometheus Scraping

- The FastAPI application exposes a `/metrics` endpoint that returns `prometheus_client` output.
- `build_metrics_payload()` in `app/observability/prometheus.py` handles both single-process and multiprocess modes.

### Multiprocess Support

When `PROMETHEUS_MULTIPROC_DIR` is set, the metrics system uses `prometheus_client.multiprocess.MultiProcessCollector` to aggregate metrics across forked worker processes. The `mark_worker_process_dead(pid)` function cleans up stale process entries.

### Worker Metrics

Celery workers start a dedicated Prometheus HTTP server via `start_worker_metrics_server()`. The port is configurable via `PROMETHEUS_WORKER_PORT` (default: `9101`). A thread lock ensures only one metrics server starts per process.

### Grafana

Grafana dashboards consume the Prometheus metrics for visualization. Dashboard configuration is external to the application.

---

## Known Limitations

- **No distributed tracing across Celery workers.** The `correlation_id` is propagated, but there is no span-level tracing linking the API process to the worker process.
- **Basic logging.** INFO level only, plain text format (not structured JSON). No log-level configuration at runtime.
- **No alerting rules configured by default.** Prometheus alerting must be set up separately.
- **Debug trace export is file-based.** No centralized trace storage; files are written to local disk.
- **No LLM prompt/response logging to external systems.** LLM interactions are captured in agent step payloads and run traces, but not streamed to a dedicated logging backend.
- **Process-level metrics.** Not all dimensions have request-level histograms; some counters aggregate across all requests in a process.
