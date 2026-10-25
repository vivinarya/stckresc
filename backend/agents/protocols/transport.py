"""Specialist transport and client for inter-agent communication.

Supports both:
- Deterministic local execution (for development and local test suites)
- Remote Agentverse transport with correlation verification, bounded retries,
  and timeout enforcement.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from uagents_core.identity import is_valid_address

from agents.protocols.dispatcher import SpecialistDispatcher
from agents.protocols.messages import (
    CorrelationMismatchError,
    MessageContractError,
    TaskErrorCode,
    TaskRequest,
    TaskResponse,
    TaskResponseStatus,
    verify_response_matches_request,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({
    TaskResponseStatus.TIMEOUT,
    TaskResponseStatus.UNAVAILABLE,
})


def is_valid_agent_address(address: str | None) -> bool:
    """Validates that address is a non-empty 65-char uAgents address starting with agent1."""
    if not isinstance(address, str):
        return False
    address = address.strip()
    if not (address.startswith("agent1") and len(address) == 65):
        return False
    return is_valid_address(address)


class SpecialistExecutionError(Exception):
    """Raised when a specialist invocation fails or returns an error status."""

    def __init__(
        self,
        task_type: str,
        status: TaskResponseStatus | str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        self.task_type = task_type
        self.status = status
        self.error_code = error_code
        self.error_detail = error_detail
        msg = f"Specialist task '{task_type}' failed with status '{status}'"
        if error_code:
            msg += f" (code: {error_code})"
        if error_detail:
            msg += f": {error_detail}"
        super().__init__(msg)


class SpecialistTransport:
    """Dispatches tasks to specialists either locally or remotely."""

    def __init__(
        self,
        *,
        mode: str = "local",
        sender_identity: str = "stockrescue-coordinator",
        recipient_addresses: dict[str, str] | None = None,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
        remote_caller: Callable[[str, TaskRequest], TaskResponse] | None = None,
    ) -> None:
        self.mode = mode.strip().casefold() if isinstance(mode, str) else "local"
        self.sender_identity = (
            sender_identity.strip()
            if isinstance(sender_identity, str) and sender_identity.strip()
            else "stockrescue-coordinator"
        )
        self.recipient_addresses = recipient_addresses or {}
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._remote_caller = remote_caller
        self._local_dispatchers: dict[str, SpecialistDispatcher] = {}

    def _get_local_dispatcher(self, role: str) -> SpecialistDispatcher:
        if role not in self._local_dispatchers:
            self._local_dispatchers[role] = SpecialistDispatcher(
                role=role,
                agent_name=f"local-{role}",
                allowed_senders={self.sender_identity},
            )
        return self._local_dispatchers[role]

    def invoke(
        self,
        task_type: str,
        payload: Any,
        *,
        workflow_id: str,
        trace_id: str,
        role: str,
        force_local: bool = False,
    ) -> dict[str, Any]:
        """Invokes a specialist task, returning validated result dictionary."""
        # 1. Deterministic Local Execution Path
        if self.mode == "local":
            logger.info("Executing specialist task '%s' for role '%s' in LOCAL mode", task_type, role)
            recipient = self.recipient_addresses.get(role) or f"local-{role}"
            request = TaskRequest.create(
                task_type=task_type,
                workflow_id=workflow_id,
                trace_id=trace_id,
                sender=self.sender_identity,
                recipient=recipient,
                payload=payload,
                timeout_seconds=self.timeout_seconds,
            )
            dispatcher = self._get_local_dispatcher(role)
            response = dispatcher.dispatch(self.sender_identity, request)
            return self._handle_response(request, response)

        # 2. Remote Agentverse Execution Path (Fail-closed)
        if self.mode == "agentverse":
            logger.info(
                "Executing specialist task '%s' for role '%s' in AGENTVERSE remote mode",
                task_type,
                role,
            )

            # Never silently fall back to local execution
            if self._remote_caller is None or not callable(self._remote_caller):
                logger.error(
                    "Agentverse remote execution failed: no functioning remote caller configured for role '%s'",
                    role,
                )
                raise SpecialistExecutionError(
                    task_type=task_type,
                    status=TaskResponseStatus.UNAVAILABLE,
                    error_code=TaskErrorCode.AGENT_UNAVAILABLE,
                    error_detail=f"Remote caller is missing or invalid for specialist '{role}' in agentverse mode",
                )

            recipient = self.recipient_addresses.get(role, "").strip()
            if not recipient or not is_valid_agent_address(recipient):
                logger.error(
                    "Agentverse remote execution failed: missing or invalid destination address for role '%s'",
                    role,
                )
                raise SpecialistExecutionError(
                    task_type=task_type,
                    status=TaskResponseStatus.INVALID_REQUEST,
                    error_code=TaskErrorCode.INVALID_PAYLOAD,
                    error_detail=f"Valid uAgents destination address required for role '{role}' in agentverse mode",
                )

            # Remote execution path with bounded retries
            attempts = 0
            last_error: Exception | None = None

            while attempts <= self.max_retries:
                attempts += 1
                request = TaskRequest.create(
                    task_type=task_type,
                    workflow_id=workflow_id,
                    trace_id=trace_id,
                    sender=self.sender_identity,
                    recipient=recipient,
                    payload=payload,
                    timeout_seconds=self.timeout_seconds,
                )

                try:
                    logger.info(
                        "Sending remote task %s (attempt %d/%d) to recipient %s",
                        task_type,
                        attempts,
                        self.max_retries + 1,
                        recipient,
                    )
                    response = self._remote_caller(recipient, request)
                    return self._handle_response(request, response)

                except SpecialistExecutionError as err:
                    last_error = err
                    if err.status in RETRYABLE_STATUSES and attempts <= self.max_retries:
                        logger.warning(
                            "Retryable failure on task %s (%s). Retrying...",
                            task_type,
                            err.status,
                        )
                        time.sleep(0.05 * attempts)
                        continue
                    raise

                except Exception as exc:
                    last_error = exc
                    if attempts <= self.max_retries:
                        logger.warning(
                            "Remote communication error on task %s: %s. Retrying...",
                            task_type,
                            exc,
                        )
                        time.sleep(0.05 * attempts)
                        continue
                    raise SpecialistExecutionError(
                        task_type=task_type,
                        status=TaskResponseStatus.UNAVAILABLE,
                        error_code=TaskErrorCode.AGENT_UNAVAILABLE,
                        error_detail=str(exc),
                    ) from exc

            if last_error:
                raise last_error
            raise SpecialistExecutionError(task_type, TaskResponseStatus.INTERNAL_ERROR)

        # 3. Unsupported Mode
        raise SpecialistExecutionError(
            task_type=task_type,
            status=TaskResponseStatus.INVALID_REQUEST,
            error_code=TaskErrorCode.INVALID_PAYLOAD,
            error_detail=f"Unsupported specialist transport mode: '{self.mode}'",
        )

    def _handle_response(self, request: TaskRequest, response: TaskResponse) -> dict[str, Any]:
        verify_response_matches_request(request, response)

        if response.status != TaskResponseStatus.OK:
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=response.status,
                error_code=response.error_code,
                error_detail=response.error_detail,
            )

        result = response.get_result()
        if result is None:
            raise SpecialistExecutionError(
                task_type=request.task_type,
                status=TaskResponseStatus.INTERNAL_ERROR,
                error_code=TaskErrorCode.INTERNAL_ERROR,
                error_detail="Task completed with OK status but empty result",
            )
        return result
