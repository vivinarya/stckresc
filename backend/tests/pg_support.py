from __future__ import annotations

import os
from urllib.parse import urlsplit

import psycopg

# the only database the destructive integration tests may ever touch
EXPECTED_DATABASE = "stockrescue_test"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class UnsafeTestDatabase(RuntimeError):
    pass


def validated_test_url() -> str:
    """Return the test URL from the process env, or '' if unset. Fail closed if unexpected."""
    url = os.environ.get("STOCKRESCUE_TEST_POSTGRES_URL", "").strip()
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold()
        name = parsed.path.strip("/")
    except ValueError:
        raise UnsafeTestDatabase("STOCKRESCUE_TEST_POSTGRES_URL is not a valid URL") from None
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise UnsafeTestDatabase("STOCKRESCUE_TEST_POSTGRES_URL must be a PostgreSQL URL")
    if host not in LOCAL_HOSTS:
        raise UnsafeTestDatabase("test database host must be local; refusing to run")
    if name != EXPECTED_DATABASE:
        raise UnsafeTestDatabase(f"test database name must be '{EXPECTED_DATABASE}'; refusing to run")
    return url


def assert_connected_to_test_database(url: str) -> None:
    """Confirm on the live server before any table is created or row written."""
    try:
        with psycopg.connect(url, connect_timeout=5, autocommit=True) as connection:
            current = connection.execute("SELECT current_database()").fetchone()[0]
    except psycopg.Error:
        raise UnsafeTestDatabase("could not connect to the test database") from None
    if current != EXPECTED_DATABASE:
        raise UnsafeTestDatabase("connected database is not the dedicated test database")
