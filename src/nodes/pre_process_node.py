"""AgentCore Platform v1.0 — PreProcessNode (outer pre_process slot)."""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class PreProcessNode(FunctionNode):
    """Outer pre_process slot — lightweight input validation / context setup.

    The structured request payload (``request_text`` + ``fields``) travels in
    ``input_context`` and is forwarded into the domain subgraph by
    ``ProcurementRoutingGraphNode.extract_input``. This node confirms a request is
    present and records ``validated_input``; mandatory-field completeness is handled
    inside the subgraph (NormalizeExtractNode -> ``missing_fields``) and surfaced as
    routing review flags, matching the proposal's "flags, not branches" contract.

    S-1: reads only the inbound request text and performs no privileged access, so
    the permissive ``ANONYMOUS`` default applies. Declared explicitly per the
    framework's trust-level declaration rules.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        input_context = state.get("input_context", {}) or {}
        text = (input_context.get("request_text") or state.get("user_input", "") or "").strip()

        # S-4: unconditional domain audit event — one request entered the pipeline.
        emit_trace_event(
            "input_validated",
            {
                "has_request_text": bool(text),
                "request_chars": len(text),
            },
            state,
        )

        return {
            "validated_input": text,
            "status": AgentStatus.SUCCESS.value,
        }
