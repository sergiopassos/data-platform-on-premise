from agents.orchestrator.router import orchestrator_node, route_next_agent
from agents.state import initial_state


def _state(**overrides):
    s = initial_state("test_table")
    return {**s, **overrides}


def test_first_invocation_routes_to_infra():
    state = _state(last_completed="", current_status="RUNNING")
    result = orchestrator_node(state)
    assert result["next_agent"] == "infra"


def test_after_infra_routes_to_data_source():
    state = _state(last_completed="infra", current_status="RUNNING")
    result = orchestrator_node(state)
    assert result["next_agent"] == "data_source"


def test_after_data_source_routes_to_spark():
    state = _state(last_completed="data_source", current_status="RUNNING")
    result = orchestrator_node(state)
    assert result["next_agent"] == "spark"


def test_after_spark_routes_to_gold():
    state = _state(last_completed="spark", current_status="RUNNING")
    result = orchestrator_node(state)
    assert result["next_agent"] == "gold"


def test_after_gold_routes_to_reporter_with_success():
    state = _state(last_completed="gold", current_status="RUNNING")
    result = orchestrator_node(state)
    assert result["next_agent"] == "reporter"
    assert result["current_status"] == "SUCCESS"


def test_error_status_routes_to_reporter():
    state = _state(last_completed="infra", current_status="ERROR", error_log="disk full")
    result = orchestrator_node(state)
    assert result["next_agent"] == "reporter"
    assert result["current_status"] == "ERROR"


def test_error_on_data_source_routes_to_reporter():
    state = _state(last_completed="data_source", current_status="ERROR")
    result = orchestrator_node(state)
    assert result["next_agent"] == "reporter"


def test_route_next_agent_returns_next_agent_field():
    state = _state(next_agent="spark")
    assert route_next_agent(state) == "spark"


def test_unknown_last_completed_returns_error():
    state = _state(last_completed="unknown_agent", current_status="RUNNING")
    result = orchestrator_node(state)
    assert result["current_status"] == "ERROR"
    assert result["next_agent"] == "reporter"
