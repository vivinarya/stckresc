from __future__ import annotations

import atexit
import enum
import logging
import threading
from dataclasses import is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel, ValidationError
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import shared.agent_contracts as agent_contracts
import shared.models as shared_models
import agents.quote_risk.agent as quote_risk_agent
import agents.recovery_planner.agent as recovery_planner_agent
import agents.procurement.agent as procurement_agent
import agents.delivery_watch.agent as delivery_watch_agent
from shared.config import Settings


logger = logging.getLogger(__name__)
_lock = threading.RLock()
_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None


class CheckpointingError(RuntimeError):
    pass


def _project_types() -> tuple[type, ...]:
    allowed = {
        datetime,
        date,
        time,
        Decimal,
        UUID,
    }

    for module in (
        agent_contracts,
        shared_models,
        quote_risk_agent,
        recovery_planner_agent,
        procurement_agent,
        delivery_watch_agent,
    ):
        for value in vars(module).values():
            if not isinstance(value, type):
                continue
            if value.__module__ != module.__name__:
                continue

            try:
                trusted = (
                    issubclass(value, (BaseModel, enum.Enum))
                    or is_dataclass(value)
                )
            except TypeError:
                trusted = False

            if trusted:
                allowed.add(value)

    return tuple(allowed)


def _serializer() -> JsonPlusSerializer:
    types = _project_types()
    return JsonPlusSerializer(
        allowed_msgpack_modules=types,
        allowed_json_modules=[
            (item.__module__, item.__name__)
            for item in types
        ],
        pickle_fallback=False,
    )


def _validate_dsn(dsn: str, environment: str) -> None:
    if not isinstance(dsn, str) or not 1 <= len(dsn) <= 2048:
        raise CheckpointingError("PostgreSQL connection URL is invalid")

    if any(ord(char) < 32 or ord(char) == 127 for char in dsn):
        raise CheckpointingError("PostgreSQL connection URL is invalid")

    try:
        parsed = urlsplit(dsn)
        host = parsed.hostname
        query = parse_qs(parsed.query)
        sslmode = query.get("sslmode", [""])[0].casefold()
    except ValueError:
        raise CheckpointingError(
            "PostgreSQL connection URL is invalid"
        ) from None

    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not host
        or not parsed.path.strip("/")
    ):
        raise CheckpointingError(
            "Use a PostgreSQL URL with a host and database name"
        )

    local_hosts = {"localhost", "127.0.0.1", "::1"}
    is_local = host.casefold() in local_hosts

    if environment == "production" and is_local:
        raise CheckpointingError(
            "Production requires a remote PostgreSQL database"
        )

    if not is_local and sslmode not in {
        "require",
        "verify-ca",
        "verify-full",
    }:
        raise CheckpointingError(
            "Remote PostgreSQL connections must use TLS"
        )


def _load_settings() -> Settings:
    # one source of truth shared with the API; never echo validation input
    try:
        return Settings()
    except ValidationError as exc:
        fields = sorted({str(err["loc"][0]) for err in exc.errors() if err.get("loc")})
        logger.error("Checkpoint configuration is invalid (%s)", ", ".join(fields))
        raise CheckpointingError("Checkpoint configuration is invalid") from None


def get_checkpointer(settings: Settings | None = None) -> PostgresSaver | None:
    global _pool, _saver

    with _lock:
        if _saver is not None:
            return _saver

        config = settings or _load_settings()
        environment = config.stockrescue_env

        if not config.langgraph_checkpoints_enabled:
            if environment == "production":
                raise CheckpointingError(
                    "Persistent checkpoints must be enabled in production"
                )
            return None

        secret = config.langgraph_postgres_url
        dsn = secret.get_secret_value().strip() if secret else ""

        if not dsn:
            raise CheckpointingError(
                "LANGGRAPH_POSTGRES_URL is required"
            )

        _validate_dsn(dsn, environment)

        pool = None

        try:
            pool = ConnectionPool(
                conninfo=dsn,
                min_size=1,
                max_size=config.langgraph_db_pool_max_size,
                timeout=10,
                max_idle=300,
                kwargs={
                    "autocommit": True,
                    "row_factory": dict_row,
                },
                open=False,
            )

            pool.open()
            pool.wait(timeout=10)

            saver = PostgresSaver(pool, serde=_serializer())
            saver.setup()

            _pool = pool
            _saver = saver

            logger.info(
                "PostgreSQL checkpoint store initialized"
            )
            return saver

        except Exception as exc:
            if pool is not None:
                pool.close()

            logger.error(
                "Checkpoint initialization failed (%s)",
                type(exc).__name__,
            )
            raise CheckpointingError(
                "PostgreSQL checkpoint store is unavailable"
            ) from None


def close_checkpointer() -> None:
    global _pool, _saver

    with _lock:
        pool = _pool
        _pool = None
        _saver = None

    if pool is not None:
        pool.close()


atexit.register(close_checkpointer)
