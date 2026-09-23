"""AgentCore Platform v1.0 — NormalizeExtractNode (inner-graph entry node)."""

import json
from typing import Any

from framework.nodes.base_node import BaseNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.services.service import MAX_REQUEST_CHARS, Service


class NormalizeExtractNode(BaseNode):
    """Parse heterogeneous request input into a canonical record.

    This is the entry node of the domain subgraph (``ProcurementWorkflowGraph``).
    The parent ``GraphNode`` forwards the request as the inner-graph ``user_input``
    **string** (a JSON envelope ``{"request_text": ..., "fields": {...}}`` built by
    ``ProcurementRoutingGraphNode.extract_input`` — ``GraphNode`` does not forward
    ``input_context``).

    **Why ``BaseNode`` with an S-2 passthrough (not ``FunctionNode``):** the default
    S-2 PII scan masks ``user_input`` with a high-recall ``name`` heuristic that would
    corrupt multi-word vendor/item names in the structured payload (e.g. "Acme
    Corporation" -> "[MASKED]"), breaking classification and coverage matching.
    Per the framework's documented ``BaseNode`` S-2 exception, a ``BaseNode`` may use
    a no-op S-2 passthrough when input gating is handled at the upstream boundary —
    here the parent ``GraphNode``. PII
    in any externally-emitted string is still controlled downstream by
    ``RouteExplainNode``'s S-3 output redaction. The input size-limit rejection is
    retained.
    """

    def __init__(self, service: Service | None = None) -> None:
        self._service = service or Service()

    # ── S-2 input gate (passthrough + size limit) ──────────────────────────────
    def _security_gate_input(self, state: AgentState) -> AgentState:
        """S-2: no PII masking (gating handled at the GraphNode boundary upstream);
        retain the pathologically-large request-body rejection."""
        payload = self._parse_payload(state)
        if len(payload["request_text"]) > MAX_REQUEST_CHARS:
            state = dict(state)
            state["status"] = AgentStatus.ERROR.value
            state["error_log"] = [f"[NormalizeExtractNode] request body exceeds {MAX_REQUEST_CHARS} chars; rejected"]
        return state

    # ── S-3 output gate (passthrough) ──────────────────────────────────────────
    def _security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3: this node emits no externally-bound strings; the final envelope is
        redacted by RouteExplainNode. No-op passthrough."""
        return result

    def execute(self, state: AgentState) -> dict[str, Any]:
        payload = self._parse_payload(state)
        parsed = self._service.normalize_extract(payload["request_text"], payload["fields"])
        record = parsed["request_record"]

        emit_trace_event(
            "request_normalized",
            {
                "has_amount": record.get("amount") is not None,
                "has_vendor": bool(record.get("vendor")),
                "missing_count": len(parsed["missing_fields"]),
            },
            state,
        )

        return {
            "request_record": record,
            "missing_fields": parsed["missing_fields"],
            "status": AgentStatus.SUCCESS.value,
        }

    @staticmethod
    def _parse_payload(state: AgentState) -> dict[str, Any]:
        """Recover ``{request_text, fields}`` from the inner-graph ``user_input``.

        Accepts the JSON envelope built by the parent GraphNode; falls back to
        treating the raw string as ``request_text`` (e.g. a direct invoke).
        """
        raw = state.get("user_input", "") or ""
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and ("request_text" in data or "fields" in data):
                return {
                    "request_text": data.get("request_text") or "",
                    "fields": data.get("fields") or {},
                }
        except (ValueError, TypeError):
            pass
        return {"request_text": raw, "fields": {}}
