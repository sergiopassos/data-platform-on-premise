from unittest.mock import MagicMock, patch

import pytest

from agents.gold.tools import query_trino_count
from agents.state import initial_state


def _state(**overrides):
    s = initial_state("test_table")
    return {**s, "langfuse_trace_id": "trace-test", **overrides}


def _make_observe_ctx():
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=None)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ── tools ─────────────────────────────────────────────────────────────────────

def test_query_trino_count_returns_integer():
    mock_result = MagicMock(returncode=0, stdout="42\n", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        assert query_trino_count("iceberg.gold.customers_orders") == 42


def test_query_trino_count_returns_zero_on_empty():
    mock_result = MagicMock(returncode=0, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        assert query_trino_count("iceberg.gold.customers_orders") == 0


def test_query_trino_count_raises_on_error():
    mock_result = MagicMock(returncode=1, stdout="", stderr="table not found")
    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="Trino query failed"):
            query_trino_count("iceberg.gold.missing")


# ── agent node ───────────���───────────────────────────��────────────────────────

def test_gold_node_success():
    from agents.gold.agent import gold_node

    with (
        patch("agents.gold.agent.trigger_airflow_dag", return_value="run-123"),
        patch("agents.gold.agent.wait_for_dag_run", return_value="success"),
        patch("agents.gold.agent.query_trino_count", return_value=100),
        patch("agents.gold.agent.emit_score"),
        patch("agents.gold.agent.observe", return_value=_make_observe_ctx()),
    ):
        result = gold_node(_state())

    assert result["current_status"] == "RUNNING"
    assert result["last_completed"] == "gold"
    assert result["scores"]["gold_rows"] == 100.0


def test_gold_node_retries_on_first_failure():
    from agents.gold.agent import gold_node

    dag_states = iter(["failed", "success"])

    with (
        patch("agents.gold.agent.trigger_airflow_dag", return_value="run-123"),
        patch("agents.gold.agent.wait_for_dag_run", side_effect=lambda *a, **kw: next(dag_states)),
        patch("agents.gold.agent.clear_and_retry_dag", return_value="run-124"),
        patch("agents.gold.agent.query_trino_count", return_value=50),
        patch("agents.gold.agent.emit_score"),
        patch("agents.gold.agent.observe", return_value=_make_observe_ctx()),
    ):
        result = gold_node(_state())

    assert result["current_status"] == "RUNNING"
    assert result["scores"]["gold_rows"] == 50.0


def test_gold_node_fails_when_dag_fails_twice():
    from agents.gold.agent import gold_node

    with (
        patch("agents.gold.agent.trigger_airflow_dag", return_value="run-123"),
        patch("agents.gold.agent.wait_for_dag_run", return_value="failed"),
        patch("agents.gold.agent.clear_and_retry_dag", return_value="run-124"),
        patch("agents.gold.agent.emit_score"),
        patch("agents.gold.agent.observe", return_value=_make_observe_ctx()),
    ):
        result = gold_node(_state())

    assert result["current_status"] == "ERROR"
    assert "failed" in result["error_log"]


def test_gold_node_fails_when_count_is_zero():
    from agents.gold.agent import gold_node

    with (
        patch("agents.gold.agent.trigger_airflow_dag", return_value="run-123"),
        patch("agents.gold.agent.wait_for_dag_run", return_value="success"),
        patch("agents.gold.agent.query_trino_count", return_value=0),
        patch("agents.gold.agent.emit_score"),
        patch("agents.gold.agent.observe", return_value=_make_observe_ctx()),
    ):
        result = gold_node(_state())

    assert result["current_status"] == "ERROR"
    assert "0 rows" in result["error_log"]
