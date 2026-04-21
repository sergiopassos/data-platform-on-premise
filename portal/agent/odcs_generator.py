"""Ollama-powered ODCS v3.1 contract generator."""
import json
from typing import Any

import httpx
import yaml

from .schema_inspector import ColumnInfo

_PG_TO_ODCS_TYPE = {
    "integer": "integer",
    "bigint": "long",
    "smallint": "integer",
    "numeric": "number",
    "real": "float",
    "double precision": "double",
    "character varying": "string",
    "varchar": "string",
    "character": "string",
    "text": "string",
    "boolean": "boolean",
    "date": "date",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamp",
    "uuid": "string",
    "jsonb": "object",
    "json": "object",
    "bytea": "bytes",
}

_PROMPT_TEMPLATE = """You are a data contract expert. Generate a valid ODCS v3.1 data contract in YAML format for the following PostgreSQL table.

Table name: {table_name}
Schema columns:
{columns_json}

Requirements:
- Use ODCS v3.1 format (dataContractSpecification: 0.9.3)
- Include id, name, version, description, owner, domain fields
- Include schema section with all columns
- Mark primary key columns with primaryKey: true
- Map PostgreSQL types to logical types (string, integer, long, double, boolean, date, timestamp)
- Include quality section with not_null checks for non-nullable columns and unique check for PK
- Output ONLY the YAML, no explanation, no markdown fences

YAML:"""


class ODCSGenerator:
    def __init__(self, ollama_url: str, model: str = "llama3.2:3b") -> None:
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model

    def generate(self, table_name: str, columns: list[ColumnInfo]) -> dict[str, Any]:
        columns_json = json.dumps(
            [
                {
                    "name": c.name,
                    "pg_type": c.data_type,
                    "nullable": c.is_nullable,
                    "primary_key": c.is_primary_key,
                }
                for c in columns
            ],
            indent=2,
        )
        prompt = _PROMPT_TEMPLATE.format(table_name=table_name, columns_json=columns_json)
        raw_yaml = self._call_ollama(prompt)
        contract = self._parse_and_validate(raw_yaml, table_name, columns)
        return contract

    def _call_ollama(self, prompt: str) -> str:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self.ollama_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            return resp.json()["response"]

    def _parse_and_validate(
        self, raw_yaml: str, table_name: str, columns: list[ColumnInfo]
    ) -> dict[str, Any]:
        raw_yaml = raw_yaml.strip()
        if raw_yaml.startswith("```"):
            lines = raw_yaml.split("\n")
            raw_yaml = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            contract = yaml.safe_load(raw_yaml)
        except yaml.YAMLError:
            contract = self._build_fallback_contract(table_name, columns)

        if not isinstance(contract, dict) or "schema" not in contract:
            contract = self._build_fallback_contract(table_name, columns)

        return contract

    def _build_fallback_contract(self, table_name: str, columns: list[ColumnInfo]) -> dict[str, Any]:
        fields = [
            {
                "name": c.name,
                "type": _PG_TO_ODCS_TYPE.get(c.data_type, "string"),
                "required": not c.is_nullable,
                "primaryKey": c.is_primary_key,
            }
            for c in columns
        ]
        pk_names = [c.name for c in columns if c.is_primary_key]
        quality = [{"type": "notNull", "column": c.name} for c in columns if not c.is_nullable]
        if pk_names:
            quality.append({"type": "unique", "column": pk_names[0]})

        return {
            "dataContractSpecification": "0.9.3",
            "id": f"urn:datacontract:{table_name}",
            "name": table_name,
            "version": "1.0.0",
            "description": f"Auto-generated contract for table {table_name}",
            "owner": "data-platform",
            "domain": "source",
            "schema": {
                "type": "table",
                "fields": fields,
            },
            "quality": quality,
        }
