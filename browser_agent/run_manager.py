"""Dependency-free, in-memory lifecycle management for resumable agent runs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import uuid4

from .agent_loop import AgentPauseKind, AgentRunResult, AgentRunStatus
from .secret_store import (
    SecretField,
    SecretReference,
    TransientSecretStore,
)
from .secret_application import (
    SecretApplicationError,
    SecretApplicationResult,
    SecretFieldTarget,
    SecretFormApplierProtocol,
    SecretTargetSummary,
)

_ERROR_MESSAGE_LIMIT = 500


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_SECRET = "awaiting_secret"
    AWAITING_SECRET_APPLICATION = "awaiting_secret_application"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STEP_LIMIT_REACHED = "step_limit_reached"
    DECISION_SOURCE_EXHAUSTED = "decision_source_exhausted"


_TERMINAL_STATUSES = frozenset(
    {
        RunStatus.FINISHED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.STEP_LIMIT_REACHED,
        RunStatus.DECISION_SOURCE_EXHAUSTED,
    }
)


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    status: RunStatus
    version: int
    start_url: str
    task: str
    step_count: int
    question: str | None
    interaction_id: str | None
    final_result: str | None
    error_type: str | None
    error_message: str | None
    secret_fields: tuple[SecretField, ...] | None = None
    secret_targets: tuple[SecretTargetSummary, ...] | None = None


class RunManagerError(Exception):
    """Base class for caller-visible RunManager contract errors."""


class RunManagerClosedError(RunManagerError):
    """The manager no longer accepts mutating operations."""


class RunConstructionError(RunManagerError):
    """A run or manager cannot be constructed from the supplied input."""


class RunNotFoundError(RunManagerError):
    """No run exists for the supplied identifier."""


class RunConflictError(RunManagerError):
    """The requested operation conflicts with the run's current state."""


class RunIdempotencyConflictError(RunConflictError):
    """A request identifier was already used for a different request."""


class RunSessionProtocol(Protocol):
    async def start(self, task: str) -> AgentRunResult: ...

    async def respond(self, text: str) -> AgentRunResult: ...

    async def confirm(self, approved: bool) -> AgentRunResult: ...

    async def resume_after_secret_application(
        self, fields: tuple[SecretField, ...]
    ) -> AgentRunResult: ...


class RunSessionHandleProtocol(Protocol):
    session: RunSessionProtocol
    secret_applier: SecretFormApplierProtocol

    async def close(self) -> None: ...


class RunSessionFactoryProtocol(Protocol):
    async def create(
        self, start_url: str, task: str
    ) -> RunSessionHandleProtocol: ...


@dataclass(frozen=True)
class _RequestFingerprint:
    operation: str
    interaction_id: str | None = None
    payload: str | bool | None = field(default=None, repr=False)
    secret_digest: bytes | None = field(default=None, repr=False)


@dataclass
class _RunRecord:
    run_id: str
    start_url: str
    task: str
    status: RunStatus = RunStatus.QUEUED
    version: int = 1
    step_count: int = 0
    question: str | None = None
    interaction_id: str | None = None
    final_result: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    handle: RunSessionHandleProtocol | None = None
    session: RunSessionProtocol | None = None
    active_task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[bool] | None = None
    secret_cleanup_task: asyncio.Task[None] | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requests: dict[str, _RequestFingerprint] = field(default_factory=dict, repr=False)
    secret_fields: tuple[SecretField, ...] | None = None
    secret_reference: SecretReference | None = field(default=None, repr=False)
    secret_targets: tuple[SecretFieldTarget, ...] | None = field(default=None, repr=False)
    secret_applier: SecretFormApplierProtocol | None = field(default=None, repr=False)


def _required_string(value: object, name: str, error: type[RunManagerError]) -> str:
    if type(value) is not str or not value.strip():
        raise error(f"{name} must be a non-empty string")
    return value


def _safe_error(exc: BaseException) -> tuple[str, str]:
    message = str(exc).replace("\r", " ").replace("\n", " ")
    return type(exc).__name__[:100], message[:_ERROR_MESSAGE_LIMIT]


class RunManager:
    """Own independent, resumable sessions for one application process."""

    def __init__(
        self,
        factory: RunSessionFactoryProtocol,
        *,
        secret_store: TransientSecretStore,
    ) -> None:
        if factory is None or not callable(getattr(factory, "create", None)):
            raise RunConstructionError("factory must provide an async create method")
        required_store_methods = ("put", "consume", "discard", "discard_run", "close")
        if secret_store is None or any(
            not callable(getattr(secret_store, name, None))
            for name in required_store_methods
        ):
            raise RunConstructionError(
                "secret_store must provide the transient secret store async API"
            )
        self._factory = factory
        self._secret_store = secret_store
        self._hmac_key = bytearray(secrets.token_bytes(32))
        self._runs: dict[str, _RunRecord] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[bool] | None = None

    async def create_run(self, start_url: str, task: str) -> RunSnapshot:
        start_url = _required_string(
            start_url, "start_url", RunConstructionError
        )
        task = _required_string(task, "task", RunConstructionError)
        async with self._lock:
            self._ensure_open()
            run_id = uuid4().hex
            while run_id in self._runs:
                run_id = uuid4().hex
            record = _RunRecord(run_id, start_url, task)
            self._runs[run_id] = record
            operation = asyncio.create_task(self._start(record))
            record.active_task = operation
            return self._snapshot(record)

    async def get_run(self, run_id: str) -> RunSnapshot:
        run_id = _required_string(run_id, "run_id", RunManagerError)
        record = await self._find(run_id)
        async with record.lock:
            return self._snapshot(record)

    async def respond(
        self,
        run_id: str,
        interaction_id: str,
        request_id: str,
        text: str,
    ) -> RunSnapshot:
        run_id = _required_string(run_id, "run_id", RunManagerError)
        interaction_id = _required_string(
            interaction_id, "interaction_id", RunManagerError
        )
        request_id = _required_string(request_id, "request_id", RunManagerError)
        text = _required_string(text, "text", RunManagerError)
        return await self._resume(
            run_id,
            request_id,
            _RequestFingerprint("respond", interaction_id, text),
            RunStatus.AWAITING_USER,
        )

    async def confirm(
        self,
        run_id: str,
        interaction_id: str,
        request_id: str,
        approved: bool,
    ) -> RunSnapshot:
        run_id = _required_string(run_id, "run_id", RunManagerError)
        interaction_id = _required_string(
            interaction_id, "interaction_id", RunManagerError
        )
        request_id = _required_string(request_id, "request_id", RunManagerError)
        if type(approved) is not bool:
            raise RunManagerError("approved must be a bool")
        return await self._resume(
            run_id,
            request_id,
            _RequestFingerprint("confirm", interaction_id, approved),
            RunStatus.AWAITING_CONFIRMATION,
        )

    async def cancel(self, run_id: str, request_id: str) -> RunSnapshot:
        run_id = _required_string(run_id, "run_id", RunManagerError)
        request_id = _required_string(request_id, "request_id", RunManagerError)
        fingerprint = _RequestFingerprint("cancel")
        async with self._lock:
            self._ensure_open()
            record = self._runs.get(run_id)
            if record is None:
                raise RunNotFoundError(f"run not found: {run_id}")
            async with record.lock:
                prior = record.requests.get(request_id)
                if prior is not None:
                    self._check_fingerprint(prior, fingerprint)
                    return self._snapshot(record)
                if record.status in _TERMINAL_STATUSES:
                    raise RunConflictError("terminal run cannot be cancelled")
                record.requests[request_id] = fingerprint
                task = record.active_task
                record.active_task = None
                self._transition(record, RunStatus.CANCELLED)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_handle(record)
        await self._cleanup_secrets(record)
        async with record.lock:
            return self._snapshot(record)

    async def close(self) -> None:
        async with self._lock:
            close_task = self._close_task
            if close_task is None:
                self._closed = True
                records = tuple(self._runs.values())
                close_task = asyncio.create_task(self._run_close(records))
                self._close_task = close_task
        succeeded = await asyncio.shield(close_task)
        if not succeeded:
            raise RunManagerError("run manager close failed safely") from None

    async def _run_close(self, records: tuple[_RunRecord, ...]) -> bool:
        failed = False
        try:
            for record in records:
                try:
                    await self._shutdown_record(record)
                except Exception:
                    failed = True
            try:
                await self._secret_store.close()
            except Exception:
                failed = True
        finally:
            self._hmac_key[:] = b"\x00" * len(self._hmac_key)
        return not failed

    async def submit_secret(
        self,
        run_id: str,
        interaction_id: str,
        request_id: str,
        values: Mapping[SecretField, str],
    ) -> RunSnapshot:
        run_id = _required_string(run_id, "run_id", RunManagerError)
        interaction_id = _required_string(
            interaction_id, "interaction_id", RunManagerError
        )
        request_id = _required_string(request_id, "request_id", RunManagerError)
        copied_values = self._validate_secret_values(values)
        submitted_fields = tuple(sorted(copied_values, key=lambda item: item.value))
        fingerprint = _RequestFingerprint(
            "submit_secret",
            interaction_id,
            secret_digest=self._secret_fingerprint(
                interaction_id, copied_values
            ),
        )
        async with self._lock:
            self._ensure_open()
            record = self._runs.get(run_id)
            if record is None:
                raise RunNotFoundError(f"run not found: {run_id}")
            async with record.lock:
                prior = record.requests.get(request_id)
                if prior is not None:
                    self._check_fingerprint(prior, fingerprint)
                    return self._snapshot(record)
                if (
                    record.status is not RunStatus.AWAITING_SECRET
                    or record.interaction_id != interaction_id
                ):
                    raise RunConflictError(
                        "interaction does not match the current secret pause"
                    )
                if record.secret_fields != submitted_fields:
                    raise RunConflictError(
                        "submitted fields do not match the current secret pause"
                    )
                if (
                    record.session is None
                    or record.secret_applier is None
                    or record.secret_targets is None
                    or record.active_task is not None
                    or record.secret_reference is not None
                ):
                    raise RunConflictError("run is not ready for secret submission")
                reference: SecretReference | None = None
                storage_failed = False
                try:
                    reference = await self._secret_store.put(
                        run_id, interaction_id, copied_values
                    )
                except Exception:
                    storage_failed = True
                if storage_failed:
                    raise RunManagerError("secret storage failed safely") from None
                prior_question = record.question
                prior_interaction_id = record.interaction_id
                prior_status = record.status
                prior_version = record.version
                try:
                    record.secret_reference = reference
                    record.requests[request_id] = fingerprint
                    record.question = None
                    record.interaction_id = None
                    record.status = RunStatus.AWAITING_SECRET_APPLICATION
                    record.version += 1
                    accepted = self._snapshot(record)
                    operation = asyncio.create_task(self._apply_secret(record))
                    record.active_task = operation
                    return accepted
                except BaseException as startup_error:
                    record.requests.pop(request_id, None)
                    record.question = prior_question
                    record.interaction_id = prior_interaction_id
                    record.status = prior_status
                    record.version = prior_version
                    record.secret_reference = None
                    discard_failed = False
                    try:
                        await self._secret_store.discard(reference)
                    except Exception:
                        discard_failed = True
                    if discard_failed:
                        raise RunManagerError(
                            "secret discard failed safely"
                        ) from None
                    if isinstance(
                        startup_error,
                        (asyncio.CancelledError, KeyboardInterrupt, SystemExit),
                    ):
                        raise
                    raise RunManagerError(
                        "secret application startup failed safely"
                    ) from None

    async def _resume(
        self,
        run_id: str,
        request_id: str,
        fingerprint: _RequestFingerprint,
        expected_status: RunStatus,
    ) -> RunSnapshot:
        async with self._lock:
            self._ensure_open()
            record = self._runs.get(run_id)
            if record is None:
                raise RunNotFoundError(f"run not found: {run_id}")
            async with record.lock:
                prior = record.requests.get(request_id)
                if prior is not None:
                    self._check_fingerprint(prior, fingerprint)
                    return self._snapshot(record)
                if (
                    record.status is not expected_status
                    or record.interaction_id != fingerprint.interaction_id
                ):
                    raise RunConflictError(
                        "interaction does not match the current pause"
                    )
                if record.session is None or record.active_task is not None:
                    raise RunConflictError("run is not ready to resume")
                record.requests[request_id] = fingerprint
                record.question = None
                record.interaction_id = None
                record.status = RunStatus.RUNNING
                record.version += 1
                operation = asyncio.create_task(
                    self._run_resume(record, fingerprint)
                )
                record.active_task = operation
                return self._snapshot(record)

    async def _start(self, record: _RunRecord) -> None:
        local_handle: RunSessionHandleProtocol | None = None
        try:
            async with record.lock:
                if record.status is not RunStatus.QUEUED:
                    return
                record.status = RunStatus.RUNNING
                record.version += 1
            local_handle = await self._factory.create(
                record.start_url, record.task
            )
            session, applier = self._validate_handle(local_handle)
            async with record.lock:
                if record.status is RunStatus.CANCELLED:
                    return
                record.handle = local_handle
                record.session = session
                record.secret_applier = applier
                local_handle = None
            result = await session.start(record.task)
            await self._publish_result(record, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._publish_failure(record, exc)
        finally:
            if local_handle is not None:
                await self._close_local_handle(record, local_handle)
            await self._clear_current_task(record)

    async def _run_resume(
        self, record: _RunRecord, fingerprint: _RequestFingerprint
    ) -> None:
        try:
            async with record.lock:
                session = record.session
            if session is None:
                raise RuntimeError("run session is unavailable")
            if fingerprint.operation == "respond":
                result = await session.respond(str(fingerprint.payload))
            elif fingerprint.operation == "confirm":
                result = await session.confirm(fingerprint.payload)  # type: ignore[arg-type]
            else:
                raise RuntimeError("unsupported resume operation")
            await self._publish_result(record, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._publish_failure(record, exc)
        finally:
            await self._clear_current_task(record)

    async def _apply_secret(self, record: _RunRecord) -> None:
        values: dict[SecretField, str] = {}
        try:
            async with record.lock:
                reference = record.secret_reference
                targets = record.secret_targets
                fields = record.secret_fields
                session = record.session
                applier = record.secret_applier
            if reference is None or targets is None or fields is None or session is None or applier is None:
                raise SecretApplicationError("Secret application failed safely.")
            try:
                consumed = await self._secret_store.consume(reference)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise SecretApplicationError("Secret application failed safely.") from None
            values.update(consumed)
            if isinstance(consumed, dict):
                consumed.clear()
            async with record.lock:
                if record.secret_reference == reference:
                    record.secret_reference = None
            result = await applier.apply(targets, values)
            if type(result) is not SecretApplicationResult or result.fields != fields:
                raise SecretApplicationError("Secret application failed safely.")
            resumed = await session.resume_after_secret_application(fields)
            self._translate(resumed)
            await self._publish_result(record, resumed)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._publish_failure(
                record, SecretApplicationError("Secret application failed safely.")
            )
        finally:
            values.clear()
            await self._clear_current_task(record)

    async def _publish_result(
        self, record: _RunRecord, result: AgentRunResult
    ) -> None:
        try:
            status, question, final_result, step_count, secret_fields, secret_targets = self._translate(result)
        except Exception as exc:
            await self._publish_failure(record, exc)
            return
        async with record.lock:
            if record.status is RunStatus.CANCELLED:
                return
            record.status = status
            record.version += 1
            record.step_count = step_count
            record.question = question
            record.interaction_id = (
                uuid4().hex
                if status
                in (
                    RunStatus.AWAITING_USER,
                    RunStatus.AWAITING_CONFIRMATION,
                    RunStatus.AWAITING_SECRET,
                )
                else None
            )
            record.final_result = final_result
            record.secret_fields = secret_fields
            record.secret_targets = secret_targets
            record.error_type = None
            record.error_message = None
        if status in _TERMINAL_STATUSES:
            await self._close_handle(record)
            await self._cleanup_secrets(record)

    async def _publish_failure(
        self, record: _RunRecord, exc: BaseException
    ) -> None:
        error_type, error_message = _safe_error(exc)
        async with record.lock:
            if record.status is RunStatus.CANCELLED:
                return
            record.status = RunStatus.FAILED
            record.version += 1
            record.question = None
            record.interaction_id = None
            record.final_result = None
            record.secret_fields = None
            record.secret_targets = None
            record.secret_reference = None
            record.error_type = error_type
            record.error_message = error_message
        await self._close_handle(record)
        await self._cleanup_secrets(record)

    async def _close_handle(self, record: _RunRecord) -> bool:
        async with record.lock:
            cleanup_task = record.cleanup_task
            if cleanup_task is None and record.handle is None:
                return True
            if cleanup_task is None:
                handle = record.handle
                record.handle = None
                record.session = None
                record.secret_applier = None
                cleanup_task = asyncio.create_task(
                    self._run_handle_cleanup(record, handle)
                )
                record.cleanup_task = cleanup_task
        return await asyncio.shield(cleanup_task)

    async def _run_handle_cleanup(
        self,
        record: _RunRecord,
        handle: RunSessionHandleProtocol,
    ) -> bool:
        try:
            await handle.close()
        except Exception as exc:
            await self._record_cleanup_failure(record, exc)
            return False
        return True

    async def _close_local_handle(
        self, record: _RunRecord, handle: RunSessionHandleProtocol
    ) -> None:
        try:
            await handle.close()
        except Exception as exc:
            await self._record_cleanup_failure(record, exc)

    async def _record_cleanup_failure(
        self, record: _RunRecord, exc: BaseException
    ) -> None:
        error_type, error_message = _safe_error(exc)
        async with record.lock:
            before = (
                record.status,
                record.question,
                record.interaction_id,
                record.final_result,
                record.error_type,
                record.error_message,
            )
            if record.status is not RunStatus.CANCELLED:
                record.status = RunStatus.FAILED
                record.question = None
                record.interaction_id = None
                record.final_result = None
                record.secret_fields = None
                record.secret_targets = None
                record.secret_reference = None
            record.error_type = error_type
            record.error_message = error_message
            after = (
                record.status,
                record.question,
                record.interaction_id,
                record.final_result,
                record.error_type,
                record.error_message,
            )
            if after != before:
                record.version += 1

    async def _shutdown_record(self, record: _RunRecord) -> None:
        async with record.lock:
            task = record.active_task
            terminal = record.status in _TERMINAL_STATUSES
            if not terminal:
                record.active_task = None
                self._transition(record, RunStatus.CANCELLED)
        current = asyncio.current_task()
        if task is not None and task is not current:
            if not terminal:
                task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise
        handle_cleanup_succeeded = await self._close_handle(record)
        await self._cleanup_secrets(record)
        if not handle_cleanup_succeeded:
            raise RunManagerError("run cleanup failed safely") from None

    async def _cleanup_secrets(self, record: _RunRecord) -> None:
        async with record.lock:
            record.secret_reference = None
            if record.status in _TERMINAL_STATUSES:
                record.secret_fields = None
                record.secret_targets = None
            cleanup_task = record.secret_cleanup_task
            if cleanup_task is None:
                cleanup_task = asyncio.create_task(
                    self._run_secret_cleanup(record.run_id)
                )
                record.secret_cleanup_task = cleanup_task
        await asyncio.shield(cleanup_task)

    async def _run_secret_cleanup(self, run_id: str) -> None:
        cleanup_failed = False
        try:
            await self._secret_store.discard_run(run_id)
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            raise RunManagerError("secret cleanup failed safely") from None

    async def _clear_current_task(self, record: _RunRecord) -> None:
        current = asyncio.current_task()
        async with record.lock:
            if record.active_task is current:
                record.active_task = None

    async def _find(self, run_id: str) -> _RunRecord:
        async with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return record

    def _ensure_open(self) -> None:
        if self._closed:
            raise RunManagerClosedError("run manager is closed")

    @staticmethod
    def _validate_handle(
        handle: object,
    ) -> tuple[RunSessionProtocol, SecretFormApplierProtocol]:
        if handle is None or not callable(getattr(handle, "close", None)):
            raise TypeError("factory returned an invalid session handle")
        session = getattr(handle, "session", None)
        if session is None or any(
            not callable(getattr(session, name, None))
            for name in ("start", "respond", "confirm", "resume_after_secret_application")
        ):
            raise TypeError("factory handle has an invalid session")
        applier = getattr(handle, "secret_applier", None)
        if applier is None or not callable(getattr(applier, "apply", None)):
            raise TypeError("factory handle has an invalid secret applier")
        return session, applier

    @staticmethod
    def _translate(
        result: AgentRunResult,
    ) -> tuple[RunStatus, str | None, str | None, int, tuple[SecretField, ...] | None, tuple[SecretFieldTarget, ...] | None]:
        if not isinstance(result, AgentRunResult) or type(result.steps) is not tuple:
            raise TypeError("session returned an invalid AgentRunResult")
        step_count = len(result.steps)
        if result.status is AgentRunStatus.AWAITING_USER:
            if (
                type(result.question) is not str
                or not result.question.strip()
                or result.final_result is not None
            ):
                raise ValueError("session returned an inconsistent pause result")
            if result.pause_kind is AgentPauseKind.USER_INPUT:
                if result.pending_confirmation is not None or result.secret_fields is not None or result.secret_targets is not None:
                    raise ValueError(
                        "session returned an inconsistent pause result"
                    )
                status = RunStatus.AWAITING_USER
            elif result.pause_kind is AgentPauseKind.CONFIRMATION:
                if result.pending_confirmation is None or result.secret_fields is not None or result.secret_targets is not None:
                    raise ValueError(
                        "session returned an inconsistent pause result"
                    )
                status = RunStatus.AWAITING_CONFIRMATION
            elif result.pause_kind is AgentPauseKind.SECRET:
                fields = result.secret_fields
                targets = result.secret_targets
                if (
                    type(fields) is not tuple
                    or not fields
                    or any(type(item) is not SecretField for item in fields)
                    or fields != tuple(sorted(set(fields), key=lambda item: item.value))
                    or result.pending_confirmation is not None
                    or type(targets) is not tuple
                    or not targets
                    or any(type(item) is not SecretFieldTarget for item in targets)
                    or tuple(item.field for item in targets) != fields
                    or len({item.ref for item in targets}) != len(targets)
                ):
                    raise ValueError("session returned an inconsistent secret pause result")
                status = RunStatus.AWAITING_SECRET
            else:
                raise ValueError("session returned an inconsistent pause result")
            return status, result.question, None, step_count, result.secret_fields, result.secret_targets
        if (
            result.question is not None
            or result.pause_kind is not None
            or result.pending_confirmation is not None
            or result.secret_fields is not None
            or result.secret_targets is not None
        ):
            raise ValueError("session returned inconsistent terminal fields")
        if result.status is AgentRunStatus.FINISHED:
            if (
                type(result.final_result) is not str
                or not result.final_result.strip()
            ):
                raise ValueError("finished result must contain final_result")
            return RunStatus.FINISHED, None, result.final_result, step_count, None, None
        if result.final_result is not None:
            raise ValueError("non-finished result cannot contain final_result")
        mapping = {
            AgentRunStatus.STEP_LIMIT_REACHED: RunStatus.STEP_LIMIT_REACHED,
            AgentRunStatus.DECISION_SOURCE_EXHAUSTED:
                RunStatus.DECISION_SOURCE_EXHAUSTED,
        }
        try:
            return mapping[result.status], None, None, step_count, None, None
        except (KeyError, TypeError) as exc:
            raise ValueError("session returned an unsupported status") from exc

    @staticmethod
    def _transition(record: _RunRecord, status: RunStatus) -> None:
        record.status = status
        record.version += 1
        record.question = None
        record.interaction_id = None
        record.final_result = None
        record.secret_fields = None
        record.secret_targets = None
        record.secret_reference = None

    @staticmethod
    def _snapshot(record: _RunRecord) -> RunSnapshot:
        return RunSnapshot(
            run_id=record.run_id,
            status=record.status,
            version=record.version,
            start_url=record.start_url,
            task=record.task,
            step_count=record.step_count,
            question=record.question,
            interaction_id=record.interaction_id,
            final_result=record.final_result,
            error_type=record.error_type,
            error_message=record.error_message,
            secret_fields=record.secret_fields,
            secret_targets=(
                tuple(SecretTargetSummary.from_target(item) for item in record.secret_targets)
                if record.secret_targets is not None else None
            ),
        )

    @staticmethod
    def _validate_secret_values(
        values: object,
    ) -> dict[SecretField, str]:
        if not isinstance(values, Mapping) or not values:
            raise RunManagerError("values must be a non-empty mapping")
        copied: dict[SecretField, str] = {}
        for key, value in values.items():
            if type(key) is not SecretField:
                raise RunManagerError("secret field key is invalid")
            if type(value) is not str or not value.strip():
                raise RunManagerError("secret field value must be a non-empty string")
            copied[key] = value
        return copied

    def _secret_fingerprint(
        self,
        interaction_id: str,
        values: Mapping[SecretField, str],
    ) -> bytes:
        digest = hmac.new(bytes(self._hmac_key), digestmod=hashlib.sha256)
        for part in (b"submit_secret", interaction_id.encode("utf-8")):
            digest.update(struct.pack("!Q", len(part)))
            digest.update(part)
        for secret_field in sorted(values, key=lambda item: item.value):
            for part in (
                secret_field.value.encode("utf-8"),
                values[secret_field].encode("utf-8"),
            ):
                digest.update(struct.pack("!Q", len(part)))
                digest.update(part)
        return digest.digest()

    @staticmethod
    def _check_fingerprint(
        prior: _RequestFingerprint, current: _RequestFingerprint
    ) -> None:
        if prior != current:
            raise RunIdempotencyConflictError(
                "request_id was already used for a different request"
            )
