"""Unit tests for PostgresSchemaInspector."""
from unittest.mock import MagicMock, patch

import pytest

from portal.agent.schema_inspector import ColumnInfo, PostgresSchemaInspector


@pytest.fixture
def inspector():
    return PostgresSchemaInspector(
        host="localhost", port=5432, dbname="testdb", user="user", password="pass"
    )


def _make_conn_mock(columns: list[tuple], pk_columns: list[tuple]):
    cursor_mock = MagicMock()
    cursor_mock.__enter__ = lambda s: cursor_mock
    cursor_mock.__exit__ = MagicMock(return_value=False)
    cursor_mock.fetchall.side_effect = [pk_columns, columns]

    conn_mock = MagicMock()
    conn_mock.__enter__ = lambda s: conn_mock
    conn_mock.__exit__ = MagicMock(return_value=False)
    conn_mock.cursor.return_value = cursor_mock
    return conn_mock


class TestIntrospect:
    @patch("portal.agent.schema_inspector.psycopg.connect")
    def test_returns_columns_with_pk_flags(self, mock_connect, inspector):
        pk_rows = [("order_id",)]
        col_rows = [
            ("order_id", "integer", "NO", 1),
            ("status", "character varying", "NO", 2),
            ("amount", "numeric", "YES", 3),
        ]
        mock_connect.return_value = _make_conn_mock(col_rows, pk_rows)

        result = inspector.introspect("orders")

        assert len(result) == 3
        assert result[0] == ColumnInfo(
            name="order_id", data_type="integer", is_nullable=False, is_primary_key=True, ordinal_position=1
        )
        assert result[1].is_primary_key is False
        assert result[2].is_nullable is True

    @patch("portal.agent.schema_inspector.psycopg.connect")
    def test_composite_primary_key(self, mock_connect, inspector):
        pk_rows = [("order_id",), ("line_id",)]
        col_rows = [
            ("order_id", "integer", "NO", 1),
            ("line_id", "integer", "NO", 2),
            ("product_id", "integer", "NO", 3),
        ]
        mock_connect.return_value = _make_conn_mock(col_rows, pk_rows)

        result = inspector.introspect("order_lines")

        pks = [c for c in result if c.is_primary_key]
        assert len(pks) == 2
        assert {c.name for c in pks} == {"order_id", "line_id"}

    @patch("portal.agent.schema_inspector.psycopg.connect")
    def test_no_primary_key(self, mock_connect, inspector):
        pk_rows = []
        col_rows = [("name", "text", "YES", 1)]
        mock_connect.return_value = _make_conn_mock(col_rows, pk_rows)

        result = inspector.introspect("log_entries")

        assert all(not c.is_primary_key for c in result)


class TestTableExists:
    @patch("portal.agent.schema_inspector.psycopg.connect")
    def test_existing_table_returns_true(self, mock_connect, inspector):
        cursor_mock = MagicMock()
        cursor_mock.__enter__ = lambda s: cursor_mock
        cursor_mock.__exit__ = MagicMock(return_value=False)
        cursor_mock.fetchone.return_value = (1,)

        conn_mock = MagicMock()
        conn_mock.__enter__ = lambda s: conn_mock
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.cursor.return_value = cursor_mock
        mock_connect.return_value = conn_mock

        assert inspector.table_exists("orders") is True

    @patch("portal.agent.schema_inspector.psycopg.connect")
    def test_missing_table_returns_false(self, mock_connect, inspector):
        cursor_mock = MagicMock()
        cursor_mock.__enter__ = lambda s: cursor_mock
        cursor_mock.__exit__ = MagicMock(return_value=False)
        cursor_mock.fetchone.return_value = None

        conn_mock = MagicMock()
        conn_mock.__enter__ = lambda s: conn_mock
        conn_mock.__exit__ = MagicMock(return_value=False)
        conn_mock.cursor.return_value = cursor_mock
        mock_connect.return_value = conn_mock

        assert inspector.table_exists("nonexistent") is False
