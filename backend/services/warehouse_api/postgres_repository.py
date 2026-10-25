
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.errors import ForeignKeyViolation, UniqueViolation
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import ValidationError

from services.warehouse_api.repository import (
    SCHEMA_VERSION,
    RepositoryConflict,
    RepositoryCorruption,
    RepositoryError,
    RepositoryNotFound,
    deserialize_approval,
    deserialize_audit_event,
    deserialize_case,
    deserialize_delivery_event,
    deserialize_plan,
    serialize_approval,
    serialize_audit_event,
    serialize_case,
    serialize_delivery_event,
    serialize_plan,
    validate_identifier,
    validate_pagination,
)
from shared.models import (
    AuditEvent,
    BusinessProfile,
    DeliveryEvent,
    DisruptionCase,
    Organization,
    OrganizationMembership,
    ProcurementApproval,
    RecoveryPlan,
    RecoveryPlanStatus,
)


logger = logging.getLogger(__name__)


class PostgresWarehouseRepository:
    def __init__(
        self,
        database_url: str,
        *,
        timeout: float = 5.0,
        max_pool_size: int = 5,
        environment: str = "development",
    ) -> None:
        if (
            not isinstance(database_url, str)
            or not database_url.strip()
            or len(database_url) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in database_url)
        ):
            raise RepositoryError("database configuration is invalid")

        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0.5 <= timeout <= 30
        ):
            raise RepositoryError("database timeout is invalid")

        if (
            not isinstance(max_pool_size, int)
            or isinstance(max_pool_size, bool)
            or not 1 <= max_pool_size <= 20
        ):
            raise RepositoryError("database pool size is invalid")

        if environment not in {"development", "test", "production"}:
            raise RepositoryError("database environment is invalid")

        self._database_url = database_url.strip()
        self.timeout = float(timeout)
        self.max_pool_size = max_pool_size
        self.environment = environment
        self._pool: ConnectionPool | None = None

        self._validate_database_url()

    def _validate_database_url(self) -> None:
        try:
            parsed = urlsplit(self._database_url)
            host = parsed.hostname
            query = parse_qs(parsed.query)
            sslmode = query.get("sslmode", [""])[0].casefold()
        except ValueError:
            raise RepositoryError("database URL is invalid") from None

        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not host
            or not parsed.path.strip("/")
        ):
            raise RepositoryError(
                "a PostgreSQL URL with a host and database name is required"
            )

        is_local = host.casefold() in {
            "localhost",
            "127.0.0.1",
            "::1",
        }

        if self.environment == "production" and is_local:
            raise RepositoryError(
                "production requires a remote PostgreSQL database"
            )

        if not is_local and sslmode not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise RepositoryError(
                "remote PostgreSQL connections must use TLS"
            )

    def initialize(self) -> None:
        if self._pool is not None:
            return

        pool = None

        try:
            pool = ConnectionPool(
                conninfo=self._database_url,
                min_size=1,
                max_size=self.max_pool_size,
                timeout=self.timeout,
                max_idle=300,
                kwargs={
                    "autocommit": True,
                    "row_factory": dict_row,
                },
                open=False,
            )
            pool.open()
            pool.wait(timeout=self.timeout)

            with pool.connection() as connection:
                with connection.transaction():
                    connection.execute("SELECT pg_advisory_xact_lock(727001)")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS schema_version (
                            version INTEGER PRIMARY KEY,
                            applied_at TIMESTAMPTZ NOT NULL,
                            description TEXT NOT NULL
                        )
                        """
                    )
                    ver_row = connection.execute(
                        "SELECT MAX(version) AS max_ver FROM schema_version"
                    ).fetchone()
                    current_ver = (
                        ver_row["max_ver"]
                        if ver_row and ver_row["max_ver"] is not None
                        else 0
                    )
                    if current_ver > SCHEMA_VERSION:
                        raise RepositoryError(
                            f"database schema version {current_ver} exceeds supported version {SCHEMA_VERSION}"
                        )
                    if current_ver < 1:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS disruption_cases (
                                case_id TEXT PRIMARY KEY,
                                affected_sku TEXT NOT NULL,
                                reported_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL,
                                updated_at TIMESTAMPTZ NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS recovery_plans (
                                plan_id TEXT PRIMARY KEY,
                                case_id TEXT NOT NULL
                                    REFERENCES disruption_cases(case_id)
                                    ON DELETE CASCADE,
                                status TEXT NOT NULL,
                                generated_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_cases_updated
                            ON disruption_cases(updated_at DESC)
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_plans_case
                            ON recovery_plans(case_id, generated_at DESC)
                            """
                        )
                        now_dt = datetime.now(timezone.utc)
                        connection.execute(
                            """
                            INSERT INTO schema_version (version, applied_at, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            (1, now_dt, "initial schema: disruption_cases and recovery_plans"),
                        )
                    if current_ver < 2:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS procurement_approvals (
                                approval_id TEXT PRIMARY KEY,
                                case_id TEXT NOT NULL
                                    REFERENCES disruption_cases(case_id)
                                    ON DELETE CASCADE,
                                plan_id TEXT NOT NULL
                                    REFERENCES recovery_plans(plan_id)
                                    ON DELETE CASCADE,
                                plan_fingerprint TEXT NOT NULL,
                                approved_by TEXT NOT NULL,
                                approved_at TIMESTAMPTZ NOT NULL,
                                expires_at TIMESTAMPTZ NOT NULL,
                                max_total_cost NUMERIC(14, 2) NOT NULL,
                                currency VARCHAR(3) NOT NULL,
                                revoked BOOLEAN NOT NULL DEFAULT FALSE,
                                revoked_at TIMESTAMPTZ,
                                revoked_by TEXT,
                                revocation_reason TEXT,
                                payload_json TEXT NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL,
                                updated_at TIMESTAMPTZ NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_approvals_plan
                            ON procurement_approvals(plan_id, approved_at DESC)
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_approvals_case
                            ON procurement_approvals(case_id, approved_at DESC)
                            """
                        )
                        now_dt = datetime.now(timezone.utc)
                        connection.execute(
                            """
                            INSERT INTO schema_version (version, applied_at, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            (2, now_dt, "procurement_approvals schema and indexes"),
                        )
                    if current_ver < 3:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS audit_events (
                                event_id TEXT PRIMARY KEY,
                                event_type TEXT NOT NULL,
                                entity_type TEXT NOT NULL,
                                entity_id TEXT NOT NULL,
                                actor_id TEXT NOT NULL,
                                occurred_at TIMESTAMPTZ NOT NULL,
                                details_json TEXT NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_audit_entity
                            ON audit_events(entity_id, occurred_at DESC)
                            """
                        )
                        now_dt = datetime.now(timezone.utc)
                        connection.execute(
                            """
                            INSERT INTO schema_version (version, applied_at, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            (3, now_dt, "audit_events schema and indexes"),
                        )
                    if current_ver < 4:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS delivery_events (
                                event_id TEXT PRIMARY KEY,
                                plan_id TEXT NOT NULL REFERENCES recovery_plans(plan_id) ON DELETE CASCADE,
                                status TEXT NOT NULL,
                                occurred_at TIMESTAMPTZ NOT NULL,
                                source TEXT NOT NULL,
                                reference TEXT,
                                details TEXT NOT NULL,
                                payload_json TEXT NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_delivery_events_plan
                            ON delivery_events(plan_id, occurred_at DESC)
                            """
                        )
                        now_dt = datetime.now(timezone.utc)
                        connection.execute(
                            """
                            INSERT INTO schema_version (version, applied_at, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            (4, now_dt, "delivery_events schema and indexes"),
                        )
                    if current_ver < 5:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS payment_records (
                                reference TEXT PRIMARY KEY,
                                case_id TEXT NOT NULL,
                                customer_address TEXT NOT NULL,
                                amount TEXT NOT NULL,
                                currency VARCHAR(10) NOT NULL,
                                status TEXT NOT NULL,
                                transaction_id TEXT,
                                provider_reference TEXT,
                                created_at TIMESTAMPTZ NOT NULL,
                                expires_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS premium_entitlements (
                                entitlement_id TEXT PRIMARY KEY,
                                case_id TEXT NOT NULL,
                                customer_address TEXT NOT NULL,
                                payment_reference TEXT NOT NULL,
                                provider_transaction_id TEXT NOT NULL,
                                granted_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL,
                                UNIQUE(case_id, customer_address)
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_payments_case
                            ON payment_records(case_id, created_at DESC)
                            """
                        )
                        now_dt = datetime.now(timezone.utc)
                        connection.execute(
                            """
                            INSERT INTO schema_version (version, applied_at, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            (5, now_dt, "payments and premium entitlements schema"),
                        )
                    if current_ver < 6:
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS organizations (
                                org_id TEXT PRIMARY KEY,
                                name TEXT NOT NULL,
                                business_type TEXT NOT NULL,
                                location TEXT NOT NULL,
                                country VARCHAR(100) NOT NULL,
                                currency VARCHAR(10) NOT NULL,
                                business_size VARCHAR(20) NOT NULL,
                                primary_objective TEXT NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL,
                                updated_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS business_profiles (
                                profile_id TEXT PRIMARY KEY,
                                org_id TEXT NOT NULL UNIQUE REFERENCES organizations(org_id),
                                budget_threshold TEXT NOT NULL,
                                approval_policy TEXT NOT NULL,
                                warehouses_json TEXT NOT NULL,
                                operating_currency VARCHAR(10) NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL,
                                updated_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS organization_memberships (
                                membership_id TEXT PRIMARY KEY,
                                org_id TEXT NOT NULL REFERENCES organizations(org_id),
                                user_address TEXT NOT NULL,
                                role VARCHAR(50) NOT NULL,
                                created_at TIMESTAMPTZ NOT NULL,
                                payload_json TEXT NOT NULL,
                                UNIQUE(org_id, user_address)
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_memberships_user
                            ON organization_memberships(user_address)
                            """
                        )
                        now_dt = datetime.now(timezone.utc)
                        connection.execute(
                            """
                            INSERT INTO schema_version (version, applied_at, description)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (version) DO NOTHING
                            """,
                            (6, now_dt, "organizations and business profiles schema"),
                        )

            self._pool = pool

        except RepositoryError:
            if pool is not None:
                pool.close()
            raise
        except Exception as exc:
            if pool is not None:
                pool.close()

            logger.error(
                "PostgreSQL repository initialization failed (%s)",
                type(exc).__name__,
            )
            raise RepositoryError(
                "PostgreSQL repository could not be initialized"
            ) from None

    @contextmanager
    def _session(self) -> Iterator:
        if self._pool is None:
            raise RepositoryError(
                "PostgreSQL repository has not been initialized"
            )

        try:
            with self._pool.connection() as connection:
                yield connection
        except (UniqueViolation, ForeignKeyViolation):
            raise
        except PoolTimeout:
            raise RepositoryError(
                "database connection pool is busy"
            ) from None
        except psycopg.Error:
            logger.error("PostgreSQL operation failed")
            raise RepositoryError(
                "PostgreSQL operation failed"
            ) from None

    def close(self) -> None:
        pool = self._pool
        self._pool = None

        if pool is not None:
            pool.close()

    def create_case(
        self,
        case: DisruptionCase,
    ) -> DisruptionCase:
        clean, payload = serialize_case(case)
        now = datetime.now(timezone.utc)

        try:
            with self._session() as connection:
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO disruption_cases
                        (case_id, affected_sku, reported_at,
                         payload_json, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            clean.case_id,
                            clean.affected_sku,
                            clean.reported_at,
                            payload,
                            now,
                            now,
                        ),
                    )
        except RepositoryError:
            raise
        except UniqueViolation:
            raise RepositoryConflict(
                "case identifier already exists"
            ) from None

        return clean

    def get_case(self, case_id: str) -> DisruptionCase:
        clean_id = validate_identifier(case_id)

        try:
            with self._session() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM disruption_cases
                    WHERE case_id = %s
                    """,
                    (clean_id,),
                ).fetchone()
        except RepositoryError:
            raise

        if row is None:
            raise RepositoryNotFound(
                "disruption case was not found"
            )

        return deserialize_case(row["payload_json"])

    def list_cases(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[DisruptionCase], int]:
        limit, offset = validate_pagination(limit, offset)

        try:
            with self._session() as connection:
                count_row = connection.execute(
                    "SELECT COUNT(*) AS count FROM disruption_cases"
                ).fetchone()

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM disruption_cases
                    ORDER BY updated_at DESC, case_id ASC
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                ).fetchall()
        except RepositoryError:
            raise

        return (
            [deserialize_case(row["payload_json"]) for row in rows],
            int(count_row["count"]),
        )

    def save_plan(self, plan: RecoveryPlan) -> RecoveryPlan:
        clean, payload = serialize_plan(plan)
        now = datetime.now(timezone.utc)

        try:
            with self._session() as connection:
                with connection.transaction():
                    exists = connection.execute(
                        """
                        SELECT 1
                        FROM disruption_cases
                        WHERE case_id = %s
                        """,
                        (clean.case_id,),
                    ).fetchone()

                    if exists is None:
                        raise RepositoryNotFound(
                            "disruption case was not found"
                        )

                    connection.execute(
                        """
                        INSERT INTO recovery_plans
                        (plan_id, case_id, status,
                         generated_at, payload_json, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            clean.plan_id,
                            clean.case_id,
                            clean.status.value,
                            clean.generated_at,
                            payload,
                            now,
                        ),
                    )
        except RepositoryNotFound:
            raise
        except RepositoryError:
            raise
        except UniqueViolation:
            raise RepositoryConflict(
                "recovery plan identifier already exists"
            ) from None
        except ForeignKeyViolation:
            raise RepositoryNotFound(
                "disruption case was not found"
            ) from None

        return clean

    def get_plan(self, plan_id: str) -> RecoveryPlan:
        clean_id = validate_identifier(plan_id)

        try:
            with self._session() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM recovery_plans
                    WHERE plan_id = %s
                    """,
                    (clean_id,),
                ).fetchone()
        except RepositoryError:
            raise

        if row is None:
            raise RepositoryNotFound(
                "recovery plan was not found"
            )

        return deserialize_plan(row["payload_json"])

    def list_plans(
        self,
        case_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RecoveryPlan], int]:
        clean_id = validate_identifier(case_id)
        limit, offset = validate_pagination(limit, offset)

        try:
            with self._session() as connection:
                exists = connection.execute(
                    """
                    SELECT 1
                    FROM disruption_cases
                    WHERE case_id = %s
                    """,
                    (clean_id,),
                ).fetchone()

                if exists is None:
                    raise RepositoryNotFound(
                        "disruption case was not found"
                    )

                count_row = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM recovery_plans
                    WHERE case_id = %s
                    """,
                    (clean_id,),
                ).fetchone()

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM recovery_plans
                    WHERE case_id = %s
                    ORDER BY generated_at DESC, plan_id ASC
                    LIMIT %s OFFSET %s
                    """,
                    (clean_id, limit, offset),
                ).fetchall()
        except RepositoryNotFound:
            raise
        except RepositoryError:
            raise

        return (
            [deserialize_plan(row["payload_json"]) for row in rows],
            int(count_row["count"]),
        )

    def save_approval(self, approval: ProcurementApproval) -> ProcurementApproval:
        clean, payload = serialize_approval(approval)
        now_dt = datetime.now(timezone.utc)
        try:
            with self._session() as connection:
                with connection.transaction():
                    case_row = connection.execute(
                        "SELECT payload_json FROM disruption_cases WHERE case_id = %s FOR SHARE",
                        (clean.case_id,),
                    ).fetchone()
                    if case_row is None:
                        raise RepositoryNotFound("disruption case was not found")
                    case = deserialize_case(case_row["payload_json"])

                    plan_row = connection.execute(
                        "SELECT payload_json FROM recovery_plans WHERE plan_id = %s FOR SHARE",
                        (clean.plan_id,),
                    ).fetchone()
                    if plan_row is None:
                        raise RepositoryNotFound("recovery plan was not found")
                    plan = deserialize_plan(plan_row["payload_json"])

                    if plan.case_id != clean.case_id:
                        raise RepositoryConflict("approval case_id does not match recovery plan case_id")
                    if plan.status != RecoveryPlanStatus.PROPOSED:
                        raise RepositoryError("only proposed recovery plans can be approved")
                    if clean.currency != plan.currency or clean.currency != case.currency:
                        raise RepositoryError("approval currency does not match recovery plan or case")
                    if plan.total_cost > clean.max_total_cost:
                        raise RepositoryError("plan total cost exceeds approved maximum total cost")
                    if case.budget is not None and plan.total_cost > case.budget:
                        raise RepositoryError("recovery plan exceeds case budget")

                    expected_fingerprint = plan.compute_fingerprint()
                    if clean.plan_fingerprint and clean.plan_fingerprint != expected_fingerprint:
                        raise RepositoryConflict("approval fingerprint does not match persisted recovery plan")

                    if not clean.plan_fingerprint:
                        clean = clean.model_copy(update={"plan_fingerprint": expected_fingerprint})
                        _, payload = serialize_approval(clean)

                    if clean.is_expired(clean.approved_at):
                        raise RepositoryError("cannot record an expired approval")
                    if clean.is_revoked():
                        raise RepositoryError("cannot record an already revoked approval")

                    connection.execute(
                        """
                        INSERT INTO procurement_approvals
                        (approval_id, case_id, plan_id, plan_fingerprint, approved_by, approved_at,
                         expires_at, max_total_cost, currency, revoked, revoked_at, revoked_by,
                         revocation_reason, payload_json, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            clean.approval_id,
                            clean.case_id,
                            clean.plan_id,
                            clean.plan_fingerprint,
                            clean.approved_by,
                            clean.approved_at,
                            clean.expires_at,
                            clean.max_total_cost,
                            clean.currency,
                            clean.is_revoked(),
                            clean.revoked_at,
                            clean.revoked_by,
                            clean.revocation_reason,
                            payload,
                            now_dt,
                            now_dt,
                        ),
                    )
        except (RepositoryNotFound, RepositoryConflict, RepositoryError):
            raise
        except UniqueViolation as exc:
            raise RepositoryConflict("approval identifier already exists") from exc
        except ForeignKeyViolation as exc:
            raise RepositoryNotFound("referenced case or plan was not found") from exc
        except Exception as exc:
            logger.error("procurement approval write failed (%s)", type(exc).__name__)
            raise RepositoryError("procurement approval could not be saved") from exc
        return clean

    def get_approval(self, approval_id: str) -> ProcurementApproval:
        clean_id = validate_identifier(approval_id)
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM procurement_approvals WHERE approval_id = %s",
                    (clean_id,),
                ).fetchone()
        except Exception as exc:
            logger.error("procurement approval read failed (%s)", type(exc).__name__)
            raise RepositoryError("procurement approval could not be read") from exc
        if row is None:
            raise RepositoryNotFound("procurement approval was not found")
        return deserialize_approval(row["payload_json"])

    def list_approvals(
        self, plan_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[ProcurementApproval], int]:
        clean_id = validate_identifier(plan_id)
        limit, offset = validate_pagination(limit, offset)
        try:
            with self._session() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM recovery_plans WHERE plan_id = %s",
                    (clean_id,),
                ).fetchone()
                if exists is None:
                    raise RepositoryNotFound("recovery plan was not found")
                count_row = connection.execute(
                    "SELECT COUNT(*) AS count FROM procurement_approvals WHERE plan_id = %s",
                    (clean_id,),
                ).fetchone()
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM procurement_approvals
                    WHERE plan_id = %s
                    ORDER BY approved_at DESC, approval_id ASC
                    LIMIT %s OFFSET %s
                    """,
                    (clean_id, limit, offset),
                ).fetchall()
        except RepositoryNotFound:
            raise
        except Exception as exc:
            logger.error("procurement approvals listing failed (%s)", type(exc).__name__)
            raise RepositoryError("procurement approvals could not be listed") from exc
        return (
            [deserialize_approval(row["payload_json"]) for row in rows],
            int(count_row["count"]),
        )

    def get_active_approval(
        self, plan_id: str, *, as_of: datetime | None = None
    ) -> ProcurementApproval | None:
        clean_id = validate_identifier(plan_id)
        check_time = as_of or datetime.now(timezone.utc)
        if check_time.tzinfo is None or check_time.utcoffset() is None:
            raise RepositoryError("comparison time must include a timezone")
        try:
            with self._session() as connection:
                plan_row = connection.execute(
                    "SELECT payload_json FROM recovery_plans WHERE plan_id = %s",
                    (clean_id,),
                ).fetchone()
                if plan_row is None:
                    raise RepositoryNotFound("recovery plan was not found")
                plan = deserialize_plan(plan_row["payload_json"])
                current_fingerprint = plan.compute_fingerprint()

                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM procurement_approvals
                    WHERE plan_id = %s AND revoked = FALSE
                    ORDER BY approved_at DESC, created_at DESC
                    """,
                    (clean_id,),
                ).fetchall()

                for row in rows:
                    approval = deserialize_approval(row["payload_json"])
                    if approval.is_revoked():
                        continue
                    if approval.is_expired(check_time):
                        continue
                    if approval.approved_at > check_time:
                        continue
                    if approval.plan_fingerprint != current_fingerprint:
                        continue
                    if plan.total_cost > approval.max_total_cost:
                        continue
                    if plan.currency != approval.currency:
                        continue
                    return approval
                return None
        except RepositoryNotFound:
            raise
        except Exception as exc:
            logger.error("active approval read failed (%s)", type(exc).__name__)
            raise RepositoryError("active approval could not be read") from exc

    def revoke_approval(
        self,
        approval_id: str,
        *,
        revoked_by: str,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> ProcurementApproval:
        clean_id = validate_identifier(approval_id)
        if not isinstance(revoked_by, str) or not revoked_by.strip():
            raise RepositoryError("revoking approver identity is required")
        revocation_time = at or datetime.now(timezone.utc)
        if revocation_time.tzinfo is None or revocation_time.utcoffset() is None:
            raise RepositoryError("comparison time must include a timezone")
        try:
            with self._session() as connection:
                with connection.transaction():
                    row = connection.execute(
                        "SELECT payload_json FROM procurement_approvals WHERE approval_id = %s FOR UPDATE",
                        (clean_id,),
                    ).fetchone()
                    if row is None:
                        raise RepositoryNotFound("procurement approval was not found")
                    approval = deserialize_approval(row["payload_json"])
                    updated_approval = approval.model_copy(
                        update={
                            "revoked": True,
                            "revoked_at": revocation_time,
                            "revoked_by": revoked_by.strip(),
                            "revocation_reason": reason.strip() if reason else "revoked by authorized actor",
                        }
                    )
                    _, payload = serialize_approval(updated_approval)
                    connection.execute(
                        """
                        UPDATE procurement_approvals
                        SET revoked = TRUE,
                            revoked_at = %s,
                            revoked_by = %s,
                            revocation_reason = %s,
                            payload_json = %s,
                            updated_at = %s
                        WHERE approval_id = %s
                        """,
                        (
                            updated_approval.revoked_at,
                            updated_approval.revoked_by,
                            updated_approval.revocation_reason,
                            payload,
                            revocation_time,
                            clean_id,
                        ),
                    )
                    return updated_approval
        except RepositoryNotFound:
            raise
        except Exception as exc:
            logger.error("procurement approval revocation failed (%s)", type(exc).__name__)
            raise RepositoryError("procurement approval could not be revoked") from exc

    def record_audit_event(self, event: AuditEvent) -> AuditEvent:
        clean, payload = serialize_audit_event(event)
        try:
            with self._session() as connection:
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO audit_events
                        (event_id, event_type, entity_type, entity_id, actor_id, occurred_at, details_json)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            clean.event_id,
                            clean.event_type,
                            clean.entity_type,
                            clean.entity_id,
                            clean.actor_id,
                            clean.occurred_at,
                            payload,
                        ),
                    )
        except UniqueViolation as exc:
            raise RepositoryConflict("audit event identifier already exists") from exc
        except Exception as exc:
            logger.error("audit event recording failed (%s)", type(exc).__name__)
            raise RepositoryError("audit event could not be recorded") from exc
        return clean

    def list_audit_events(
        self, entity_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[AuditEvent], int]:
        clean_id = validate_identifier(entity_id)
        limit, offset = validate_pagination(limit, offset)
        try:
            with self._session() as connection:
                count_row = connection.execute(
                    "SELECT COUNT(*) AS count FROM audit_events WHERE entity_id = %s",
                    (clean_id,),
                ).fetchone()
                rows = connection.execute(
                    """
                    SELECT details_json
                    FROM audit_events
                    WHERE entity_id = %s
                    ORDER BY occurred_at DESC, event_id ASC
                    LIMIT %s OFFSET %s
                    """,
                    (clean_id, limit, offset),
                ).fetchall()
        except Exception as exc:
            logger.error("audit events listing failed (%s)", type(exc).__name__)
            raise RepositoryError("audit events could not be listed") from exc
        return (
            [deserialize_audit_event(row["details_json"]) for row in rows],
            int(count_row["count"]),
        )

    def record_delivery_event(self, event: DeliveryEvent) -> DeliveryEvent:
        clean, payload = serialize_delivery_event(event)
        now_dt = datetime.now(timezone.utc)
        try:
            with self._session() as connection:
                plan_row = connection.execute(
                    "SELECT 1 FROM recovery_plans WHERE plan_id = %s",
                    (clean.plan_id,),
                ).fetchone()
                if plan_row is None:
                    raise RepositoryNotFound("recovery plan was not found")

                connection.execute(
                    """
                    INSERT INTO delivery_events
                    (event_id, plan_id, status, occurred_at, source, reference, details, payload_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        clean.event_id,
                        clean.plan_id,
                        clean.status,
                        clean.occurred_at,
                        clean.source,
                        clean.reference,
                        clean.details,
                        payload,
                        now_dt,
                    ),
                )
        except (RepositoryNotFound, RepositoryConflict, RepositoryError):
            raise
        except ForeignKeyViolation as exc:
            raise RepositoryNotFound("referenced recovery plan was not found") from exc
        except UniqueViolation as exc:
            raise RepositoryConflict("delivery event identifier already exists") from exc
        except Exception as exc:
            logger.error("delivery event recording failed (%s)", type(exc).__name__)
            raise RepositoryError("delivery event could not be recorded") from exc
        return clean

    def list_delivery_events(
        self, plan_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DeliveryEvent], int]:
        clean_id = validate_identifier(plan_id)
        limit, offset = validate_pagination(limit, offset)
        try:
            with self._session() as connection:
                plan_row = connection.execute(
                    "SELECT 1 FROM recovery_plans WHERE plan_id = %s",
                    (clean_id,),
                ).fetchone()
                if plan_row is None:
                    raise RepositoryNotFound("recovery plan was not found")

                count_row = connection.execute(
                    "SELECT COUNT(*) AS total FROM delivery_events WHERE plan_id = %s",
                    (clean_id,),
                ).fetchone()
                count = int(count_row["total"]) if count_row else 0

                rows = connection.execute(
                    """
                    SELECT payload_json FROM delivery_events WHERE plan_id = %s
                    ORDER BY occurred_at DESC, event_id ASC
                    LIMIT %s OFFSET %s
                    """,
                    (clean_id, limit, offset),
                ).fetchall()
        except RepositoryNotFound:
            raise
        except Exception as exc:
            logger.error("delivery events listing failed (%s)", type(exc).__name__)
            raise RepositoryError("delivery events could not be listed") from exc
        return [deserialize_delivery_event(row["payload_json"]) for row in rows], count

    def save_payment(self, payment: Any) -> Any:
        from shared.payment_models import PaymentRecord
        if not isinstance(payment, PaymentRecord):
            raise RepositoryError("validated PaymentRecord required")
        payload = payment.model_dump_json()
        try:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO payment_records
                    (reference, case_id, customer_address, amount, currency, status, transaction_id, provider_reference, created_at, expires_at, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (reference) DO UPDATE SET
                        status = EXCLUDED.status,
                        transaction_id = EXCLUDED.transaction_id,
                        provider_reference = EXCLUDED.provider_reference,
                        payload_json = EXCLUDED.payload_json
                    """,
                    (
                        payment.reference,
                        payment.case_id,
                        payment.customer_address,
                        str(payment.amount),
                        payment.currency,
                        payment.status.value if hasattr(payment.status, "value") else str(payment.status),
                        payment.transaction_id,
                        payment.provider_reference,
                        payment.created_at,
                        payment.expires_at,
                        payload,
                    ),
                )
        except Exception as exc:
            logger.error("postgres payment save failed (%s)", type(exc).__name__)
            raise RepositoryError("payment could not be saved") from exc
        return payment

    def get_payment(self, reference: str) -> Any:
        from shared.payment_models import PaymentRecord
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM payment_records WHERE reference = %s",
                    (reference,),
                ).fetchone()
                if row is None:
                    raise RepositoryNotFound("payment record was not found")
                return PaymentRecord.model_validate_json(row["payload_json"])
        except RepositoryNotFound:
            raise
        except Exception as exc:
            logger.error("postgres payment lookup failed (%s)", type(exc).__name__)
            raise RepositoryError("payment lookup failed") from exc

    def list_payments(self, case_id: str, *, limit: int = 50, offset: int = 0) -> tuple[list[Any], int]:
        from shared.payment_models import PaymentRecord
        limit, offset = validate_pagination(limit, offset)
        try:
            with self._session() as connection:
                count_row = connection.execute(
                    "SELECT COUNT(*) AS total FROM payment_records WHERE case_id = %s",
                    (case_id,),
                ).fetchone()
                count = int(count_row["total"]) if count_row else 0
                rows = connection.execute(
                    """
                    SELECT payload_json FROM payment_records WHERE case_id = %s
                    ORDER BY created_at DESC LIMIT %s OFFSET %s
                    """,
                    (case_id, limit, offset),
                ).fetchall()
                return [PaymentRecord.model_validate_json(r["payload_json"]) for r in rows], count
        except Exception as exc:
            logger.error("postgres payment list failed (%s)", type(exc).__name__)
            raise RepositoryError("payment list failed") from exc

    def grant_entitlement(self, entitlement: Any) -> Any:
        from shared.payment_models import PremiumReportEntitlement
        if not isinstance(entitlement, PremiumReportEntitlement):
            raise RepositoryError("validated PremiumReportEntitlement required")
        payload = entitlement.model_dump_json()
        try:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO premium_entitlements
                    (entitlement_id, case_id, customer_address, payment_reference, provider_transaction_id, granted_at, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (case_id, customer_address) DO UPDATE SET
                        payment_reference = EXCLUDED.payment_reference,
                        provider_transaction_id = EXCLUDED.provider_transaction_id,
                        granted_at = EXCLUDED.granted_at,
                        payload_json = EXCLUDED.payload_json
                    """,
                    (
                        entitlement.entitlement_id,
                        entitlement.case_id,
                        entitlement.customer_address,
                        entitlement.payment_reference,
                        entitlement.provider_transaction_id,
                        entitlement.granted_at,
                        payload,
                    ),
                )
        except Exception as exc:
            logger.error("postgres grant entitlement failed (%s)", type(exc).__name__)
            raise RepositoryError("entitlement could not be granted") from exc
        return entitlement

    def get_entitlement(self, case_id: str, customer_address: str) -> Any | None:
        from shared.payment_models import PremiumReportEntitlement
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM premium_entitlements WHERE case_id = %s AND customer_address = %s",
                    (case_id, customer_address),
                ).fetchone()
                if row is None:
                    return None
                return PremiumReportEntitlement.model_validate_json(row["payload_json"])
        except Exception as exc:
            logger.error("postgres get entitlement failed (%s)", type(exc).__name__)
            raise RepositoryError("entitlement lookup failed") from exc

    def save_organization(self, org: Organization) -> Organization:
        payload = org.model_dump_json()
        now = datetime.now(timezone.utc)
        try:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO organizations
                    (org_id, name, business_type, location, country, currency, business_size, primary_objective, created_at, updated_at, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (org_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        business_type = EXCLUDED.business_type,
                        location = EXCLUDED.location,
                        country = EXCLUDED.country,
                        currency = EXCLUDED.currency,
                        business_size = EXCLUDED.business_size,
                        primary_objective = EXCLUDED.primary_objective,
                        updated_at = EXCLUDED.updated_at,
                        payload_json = EXCLUDED.payload_json
                    """,
                    (
                        org.org_id,
                        org.name,
                        org.business_type,
                        org.location,
                        org.country,
                        org.currency,
                        org.business_size,
                        org.primary_objective,
                        org.created_at,
                        now,
                        payload,
                    ),
                )
        except Exception as exc:
            logger.error("postgres save organization failed (%s)", type(exc).__name__)
            raise RepositoryError("organization could not be saved") from exc
        return org

    def get_organization(self, org_id: str) -> Organization:
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM organizations WHERE org_id = %s",
                    (org_id,),
                ).fetchone()
                if row is None:
                    raise RepositoryNotFound(f"organization '{org_id}' not found")
                return Organization.model_validate_json(row["payload_json"])
        except RepositoryNotFound:
            raise
        except Exception as exc:
            logger.error("postgres get organization failed (%s)", type(exc).__name__)
            raise RepositoryError("organization lookup failed") from exc

    def save_business_profile(self, profile: BusinessProfile) -> BusinessProfile:
        import json
        payload = profile.model_dump_json()
        now = datetime.now(timezone.utc)
        warehouses_json = json.dumps(profile.warehouses)
        try:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO business_profiles
                    (profile_id, org_id, budget_threshold, approval_policy, warehouses_json, operating_currency, created_at, updated_at, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (org_id) DO UPDATE SET
                        budget_threshold = EXCLUDED.budget_threshold,
                        approval_policy = EXCLUDED.approval_policy,
                        warehouses_json = EXCLUDED.warehouses_json,
                        operating_currency = EXCLUDED.operating_currency,
                        updated_at = EXCLUDED.updated_at,
                        payload_json = EXCLUDED.payload_json
                    """,
                    (
                        profile.profile_id,
                        profile.org_id,
                        str(profile.budget_threshold),
                        profile.approval_policy,
                        warehouses_json,
                        profile.operating_currency,
                        profile.created_at,
                        now,
                        payload,
                    ),
                )
        except Exception as exc:
            logger.error("postgres save business profile failed (%s)", type(exc).__name__)
            raise RepositoryError("business profile could not be saved") from exc
        return profile

    def get_business_profile(self, org_id: str) -> BusinessProfile | None:
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM business_profiles WHERE org_id = %s",
                    (org_id,),
                ).fetchone()
                if row is None:
                    return None
                return BusinessProfile.model_validate_json(row["payload_json"])
        except Exception as exc:
            logger.error("postgres get business profile failed (%s)", type(exc).__name__)
            raise RepositoryError("business profile lookup failed") from exc

    def save_membership(self, membership: OrganizationMembership) -> OrganizationMembership:
        payload = membership.model_dump_json()
        try:
            with self._session() as connection:
                connection.execute(
                    """
                    INSERT INTO organization_memberships
                    (membership_id, org_id, user_address, role, created_at, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (org_id, user_address) DO UPDATE SET
                        role = EXCLUDED.role,
                        payload_json = EXCLUDED.payload_json
                    """,
                    (
                        membership.membership_id,
                        membership.org_id,
                        membership.user_address,
                        membership.role,
                        membership.created_at,
                        payload,
                    ),
                )
        except Exception as exc:
            logger.error("postgres save membership failed (%s)", type(exc).__name__)
            raise RepositoryError("membership could not be saved") from exc
        return membership

    def get_user_membership(self, user_address: str) -> OrganizationMembership | None:
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT payload_json FROM organization_memberships WHERE user_address = %s ORDER BY created_at DESC LIMIT 1",
                    (user_address,),
                ).fetchone()
                if row is None:
                    return None
                return OrganizationMembership.model_validate_json(row["payload_json"])
        except Exception as exc:
            logger.error("postgres get user membership failed (%s)", type(exc).__name__)
            raise RepositoryError("user membership lookup failed") from exc
