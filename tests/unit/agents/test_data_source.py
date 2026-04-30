from unittest.mock import MagicMock, patch

from agents.state import initial_state


def _state(**overrides):
    s = initial_state("test_table")
    return {**s, "langfuse_trace_id": "trace-test", **overrides}


def _make_observe_ctx():
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=None)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def test_data_source_node_happy_path():
    from agents.data_source.agent import data_source_node

    mock_columns = [MagicMock(name="id", is_primary_key=True, is_nullable=False, data_type="integer")]
    mock_contract = {
        "dataContractSpecification": "0.9.3",
        "id": "urn:test",
        "schema": {"fields": []},
    }

    with (
        patch("agents.data_source.agent.create_and_seed_table"),
        patch("agents.data_source.agent.PostgresSchemaInspector") as mock_inspector_cls,
        patch("agents.data_source.agent.build_llm_provider", return_value=MagicMock()),
        patch("agents.data_source.agent.asyncio.run", return_value=mock_contract),
        patch("agents.data_source.agent.validate_contract_cli", return_value=(True, "")),
        patch("agents.data_source.agent.upload_contract_to_minio", return_value="s3://contracts/test_table.yaml"),
        patch("agents.data_source.agent.ConnectorActivator") as mock_activator_cls,
        patch("agents.data_source.agent.consume_one_kafka_message", return_value=True),
        patch("agents.data_source.agent.emit_score"),
        patch("agents.data_source.agent.observe", return_value=_make_observe_ctx()),
    ):
        mock_inspector_cls.return_value.introspect.return_value = mock_columns
        mock_activator_cls.return_value.activate.return_value = {"status": "created"}
        result = data_source_node(_state())

    assert result["current_status"] == "RUNNING"
    assert result["data_contract_path"] == "s3://contracts/test_table.yaml"
    assert result["last_completed"] == "data_source"
    assert result["scores"]["contract_valid"] == 1.0
    assert result["scores"]["cdc_active"] == 1.0


def test_data_source_node_falls_back_on_cli_validation_failure():
    from agents.data_source.agent import data_source_node

    mock_columns = []
    mock_contract = {"dataContractSpecification": "0.9.3", "schema": {"fields": []}}

    with (
        patch("agents.data_source.agent.create_and_seed_table"),
        patch("agents.data_source.agent.PostgresSchemaInspector") as mock_inspector_cls,
        patch("agents.data_source.agent.build_llm_provider", return_value=MagicMock()),
        patch("agents.data_source.agent.asyncio.run", return_value=mock_contract),
        patch("agents.data_source.agent.validate_contract_cli", return_value=(False, "invalid schema")),
        patch("agents.data_source.agent.upload_contract_to_minio", return_value="s3://contracts/test_table.yaml"),
        patch("agents.data_source.agent.ConnectorActivator") as mock_activator_cls,
        patch("agents.data_source.agent.consume_one_kafka_message", return_value=True),
        patch("agents.data_source.agent.emit_score"),
        patch("agents.data_source.agent.observe", return_value=_make_observe_ctx()),
    ):
        mock_inspector_cls.return_value.introspect.return_value = mock_columns
        mock_activator_cls.return_value.activate.return_value = {"status": "created"}
        result = data_source_node(_state())

    assert result["current_status"] == "RUNNING"


def test_data_source_node_fails_on_no_kafka_message():
    from agents.data_source.agent import data_source_node

    mock_contract = {"dataContractSpecification": "0.9.3", "schema": {"fields": []}}

    with (
        patch("agents.data_source.agent.create_and_seed_table"),
        patch("agents.data_source.agent.PostgresSchemaInspector") as mock_inspector_cls,
        patch("agents.data_source.agent.build_llm_provider", return_value=MagicMock()),
        patch("agents.data_source.agent.asyncio.run", return_value=mock_contract),
        patch("agents.data_source.agent.validate_contract_cli", return_value=(True, "")),
        patch("agents.data_source.agent.upload_contract_to_minio", return_value="s3://contracts/test_table.yaml"),
        patch("agents.data_source.agent.ConnectorActivator") as mock_activator_cls,
        patch("agents.data_source.agent.consume_one_kafka_message", return_value=False),
        patch("agents.data_source.agent.emit_score"),
        patch("agents.data_source.agent.observe", return_value=_make_observe_ctx()),
    ):
        mock_inspector_cls.return_value.introspect.return_value = []
        mock_activator_cls.return_value.activate.return_value = {"status": "created"}
        result = data_source_node(_state())

    assert result["current_status"] == "ERROR"
    assert "Kafka" in result["error_log"]
