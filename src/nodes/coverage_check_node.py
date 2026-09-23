"""AgentCore Platform v1.0 — CoverageCheckNode (coverage slot)."""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services.service import Service


class CoverageCheckNode(FunctionNode):
    """Match vendor against the approved-supplier list and contract coverage.

    S-1: supplier and contract data are sensitive, so this node requires a
    VERIFIED_EXTERNAL (or higher) caller. An ANONYMOUS caller is denied by the
    framework trust gate before ``execute()`` runs.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, service: Service | None = None) -> None:
        self._service = service or Service()

    def execute(self, state: AgentState) -> dict[str, Any]:
        record = state.get("request_record", {}) or {}
        classification = state.get("classification", {}) or {}
        coverage = self._service.check_coverage(record, classification)

        emit_trace_event(
            "coverage_checked",
            {
                "status": coverage["status"],
                "has_matched_contract": coverage["matched_contract"] is not None,
            },
            state,
        )

        return {
            "coverage": coverage,
            "status": AgentStatus.SUCCESS.value,
        }
