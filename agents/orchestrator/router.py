from __future__ import annotations

from agents.state import E2EState

_PIPELINE: list[str] = ["infra", "data_source", "spark", "gold"]


def orchestrator_node(state: E2EState) -> E2EState:
    """Deterministic router. Sets next_agent; never calls external services."""
    if state["current_status"] == "ERROR":
        return {**state, "next_agent": "reporter"}

    last = state.get("last_completed", "")

    if not last:
        return {**state, "next_agent": "infra"}

    if last == "gold":
        return {**state, "next_agent": "reporter", "current_status": "SUCCESS"}

    if last not in _PIPELINE:
        return {**state, "next_agent": "reporter", "current_status": "ERROR",
                "error_log": f"Orchestrator: unknown last_completed='{last}'"}

    idx = _PIPELINE.index(last)
    return {**state, "next_agent": _PIPELINE[idx + 1]}


def route_next_agent(state: E2EState) -> str:
    """LangGraph conditional edge function: returns the next node name."""
    return state["next_agent"]
