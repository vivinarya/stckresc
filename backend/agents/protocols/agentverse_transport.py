"""Agentverse asynchronous request/response transport and event-loop bridge."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Union

from uagents import Agent, Context, Protocol
from uagents.communication import send_message
from uagents_core.identity import Identity
from uagents_core.types import DeliveryStatus, MsgStatus

from agents.protocols.messages import (
    CorrelationMismatchError,
    MessageContractError,
    TaskErrorCode,
    TaskRequest,
    TaskResponse,
    TaskResponseStatus,
    verify_response_matches_request,
)
from agents.protocols.transport import SpecialistExecutionError, is_valid_agent_address
from observability.tracing import trace_span

logger = logging.getLogger(__name__)


class PendingRequest:
    def __init__(
        self,
        request: TaskRequest,
        expected_framework_sender: str,
        deadline: float,
        future: asyncio.Future[TaskResponse],
    ) -> None:
        self.request = request
        self.expected_framework_sender = expected_framework_sender
        self.deadline = deadline
        self.future = future


class AgentverseTaskExchange:
    """Manages asynchronous TaskRequest/TaskResponse exchange over Agentverse.

    Maintains pending correlation states, verifies framework sender authorization,
    validates response reciprocity, and provides both async and thread-safe sync bridges
    so the uAgents event loop remains responsive during LangGraph execution.
    """

    def __init__(
        self,
        agent_or_identity: Union[Agent, Identity] | None = None,
        *,
        agent: Agent | None = None,
        identity: Identity | None = None,
        allowed_specialist_addresses: set[str] | None = None,
        default_timeout: float = 15.0,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        target = agent_or_identity or agent or identity
        if target is None:
            raise ValueError("Either agent or identity must be provided to AgentverseTaskExchange")
        if isinstance(target, Agent):
            self.agent: Agent | None = target
            self._identity: Identity = target._identity
            self._explicit_loop = loop or getattr(target, "_loop", None)
        else:
            self.agent = None
            self._identity = target
            self._explicit_loop = loop

        self.allowed_specialist_addresses = allowed_specialist_addresses or set()
        self.default_timeout = default_timeout
        self._pending: dict[str, PendingRequest] = {}
        self.protocol = Protocol(name="stockrescue-task-exchange", version="1.0")
        self._setup_protocol()

    def _setup_protocol(self) -> None:
        @self.protocol.on_message(TaskResponse)
        async def handle_task_response(ctx: Context, sender: str, msg: TaskResponse) -> None:
            ctx.logger.info(
                "Received TaskResponse from framework sender=%s (correlation_id=%s, status=%s)",
                sender,
                msg.correlation_id,
                msg.status,
            )
            self._route_response(sender, msg)

    def attach_to_agent(self, agent: Agent | None = None) -> None:
        """Includes the response protocol in the underlying uAgents Agent."""
        target = agent or self.agent
        if target is not None:
            target.include(self.protocol, publish_manifest=True)

    def _route_response(self, framework_sender: str, msg: TaskResponse) -> None:
        correlation_id = msg.correlation_id
        pending = self._pending.get(correlation_id)
        if pending is None:
            logger.warning(
                "Dropping unmatched TaskResponse with correlation_id=%s from %s (no pending request or expired)",
                correlation_id,
                framework_sender,
            )
            return

        # 1. Authorize framework sender
        # Do not accept an arbitrary response from an unrelated agent!
        if framework_sender != pending.expected_framework_sender:
            logger.error(
                "REJECTED: TaskResponse correlation_id=%s came from unauthorized framework sender '%s' (expected '%s')",
                correlation_id,
                framework_sender,
                pending.expected_framework_sender,
            )
            return

        if (
            self.allowed_specialist_addresses
            and framework_sender not in self.allowed_specialist_addresses
        ):
            logger.error(
                "REJECTED: Framework sender '%s' is not in configured allowed specialist addresses",
                framework_sender,
            )
            return

        # 2. Check deadline
        if time.time() > pending.deadline:
            logger.warning(
                "REJECTED: TaskResponse for correlation_id=%s arrived after deadline expired",
                correlation_id,
            )
            if not pending.future.done():
                pending.future.set_exception(
                    SpecialistExecutionError(
                        task_type=pending.request.task_type,
                        status=TaskResponseStatus.TIMEOUT,
                        error_code=TaskErrorCode.TIMEOUT,
                        error_detail=f"Response arrived after deadline for correlation_id={correlation_id}",
                    )
                )
            return

        # 3. Verify correlation fields and reciprocal identities
        try:
            verify_response_matches_request(pending.request, msg)
        except (CorrelationMismatchError, MessageContractError) as exc:
            logger.error(
                "REJECTED: TaskResponse verification failed for correlation_id=%s: %s",
                correlation_id,
                exc,
            )
            if not pending.future.done():
                err_code = (
                    TaskErrorCode.CORRELATION_MISMATCH
                    if isinstance(exc, CorrelationMismatchError)
                    else TaskErrorCode.INVALID_PAYLOAD
                )
                pending.future.set_exception(
                    SpecialistExecutionError(
                        task_type=pending.request.task_type,
                        status=TaskResponseStatus.INVALID_REQUEST,
                        error_code=err_code,
                        error_detail=str(exc),
                    )
                )
            return

        # 4. Resolve future successfully
        if not pending.future.done():
            pending.future.set_result(msg)

    async def send_and_wait_async(
        self,
        destination: str,
        request: TaskRequest,
        timeout: float | None = None,
        existing_future: asyncio.Future[TaskResponse] | None = None,
    ) -> TaskResponse:
        """Sends request via uAgents transport and awaits the validated response."""
        if not is_valid_agent_address(destination):
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=TaskResponseStatus.INVALID_REQUEST,
                error_code=TaskErrorCode.INVALID_PAYLOAD,
                error_detail=f"Invalid destination agent address: '{destination}'",
            )

        timeout_sec = timeout or self.default_timeout
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TaskResponse] = existing_future if existing_future is not None else loop.create_future()
        deadline = time.time() + timeout_sec

        if request.correlation_id not in self._pending:
            pending = PendingRequest(
                request=request,
                expected_framework_sender=destination,
                deadline=deadline,
                future=future,
            )
            self._pending[request.correlation_id] = pending

        start_time = time.time()
        with trace_span(
            name=f"transport.remote.{request.task_type}",
            workflow_id=request.workflow_id,
            correlation_id=request.correlation_id,
            trace_id=request.trace_id,
            input_data={"destination": destination, "task_type": request.task_type},
        ) as span:
            try:
                logger.info(
                    "Dispatching remote TaskRequest (%s) to %s (correlation_id=%s, timeout=%ss)",
                    request.task_type,
                    destination,
                    request.correlation_id,
                    timeout_sec,
                )

                status = await send_message(
                    destination=destination,
                    message=request,
                    sender=self._identity,
                    sync=False,
                )

                if isinstance(status, MsgStatus):
                    if status.status == DeliveryStatus.FAILED:
                        logger.error("Envelope delivery failed to %s: %s", destination, status.detail)
                        raise SpecialistExecutionError(
                            task_type=request.task_type,
                            status=TaskResponseStatus.UNAVAILABLE,
                            error_code=TaskErrorCode.AGENT_UNAVAILABLE,
                            error_detail=f"Delivery failed to destination {destination}: {status.detail}",
                        )
                elif not status:
                    raise SpecialistExecutionError(
                        task_type=request.task_type,
                        status=TaskResponseStatus.UNAVAILABLE,
                        error_code=TaskErrorCode.AGENT_UNAVAILABLE,
                        error_detail=f"Delivery failed: invalid status response '{status}'",
                    )

                remaining = max(0.1, deadline - time.time())
                response = await asyncio.wait_for(future, timeout=remaining)
                logger.info(
                    "Successfully received and validated remote TaskResponse (%s) from %s",
                    request.task_type,
                    destination,
                )
                span.update(
                    output={
                        "status": str(response.status),
                        "duration_seconds": round(time.time() - start_time, 3),
                    }
                )
                return response

            except asyncio.TimeoutError as exc:
                logger.warning(
                    "Timed out waiting for TaskResponse from %s (correlation_id=%s, %ss)",
                    destination,
                    request.correlation_id,
                    timeout_sec,
                )
                span.update(level="ERROR", status_message="TimeoutError")
                raise SpecialistExecutionError(
                    task_type=request.task_type,
                    status=TaskResponseStatus.TIMEOUT,
                    error_code=TaskErrorCode.TIMEOUT,
                    error_detail=f"Timed out after {timeout_sec}s waiting for specialist response from {destination}",
                ) from exc
            except Exception as exc:
                span.update(level="ERROR", status_message=type(exc).__name__)
                raise
            finally:
                self._pending.pop(request.correlation_id, None)

    def sync_caller(self, destination: str, request: TaskRequest) -> TaskResponse:
        """Thread-safe synchronous bridge for LangGraph worker threads.

        Schedules send_and_wait_async on the agent's active event loop and blocks
        the worker thread until completion, preserving event-loop responsiveness.
        """
        if request.is_expired():
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=TaskResponseStatus.TIMEOUT,
                error_code=TaskErrorCode.TIMEOUT,
                error_detail=f"Request deadline expired: {request.deadline_at}",
            )

        loop = self._explicit_loop
        if loop is None and self.agent is not None:
            loop = getattr(self.agent, "_loop", None)
        if loop is None or not loop.is_running():
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=TaskResponseStatus.UNAVAILABLE,
                error_code=TaskErrorCode.AGENT_UNAVAILABLE,
                error_detail="Agent event loop is not running; cannot bridge remote call",
            )

        from datetime import datetime, timezone
        remaining = (request.deadline_at - datetime.now(timezone.utc)).total_seconds()
        call_timeout = max(0.5, remaining) if remaining > 0 else self.default_timeout

        future = loop.create_future()
        deadline = time.time() + call_timeout
        pending = PendingRequest(
            request=request,
            expected_framework_sender=destination,
            deadline=deadline,
            future=future,
        )
        self._pending[request.correlation_id] = pending

        coro = self.send_and_wait_async(
            destination,
            request,
            timeout=call_timeout,
            existing_future=future,
        )
        cf_future = asyncio.run_coroutine_threadsafe(coro, loop)
        wait_timeout = call_timeout + 3.0
        try:
            return cf_future.result(timeout=wait_timeout)
        except TimeoutError as exc:
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=TaskResponseStatus.TIMEOUT,
                error_code=TaskErrorCode.TIMEOUT,
                error_detail=f"Sync bridge timed out waiting for specialist response ({wait_timeout}s)",
            ) from exc
        except SpecialistExecutionError:
            raise
        except Exception as exc:
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=TaskResponseStatus.INTERNAL_ERROR,
                error_code=TaskErrorCode.INTERNAL_ERROR,
                error_detail=f"Sync bridge failure: {exc}",
            ) from exc
