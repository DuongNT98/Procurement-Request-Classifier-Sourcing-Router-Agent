"""AgentCore Platform v1.0 — CMN-C2-213 state schema."""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption. Extend AgentState with agent-specific fields
# only. Do NOT add credentials, secrets, or Pydantic models.
#
# All shared fields (user_input, validated_input, status, session_id,
# node_history, error_log, hitl_*, etc.) are inherited from AgentState.

from typing import NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Procurement Request Classifier & Sourcing Router state.

    Pipeline contract (one writer per field):
      NormalizeExtractNode -> request_record, missing_fields
      ClassifyNode         -> classification
      CoverageCheckNode    -> coverage
      RouteExplainNode     -> routing_decision (+ formatted_output)

    Every field is a primitive / JSON-serializable container so the state
    round-trips cleanly through msgpack checkpoints.
    """

    # mypy can't see AgentState is a TypedDict without SDK py.typed stubs (framework.*
    # is ignore_missing_imports), so it rejects every NotRequired[] field below as used
    # outside a TypedDict definition. This is a stub-visibility limitation, not a real
    # code error — each field is genuinely optional/JSON-safe at runtime.

    # NormalizeExtractNode — canonical request parsed from free text + form fields.
    # Keys: item, amount, currency, vendor, requester, need_by, justification.
    request_record: NotRequired[dict]  # type: ignore[valid-type]
    # Mandatory request fields that were absent from the input (item / amount / vendor).
    missing_fields: NotRequired[list[str]]  # type: ignore[valid-type]

    # ClassifyNode — three-axis labels + per-axis confidence in [0.0, 1.0].
    # Keys: spend_category, urgency_tier, compliance_risk, confidence (dict per axis), confidence_overall.
    classification: NotRequired[dict]  # type: ignore[valid-type]

    # CoverageCheckNode — supplier/contract coverage lookup result.
    # Keys: status (approved|off_contract|unknown), matched_supplier, matched_contract.
    coverage: NotRequired[dict]  # type: ignore[valid-type]

    # RouteExplainNode — final routing decision the downstream queue consumes.
    # Keys: pathway, rationale, confidence, review_flags (list[str]).
    routing_decision: NotRequired[dict]  # type: ignore[valid-type]
