from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add backend root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
from services.warehouse_api.postgres_repository import PostgresWarehouseRepository
from services.warehouse_api.repository import (
    RepositoryConflict,
    RepositoryCorruption,
    RepositoryError,
    RepositoryNotFound,
    deserialize_approval,
    deserialize_case,
    deserialize_plan,
)
from shared.config import get_settings
from shared.models import DisruptionCase, ProcurementApproval, RecoveryPlan

logger = logging.getLogger("migration")


class MigrationRecord:
    def __init__(
        self,
        item: DisruptionCase | RecoveryPlan | ProcurementApproval,
        raw_row: dict,
    ) -> None:
        self.item = item
        self.raw_row = raw_row


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate StockRescue data from SQLite to PostgreSQL safely without modifying source."
    )
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default=None,
        help="Path to source SQLite database file (defaults to configured database_path)",
    )
    parser.add_argument(
        "--postgres-url",
        type=str,
        default=None,
        help="PostgreSQL target URL (defaults to configured WAREHOUSE_DATABASE_URL)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply the migration (defaults to dry-run if omitted)",
    )
    return parser.parse_args(argv)


def load_sqlite_data(
    sqlite_path: Path,
    *,
    include_approvals: bool = False,
) -> tuple:
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database file not found: {sqlite_path}")

    uri = f"file:{sqlite_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row

    cases: list[MigrationRecord] = []
    plans: list[MigrationRecord] = []
    approvals: list[MigrationRecord] = []

    try:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {row["name"] for row in table_rows}

        if "disruption_cases" in table_names:
            case_rows = connection.execute(
                "SELECT case_id, affected_sku, reported_at, payload_json, created_at, updated_at "
                "FROM disruption_cases ORDER BY updated_at ASC"
            ).fetchall()
            for row in case_rows:
                try:
                    case = deserialize_case(row["payload_json"])
                    cases.append(MigrationRecord(case, dict(row)))
                except RepositoryCorruption as exc:
                    logger.error("Corrupt SQLite case row %s: %s", row["case_id"], exc)
                    raise RepositoryCorruption(
                        f"SQLite source contains corrupt case row: {row['case_id']}"
                    ) from exc

        if "recovery_plans" in table_names:
            plan_rows = connection.execute(
                "SELECT plan_id, case_id, status, generated_at, payload_json, created_at "
                "FROM recovery_plans ORDER BY generated_at ASC"
            ).fetchall()
            for row in plan_rows:
                try:
                    plan = deserialize_plan(row["payload_json"])
                    plans.append(MigrationRecord(plan, dict(row)))
                except RepositoryCorruption as exc:
                    logger.error("Corrupt SQLite plan row %s: %s", row["plan_id"], exc)
                    raise RepositoryCorruption(
                        f"SQLite source contains corrupt plan row: {row['plan_id']}"
                    ) from exc

        if "procurement_approvals" in table_names:
            approval_rows = connection.execute(
                "SELECT approval_id, case_id, plan_id, plan_fingerprint, approved_by, "
                "approved_at, expires_at, max_total_cost, currency, revoked, revoked_at, "
                "revoked_by, revocation_reason, payload_json, created_at, updated_at "
                "FROM procurement_approvals ORDER BY approved_at ASC"
            ).fetchall()
            for row in approval_rows:
                try:
                    approval = deserialize_approval(row["payload_json"])
                    approvals.append(MigrationRecord(approval, dict(row)))
                except RepositoryCorruption as exc:
                    logger.error("Corrupt SQLite approval row %s: %s", row["approval_id"], exc)
                    raise RepositoryCorruption(
                        f"SQLite source contains corrupt approval row: {row['approval_id']}"
                    ) from exc

    finally:
        connection.close()

    if include_approvals:
        return cases, plans, approvals
    return cases, plans


def run_migration(
    sqlite_path: Path,
    postgres_url: str,
    *,
    apply: bool = False,
    environment: str = "development",
) -> int:
    print(f"Reading SQLite source: {sqlite_path}")
    cases, plans, approvals = load_sqlite_data(sqlite_path, include_approvals=True)
    print(
        f"Loaded {len(cases)} cases, {len(plans)} plans, and {len(approvals)} approvals from SQLite."
    )

    pg_repo = PostgresWarehouseRepository(
        postgres_url,
        environment=environment,
    )
    pg_repo.initialize()

    try:
        conflicting_cases: list[str] = []
        for rec in cases:
            case: DisruptionCase = rec.item  # type: ignore
            try:
                pg_repo.get_case(case.case_id)
                conflicting_cases.append(case.case_id)
            except RepositoryNotFound:
                pass

        conflicting_plans: list[str] = []
        for rec in plans:
            plan: RecoveryPlan = rec.item  # type: ignore
            try:
                pg_repo.get_plan(plan.plan_id)
                conflicting_plans.append(plan.plan_id)
            except RepositoryNotFound:
                pass

        conflicting_approvals: list[str] = []
        for rec in approvals:
            approval: ProcurementApproval = rec.item  # type: ignore
            try:
                pg_repo.get_approval(approval.approval_id)
                conflicting_approvals.append(approval.approval_id)
            except RepositoryNotFound:
                pass

        if conflicting_cases or conflicting_plans or conflicting_approvals:
            print(
                "ERROR: ID conflicts detected in PostgreSQL destination. Failing safely.",
                file=sys.stderr,
            )
            if conflicting_cases:
                print(
                    f"Conflicting case IDs ({len(conflicting_cases)}): {conflicting_cases[:10]}",
                    file=sys.stderr,
                )
            if conflicting_plans:
                print(
                    f"Conflicting plan IDs ({len(conflicting_plans)}): {conflicting_plans[:10]}",
                    file=sys.stderr,
                )
            if conflicting_approvals:
                print(
                    f"Conflicting approval IDs ({len(conflicting_approvals)}): {conflicting_approvals[:10]}",
                    file=sys.stderr,
                )
            return 1

        if not apply:
            print(
                f"DRY RUN: {len(cases)} cases, {len(plans)} plans, and {len(approvals)} approvals ready to migrate."
            )
            print("DRY RUN: No conflicts detected. SQLite source database was not modified.")
            print("DRY RUN: Re-run with --apply to execute migration.")
            return 0

        # Atomic migration in a single transaction preserving timestamps
        print("Applying atomic migration to PostgreSQL...")
        with pg_repo._session() as connection:
            with connection.transaction():
                for rec in cases:
                    case_item: DisruptionCase = rec.item  # type: ignore
                    created_at = rec.raw_row.get("created_at")
                    updated_at = rec.raw_row.get("updated_at")
                    connection.execute(
                        """
                        INSERT INTO disruption_cases
                        (case_id, affected_sku, reported_at, payload_json, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            case_item.case_id,
                            case_item.affected_sku,
                            case_item.reported_at,
                            rec.raw_row["payload_json"],
                            created_at or datetime.now(timezone.utc),
                            updated_at or datetime.now(timezone.utc),
                        ),
                    )

                for rec in plans:
                    plan_item: RecoveryPlan = rec.item  # type: ignore
                    created_at = rec.raw_row.get("created_at")
                    connection.execute(
                        """
                        INSERT INTO recovery_plans
                        (plan_id, case_id, status, generated_at, payload_json, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            plan_item.plan_id,
                            plan_item.case_id,
                            plan_item.status.value,
                            plan_item.generated_at,
                            rec.raw_row["payload_json"],
                            created_at or datetime.now(timezone.utc),
                        ),
                    )

                for rec in approvals:
                    appr_item: ProcurementApproval = rec.item  # type: ignore
                    created_at = rec.raw_row.get("created_at")
                    updated_at = rec.raw_row.get("updated_at")
                    connection.execute(
                        """
                        INSERT INTO procurement_approvals
                        (approval_id, case_id, plan_id, plan_fingerprint, approved_by, approved_at,
                         expires_at, max_total_cost, currency, revoked, revoked_at, revoked_by,
                         revocation_reason, payload_json, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            appr_item.approval_id,
                            appr_item.case_id,
                            appr_item.plan_id,
                            appr_item.plan_fingerprint,
                            appr_item.approved_by,
                            appr_item.approved_at,
                            appr_item.expires_at,
                            appr_item.max_total_cost,
                            appr_item.currency,
                            appr_item.is_revoked(),
                            appr_item.revoked_at,
                            appr_item.revoked_by,
                            appr_item.revocation_reason,
                            rec.raw_row["payload_json"],
                            created_at or datetime.now(timezone.utc),
                            updated_at or datetime.now(timezone.utc),
                        ),
                    )

        # Verification step: counts and payload round-trips
        print("Verifying migrated data...")
        _, total_cases = pg_repo.list_cases(limit=1)
        if total_cases < len(cases):
            raise RepositoryError(
                f"PostgreSQL case count ({total_cases}) is less than migrated count ({len(cases)})"
            )

        for rec in cases:
            case_item = rec.item
            pg_case = pg_repo.get_case(case_item.case_id)
            if pg_case != case_item:
                raise RepositoryError(
                    f"Verification mismatch for case: {case_item.case_id}"
                )

        for rec in plans:
            plan_item = rec.item
            pg_plan = pg_repo.get_plan(plan_item.plan_id)
            if pg_plan != plan_item:
                raise RepositoryError(
                    f"Verification mismatch for plan: {plan_item.plan_id}"
                )

        for rec in approvals:
            appr_item = rec.item
            pg_appr = pg_repo.get_approval(appr_item.approval_id)
            if pg_appr != appr_item:
                raise RepositoryError(
                    f"Verification mismatch for approval: {appr_item.approval_id}"
                )

        print(
            f"SUCCESS: Successfully migrated and verified {len(cases)} cases, {len(plans)} plans, and {len(approvals)} approvals."
        )
        return 0

    finally:
        pg_repo.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    sqlite_path = Path(args.sqlite_path or settings.database_path)
    postgres_url = args.postgres_url or (
        settings.warehouse_database_url.get_secret_value()
        if settings.warehouse_database_url
        else ""
    )

    if not postgres_url:
        print(
            "ERROR: Target PostgreSQL URL must be provided via --postgres-url or WAREHOUSE_DATABASE_URL.",
            file=sys.stderr,
        )
        return 1

    return run_migration(
        sqlite_path,
        postgres_url,
        apply=args.apply,
        environment=settings.stockrescue_env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
