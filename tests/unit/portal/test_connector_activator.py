"""Unit tests for ConnectorActivator."""
from unittest.mock import MagicMock, patch

import httpx
import pytest

from portal.agent.connector_activator import ConnectorActivator, PostgresConfig


@pytest.fixture
def pg_config():
    return PostgresConfig(host="postgres", port=5432, dbname="sourcedb", user="user", password="pass")


@pytest.fixture
def activator(pg_config):
    return ConnectorActivator(
        kafka_connect_url="http://kafka-connect:8083",
        postgres=pg_config,
    )


def _mock_http_response(status_code: int, body: dict | list) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = body
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


class TestActivate:
    @patch("portal.agent.connector_activator.httpx.Client")
    def test_creates_connector_when_not_exists(self, mock_client_cls, activator):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_client.get.return_value = _mock_http_response(404, {})
        mock_client.post.return_value = _mock_http_response(201, {"name": "debezium-public-orders"})
        mock_client_cls.return_value = mock_client

        result = activator.activate("orders")

        assert result["name"] == "debezium-public-orders"
        mock_client.post.assert_called_once()

    @patch("portal.agent.connector_activator.httpx.Client")
    def test_skips_creation_when_connector_exists(self, mock_client_cls, activator):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_client.get.return_value = _mock_http_response(200, {"name": "debezium-public-orders"})
        mock_client_cls.return_value = mock_client

        result = activator.activate("orders")

        assert result["status"] == "already_active"
        mock_client.post.assert_not_called()

    @patch("portal.agent.connector_activator.httpx.Client")
    def test_connector_config_contains_required_fields(self, mock_client_cls, activator):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_client.get.return_value = _mock_http_response(404, {})
        mock_client.post.return_value = _mock_http_response(201, {"name": "debezium-public-orders"})
        mock_client_cls.return_value = mock_client

        activator.activate("orders")

        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
        config = payload["config"]

        assert config["connector.class"] == "io.debezium.connector.postgresql.PostgresConnector"
        assert config["plugin.name"] == "pgoutput"
        assert "orders" in config["table.include.list"]
        assert config["database.hostname"] == "postgres"

    @patch("portal.agent.connector_activator.httpx.Client")
    def test_retries_on_5xx(self, mock_client_cls, activator):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)

        mock_client.get.return_value = _mock_http_response(404, {})
        mock_client.post.side_effect = [
            _mock_http_response(503, {}),
            _mock_http_response(503, {}),
            _mock_http_response(201, {"name": "debezium-public-orders"}),
        ]
        mock_client_cls.return_value = mock_client

        with patch("portal.agent.connector_activator.time.sleep"):
            result = activator.activate("orders")

        assert result["name"] == "debezium-public-orders"
        assert mock_client.post.call_count == 3
