"""Small PostgreSQL connection adapter for the central server's existing queries."""

from __future__ import annotations


class PostgresConnection:
    is_postgres = True

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("DASHBOARD_POSTGRES_DSN is required")
        try:
            import psycopg
        except ImportError as exc:
            raise ValueError("install psycopg[binary] for PostgreSQL storage") from exc
        self._connection = psycopg.connect(dsn, autocommit=True)
        self._transaction = None

    def execute(self, query: str, params=()):
        return self._connection.execute(query.replace("?", "%s"), params)

    def executemany(self, query: str, params):
        with self._connection.cursor() as cursor:
            cursor.executemany(query.replace("?", "%s"), params)

    def __enter__(self):
        self._transaction = self._connection.transaction()
        self._transaction.__enter__()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._transaction.__exit__(exc_type, exc_value, traceback)
        finally:
            self._transaction = None

    def close(self):
        self._connection.close()
