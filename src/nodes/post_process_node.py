"""AgentCore Platform v1.0 — PostProcessNode (outer post_process slot)."""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class PostProcessNode(FunctionNode):
    """Outer post_process slot — finalise the routing envelope for the caller.

    The domain subgraph (``main``) already produced ``routing_decision`` and the
    ``formatted_output`` envelope via RouteExplainNode (including S-3 PII redaction).
    This slot surfaces that envelope as ``result``; it is only reached on the success
    path (the backbone routes terminal/error status straight to ``finalize``).

    S-1: only re-surfaces the already-gated in-state envelope and performs no
    privileged access, so the permissive ``ANONYMOUS`` default applies. Declared
    explicitly per the framework's trust-level declaration rules.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: AgentState) -> dict[str, Any]:
        envelope = state.get("formatted_output")
        result = envelope if envelope is not None else state.get("result")

        # S-4: unconditional domain audit event — a request left the pipeline.
        decision = state.get("routing_decision") or {}
        emit_trace_event(
            "request_finalised",
            {
                "pathway": decision.get("pathway"),
                "has_result": result is not None,
            },
            state,
        )

        return {
            "result": result,
            "status": AgentStatus.SUCCESS.value,
        }
