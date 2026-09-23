"""AgentCore Platform v1.0 — CMN-C2-213 inner domain workflow graph.

Cat 2 inner graph. Instantiated by ``ProcurementRoutingGraphNode.get_subgraph()``
(the ``main`` slot of the outer ``AgentBaseGraph``). Inherits ``BaseGraph`` directly
for a fully custom node topology — it is NOT the fixed initialize/pre/main/post/
finalize backbone (those are outer-graph concerns).

Happy path is the proposal's linear 4-step pipeline; low-confidence / off-contract
cases are flag values in ``routing_decision`` (handled by RouteExplainNode), NOT
graph branches. The conditional edges exist only to short-circuit to END on an
upstream error status (the framework does not auto-skip ``execute()`` on an inbound
error — see BaseNode.__call__), preserving the original error-routing behaviour.

    START -> normalize -> classify -> coverage -> route_explain -> END
                 |           |           |
                 +-----------+-----------+--(status == error)--> END
"""

from typing import Any

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus

from src.nodes.classify_node import ClassifyNode
from src.nodes.coverage_check_node import CoverageCheckNode
from src.nodes.normalize_extract_node import NormalizeExtractNode
from src.nodes.route_explain_node import RouteExplainNode
from src.schemas.state import State
from src.services.service import Service


class ProcurementWorkflowGraph(BaseGraph):
    """Inner multi-step domain workflow: normalize -> classify -> coverage -> route."""

    @property
    def name(self) -> str:
        return "procurement_routing_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        # No mandatory config; the service falls back to baked-in defaults so the
        # workflow runs offline.
        return None

    def register_nodes(self) -> None:
        # No super() call — BaseGraph.register_nodes() is abstract. initialize /
        # finalize are outer-backbone concerns and are NOT registered here.
        service = Service()  # one shared deterministic service for the pipeline
        self._nodes["normalize"] = NormalizeExtractNode(service)
        self._nodes["classify"] = ClassifyNode(service)
        self._nodes["coverage"] = CoverageCheckNode(service)
        self._nodes["route_explain"] = RouteExplainNode(service)

    def add_edges(self) -> None:
        self._sg.add_edge(START, "normalize")
        # Each fallible step routes on to the next, or short-circuits to END on error.
        self._sg.add_conditional_edges("normalize", self.route)
        self._sg.add_conditional_edges("classify", self._after_classify)
        self._sg.add_conditional_edges("coverage", self._after_coverage)
        self._sg.add_edge("route_explain", END)

    # ── Routing (error short-circuit only; happy path is linear) ───────────────
    def route(self, state: AgentState) -> str:
        return END if state.get("status") == AgentStatus.ERROR.value else "classify"

    def _after_classify(self, state: AgentState) -> str:
        return END if state.get("status") == AgentStatus.ERROR.value else "coverage"

    def _after_coverage(self, state: AgentState) -> str:
        return END if state.get("status") == AgentStatus.ERROR.value else "route_explain"

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the ``sub_result`` consumed by ProcurementRoutingGraphNode.merge_output()."""
        return {
            "output": state.get("formatted_output")
            if state.get("formatted_output") is not None
            else state.get("result"),
            "request_record": state.get("request_record"),
            "missing_fields": state.get("missing_fields", []),
            "classification": state.get("classification"),
            "coverage": state.get("coverage"),
            "routing_decision": state.get("routing_decision"),
            "formatted_output": state.get("formatted_output"),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
            "error_log": state.get("error_log", []),
        }
