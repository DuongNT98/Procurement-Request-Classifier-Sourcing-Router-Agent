# PB-6: Invoke Execution Order Verification
# Verifies BaseNode.__call__() enforces the fixed security envelope for every
# concrete leaf node under src/nodes/:
#   S-1 trust gate -> S-4 node_start -> S-2 _security_gate_input()
#   -> execute() -> S-3 _security_gate_output() -> S-4 node_complete
#
# GraphNode subclasses (here: ProcurementRoutingGraphNode) are excluded: invoking
# one standalone runs its inner ProcurementWorkflowGraph, whose inner nodes emit
# their own framework node_start/node_complete via the same (patched) base_node
# binding and would pollute the outer order capture. The GraphNode's own envelope
# order is exercised end-to-end by tests/proof_of_boundary/test_boundary_pipeline.py
# (outer backbone + inner workflow node_history), so PB-6 coverage is not lost.

import importlib
import inspect
import pkgutil

import pytest


def _discover_leaf_node_classes() -> list[type]:
    """Import every module under src/nodes/ and collect concrete BaseNode
    subclasses, excluding GraphNode subclasses (subgraph-wrapping nodes)."""
    from framework.nodes.base_node import BaseNode
    from framework.nodes.graph_node import GraphNode

    try:
        pkg = importlib.import_module("src.nodes")
    except ImportError:
        return []

    discovered = []
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, prefix="src.nodes."):
        module = importlib.import_module(modname)
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseNode)
                and attr is not BaseNode
                and not issubclass(attr, GraphNode)
                and attr.__module__ == modname
                and not inspect.isabstract(attr)
            ):
                discovered.append(attr)
    return discovered


class TestInvokeOrder:
    """PB-6: __call__ must run S-1 -> node_start -> S-2 -> execute() -> S-3 -> node_complete."""

    def test_call_order_for_every_leaf_node(self, monkeypatch):
        node_classes = _discover_leaf_node_classes()
        # This template has five leaf nodes under src/nodes/ (the sixth, the
        # ProcurementRoutingGraphNode, is a GraphNode and intentionally excluded).
        assert node_classes, "no concrete leaf BaseNode subclasses found under src/nodes/"

        import framework.nodes.base_node as base_node_module

        failures: list[str] = []
        for node_cls in node_classes:
            order: list[str] = []
            # Patch only the base_node binding: this captures the framework-emitted
            # node_start/node_complete events without capturing the domain events a
            # node emits via its own `from shared.utils.audit_logger import ...` binding.
            monkeypatch.setattr(
                base_node_module,
                "emit_trace_event",
                lambda event_type, _payload, _state, _o=order: _o.append(f"event:{event_type}"),
            )

            for method_name, label in (
                ("_security_gate_input", "security_gate_input"),
                ("execute", "execute"),
                ("_security_gate_output", "security_gate_output"),
            ):
                original = getattr(node_cls, method_name)

                def spy(self, arg, _o=order, _label=label, _orig=original):
                    _o.append(_label)
                    return _orig(self, arg)

                monkeypatch.setattr(node_cls, method_name, spy)

            instance = node_cls()
            state = {
                "caller_trust_level": node_cls.required_trust_level.value,
                "correlation_id": "pb6-invoke-order-test",
            }
            instance(state)

            expected = [
                "event:node_start",
                "security_gate_input",
                "execute",
                "security_gate_output",
                "event:node_complete",
            ]
            if order != expected:
                failures.append(
                    f"{node_cls.__name__}: invoke order violation.\n"
                    f"expected: {expected}\nactual:   {order}"
                )

        assert not failures, "\n\n".join(failures)
