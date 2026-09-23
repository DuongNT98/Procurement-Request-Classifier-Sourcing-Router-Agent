"""AgentCore Platform v1.0 — ClassifyNode (main slot)."""

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from shared.services.llm.azure_openai_client import AzureOpenAIClient
from shared.utils.audit_logger import emit_trace_event
from shared.utils.llm_json import extract_json_object

from src.services.service import Service

_VALID_URGENCY = frozenset({"low", "normal", "high"})
_VALID_COMPLIANCE = frozenset({"low", "medium", "high"})

_SYSTEM_PROMPT = (
    "You are a procurement request classifier. Given a request record, respond "
    "with a JSON object containing exactly these keys: spend_category (short "
    "lowercase_with_underscores string), urgency_tier (one of: low, normal, "
    "high), compliance_risk (one of: low, medium, high), confidence_overall "
    "(number between 0 and 1). Return JSON only, no prose, no markdown fence."
)


class ClassifyNode(FunctionNode):
    """Assign spend category, urgency tier, and compliance risk + confidence.

    Deterministic over the configured taxonomy by default (``Service.classify``).
    When ``AZURE_OPENAI_API_KEY`` / ``AZURE_OPENAI_ENDPOINT`` /
    ``AZURE_OPENAI_DEPLOYMENT`` are configured, a live LLM call attempts a
    higher-recall override for ambiguous free text (design §6 LLM seam). Any
    LLM failure — missing secret, API error, malformed/wrong-shape response, or
    no input to reason over — silently keeps the deterministic result. This
    node never raises and never returns ``status=error`` because of the LLM
    path; the deterministic classification is always a valid result on its own.

    S-1: operates only on the already-normalised in-state ``request_record``
    (no external resource access beyond the optional LLM call, which itself
    degrades to no-op on failure), so the permissive ``ANONYMOUS`` default is
    appropriate. Declared explicitly per the framework's trust-level declaration rules.
    """

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, service: Service | None = None, llm: Any | None = None) -> None:
        self._service = service or Service()
        # Test-double seam only. Production wiring (register_nodes()) never
        # passes this — a real deployment always resolves a fresh client
        # inside execute() from that invocation's own secrets (§2-2a: never
        # cache an LLM client built from caller secrets on a shared instance).
        self._injected_llm = llm

    def execute(self, state: AgentState) -> dict[str, Any]:
        record = state.get("request_record", {}) or {}
        classification = self._service.classify(record)

        llm_classification = self._try_llm_classify(record, state)
        if llm_classification is not None:
            classification = llm_classification

        emit_trace_event(
            "request_classified",
            {
                "spend_category": classification["spend_category"],
                "urgency_tier": classification["urgency_tier"],
                "compliance_risk": classification["compliance_risk"],
                "confidence_overall": classification["confidence_overall"],
                "source": "llm" if llm_classification is not None else "deterministic",
            },
            state,
        )

        return {
            "classification": classification,
            "status": AgentStatus.SUCCESS.value,
        }

    def _try_llm_classify(self, record: dict[str, Any], state: AgentState) -> dict[str, Any] | None:
        """Best-effort LLM override. Returns ``None`` on ANY failure so the
        caller keeps the deterministic baseline. Never raises."""
        if not record.get("item") and not record.get("justification"):
            return None  # nothing to reason over
        try:
            llm = self._injected_llm
            if llm is None:
                ctx = InvocationContext.from_state(state)
                llm = AzureOpenAIClient(
                    {
                        "api_key": ctx.secrets.require("AZURE_OPENAI_API_KEY"),
                        "azure_endpoint": ctx.secrets.require("AZURE_OPENAI_ENDPOINT"),
                        "azure_deployment": ctx.secrets.require("AZURE_OPENAI_DEPLOYMENT"),
                    }
                )
            user_prompt = (
                f"item: {record.get('item', '')}\n"
                f"justification: {record.get('justification', '')}\n"
                f"amount: {record.get('amount')}\n"
            )
            response = llm.complete(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
            parsed = extract_json_object(response.get("content", ""))
            return self._validate_llm_result(parsed)
        except Exception:
            return None

    @staticmethod
    def _validate_llm_result(parsed: Any) -> dict[str, Any] | None:
        """Reject anything not matching the exact documented response shape —
        an unvalidated LLM response must never reach state/output."""
        if not isinstance(parsed, dict):
            return None
        category = parsed.get("spend_category")
        urgency = parsed.get("urgency_tier")
        compliance = parsed.get("compliance_risk")
        confidence = parsed.get("confidence_overall")
        if not isinstance(category, str) or not category.strip():
            return None
        if urgency not in _VALID_URGENCY:
            return None
        if compliance not in _VALID_COMPLIANCE:
            return None
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return None
        if not (0.0 <= confidence <= 1.0):
            return None
        overall = round(float(confidence), 3)
        return {
            "spend_category": category.strip(),
            "urgency_tier": urgency,
            "compliance_risk": compliance,
            "confidence": {
                "spend_category": overall,
                "urgency_tier": overall,
                "compliance_risk": overall,
            },
            "confidence_overall": overall,
        }
