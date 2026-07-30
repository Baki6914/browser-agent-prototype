"""Dependency-free, in-memory lifecycle management for resumable agent runs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
from uuid import uuid4

from .agent_loop import AgentPauseKind, AgentRunResult, AgentRunStatus

_ERROR_MESSAGE_LIMIT = 500


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
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


class RunSessionHandleProtocol(Protocol):
    session: RunSessionProtocol

    async def close(self) -> None: ...


class RunSessionFactoryProtocol(Protocol):
    async def create(
        self, start_url: str, task: str
    ) -> RunSessionHandleProtocol: ...


@dataclass(frozen=True)
class _RequestFingerprint:
    operation: str
    interaction_id: str | None = None
    payload: str | bool | None = None


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
    cleanup_task: asyncio.Task[None] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    requests: dict[str, _RequestFingerprint] = field(default_factory=dict)


def _required_string(value: object, name: str, error: type[RunManagerError]) -> str:
    if type(value) is not str or not value.strip():
        raise error(f"{name} must be a non-empty string")
    return value


def _safe_error(exc: BaseException) -> tuple[str, str]:
    message = str(exc).replace("\r", " ").replace("\n", " ")
    return type(exc).__name__[:100], message[:_ERROR_MESSAGE_LIMIT]


class RunManager:
    """Own independent, resumable sessions for one application process."""

    def __init__(self, factory: RunSessionFactoryProtocol) -> None:
        if factory is None or not callable(getattr(factory, "create", None)):
            raise RunConstructionError("factory must provide an async create method")
        self._factory = factory
        self._runs: dict[str, _RunRecord] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_complete = asyncio.Event()

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
        async with record.lock:
            return self._snapshot(record)

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                wait_for_close = True
                records: tuple[_RunRecord, ...] = ()
            else:
                self._closed = True
                wait_for_close = False
                records = tuple(self._runs.values())
        if wait_for_close:
            await self._close_complete.wait()
            return
        try:
            for record in records:
                await self._shutdown_record(record)
        finally:
            self._close_complete.set()

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
            session = self._validate_handle(local_handle)
            async with record.lock:
                if record.status is RunStatus.CANCELLED:
                    return
                record.handle = local_handle
                record.session = session
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

    async def _publish_result(
        self, record: _RunRecord, result: AgentRunResult
    ) -> None:
        try:
            status, question, final_result, step_count = self._translate(result)
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
                in (RunStatus.AWAITING_USER, RunStatus.AWAITING_CONFIRMATION)
                else None
            )
            record.final_result = final_result
            record.error_type = None
            record.error_message = None
        if status in _TERMINAL_STATUSES:
            await self._close_handle(record)

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
            record.error_type = error_type
            record.error_message = error_message
        await self._close_handle(record)

    async def _close_handle(self, record: _RunRecord) -> None:
        async with record.lock:
            cleanup_task = record.cleanup_task
            if cleanup_task is None and record.handle is None:
                return
            if cleanup_task is None:
                handle = record.handle
                record.handle = None
                record.session = None
                cleanup_task = asyncio.create_task(
                    self._run_handle_cleanup(record, handle)
                )
                record.cleanup_task = cleanup_task
        await asyncio.shield(cleanup_task)

    async def _run_handle_cleanup(
        self,
        record: _RunRecord,
        handle: RunSessionHandleProtocol,
    ) -> None:
        try:
            await handle.close()
        except Exception as exc:
            await self._record_cleanup_failure(record, exc)

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
                pass
        await self._close_handle(record)

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
    ) -> RunSessionProtocol:
        if handle is None or not callable(getattr(handle, "close", None)):
            raise TypeError("factory returned an invalid session handle")
        session = getattr(handle, "session", None)
        if session is None or any(
            not callable(getattr(session, name, None))
            for name in ("start", "respond", "confirm")
        ):
            raise TypeError("factory handle has an invalid session")
        return session

    @staticmethod
    def _translate(
        result: AgentRunResult,
    ) -> tuple[RunStatus, str | None, str | None, int]:
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
                if result.pending_confirmation is not None:
                    raise ValueError(
                        "session returned an inconsistent pause result"
                    )
                status = RunStatus.AWAITING_USER
            elif result.pause_kind is AgentPauseKind.CONFIRMATION:
                if result.pending_confirmation is None:
                    raise ValueError(
                        "session returned an inconsistent pause result"
                    )
                status = RunStatus.AWAITING_CONFIRMATION
            else:
                raise ValueError("session returned an inconsistent pause result")
            return status, result.question, None, step_count
        if (
            result.question is not None
            or result.pause_kind is not None
            or result.pending_confirmation is not None
        ):
            raise ValueError("session returned inconsistent terminal fields")
        if result.status is AgentRunStatus.FINISHED:
            if (
                type(result.final_result) is not str
                or not result.final_result.strip()
            ):
                raise ValueError("finished result must contain final_result")
            return RunStatus.FINISHED, None, result.final_result, step_count
        if result.final_result is not None:
            raise ValueError("non-finished result cannot contain final_result")
        mapping = {
            AgentRunStatus.STEP_LIMIT_REACHED: RunStatus.STEP_LIMIT_REACHED,
            AgentRunStatus.DECISION_SOURCE_EXHAUSTED:
                RunStatus.DECISION_SOURCE_EXHAUSTED,
        }
        try:
            return mapping[result.status], None, None, step_count
        except (KeyError, TypeError) as exc:
            raise ValueError("session returned an unsupported status") from exc

    @staticmethod
    def _transition(record: _RunRecord, status: RunStatus) -> None:
        record.status = status
        record.version += 1
        record.question = None
        record.interaction_id = None
        record.final_result = None

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
        )

    @staticmethod
    def _check_fingerprint(
        prior: _RequestFingerprint, current: _RequestFingerprint
    ) -> None:
        if prior != current:
            raise RunIdempotencyConflictError(
                "request_id was already used for a different request"
            )
