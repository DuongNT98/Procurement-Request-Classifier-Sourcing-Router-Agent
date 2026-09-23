# CMN-C2-213 — Unit tests: domain node execute() contracts

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from src.nodes.normalize_extract_node import NormalizeExtractNode
from src.nodes.classify_node import ClassifyNode
from src.nodes.coverage_check_node import CoverageCheckNode
from src.nodes.route_explain_node import RouteExplainNode
from src.services.service import MAX_REQUEST_CHARS


def _envelope(request_text, fields=None):
    # The parent GraphNode serialises the payload into the inner-graph user_input string.
    return {"user_input": json.dumps({"request_text": request_text, "fields": fields or {}})}


class TestNormalizeExtractNode:
    def setup_method(self):
        self.node = NormalizeExtractNode()

    def test_extracts_record_from_json_envelope(self):
        out = self.node.execute(_envelope("Order paper, 800 USD, vendor Acme Office Supplies"))
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["request_record"]["amount"] == 800.0
        assert out["missing_fields"] == []

    def test_s2_passthrough_preserves_multiword_vendor(self):
        # The BaseNode S-2 passthrough must NOT mask multi-word vendor/item names
        # (the default `name` PII heuristic would corrupt "Acme Office Supplies").
        state = _envelope("Buy from Acme Office Supplies")
        gated = self.node._security_gate_input(state)
        assert "Acme Office Supplies" in gated["user_input"]
        assert gated.get("status") != AgentStatus.ERROR.value

    def test_s2_gate_rejects_oversized_body(self):
        state = _envelope("x" * (MAX_REQUEST_CHARS + 1))
        state["error_log"] = []
        gated = self.node._security_gate_input(state)
        assert gated["status"] == AgentStatus.ERROR.value
        assert any("exceeds" in m for m in gated["error_log"])


class _FakeLLM:
    """Test double for shared.services.llm.BaseLLM — same complete() shape,
    no real network call. Raises when constructed with raise_error=True."""

    def __init__(self, content=None, raise_error=False):
        self._content = content
        self._raise_error = raise_error

    def complete(self, messages):
        if self._raise_error:
            raise RuntimeError("simulated LLM API error")
        return {"content": self._content}


class TestClassifyNode:
    def test_returns_classification_and_success(self):
        out = ClassifyNode().execute({"request_record": {"item": "software", "justification": "renew license", "amount": 1200}})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["classification"]["spend_category"] == "it_software"

    def test_llm_override_on_wellformed_json(self):
        fake = _FakeLLM(content=json.dumps({
            "spend_category": "office_supplies", "urgency_tier": "high",
            "compliance_risk": "low", "confidence_overall": 0.92,
        }))
        out = ClassifyNode(llm=fake).execute(
            {"request_record": {"item": "chairs", "justification": "need chairs", "amount": 500}}
        )
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["classification"]["spend_category"] == "office_supplies"
        assert out["classification"]["urgency_tier"] == "high"
        assert out["classification"]["confidence_overall"] == 0.92

    def test_llm_override_parses_markdown_fenced_response(self):
        fake = _FakeLLM(content='```json\n{"spend_category": "it_software", '
                                 '"urgency_tier": "normal", "compliance_risk": "medium", '
                                 '"confidence_overall": 0.8}\n```')
        out = ClassifyNode(llm=fake).execute(
            {"request_record": {"item": "laptop", "justification": "new laptop", "amount": 2000}}
        )
        assert out["classification"]["spend_category"] == "it_software"
        assert out["classification"]["confidence_overall"] == 0.8

    def test_malformed_llm_response_falls_back_to_deterministic(self):
        fake = _FakeLLM(content="not valid json at all")
        record = {"item": "software", "justification": "renew license", "amount": 1200}
        deterministic = ClassifyNode().execute({"request_record": record})
        out = ClassifyNode(llm=fake).execute({"request_record": record})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["classification"] == deterministic["classification"]

    def test_wrong_shape_llm_response_falls_back_to_deterministic(self):
        # Valid JSON, invalid enum value for urgency_tier.
        fake = _FakeLLM(content=json.dumps({
            "spend_category": "office_supplies", "urgency_tier": "asap",
            "compliance_risk": "low", "confidence_overall": 0.9,
        }))
        record = {"item": "chairs", "justification": "need chairs", "amount": 500}
        deterministic = ClassifyNode().execute({"request_record": record})
        out = ClassifyNode(llm=fake).execute({"request_record": record})
        assert out["classification"] == deterministic["classification"]

    def test_llm_raising_falls_back_to_deterministic(self):
        fake = _FakeLLM(raise_error=True)
        record = {"item": "software", "justification": "renew license", "amount": 1200}
        deterministic = ClassifyNode().execute({"request_record": record})
        out = ClassifyNode(llm=fake).execute({"request_record": record})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert out["classification"] == deterministic["classification"]

    def test_no_llm_injected_and_no_secret_bound_falls_back_to_deterministic(self):
        # Production shape: no llm= injected, no SecretProvider bound in state —
        # InvocationContext.from_state() defaults to NullProvider, so
        # ctx.secrets.require() raises MissingSecret and the node degrades.
        record = {"item": "software", "justification": "renew license", "amount": 1200}
        deterministic = ClassifyNode().execute({"request_record": record})
        out = ClassifyNode().execute({"request_record": record})
        assert out["classification"] == deterministic["classification"]

    def test_empty_input_never_calls_llm(self):
        calls = []

        class _CountingLLM(_FakeLLM):
            def complete(self, messages):
                calls.append(messages)
                return super().complete(messages)

        out = ClassifyNode(llm=_CountingLLM(content="{}")).execute({"request_record": {}})
        assert out["status"] == AgentStatus.SUCCESS.value
        assert calls == []


class TestCoverageCheckNode:
    def test_trust_level_is_verified_external(self):
        # S-1: this node is privileged — declared trust level must be the exact enum.
        assert CoverageCheckNode.required_trust_level == TrustLevel.VERIFIED_EXTERNAL

    def test_returns_coverage(self):
        out = CoverageCheckNode().execute({
            "request_record": {"vendor": "Globex Software", "amount": 8_000_000},
            "classification": {"spend_category": "it_software"},
        })
        assert out["coverage"]["status"] == "approved"
        assert out["coverage"]["matched_contract"] == "C-2024-IT-001"


class TestRouteExplainNode:
    def setup_method(self):
        self.node = RouteExplainNode()

    def test_produces_routing_decision_and_formatted_output(self):
        state = {
            "request_record": {"amount": 800},
            "classification": {"spend_category": "office_supplies", "compliance_risk": "low", "confidence_overall": 0.8},
            "coverage": {"status": "approved", "matched_contract": None},
            "missing_fields": [],
        }
        out = self.node.execute(state)
        assert out["routing_decision"]["pathway"] == "catalog_order"
        assert out["formatted_output"]["coverage_status"] == "approved"
        assert out["status"] == AgentStatus.SUCCESS.value

    def test_s3_gate_redacts_contact_pii(self):
        # S-3 extension: an email leaking into an output string is redacted.
        result = {"routing_decision": {"rationale": "contact buyer at agent@example.com for review"}}
        gated = self.node._extra_security_gate_output(result)
        assert "agent@example.com" not in gated["routing_decision"]["rationale"]
        assert "[REDACTED]" in gated["routing_decision"]["rationale"]
