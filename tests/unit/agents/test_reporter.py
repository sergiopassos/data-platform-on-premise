from unittest.mock import MagicMock, patch

from agents.reporter.tools import format_slack_report, get_langfuse_scores
from agents.state import initial_state


def _state(**overrides):
    s = initial_state("test_table")
    return {
        **s,
        "langfuse_trace_id": "trace-abc123",
        "agent_timings": {"infra": 5.0, "data_source": 30.0, "spark": 120.0, "gold": 60.0},
        "scores": {"infra_health": 1.0, "contract_valid": 1.0, "silver_rows": 1.0, "gold_rows": 42.0},
        **overrides,
    }


def test_format_slack_report_success():
    state = _state(current_status="SUCCESS")
    report = format_slack_report(state, [])
    assert "✅" in report
    assert "SUCCESS" in report
    assert "test_table" in report
    assert "infra" in report


def test_format_slack_report_failure_includes_rca():
    state = _state(current_status="ERROR", error_log="[Spark] OOM on executor")
    report = format_slack_report(state, [])
    assert "❌" in report
    assert "Root-Cause" in report
    assert "OOM on executor" in report


def test_format_slack_report_includes_per_agent_timing():
    state = _state(current_status="SUCCESS")
    report = format_slack_report(state, [])
    assert "infra" in report
    assert "5.0s" in report
    assert "gold" in report
    assert "60.0s" in report


def test_format_slack_report_shows_scores_from_list():
    scores = [{"name": "infra_health", "value": 1.0, "comment": ""},
              {"name": "gold_rows", "value": 42.0, "comment": ""}]
    state = _state(current_status="SUCCESS")
    report = format_slack_report(state, scores)
    assert "infra_health" in report
    assert "gold_rows" in report


def test_format_slack_report_falls_back_to_state_scores():
    state = _state(current_status="SUCCESS")
    report = format_slack_report(state, scores=[])
    assert "infra_health" in report
    assert "gold_rows" in report


def test_reporter_node_always_runs():
    from agents.reporter.agent import reporter_node

    state = _state(current_status="ERROR", error_log="something broke")

    with (
        patch("agents.reporter.agent.get_langfuse_scores", return_value=[]),
        patch("agents.reporter.agent.observe", return_value=MagicMock(
            __enter__=MagicMock(return_value=None),
            __exit__=MagicMock(return_value=False),
        )),
    ):
        result = reporter_node(state)

    assert result["report_markdown"] is not None
    assert "ERROR" in result["report_markdown"]
    assert result["last_completed"] == "reporter"


def test_get_langfuse_scores_returns_empty_when_unconfigured():
    with patch("agents.config.Config.from_env") as mock_from_env:
        mock_from_env.return_value = MagicMock(langfuse_public_key="")
        scores = get_langfuse_scores("some-trace-id")
    assert scores == []
