import pytest

from shared import checkpointing as cp

LOCAL = "postgresql://user:s3cretpw@localhost:5432/stockrescue_test"
REMOTE_TLS = "postgresql://user:s3cretpw@db.example.com:5432/app?sslmode=require"


@pytest.fixture(autouse=True)
def reset_checkpointer():
    cp.close_checkpointer()
    yield
    cp.close_checkpointer()


def test_disabled_checkpoints_return_none_in_development():
    assert cp.get_checkpointer() is None


def test_production_requires_persistent_checkpoints(monkeypatch):
    monkeypatch.setenv("STOCKRESCUE_ENV", "production")
    with pytest.raises(cp.CheckpointingError):
        cp.get_checkpointer()


def test_enabled_checkpoints_require_a_url(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTS_ENABLED", "true")
    with pytest.raises(cp.CheckpointingError):
        cp.get_checkpointer()


@pytest.mark.parametrize(
    "dsn, environment",
    [
        ("mysql://user:s3cretpw@localhost/db", "development"),
        ("postgresql://user:s3cretpw@localhost", "development"),
        ("postgresql:///db", "development"),
        ("postgresql://user:s3cretpw@localhost/db\n", "development"),
        ("postgresql://user:s3cretpw@db.example.com/app", "development"),
        ("postgresql://user:s3cretpw@db.example.com/app?sslmode=disable", "development"),
        (LOCAL, "production"),
        ("", "development"),
        ("x" * 2049, "development"),
    ],
)
def test_unsafe_connection_urls_are_rejected_without_leaking_secrets(dsn, environment):
    with pytest.raises(cp.CheckpointingError) as error:
        cp._validate_dsn(dsn, environment)
    assert "s3cretpw" not in str(error.value)


@pytest.mark.parametrize(
    "dsn, environment",
    [(LOCAL, "development"), (REMOTE_TLS, "production"),
     (REMOTE_TLS.replace("require", "verify-full"), "development")],
)
def test_safe_connection_urls_are_accepted(dsn, environment):
    cp._validate_dsn(dsn, environment)


@pytest.mark.parametrize("size", ["abc", "0", "21", "-1", "4.5", ""])
def test_pool_size_is_validated_before_connecting(monkeypatch, size):
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTS_ENABLED", "true")
    monkeypatch.setenv("LANGGRAPH_POSTGRES_URL", LOCAL)
    monkeypatch.setenv("LANGGRAPH_DB_POOL_MAX_SIZE", size)
    with pytest.raises(cp.CheckpointingError) as error:
        cp.get_checkpointer()
    assert "s3cretpw" not in str(error.value)


def test_serializer_allowlist_covers_project_types_only():
    from decimal import Decimal
    from shared.agent_contracts import CoordinatorResult
    from shared.models import DisruptionCase

    types = cp._project_types()
    assert {DisruptionCase, CoordinatorResult, Decimal} <= set(types)
    assert all(isinstance(item, type) for item in types)
    assert not any(item.__module__.split(".")[0] in {"os", "subprocess", "builtins"}
                   for item in types)
