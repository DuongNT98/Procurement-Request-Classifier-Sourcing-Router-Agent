# PB — Pipeline boundary: invoke execution order + msgpack-safe state + end-to-end
# routing across the four canonical use cases. Canonical Cat 2: the outer
# AgentBaseGraph backbone wraps the domain pipeline in a GraphNode (`main`); the
# four domain steps run inside ProcurementWorkflowGraph.

import json
from uuid import uuid4

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.domain_workflow_graph import ProcurementWorkflowGraph
from src.graph.graph import Graph

# Outer backbone: the domain pipeline appears as a single GraphNode in `main`.
EXPECTED_OUTER_ORDER = [
    "InitializeNode",
    "PreProcessNode",
    "ProcurementRoutingGraphNode",
    "PostProcessNode",
    "FinalizeNode",
]

# Inner domain workflow: the four canonical steps, in order.
EXPECTED_INNER_ORDER = [
    "NormalizeExtractNode",
    "ClassifyNode",
    "CoverageCheckNode",
    "RouteExplainNode",
]


def _invoke(text):
    g = Graph(config={"max_retry": 3})
    g.compile()
    ctx = InvocationContext(session_id=str(uuid4()), caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
    return g.invoke("procurement request", ctx=ctx, input_context={"request_text": text})


class TestExecutionOrder:
    """PB-6: nodes execute in the exact designed sequence (outer backbone + inner workflow)."""

    def test_outer_backbone_order(self):
        out = _invoke("Order 10 reams of A4 paper, 800 USD, vendor Acme Office Supplies")
        assert out["node_history"] == EXPECTED_OUTER_ORDER

    def test_inner_workflow_order(self):
        sub = ProcurementWorkflowGraph(config={})
        sub.compile()
        ctx = InvocationContext(session_id=str(uuid4()), caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        payload = json.dumps({
            "request_text": "Order 10 reams of A4 paper, 800 USD, vendor Acme Office Supplies",
            "fields": {},
        })
        out = sub.invoke(payload, ctx=ctx)
        assert out["node_history"] == EXPECTED_INNER_ORDER


class TestStateSerializable:
    """PB-2: post-invoke output carries only msgpack-safe primitives/containers."""

    def test_output_is_plain_types(self):
        out = _invoke("Order 10 reams of A4 paper, 800 USD, vendor Acme Office Supplies")
        rd = out["routing_decision"]
        assert isinstance(rd, dict)
        assert isinstance(rd["pathway"], str)
        assert isinstance(rd["review_flags"], list)
        assert isinstance(out["classification"], dict)
        assert isinstance(out["coverage"], dict)
        # confidence is a float, not a numpy/Decimal/object
        assert isinstance(rd["confidence"], float)


class TestEndToEndRouting:
    """PB: each canonical use case resolves to its expected sourcing pathway."""

    def test_uc01_catalog_order(self):
        out = _invoke("Order 10 reams of A4 paper, approx 800 USD, vendor Acme Office Supplies")
        assert out["routing_decision"]["pathway"] == "catalog_order"
        assert out["coverage"]["status"] == "approved"

    def test_uc02_contract_amendment(self):
        out = _invoke("Renew enterprise software license, 8M JPY, vendor Globex Software")
        assert out["routing_decision"]["pathway"] == "contract_amendment"
        assert out["coverage"]["matched_contract"] == "C-2024-IT-001"

    def test_uc03_spot_buy_off_contract(self):
        out = _invoke("URGENT order 5 office chairs, 4500 USD, vendor QuickFurnish Co")
        assert out["routing_decision"]["pathway"] == "spot_buy"
        assert "off_contract" in out["routing_decision"]["review_flags"]

    def test_uc04_manual_review_incomplete(self):
        out = _invoke("can someone order the thing asap")
        assert out["routing_decision"]["pathway"] == "manual_review"
        assert "missing_fields" in out["routing_decision"]["review_flags"]
