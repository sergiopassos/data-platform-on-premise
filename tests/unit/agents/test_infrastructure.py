from unittest.mock import MagicMock, patch

import pytest

from agents.infrastructure.agent import infra_node
from agents.infrastructure.tools import check_minio_bucket, check_namespace_pods
from agents.state import initial_state


def _state(**overrides):
    s = initial_state("test_table")
    return {**s, "langfuse_trace_id": "trace-test", **overrides}


# ── tools ─────────────────────────────────────────────────────────────────────

def test_check_namespace_pods_parses_output():
    payload = {
        "items": [
            {
                "metadata": {"name": "pod-a"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
            {
                "metadata": {"name": "pod-b"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                },
            },
        ]
    }
    import json
    mock_result = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch("subprocess.run", return_value=mock_result):
        pods = check_namespace_pods("infra")
    assert len(pods) == 2
    assert pods[0] == {"name": "pod-a", "phase": "Running", "ready": "True"}


def test_check_namespace_pods_raises_on_kubectl_error():
    mock_result = MagicMock(returncode=1, stdout="", stderr="namespace not found")
    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="kubectl get pods"):
            check_namespace_pods("missing-ns")


def test_check_minio_bucket_returns_true_when_exists():
    mock_s3 = MagicMock()
    mock_s3.head_bucket.return_value = {}
    with patch("boto3.client", return_value=mock_s3):
        from agents.config import Config
        cfg = Config.from_env()
        assert check_minio_bucket("warehouse", cfg) is True


def test_check_minio_bucket_returns_false_when_missing():
    from botocore.exceptions import ClientError
    mock_s3 = MagicMock()
    mock_s3.head_bucket.side_effect = ClientError(
        {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadBucket"
    )
    with patch("boto3.client", return_value=mock_s3):
        from agents.config import Config
        cfg = Config.from_env()
        assert check_minio_bucket("missing-bucket", cfg) is False


# ── agent node ────────────────────────────────────────────────────────────────

def _mock_healthy_pods(*args, **kwargs):
    return [{"name": f"pod-{i}", "phase": "Running", "ready": "True"} for i in range(2)]


def test_infra_node_success():
    with (
        patch("agents.infrastructure.agent.check_namespace_pods", side_effect=_mock_healthy_pods),
        patch("agents.infrastructure.agent.check_minio_bucket", return_value=True),
        patch("agents.infrastructure.agent.emit_score"),
        patch("agents.infrastructure.agent.observe", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
    ):
        result = infra_node(_state())

    assert result["current_status"] == "RUNNING"
    assert result["last_completed"] == "infra"
    assert result["scores"]["infra_health"] == 1.0
    assert "infra" in result["agent_timings"]


def test_infra_node_fails_on_pod_not_ready():
    def mock_pods(namespace):
        if namespace == "streaming":
            return [{"name": "kafka-pod", "phase": "Pending", "ready": "False"}]
        return [{"name": "pod", "phase": "Running", "ready": "True"}]

    with (
        patch("agents.infrastructure.agent.check_namespace_pods", side_effect=mock_pods),
        patch("agents.infrastructure.agent.check_minio_bucket", return_value=True),
        patch("agents.infrastructure.agent.emit_score"),
        patch("agents.infrastructure.agent.observe", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
    ):
        result = infra_node(_state())

    assert result["current_status"] == "ERROR"
    assert "streaming" in result["error_log"]
    assert result["scores"]["infra_health"] == 0.0


def test_infra_node_fails_on_missing_bucket():
    with (
        patch("agents.infrastructure.agent.check_namespace_pods", side_effect=_mock_healthy_pods),
        patch("agents.infrastructure.agent.check_minio_bucket", return_value=False),
        patch("agents.infrastructure.agent.emit_score"),
        patch("agents.infrastructure.agent.observe", return_value=MagicMock(__enter__=MagicMock(return_value=None), __exit__=MagicMock(return_value=False))),
    ):
        result = infra_node(_state())

    assert result["current_status"] == "ERROR"
    assert "bucket" in result["error_log"].lower()
