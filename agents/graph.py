from __future__ import annotations

from langgraph.graph import END, StateGraph

from agents.data_source.agent import data_source_node
from agents.gold.agent import gold_node
from agents.infrastructure.agent import infra_node
from agents.orchestrator.router import orchestrator_node, route_next_agent
from agents.reporter.agent import reporter_node
from agents.spark_processing.agent import spark_node
from agents.state import E2EState

_AGENT_NODES: dict[str, object] = {
    "infra": infra_node,
    "data_source": data_source_node,
    "spark": spark_node,
    "gold": gold_node,
    "reporter": reporter_node,
}


def build_graph():
    graph = StateGraph(E2EState)

    graph.add_node("orchestrator", orchestrator_node)
    for name, fn in _AGENT_NODES.items():
        graph.add_node(name, fn)

    graph.set_entry_point("orchestrator")

    graph.add_conditional_edges(
        "orchestrator",
        route_next_agent,
        {name: name for name in _AGENT_NODES},
    )

    for name in ("infra", "data_source", "spark", "gold"):
        graph.add_edge(name, "orchestrator")

    graph.add_edge("reporter", END)

    return graph.compile()
