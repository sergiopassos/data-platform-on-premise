"""Unit tests for ODCSGenerator (refactored for provider-injection)."""
import pytest
import yaml

from portal.agent.odcs_generator import ODCSGenerator
from portal.agent.providers.base import ProviderError
from portal.agent.schema_inspector import ColumnInfo


class _FakeProvider:
    name = "fake"

    def __init__(self, yaml_payload: str = "", *, raise_exc: Exception | None = None) -> None:
        self._payload = yaml_payload
        self._raise = raise_exc

    async def generate_yaml(self, prompt: str) -> str:
        if self._raise:
            raise self._raise
        return self._payload


@pytest.fixture
def fake_provider_factory():
    return _FakeProvider


@pytest.fixture
def generator():
    return ODCSGenerator()


@pytest.fixture
def orders_columns():
    return [
        ColumnInfo("order_id", "integer", False, True, 1),
        ColumnInfo("customer_id", "integer", False, False, 2),
        ColumnInfo("status", "character varying", False, False, 3),
        ColumnInfo("amount", "numeric", True, False, 4),
    ]


class TestGenerate:
    @pytest.mark.asyncio
    async def test_valid_yaml_response_is_parsed(self, generator, orders_columns):
        valid_contract = yaml.dump({
            "dataContractSpecification": "0.9.3",
            "id": "urn:datacontract:orders",
            "name": "orders",
            "version": "1.0.0",
            "schema": {"type": "table", "fields": [{"name": "order_id", "type": "integer", "primaryKey": True}]},
            "quality": [],
        })
        provider = _FakeProvider(yaml_payload=valid_contract)

        result = await generator.generate("orders", orders_columns, provider=provider)

        assert result["dataContractSpecification"] == "0.9.3"
        assert result["name"] == "orders"
        assert "schema" in result

    @pytest.mark.asyncio
    async def test_invalid_yaml_falls_back_to_built_contract(self, generator, orders_columns):
        provider = _FakeProvider(yaml_payload="this is not valid yaml: {{{")

        result = await generator.generate("orders", orders_columns, provider=provider)

        assert result["name"] == "orders"
        fields = result["schema"]["fields"]
        assert any(f["name"] == "order_id" for f in fields)

    @pytest.mark.asyncio
    async def test_primary_key_flagged_in_fallback(self, generator, orders_columns):
        provider = _FakeProvider(yaml_payload="")

        result = await generator.generate("orders", orders_columns, provider=provider)

        fields = {f["name"]: f for f in result["schema"]["fields"]}
        assert fields["order_id"]["primaryKey"] is True
        assert fields.get("customer_id", {}).get("primaryKey") is not True

    @pytest.mark.asyncio
    async def test_markdown_fences_stripped(self, generator, orders_columns):
        fenced = "```yaml\ndataContractSpecification: '0.9.3'\nname: orders\nschema:\n  type: table\n  fields: []\nquality: []\n```"
        provider = _FakeProvider(yaml_payload=fenced)

        result = await generator.generate("orders", orders_columns, provider=provider)
        assert result.get("dataContractSpecification") == "0.9.3"

    @pytest.mark.asyncio
    async def test_provider_error_propagates(self, generator, orders_columns):
        from portal.agent.providers.base import ProviderAPIError
        provider = _FakeProvider(raise_exc=ProviderAPIError("fake", "network error"))

        with pytest.raises(ProviderError):
            await generator.generate("orders", orders_columns, provider=provider)
