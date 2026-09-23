# Test Specification — CMN-C2-213

Procurement Request Classifier & Sourcing Router Agent.

## Test Strategy

Three layers, all deterministic and offline (no live LLM, no network):

| Layer | Location | What it proves |
|-------|----------|----------------|
| Unit — service | `tests/unit/test_service.py` | Domain rules: amount/vendor parsing, classification, coverage lookup, pathway selection |
| Unit — nodes | `tests/unit/test_nodes.py` | Each node's `execute()` contract, S-2 size gate, S-3 PII redaction, S-1 trust declaration |
| Proof-of-Boundary | `tests/proof_of_boundary/` | Framework boundaries: security pipeline, audit emission, execution order, state safety, import isolation |

Determinism: `Service` runs on baked-in defaults (the `config/*.yaml` + `data/*.csv`
mirror them), so assertions are exact (pathway strings, confidence values, contract refs),
never `is not None`.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass/`InvocationContext` (`test_state_safety.py`) | PASS |
| TC-02 | Invalid/incomplete input handled safely | Missing fields → `manual_review`, never an unhandled raise (`test_boundary_pipeline.py::UC-04`) | PASS |
| TC-03 | No JWT/Credential in `src/` | CI `gate-credential-scan` + S-5 import-time scan: 0 violations | PASS |
| TC-04 | InvocationContext via `config["configurable"]` only | No `InvocationContext` field in State (`test_state_safety.py`) | PASS |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | Templates emit only domain events; `node_start`/`complete`/`error` framework-only (`TestS4AuditEmission`) | PASS |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode`) | `@final` enforced at class definition; extension via `_extra_security_gate_input` (`test_nodes.py`) | PASS |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode`) | `@final` enforced; extension via `_extra_security_gate_output` (`test_nodes.py`) | PASS |
| TC-08 | `required_trust_level` enforced | ANONYMOUS denied at CoverageCheckNode; VERIFIED_EXTERNAL passes (`TestS1TrustGate`) | PASS |
| TC-09 | S-2 `_extra_security_gate_input()` non-trivial | NormalizeExtractNode rejects oversized request body (`test_s2_gate_rejects_oversized_body`) | PASS |
| TC-10 | S-3 `_extra_security_gate_output()` non-trivial | RouteExplainNode redacts contact PII from output (`test_s3_gate_redacts_contact_pii`) | PASS |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` per node | `request_normalized`/`request_classified`/`coverage_checked`/`request_routed` (`TestS4AuditEmission`) | PASS |
| TC-13 | `required_trust_level` is a valid enum value | `TrustLevel.VERIFIED_EXTERNAL` — no AttributeError at import (`test_trust_level_is_verified_external`) | PASS |
| TC-14 | LLM override on well-formed response | Well-formed JSON overrides the deterministic classification (`test_llm_override_on_wellformed_json`) | PASS |
| TC-15 | LLM response parses through markdown fences | A fenced ` ```json ` reply still parses via `extract_json_object` (`test_llm_override_parses_markdown_fenced_response`) | PASS |
| TC-16 | Malformed/wrong-shape LLM response degrades safely | Non-JSON and invalid-enum responses fall back to the deterministic result, never `status=error` (`test_malformed_llm_response_falls_back_to_deterministic`, `test_wrong_shape_llm_response_falls_back_to_deterministic`) | PASS |
| TC-17 | LLM call raising degrades safely | Simulated API error falls back to the deterministic result (`test_llm_raising_falls_back_to_deterministic`) | PASS |
| TC-18 | No LLM injected + no secret bound (production shape) | `MissingSecret` from `NullProvider` degrades to the deterministic result (`test_no_llm_injected_and_no_secret_bound_falls_back_to_deterministic`) | PASS |
| TC-19 | Empty input never calls the LLM | No `item`/`justification` → LLM `complete()` not invoked (`test_empty_input_never_calls_llm`) | PASS |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → AuditLogger | `emit_trace_event()` fires for each domain op | 4 domain events logged (`TestS4AuditEmission`) | PASS |
| PB-2 | State serialization | Post-invoke output is primitives only | dict/list/str/float (`TestStateSerializable`) | PASS |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations (`test_import_isolation.py`) | PASS |
| PB-6 | Invoke execution order | S-1 → S-4 node_start → S-2 → execute → S-3 → S-4 node_complete, across the 6-node graph | `node_history` exact order (`TestExecutionOrder`) | PASS |
| PB (S-1) | Trust gate denial | ANONYMOUS caller blocked before `execute()` | `error_log` contains "S-1 trust gate denied" + "CoverageCheckNode" | PASS |
| PB (S-3) | Output redaction | contact PII never leaves the agent | email → `[REDACTED]` (`TestS3OutputRedaction`) | PASS |

> PB-3 (external service) and PB-5 (checkpoint inspection) are N/A for the default build:
> this template has no external service dependency (supplier/contract data is local config)
> and runs without a checkpointer unless `memory_enabled`/`hitl` is set. State safety (PB-2)
> already proves checkpoint payloads are msgpack-safe.

## Business Logic / Use-Case Acceptance

`tests/proof_of_boundary/test_boundary_pipeline.py::TestEndToEndRouting`

| BL-ID | Input (request_text) | Expected pathway | Flags / coverage |
|-------|----------------------|------------------|------------------|
| BL-01 | "Order 10 reams of A4 paper, approx 800 USD, vendor Acme Office Supplies" | `catalog_order` | approved, no flags |
| BL-02 | "Renew enterprise software license, 8M JPY, vendor Globex Software" | `contract_amendment` | matched_contract `C-2024-IT-001` |
| BL-03 | "URGENT order 5 office chairs, 4500 USD, vendor QuickFurnish Co" | `spot_buy` | flag `off_contract` |
| BL-04 | "can someone order the thing asap" | `manual_review` | flag `missing_fields` |

## Test Execution Summary

- Execution date: 2026-09-07
- Total tests: 54 (38 unit + 16 proof-of-boundary)
- Pass: 52 / Fail: 0 / Skip: 2 (PB-7 HITL — auto-skipped, this template has no `hitl.enabled`)
- Command: `python -m pytest tests/ -v` (prefix `PYTHONUTF8=1` on Windows to match the Linux CI runner)
