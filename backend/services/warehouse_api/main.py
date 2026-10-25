from __future__ import annotations
from datetime import datetime, timezone
import hmac
import json
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from shared.checkpointing import close_checkpointer, get_checkpointer
from shared.config import Settings, get_settings
from shared.models import (
    AuditEvent,
    DeliveryEvent,
    DisruptionCase,
    ProcurementApproval,
    RecoveryPlan,
    RecoveryPlanStatus,
)
from tools.recovery_optimizer import (
    NoProcurementRequired,
    RecoveryOptimizationError,
    plan_recovery,
)
from services.warehouse_api.auth import (
    OperatorPrincipal,
    extract_bearer_token,
    require_procurement_approver,
    verify_supabase_jwt,
)
from services.warehouse_api.repository import (
    RepositoryConflict,
    RepositoryCorruption,
    RepositoryError,
    RepositoryNotFound,
    WarehouseRepository,
    get_repository,
)
from services.warehouse_api.schemas import (
    ApprovalCreateRequest,
    ApprovalListResponse,
    ApprovalRevokeRequest,
    AuditEventListResponse,
    CaseCreateRequest,
    CaseListResponse,
    DeliveryEventCreateRequest,
    DeliveryEventListResponse,
    HealthResponse,
    PlanListResponse,
)
from agents.delivery_watch.agent import DeliveryWatchResult, run_delivery_watch
from observability.tracing import trace_span

SAFE_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
CASE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"

class RequestBodyLimitMiddleware:
    def __init__(self, app, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        content_length = None
        for key, value in scope.get("headers", []):
            if key.lower() == b"content-length":
                try:
                    content_length = int(value.decode("ascii"))
                except (ValueError, UnicodeError):
                    await self._respond(send, 400, b'{"detail":"invalid content length"}')
                    return
                break
        if content_length is not None and (content_length < 0 or content_length > self.max_bytes):
            await self._respond(send, 413, b'{"detail":"request body too large"}')
            return
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await self._respond(send, 413, b'{"detail":"request body too large"}')
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        delivered = False
        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay_receive, send)
    @staticmethod
    async def _respond(send, code: int, body: bytes) -> None:
        await send({
            "type": "http.response.start",
            "status": code,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))],
        })
        await send({"type": "http.response.body", "body": body})
def create_app(
    *,
    settings: Settings | None = None,
    repository: WarehouseRepository | None = None,
) -> FastAPI:
    config = settings or get_settings()
    repo = repository or get_repository(config)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config.validate_network_binding()
        if config.real_purchasing_enabled:
            raise RuntimeError("live purchasing is not implemented")
        repo.initialize()
        try:
            # durable checkpoints must be ready at startup, not on first request
            if config.stockrescue_env == "production" or config.langgraph_checkpoints_enabled:
                get_checkpointer(config)
            app.state.settings = config
            app.state.repository = repo
            yield
        finally:
            repo.close()
            close_checkpointer()
    app = FastAPI(
        title=config.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if config.demo_mode else None,
        redoc_url=None,
    )
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=config.max_request_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origin_list(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization"],
        max_age=600,
    )
    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors()[:20]:
            location = [str(part)[:80] for part in error.get("loc", ())[:8]]
            errors.append({"loc": location, "type": str(error.get("type", "invalid"))[:80], "msg": "invalid request value"})
        return JSONResponse(status_code=422, content={"detail": errors})
    async def authorize(
        request: Request,
        x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> OperatorPrincipal | None:
        active_settings: Settings = request.app.state.settings
        bearer = extract_bearer_token(authorization)
        if bearer:
            return verify_supabase_jwt(bearer, active_settings)

        configured = active_settings.warehouse_api_key
        if configured is not None:
            expected = configured.get_secret_value()
            if not isinstance(x_api_key, str) or len(x_api_key) > 512 or not hmac.compare_digest(x_api_key, expected):
                raise HTTPException(status_code=401, detail="API authentication required")
            return None
        client_host = request.client.host if request.client else ""
        if client_host not in SAFE_CLIENT_HOSTS:
            raise HTTPException(status_code=403, detail="API is restricted to local clients")
        return None

    def repository_or_http_error(callable_, *args, **kwargs):
        try:
            return callable_(*args, **kwargs)
        except RepositoryNotFound as exc:
            raise HTTPException(status_code=404, detail="resource not found") from exc
        except RepositoryConflict as exc:
            raise HTTPException(status_code=409, detail="resource already exists") from exc
        except RepositoryCorruption as exc:
            raise HTTPException(status_code=500, detail="stored data failed validation") from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=503, detail="warehouse storage is unavailable") from exc

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health(request: Request) -> HealthResponse:
        active_settings: Settings = request.app.state.settings
        return HealthResponse(
            app_name=active_settings.app_name,
            demo_mode=active_settings.demo_mode,
            real_purchasing_enabled=False,
        )
    @app.post("/cases", response_model=DisruptionCase, status_code=status.HTTP_201_CREATED, tags=["cases"])
    def create_case(
        body: CaseCreateRequest,
        request: Request,
        _: None = Depends(authorize),
    ) -> DisruptionCase:
        try:
            case = body.to_case()
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="case data failed validation") from exc
        with trace_span("api.create_case", workflow_id=case.case_id, metadata={"operation": "create_case"}):
            return repository_or_http_error(request.app.state.repository.create_case, case)

    @app.get("/cases", response_model=CaseListResponse, tags=["cases"])
    def list_cases(
        request: Request,
        _: None = Depends(authorize),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ) -> CaseListResponse:
        items, count = repository_or_http_error(
            request.app.state.repository.list_cases, limit=limit, offset=offset
        )
        return CaseListResponse(items=items, count=count, limit=limit, offset=offset)

    @app.get("/cases/{case_id}", response_model=DisruptionCase, tags=["cases"])
    def get_case(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
    ) -> DisruptionCase:
        return repository_or_http_error(request.app.state.repository.get_case, case_id)

    @app.post("/cases/{case_id}/plans", response_model=RecoveryPlan, tags=["planning"])
    def create_plan(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
    ) -> RecoveryPlan:
        case: DisruptionCase = repository_or_http_error(request.app.state.repository.get_case, case_id)
        try:
            with trace_span("api.plan_recovery", workflow_id=case.case_id, metadata={"operation": "plan_recovery"}):
                plan = plan_recovery(case)
        except NoProcurementRequired as exc:
            raise HTTPException(status_code=409, detail="available stock already covers the case demand") from exc
        except RecoveryOptimizationError as exc:
            raise HTTPException(status_code=422, detail="recovery planning could not validate this case") from exc
        return repository_or_http_error(request.app.state.repository.save_plan, plan)

    @app.get("/cases/{case_id}/plans", response_model=PlanListResponse, tags=["planning"])
    def list_plans(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ) -> PlanListResponse:
        items, count = repository_or_http_error(
            request.app.state.repository.list_plans,
            case_id,
            limit=limit,
            offset=offset,
        )
        return PlanListResponse(items=items, count=count, limit=limit, offset=offset)

    @app.post(
        "/cases/{case_id}/plans/{plan_id}/approvals",
        response_model=ProcurementApproval,
        status_code=status.HTTP_201_CREATED,
        tags=["approvals"],
    )
    def create_approval(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        body: ApprovalCreateRequest,
        operator: Annotated[OperatorPrincipal, Depends(require_procurement_approver)],
    ) -> ProcurementApproval:
        case: DisruptionCase = repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        if plan.status != RecoveryPlanStatus.PROPOSED:
            raise HTTPException(status_code=400, detail="only proposed recovery plans can be approved")
        if body.currency != plan.currency or body.currency != case.currency:
            raise HTTPException(status_code=400, detail="approval currency does not match recovery plan or case")
        if plan.total_cost > body.max_total_cost:
            raise HTTPException(status_code=400, detail="plan total cost exceeds approved maximum total cost")
        if case.budget is not None and plan.total_cost > case.budget:
            raise HTTPException(status_code=400, detail="recovery plan total cost exceeds case budget")

        now_dt = datetime.now(timezone.utc)
        if body.expires_at <= now_dt:
            raise HTTPException(status_code=400, detail="approval expiry must be in the future")

        canonical_fingerprint = plan.compute_fingerprint()
        existing_active = repository_or_http_error(
            request.app.state.repository.get_active_approval, plan_id, as_of=now_dt
        )
        if existing_active is not None and existing_active.plan_fingerprint == canonical_fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="an active approval already exists for this recovery plan",
            )

        approval = ProcurementApproval(
            approval_id=f"APPR-{uuid4().hex[:12].upper()}",
            case_id=case.case_id,
            plan_id=plan.plan_id,
            plan_fingerprint=canonical_fingerprint,
            approved_by=operator.approver_identity,
            approved_at=now_dt,
            expires_at=body.expires_at,
            max_total_cost=body.max_total_cost,
            currency=body.currency,
            revoked=False,
        )
        saved_approval = repository_or_http_error(request.app.state.repository.save_approval, approval)

        audit_event = AuditEvent(
            event_type="approval_created",
            entity_type="recovery_plan",
            entity_id=plan.plan_id,
            actor_id=operator.approver_identity,
            occurred_at=now_dt,
            details={
                "approval_id": saved_approval.approval_id,
                "case_id": case.case_id,
                "plan_id": plan.plan_id,
                "plan_fingerprint": canonical_fingerprint,
                "max_total_cost": str(saved_approval.max_total_cost),
                "currency": saved_approval.currency,
                "expires_at": saved_approval.expires_at.isoformat(),
            },
        )
        repository_or_http_error(request.app.state.repository.record_audit_event, audit_event)
        return saved_approval

    @app.post(
        "/cases/{case_id}/plans/{plan_id}/approvals/{approval_id}/revoke",
        response_model=ProcurementApproval,
        tags=["approvals"],
    )
    def revoke_approval(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        approval_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        body: ApprovalRevokeRequest,
        operator: Annotated[OperatorPrincipal, Depends(require_procurement_approver)],
    ) -> ProcurementApproval:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        approval: ProcurementApproval = repository_or_http_error(
            request.app.state.repository.get_approval, approval_id
        )
        if approval.case_id != case_id or approval.plan_id != plan_id:
            raise HTTPException(status_code=404, detail="approval does not belong to this case and plan")
        if approval.is_revoked():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="approval is already revoked")

        now_dt = datetime.now(timezone.utc)
        revoked_approval = repository_or_http_error(
            request.app.state.repository.revoke_approval,
            approval_id,
            revoked_by=operator.approver_identity,
            reason=body.revocation_reason,
            at=now_dt,
        )
        audit_event = AuditEvent(
            event_type="approval_revoked",
            entity_type="recovery_plan",
            entity_id=plan.plan_id,
            actor_id=operator.approver_identity,
            occurred_at=now_dt,
            details={
                "approval_id": approval_id,
                "case_id": case_id,
                "plan_id": plan_id,
                "revocation_reason": body.revocation_reason,
            },
        )
        repository_or_http_error(request.app.state.repository.record_audit_event, audit_event)
        return revoked_approval

    @app.get(
        "/cases/{case_id}/plans/{plan_id}/approvals/audit",
        response_model=AuditEventListResponse,
        tags=["approvals"],
    )
    def list_approval_audit_events(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ) -> AuditEventListResponse:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        items, count = repository_or_http_error(
            request.app.state.repository.list_audit_events,
            plan_id,
            limit=limit,
            offset=offset,
        )
        return AuditEventListResponse(items=items, count=count, limit=limit, offset=offset)


    @app.get(
        "/cases/{case_id}/plans/{plan_id}/approvals",
        response_model=ApprovalListResponse,
        tags=["approvals"],
    )
    def list_approvals(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ) -> ApprovalListResponse:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        items, count = repository_or_http_error(
            request.app.state.repository.list_approvals,
            plan_id,
            limit=limit,
            offset=offset,
        )
        return ApprovalListResponse(items=items, count=count, limit=limit, offset=offset)

    @app.get(
        "/cases/{case_id}/plans/{plan_id}/approvals/active",
        response_model=ProcurementApproval | None,
        tags=["approvals"],
    )
    def get_active_approval(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
        as_of: datetime | None = Query(default=None),
    ) -> ProcurementApproval | None:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        return repository_or_http_error(
            request.app.state.repository.get_active_approval, plan_id, as_of=as_of
        )

    @app.get(
        "/approvals/{approval_id}",
        response_model=ProcurementApproval,
        tags=["approvals"],
    )
    def get_approval(
        request: Request,
        approval_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
    ) -> ProcurementApproval:
        return repository_or_http_error(request.app.state.repository.get_approval, approval_id)


    @app.post(
        "/cases/{case_id}/plans/{plan_id}/delivery-events",
        response_model=DeliveryEvent,
        status_code=status.HTTP_201_CREATED,
        tags=["delivery"],
    )
    def record_delivery_event(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        body: DeliveryEventCreateRequest,
        principal: OperatorPrincipal | None = Depends(authorize),
    ) -> DeliveryEvent:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")

        event_id = body.event_id or f"DEL-{uuid4().hex[:12].upper()}"
        event = DeliveryEvent(
            event_id=event_id,
            plan_id=plan_id,
            status=body.status,
            occurred_at=body.occurred_at,
            source=body.source,
            reference=body.reference,
            details=body.details,
        )
        saved = repository_or_http_error(request.app.state.repository.record_delivery_event, event)
        actor_id = principal.sub if principal else "system"
        audit = AuditEvent(
            event_type="delivery_event.recorded",
            entity_type="recovery_plan",
            entity_id=plan_id,
            actor_id=actor_id,
            details={
                "event_id": saved.event_id,
                "status": saved.status,
                "source": saved.source,
            },
        )
        try:
            request.app.state.repository.record_audit_event(audit)
        except Exception:
            pass
        return saved

    @app.get(
        "/cases/{case_id}/plans/{plan_id}/delivery-events",
        response_model=DeliveryEventListResponse,
        tags=["delivery"],
    )
    def list_delivery_events(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ) -> DeliveryEventListResponse:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        items, count = repository_or_http_error(
            request.app.state.repository.list_delivery_events,
            plan_id,
            limit=limit,
            offset=offset,
        )
        return DeliveryEventListResponse(items=items, count=count, limit=limit, offset=offset)

    @app.get(
        "/cases/{case_id}/plans/{plan_id}/delivery-status",
        response_model=DeliveryWatchResult,
        tags=["delivery"],
    )
    def get_delivery_status(
        request: Request,
        case_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        plan_id: Annotated[str, Path(min_length=1, max_length=100, pattern=CASE_ID_PATTERN)],
        _: None = Depends(authorize),
        as_of: datetime | None = Query(default=None),
        allow_simulated: bool = Query(default=False),
    ) -> DeliveryWatchResult:
        repository_or_http_error(request.app.state.repository.get_case, case_id)
        plan: RecoveryPlan = repository_or_http_error(request.app.state.repository.get_plan, plan_id)
        if plan.case_id != case_id:
            raise HTTPException(status_code=404, detail="recovery plan not found for this case")
        events, _ = repository_or_http_error(
            request.app.state.repository.list_delivery_events,
            plan_id,
            limit=100,
            offset=0,
        )
        return run_delivery_watch(plan, events, as_of=as_of, allow_simulated=allow_simulated)

    return app
app = create_app()
