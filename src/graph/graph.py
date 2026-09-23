"""AgentCore Platform v1.0 — CMN-C2-213 outer graph.

Cat 2 (multi-step domain workflow). L1 Base: AgentBaseGraph (L3->L1 direct).

Canonical Cat 2 shape: the fixed backbone
``initialize -> pre_process -> main -> {route} -> post_process -> finalize`` is left
untouched (no ``add_edges()`` / ``route()`` override). The four-step domain pipeline
(NormalizeExtract -> Classify -> CoverageCheck -> RouteExplain) is encapsulated in an
inner ``BaseGraph`` (``src/graph/domain_workflow_graph.py``) wrapped by a ``GraphNode``
(``ProcurementRoutingGraphNode``) in the ``main`` slot.

    outer:  initialize -> pre_process(PreProcess) -> main(ProcurementRoutingGraphNode)
                       -> {framework route} -> post_process(PostProcess) -> finalize
    inner:  normalize -> classify -> coverage -> route_explain   (see domain_workflow_graph.py)
"""

from typing import Any

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.main_node import ProcurementRoutingGraphNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class Graph(AgentBaseGraph):
    """Procurement Request Classifier & Sourcing Router (CMN-C2-213)."""

    @property
    def name(self) -> str:
        return "CMN-C2-213 Procurement Request Classifier & Sourcing Router Agent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        # super() injects the default initialize + finalize nodes and declares the
        # three required backbone slots (pre_process / main / post_process).
        super().register_nodes()
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = ProcurementRoutingGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() / route() are NOT overridden — the fixed backbone wiring (and its
    # retry/route semantics) belong to the framework. All domain topology lives in
    # the inner ProcurementWorkflowGraph.

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = super().get_output(state)
        out.update(
            {
                "output": state.get("formatted_output") or state.get("result"),
                "routing_decision": state.get("routing_decision"),
                "classification": state.get("classification"),
                "coverage": state.get("coverage"),
                "request_record": state.get("request_record"),
                "missing_fields": state.get("missing_fields", []),
                "status": state.get("status"),
                "error_log": state.get("error_log", []),
            }
        )
        return out
