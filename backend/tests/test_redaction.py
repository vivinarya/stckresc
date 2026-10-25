"""Tests for observability redaction utilities.

Validates that JWTs, database URLs, seeds, passwords, authorization headers,
private keys, and sensitive payload mappings are properly redacted while
preserving non-sensitive diagnostic and operational context.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, SecretStr

from observability.redaction import (
    REDACTED,
    redact_mapping,
    redact_text,
    redact_value,
    safe_exception_message,
)


def test_redact_jwt_and_bearer_tokens():
    raw_jwt = "eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0.dGhpcy1pcy1hLWZha2Utc2lnbmF0dXJl"
    text = f"Received authorization: Bearer {raw_jwt}"
    redacted = redact_text(text)
    assert raw_jwt not in redacted
    assert "authorization: [REDACTED]" in redacted

    standalone_jwt = f"Token is {raw_jwt} for user"
    assert raw_jwt not in redact_text(standalone_jwt)
    assert f"Token is {REDACTED} for user" == redact_text(standalone_jwt)


def test_redact_database_urls_with_passwords():
    db_url = "postgresql://app_user:superSecretPassword123@db.supabase.co:5432/postgres?sslmode=require"
    log_msg = f"Connecting to database: {db_url}"
    redacted = redact_text(log_msg)
    assert "superSecretPassword123" not in redacted
    assert "postgresql://app_user:[REDACTED]@" in redacted


def test_redact_key_value_patterns():
    log_line = "agentverse_seed: mySecretSeed123456789 and api_key=sk-1234567890abcdef1234"
    redacted = redact_text(log_line)
    assert "mySecretSeed123456789" not in redacted
    assert f"agentverse_seed: {REDACTED}" in redacted
    assert "sk-1234567890abcdef1234" not in redacted


def test_redact_mapping_nested_and_keys():
    data = {
        "case_id": "CASE-100",
        "actor_id": "operator-1",
        "password": "plain_password",
        "secret_key": "topsecret",
        "nested": {
            "api_key": "xyz-123",
            "safe_field": 42,
            "token": "tok_abcdef",
        },
        "tags": ["prod", "secret_value"],
    }
    redacted = redact_mapping(data)
    assert redacted["case_id"] == "CASE-100"
    assert redacted["actor_id"] == "operator-1"
    assert redacted["password"] == REDACTED
    assert redacted["secret_key"] == REDACTED
    assert redacted["nested"]["api_key"] == REDACTED
    assert redacted["nested"]["token"] == REDACTED
    assert redacted["nested"]["safe_field"] == 42


def test_redact_secret_str_and_base_model():
    class CredentialsModel(BaseModel):
        user: str
        api_key: SecretStr

    model = CredentialsModel(user="alice", api_key=SecretStr("supersecretvalue123"))
    redacted = redact_value(model)
    assert redacted["user"] == "alice"
    assert redacted["api_key"] == REDACTED


def test_safe_exception_message():
    try:
        raise ValueError("Database connection to postgresql://user:myPass123@localhost/db failed")
    except Exception as exc:
        msg = safe_exception_message(exc)
        assert "myPass123" not in msg
        assert "ValueError" in msg
        assert "postgresql://user:[REDACTED]@" in msg
