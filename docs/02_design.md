# Template Design Specification — CMN-C2-213

Procurement Request Classifier & Sourcing Router Agent

| Field | Value |
|-------|-------|
| Template ID | CMN-C2-213 |
| Category | Cat 2 (multi-step domain workflow) |
| Industry | CMN (cross-industry; procurement specifics in config) |
| Scaffold issue | DRAFT-326 |
| Reference pattern | ChatAgent (single-turn classify + route over free text) |

## 1. Use Case

Enterprise procurement intake is a high-volume, low-structure stream: purchase requests
arrive via email, Slack, web forms, and ERP webhooks with inconsistent, often incomplete
data. Coordinators manually triage each request — inferring spend category, urgency, and
compliance risk, then checking supplier/contract coverage before choosing a sourcing
pathway. This agent replaces that manual loop with a fixed four-step pipeline that produces
a routed, explained, compliance-flagged decision a downstream sourcing queue can consume
directly.

The value is the *coordination* of classification + coverage reasoning + routing over noisy
free text — not a single lookup. A rule-only form router cannot read natural-language intent;
that reasoning is where an LLM-backed **agent** earns its place over a plain tool.

## 2. Position in AgentCore Architecture

- **Agent Class**: `Graph` (outer `AgentBaseGraph`) + `ProcurementRoutingGraphNode` (`GraphNode`, `main` slot) + `ProcurementWorkflowGraph` (inner `BaseGraph`)
- **L1 Base type**: `AgentBaseGraph` (Level 1 direct, `L3->L1`). Not `AutonomousBaseGraph` —
  this is a fixed, deterministic pipeline with no self-directed think/act/observe loop.
- **Level 2 type**: none. Reference pattern only is `ChatAgent`; the template inherits the
  L1 framework directly and implements its own multi-step workflow (permitted for Cat 2
  per the framework's Agent-vs-Tool / Level classification rules).
- **Three-Layer Separation**:
  - **State**: flat `TypedDict` composition (`src/schemas/state.py`, subtype of `AgentState`) —
    no Pydantic, no credential objects, no `InvocationContext`.
  - **Node**: L1 inheritance via Template Method — domain nodes extend `FunctionNode`
    (override `execute()` + optional `_extra_security_gate_*` hooks), except
    `NormalizeExtractNode` which extends `BaseNode` with an S-2 passthrough (see §5);
    the `main` slot extends `GraphNode`.
  - **Graph**: composition via `register_nodes()` + `add_edges()`.

### Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | `AgentBaseGraph` | `AutonomousBaseGraph` | **`AgentBaseGraph`** | Fixed 4-step pipeline; no autonomous loop, no cost ceiling. |
| Domain composition | 4 `FunctionNode`s flattened onto the outer backbone (override `add_edges()`) | `GraphNode` in `main` wrapping an inner `BaseGraph` | **`GraphNode` + inner `BaseGraph`** | Canonical Cat 2 (`graph_cat2_sample.py`): the multi-step workflow is encapsulated in `src/graph/domain_workflow_graph.py`; the outer backbone is left untouched (no `add_edges()`/`route()` override, no extra backbone slot). Keeps the framework's retry/route contract intact. |
| Subgraph input channel | structured payload via `input_context` to a flattened pre_process node | JSON string via `GraphNode.extract_input` | **JSON string + `BaseNode` S-2 passthrough on the entry node** | `GraphNode` forwards only a string and does not pass `input_context`. The payload is JSON-serialised by `extract_input`; `NormalizeExtractNode` is a `BaseNode` whose S-2 gate is a no-op passthrough (gating handled at the GraphNode boundary, per the framework's documented `BaseNode` S-2 exception) so the default `name` PII heuristic does not corrupt multi-word vendor/item names. Output PII control is preserved by `RouteExplainNode`'s S-3 redaction. |
| Classification engine | Config-driven deterministic rules | Live LLM call per request | **Config-driven, LLM-overridden seam** | `ClassifyNode` attempts a live Azure OpenAI call (see §6) and falls back to the deterministic, offline-testable taxonomy rules on any failure — no code path requires network access to produce a valid classification. |

## 3. Architecture Overview

Canonical Cat 2 two-layer composition (`graph_cat2_sample.py`):

- **Outer graph** (`src/graph/graph.py`, `Graph(AgentBaseGraph)`) keeps the fixed backbone
  `initialize -> pre_process -> main -> {route} -> post_process -> finalize` **unchanged**
  (no `add_edges()` / `route()` override). Slots: `pre_process` = `PreProcessNode` (presence
  validation / context setup), `main` = `ProcurementRoutingGraphNode` (a `GraphNode` wrapping
  the domain workflow), `post_process` = `PostProcessNode` (surfaces the routing envelope as
  `result`).
- **Inner graph** (`src/graph/domain_workflow_graph.py`, `ProcurementWorkflowGraph(BaseGraph)`)
  holds the proposal's 4-step domain pipeline with a fully custom topology.

### Outer backbone (slots)

| Slot | Node | Responsibility | Inherits/Overrides |
|------|------|---------------|-------------------|
| initialize | InitializeNode (default) | trust level, schema_version, ids | default |
| pre_process | **PreProcessNode** | confirm a request is present; record `validated_input` | FunctionNode |
| main | **ProcurementRoutingGraphNode** | run the domain subgraph; `extract_input` (serialise payload) + `merge_output` (map result); `error_strategy="handle"` | GraphNode |
| post_process | **PostProcessNode** | surface `formatted_output` envelope as `result` | FunctionNode |
| finalize | FinalizeNode (default) | response_metadata, total_time_ms | default |

### Inner domain workflow (`ProcurementWorkflowGraph`)

| Node | Responsibility | Reads | Writes | Inherits |
|------|---------------|-------|--------|----------|
| normalize | parse free text + form `fields` into a canonical request record; flag missing mandatory fields | `user_input` (JSON envelope) | `request_record`, `missing_fields`, `status` | **BaseNode** (S-2 passthrough — see §5) |
| classify | assign spend category, urgency tier, compliance risk + per-axis confidence over the configured taxonomy | `request_record` | `classification`, `status` | FunctionNode |
| coverage | match vendor against approved-supplier list and item/amount against contract coverage table | `request_record`, `classification` | `coverage`, `status` | FunctionNode (`required_trust_level = VERIFIED_EXTERNAL`) |
| route_explain | apply pathway rules to (category, amount band, urgency, coverage) -> pathway + rationale + confidence + review flags | `request_record`, `classification`, `coverage`, `missing_fields` | `routing_decision`, `formatted_output`, `status` | FunctionNode |

### Data Flow

```
outer:  START -> initialize -> pre_process(PreProcess) -> main(ProcurementRoutingGraphNode)
                 -> {framework route}  success -> post_process(PostProcess) -> finalize -> END
                                       retry   -> pre_process     (retry_count < max_retry)
                                       error   -> finalize        (graceful: GraphNode error_strategy="handle")

inner:  START -> normalize -> classify -> coverage -> route_explain -> END
                    |            |            |
                    +------------+------------+--(status == error)--> END
```

The outer graph uses the **framework default** `route()` (success -> post_process, retry ->
pre_process, terminal/error -> finalize). The inner graph's conditional edges exist only to
short-circuit to `END` on an upstream error status (the framework does not auto-skip `execute()`
on an inbound error). Low-confidence and off-contract cases are **not** graph branches — they are
flag values in `routing_decision` (`review_flags`), keeping the happy path linear.

### State Definition (`src/schemas/state.py`)

| Field | Type | Purpose | Required | Writer |
|-------|------|---------|----------|--------|
| `request_record` | `dict` | canonical parsed request (item, amount, currency, vendor, requester, need_by, justification) | NotRequired | NormalizeExtractNode |
| `missing_fields` | `list[str]` | mandatory fields absent from input | NotRequired | NormalizeExtractNode |
| `classification` | `dict` | spend_category, urgency_tier, compliance_risk, per-axis + overall confidence | NotRequired | ClassifyNode |
| `coverage` | `dict` | status (approved / off_contract / unknown), matched_supplier, matched_contract | NotRequired | CoverageCheckNode |
| `routing_decision` | `dict` | pathway, rationale, confidence, review_flags | NotRequired | RouteExplainNode |

Shared fields (`user_input`, `validated_input`, `status`, `node_history`, `error_log`, `hitl_*`, …)
are inherited from `AgentState` and not redeclared.

**State Constraints (mandatory):**
- Flat `TypedDict` only — primitives + JSON-serializable containers.
- No JWT / API keys / credentials in State (checkpoint DB leakage).
- `InvocationContext` via `config["configurable"]` only — never in State.
- No Pydantic / dataclass / arbitrary Python objects (msgpack incompatible).

## 4. Configuration (citizen-editable)

Procurement specifics live entirely in config so one CMN build yields industry variants by
swapping data. The service loads these when present and falls back to baked-in defaults so the
template runs and tests offline with zero external files.

- `config/taxonomy.yaml` — spend categories (keywords), per-category amount bands, urgency keywords.
- `config/pathway_rules.yaml` — routing rules: (category x amount band x urgency x coverage) -> pathway + thresholds.
- `data/approved_suppliers.csv` — approved vendors (name, id, categories, status).
- `data/contract_coverage.csv` — active contracts (vendor, item scope, amount ceiling, expiry, ref).

Runtime config in `config/agent.yaml`: `max_retry`, `timeout_seconds`, `required_trust_level`.

## 5. Security Layers (S-1 .. S-5)

| Layer | Implementation in this template |
|-------|---------------------------------|
| **S-1 Trust Gate** | `CoverageCheckNode` declares `required_trust_level = VERIFIED_EXTERNAL` — supplier/contract data is sensitive, so an ANONYMOUS caller is denied before `execute()`. Other nodes inherit the permissive default. Enforced by `BaseNode.__call__()`; never overridden. |
| **S-2 Input Gate** | `PreProcessNode`, `ClassifyNode`, `CoverageCheckNode`, `RouteExplainNode` are `FunctionNode`s — the `@final` `_security_gate_input()` runs the default PII scan automatically. `NormalizeExtractNode` is a `BaseNode` with a deliberate **S-2 no-op passthrough**: input gating is handled at the upstream `GraphNode` boundary (per the framework's documented `BaseNode` S-2 exception), and the default `name` heuristic would corrupt multi-word vendor/item names in the structured JSON payload. It retains the input size-limit rejection. Output-side PII control is preserved by `RouteExplainNode`'s S-3 redaction. |
| **S-3 Output Gate** | `@final` `_security_gate_output()` credential scan runs on every node. `RouteExplainNode` adds `_extra_security_gate_output()` to redact vendor names and amounts from any externally-emitted rationale string (proposal S-4 log-redaction requirement). |
| **S-4 Audit** | Each domain node calls `emit_trace_event()` for its domain operation (`request_normalized`, `request_classified`, `coverage_checked`, `request_routed`). `node_start`/`node_complete`/`node_error` are framework-emitted — not duplicated. |
| **S-5 Credential Scan** | No secrets in `src/`; enforced by `gate-credential-scan`. No `os.environ` secret reads. `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT` are declared in `agent.yaml` `requires.secrets` and resolved per-invocation via `ctx.secrets.require()`; the deterministic fallback path needs no secrets at all. |
| **Import Isolation** | No `from agenticstar` / `import agenticstar`. Imports are `framework.*` and `shared.*` only. Enforced by `gate-import-isolation` (AST scan). |

## 6. LLM seam (implemented — graceful degrade to deterministic)

`ClassifyNode` attempts a live `AzureOpenAIClient` call for higher-recall reasoning over
ambiguous free text, built fresh per invocation from `ctx.secrets` (never cached on the node
instance — nodes are shared across invocations via the registry's LRU cache). It always
computes `Service.classify()` (deterministic, keyword + threshold matching against the
taxonomy) first as the baseline, then overrides it with the LLM result only when: a secret is
missing, the API call raises, the parsed response is malformed or has an invalid enum/type,
or the response validates cleanly. The node contract and state shape are unaffected either
way — `execute()` never raises and never returns `status=error` because of the LLM path.

**Response contract:** the LLM is prompted to return exactly `{spend_category, urgency_tier,
compliance_risk, confidence_overall}` as JSON; a response failing type/enum validation is
treated identically to an API error (silent fallback). See `docs/07_operation_guide.md` for
the three required secrets and the bare-endpoint gotcha.

**Testing:** all LLM-path tests use a test-double object exposing `complete(messages) ->
{"content": ...}` — no real Azure OpenAI call anywhere in the suite (`tests/unit/test_nodes.py`
`TestClassifyNode`).

## 7. Integrations

Standalone. Supplier and contract data are caller-supplied config (CSV/YAML). An ERP webhook
(SAP Ariba / Coupa / Kintone) is an optional post-release thin adapter at `src/api/server.py`,
not required for base deployment.

## Import Isolation Confirmation
- [x] Template does not import the agenticstar-platform SDK (Level 0).
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/`).
