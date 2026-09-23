"""AgentCore Platform v1.0 — ProcurementRoutingGraphNode (outer `main` slot)."""

import json
from typing import Any, ClassVar

from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.graph.domain_workflow_graph import ProcurementWorkflowGraph


class ProcurementRoutingGraphNode(GraphNode):
    """Cat 2 `main` slot: wraps the domain workflow subgraph.

    Encapsulates the multi-step procurement pipeline (normalize -> classify ->
    coverage -> route_explain) inside an inner ``BaseGraph`` so the outer
    ``AgentBaseGraph`` keeps its fixed backbone (no outer ``add_edges()`` override).
    """

    # Composition consistency (CoE criterion #9): inner errors degrade gracefully
    # — a domain node returning status=error is surfaced as an error result via
    # on_subgraph_error(), NOT raised as an exception to the caller.
    error_strategy: ClassVar[str] = "handle"
    # No HITL in this template — the inner workflow never calls interrupt().
    propagate_hitl: ClassVar[bool] = False

    def __init__(self) -> None:
        super().__init__()
        # Build the subgraph once (stable instance; compiled lazily on first invoke).
        self._sub = ProcurementWorkflowGraph(config={})

    def get_subgraph(self) -> ProcurementWorkflowGraph:
        return self._sub

    def extract_input(self, state: AgentState) -> str:
        """Serialise the request payload for the inner graph's single string channel.

        ``GraphNode`` does not forward ``input_context``; the structured payload
        (``request_text`` + pre-structured ``fields``) is JSON-encoded here and
        recovered by ``NormalizeExtractNode``. Prefers ``input_context`` so vendor/
        item names are not run through the outer S-2 ``user_input`` mask.
        """
        input_context = state.get("input_context", {}) or {}
        request_text = (
            input_context.get("request_text") or state.get("validated_input") or state.get("user_input", "") or ""
        )
        fields = input_context.get("fields") or {}
        return json.dumps({"request_text": request_text, "fields": fields})

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        """Map the inner ``sub_result`` (ProcurementWorkflowGraph.get_output) back
        into the outer state. Returns only the keys this node changes."""
        return {
            "request_record": sub_result.get("request_record"),
            "missing_fields": sub_result.get("missing_fields", []),
            "classification": sub_result.get("classification"),
            "coverage": sub_result.get("coverage"),
            "routing_decision": sub_result.get("routing_decision"),
            "formatted_output": sub_result.get("formatted_output"),
            "result": sub_result.get("output"),
            "status": sub_result.get("status", AgentStatus.SUCCESS.value),
        }
