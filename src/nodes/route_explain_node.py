"""AgentCore Platform v1.0 — RouteExplainNode (post_process slot)."""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.security.pii_detector import detect_pii
from shared.utils.audit_logger import emit_trace_event

from src.services.service import Service


class RouteExplainNode(FunctionNode):
    """Apply pathway rules to produce the final routing decision + rationale.

    Sets ``formatted_output`` (the envelope the downstream sourcing queue
    consumes) which ``Graph.get_output`` surfaces as ``output``.

    S-1: derives the decision from in-state classification/coverage results and
    performs no privileged resource access, so the permissive ``ANONYMOUS``
    default applies. Declared explicitly per the framework's trust-level declaration
    rules. Sensitive contract/
    supplier lookups happen upstream in ``CoverageCheckNode`` (VERIFIED_EXTERNAL).
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, service: Service | None = None) -> None:
        self._service = service or Service()

    def execute(self, state: AgentState) -> dict[str, Any]:
        record = state.get("request_record", {}) or {}
        classification = state.get("classification", {}) or {}
        coverage = state.get("coverage", {}) or {}
        missing_fields = state.get("missing_fields", []) or []

        routed = self._service.route_explain(record, classification, coverage, missing_fields)
        decision = routed["routing_decision"]

        emit_trace_event(
            "request_routed",
            {
                "pathway": decision["pathway"],
                "review_flags": decision["review_flags"],
                "confidence": decision["confidence"],
            },
            state,
        )

        formatted_output = {
            "pathway": decision["pathway"],
            "spend_category": classification.get("spend_category"),
            "urgency_tier": classification.get("urgency_tier"),
            "compliance_risk": classification.get("compliance_risk"),
            "coverage_status": coverage.get("status"),
            "confidence": decision["confidence"],
            "review_flags": decision["review_flags"],
            "rationale": decision["rationale"],
        }
        return {
            "routing_decision": decision,
            "formatted_output": formatted_output,
            "status": AgentStatus.SUCCESS.value,
        }

    def _extra_security_gate_output(self, result: dict[str, Any]) -> dict[str, Any]:
        """S-3 domain check: redact contact PII (email/phone/SSN/card) from any
        string emitted downstream. Business prose and contract refs are preserved
        (the high-recall ``name`` heuristic is intentionally skipped)."""
        self._redact_pii_in_place(result)
        return result

    def _redact_pii_in_place(self, obj: object) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str):
                    obj[key] = self._mask(value)
                else:
                    self._redact_pii_in_place(value)
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                if isinstance(value, str):
                    obj[i] = self._mask(value)
                else:
                    self._redact_pii_in_place(value)

    @staticmethod
    def _mask(text: str) -> str:
        findings = [f for f in detect_pii(text) if f["type"] != "name"]
        for f in reversed(findings):
            text = text[: f["start"]] + "[REDACTED]" + text[f["end"] :]
        return text
