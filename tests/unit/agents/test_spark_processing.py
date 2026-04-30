from unittest.mock import MagicMock, patch

from agents.spark_processing.tools import (
    check_nessie_table_exists,
    get_sparkapplication_status,
    render_silver_manifest,
)
from agents.state import initial_state


def _state(**overrides):
    s = initial_state("test_table")
    return {**s, "langfuse_trace_id": "trace-test", **overrides}


def _make_observe_ctx():
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=None)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ── tools ───────────────────���─────────────────────────────────────────────────

def test_get_sparkapplication_status_running():
    mock_result = MagicMock(returncode=0, stdout="RUNNING", stderr="")
    with patch("subprocess.run", return_value=mock_result):
        assert get_sparkapplication_status("bronze-streaming") == "RUNNING"


def test_get_sparkapplication_status_not_found():
    mock_result = MagicMock(returncode=1, stdout="", stderr="not found")
    with patch("subprocess.run", return_value=mock_result):
        assert get_sparkapplication_status("missing-app") == "NOT_FOUND"


def test_check_nessie_table_exists_true():
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "entries": [
            {"name": {"elements": ["bronze", "customers_valid"]}, "type": "ICEBERG_TABLE"}
        ]
    }
    with patch("httpx.get", return_value=mock_resp):
        assert check_nessie_table_exists("http://nessie:19120/api/v1", "bronze", "customers_valid") is True


def test_check_nessie_table_exists_false():
    mock_resp = MagicMock(status_code=200)
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"entries": []}
    with patch("httpx.get", return_value=mock_resp):
        assert check_nessie_table_exists("http://nessie:19120/api/v1", "bronze", "missing_table") is False


def test_check_nessie_table_exists_on_http_error():
    import httpx
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        assert check_nessie_table_exists("http://nessie:19120/api/v1", "bronze", "t") is False


def test_render_silver_manifest_replaces_params(tmp_path):
    template = (
        "metadata:\n  name: silver-batch-{{ params.table_name }}\n"
        "spec:\n  args: [--table, '{{ params.table_name }}', --date, '{{ params.date }}']\n"
    )
    template_file = tmp_path / "silver-batch-app.yaml"
    template_file.write_text(template)
    with patch("agents.spark_processing.tools._SILVER_TEMPLATE_PATH", template_file):
        rendered = render_silver_manifest("orders")
    assert "{{ params.table_name }}" not in rendered
    assert "orders" in rendered


# ── agent node ─────────────────────────────────���────────────────────────────��─

def test_spark_node_success():
    from agents.spark_processing.agent import spark_node

    with (
        patch("agents.spark_processing.agent.get_sparkapplication_status", return_value="RUNNING"),
        patch("agents.spark_processing.agent.check_nessie_table_exists", side_effect=[True, True]),
        patch("agents.spark_processing.agent.delete_sparkapplication"),
        patch("agents.spark_processing.agent.render_silver_manifest", return_value="yaml: content"),
        patch("agents.spark_processing.agent.apply_sparkapplication"),
        patch("agents.spark_processing.agent.wait_for_sparkapplication", return_value="COMPLETED"),
        patch("agents.spark_processing.agent.emit_score"),
        patch("agents.spark_processing.agent.observe", return_value=_make_observe_ctx()),
    ):
        result = spark_node(_state())

    assert result["current_status"] == "RUNNING"
    assert result["last_completed"] == "spark"
    assert result["scores"]["silver_rows"] == 1.0


def test_spark_node_fails_when_silver_job_fails():
    from agents.spark_processing.agent import spark_node

    with (
        patch("agents.spark_processing.agent.get_sparkapplication_status", return_value="RUNNING"),
        patch("agents.spark_processing.agent.check_nessie_table_exists", return_value=True),
        patch("agents.spark_processing.agent.delete_sparkapplication"),
        patch("agents.spark_processing.agent.render_silver_manifest", return_value="yaml: content"),
        patch("agents.spark_processing.agent.apply_sparkapplication"),
        patch("agents.spark_processing.agent.wait_for_sparkapplication", return_value="FAILED"),
        patch("agents.spark_processing.agent.get_spark_driver_logs", return_value="OOM error"),
        patch("agents.spark_processing.agent.emit_score"),
        patch("agents.spark_processing.agent.observe", return_value=_make_observe_ctx()),
    ):
        result = spark_node(_state())

    assert result["current_status"] == "ERROR"
    assert "FAILED" in result["error_log"]
