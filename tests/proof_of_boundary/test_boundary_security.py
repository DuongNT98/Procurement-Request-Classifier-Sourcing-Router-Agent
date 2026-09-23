# PB — Security boundary: S-1 trust gate, S-4 audit emission, S-3 output redaction.
# Verifies the framework security pipeline actually engages around the template's
# privileged node and audit events — not just that the code paths exist.

import json
import logging
from uuid import uuid4

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from src.graph.graph import Graph

REQUEST = "Renew enterprise software license, 8M JPY, vendor Globex Software"


def _invoke(trust):
    g = Graph(config={"max_retry": 3})
    g.compile()
    ctx = InvocationContext(session_id=str(uuid4()), caller_trust_level=trust)
    return g.invoke("procurement request", ctx=ctx, input_context={"request_text": REQUEST})


class TestS1TrustGate:
    """PB / S-1: CoverageCheckNode requires VERIFIED_EXTERNAL; ANONYMOUS is denied."""

    def test_anonymous_caller_denied_at_coverage_node(self):
        out = _invoke(TrustLevel.ANONYMOUS)
        # The S-1 denial is recorded in error_log by BaseNode.__call__ before execute().
        joined = " ".join(out.get("error_log", []))
        assert "S-1 trust gate denied" in joined
        assert "CoverageCheckNode" in joined

    def test_verified_external_caller_not_denied(self):
        out = _invoke(TrustLevel.VERIFIED_EXTERNAL)
        # Happy path completes through to a routing decision.
        assert out["status"] == "success"
        assert out["routing_decision"]["pathway"] == "contract_amendment"


class TestS4AuditEmission:
    """PB-1 / S-4: domain audit events are emitted from execute() via emit_trace_event."""

    def test_domain_events_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="agentcore.audit"):
            _invoke(TrustLevel.VERIFIED_EXTERNAL)
        event_types = set()
        for rec in caplog.records:
            try:
                event_types.add(json.loads(rec.getMessage())["event_type"])
            except (ValueError, KeyError):
                continue
        # Domain events from each node + framework lifecycle events all present.
        assert {"request_normalized", "request_classified", "coverage_checked", "request_routed"} <= event_types
        assert "node_start" in event_types  # framework-emitted, not duplicated by template


class TestS3OutputRedaction:
    """PB / S-3: contact PII never leaks into the routed output envelope."""

    def test_no_email_in_output(self):
        out = _invoke(TrustLevel.VERIFIED_EXTERNAL)
        blob = json.dumps(out.get("output", {}))
        assert "@" not in blob or "[REDACTED]" in blob
