"""Unit tests for ODCSGenerator."""
import json
from unittest.mock import MagicMock, patch

import pytest
import yaml

from portal.agent.odcs_generator import ODCSGenerator
from portal.agent.schema_inspector import ColumnInfo


@pytest.fixture
def generator():
    return ODCSGenerator(ollama_url="http://ollama:11434", model="llama3.2:3b")


@pytest.fixture
def orders_columns():
    return [
        ColumnInfo("order_id", "integer", False, True, 1),
        ColumnInfo("customer_id", "integer", False, False, 2),
        ColumnInfo("status", "character varying", False, False, 3),
        ColumnInfo("amount", "numeric", True, False, 4),
    ]


def _make_ollama_response(contract_yaml: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": contract_yaml}
    return resp


class TestGenerate:
    @patch("portal.agent.odcs_generator.httpx.Client")
    def test_valid_yaml_response_is_parsed(self, mock_client_cls, generator, orders_columns):
        valid_contract = yaml.dump({
            "dataContractSpecification": "0.9.3",
            "id": "urn:datacontract:orders",
            "name": "orders",
            "version": "1.0.0",
            "schema": {
                "type": "table",
                "fields": [{"name": "order_id", "type": "integer", "primaryKey": True}],
            },
            "quality": [],
        })
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _make_ollama_response(valid_contract)
        mock_client_cls.return_value = mock_client

        result = generator.generate("orders", orders_columns)

        assert result["dataContractSpecification"] == "0.9.3"
        assert result["name"] == "orders"
        assert "schema" in result

    @patch("portal.agent.odcs_generator.httpx.Client")
    def test_invalid_yaml_falls_back_to_built_contract(self, mock_client_cls, generator, orders_columns):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _make_ollama_response("this is not valid yaml: {{{")
        mock_client_cls.return_value = mock_client

        result = generator.generate("orders", orders_columns)

        assert result["name"] == "orders"
        fields = result["schema"]["fields"]
        assert any(f["name"] == "order_id" for f in fields)

    @patch("portal.agent.odcs_generator.httpx.Client")
    def test_primary_key_flagged_in_fallback(self, mock_client_cls, generator, orders_columns):
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _make_ollama_response("")
        mock_client_cls.return_value = mock_client

        result = generator.generate("orders", orders_columns)

        fields = {f["name"]: f for f in result["schema"]["fields"]}
        assert fields["order_id"]["primaryKey"] is True
        assert fields.get("customer_id", {}).get("primaryKey") is not True

    @patch("portal.agent.odcs_generator.httpx.Client")
    def test_markdown_fences_stripped(self, mock_client_cls, generator, orders_columns):
        fenced = "```yaml\ndataContractSpecification: '0.9.3'\nname: orders\nschema:\n  type: table\n  fields: []\nquality: []\n```"
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: mock_client
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = _make_ollama_response(fenced)
        mock_client_cls.return_value = mock_client

        result = generator.generate("orders", orders_columns)
        assert result.get("dataContractSpecification") == "0.9.3"
