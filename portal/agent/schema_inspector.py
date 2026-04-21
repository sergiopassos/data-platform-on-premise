"""PostgreSQL schema introspection for the AI Agent."""
from dataclasses import dataclass

import psycopg


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    is_nullable: bool
    is_primary_key: bool
    ordinal_position: int


class PostgresSchemaInspector:
    def __init__(self, host: str, port: int, dbname: str, user: str, password: str) -> None:
        self._conninfo = f"host={host} port={port} dbname={dbname} user={user} password={password}"

    def introspect(self, table_name: str, schema: str = "public") -> list[ColumnInfo]:
        with psycopg.connect(self._conninfo) as conn:
            primary_keys = self._get_primary_keys(conn, table_name, schema)
            columns = self._get_columns(conn, table_name, schema, primary_keys)
        return columns

    def _get_primary_keys(self, conn: psycopg.Connection, table_name: str, schema: str) -> set[str]:
        query = """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s
              AND tc.table_name = %s
        """
        with conn.cursor() as cur:
            cur.execute(query, (schema, table_name))
            return {row[0] for row in cur.fetchall()}

    def _get_columns(
        self,
        conn: psycopg.Connection,
        table_name: str,
        schema: str,
        primary_keys: set[str],
    ) -> list[ColumnInfo]:
        query = """
            SELECT column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
        """
        with conn.cursor() as cur:
            cur.execute(query, (schema, table_name))
            return [
                ColumnInfo(
                    name=row[0],
                    data_type=row[1],
                    is_nullable=(row[2] == "YES"),
                    is_primary_key=(row[0] in primary_keys),
                    ordinal_position=row[3],
                )
                for row in cur.fetchall()
            ]

    def table_exists(self, table_name: str, schema: str = "public") -> bool:
        with psycopg.connect(self._conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                    (schema, table_name),
                )
                return cur.fetchone() is not None
