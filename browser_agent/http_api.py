"""FastAPI boundary for the in-memory RunManager public API."""

from __future__ import annotations

from contextlib import asynccontextmanager
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictStr, field_validator

from .run_manager import (
    RunConflictError,
    RunConstructionError,
    RunIdempotencyConflictError,
    RunManager,
    RunManagerClosedError,
    RunManagerError,
    RunFileNotFoundError,
    RunNotFoundError,
    RunSnapshot,
    RunStatus,
    RunAuditEvent,
    RunAuditKind,
    RunAuditStatus,
)
from .downloads import DownloadMetadata
from .secret_store import SecretField
from .secret_application import SecretTargetSummary
from .operator_ui import (
    CONTENT_SECURITY_POLICY,
    OPERATOR_CSS,
    OPERATOR_HTML,
    OPERATOR_JAVASCRIPT,
    SECURITY_HEADERS,
)

_ERROR_MESSAGE_LIMIT = 500


class _StrictHttpModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _non_empty_string(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty string")
    return value


class CreateRunRequest(_StrictHttpModel):
    start_url: StrictStr
    task: StrictStr

    _validate_start_url = field_validator("start_url")(_non_empty_string)
    _validate_task = field_validator("task")(_non_empty_string)


class CancelRunRequest(_StrictHttpModel):
    request_id: StrictStr

    _validate_request_id = field_validator("request_id")(_non_empty_string)


class RunInteractionType(str, Enum):
    USER_INPUT = "user_input"
    CONFIRMATION = "confirmation"
    SECRET = "secret"


class _InteractionRequest(_StrictHttpModel):
    request_id: StrictStr
    interaction_id: StrictStr

    _validate_request_id = field_validator("request_id")(_non_empty_string)
    _validate_interaction_id = field_validator("interaction_id")(
        _non_empty_string
    )


class UserInputRunResponseRequest(_InteractionRequest):
    type: Literal[RunInteractionType.USER_INPUT]
    text: StrictStr

    _validate_text = field_validator("text")(_non_empty_string)


class ConfirmationRunResponseRequest(_InteractionRequest):
    type: Literal[RunInteractionType.CONFIRMATION]
    approved: StrictBool


class SecretRunResponseRequest(_InteractionRequest):
    type: Literal[RunInteractionType.SECRET]
    values: dict[StrictStr, SecretStr]

    @field_validator("values")
    @classmethod
    def _validate_values(
        cls, values: dict[str, SecretStr]
    ) -> dict[str, SecretStr]:
        if not values:
            raise ValueError("values must be non-empty")
        if any(key not in {field.value for field in SecretField} for key in values):
            raise ValueError("secret field key is invalid")
        if any(not value.get_secret_value().strip() for value in values.values()):
            raise ValueError("secret values must be non-empty strings")
        return values


RunResponseRequest: TypeAlias = Annotated[
    UserInputRunResponseRequest | ConfirmationRunResponseRequest | SecretRunResponseRequest,
    Field(discriminator="type"),
]


class RunHttpSnapshotError(_StrictHttpModel):
    type: str
    message: str


class RunHttpAuditEvent(_StrictHttpModel):
    sequence: int
    kind: RunAuditKind
    status: RunAuditStatus
    summary: str
    step_number: int | None
    tool_name: str | None
    replay_of_step_number: int | None


class RunHttpResponse(_StrictHttpModel):
    run_id: str
    status: RunStatus
    version: int
    start_url: str
    task: str
    step_count: int
    question: str | None
    interaction_id: str | None
    final_result: str | None
    error: RunHttpSnapshotError | None
    secret_fields: tuple[SecretField, ...] | None
    secret_targets: tuple[SecretTargetSummary, ...] | None
    files: tuple[DownloadMetadata, ...]
    audit_events: tuple[RunHttpAuditEvent, ...]


class HttpErrorBody(_StrictHttpModel):
    code: str
    message: str


class HttpErrorResponse(_StrictHttpModel):
    error: HttpErrorBody


def snapshot_to_http_response(snapshot: RunSnapshot) -> RunHttpResponse:
    """Convert only the public, immutable snapshot fields to the HTTP schema."""
    if not isinstance(snapshot, RunSnapshot):
        raise TypeError("run manager must return a RunSnapshot")
    if (snapshot.error_type is None) != (snapshot.error_message is None):
        raise ValueError("snapshot error fields must both be present or absent")
    error = None
    if snapshot.error_type is not None and snapshot.error_message is not None:
        error = RunHttpSnapshotError(
            type=snapshot.error_type,
            message=snapshot.error_message,
        )
    audit_events = tuple(
        RunHttpAuditEvent(
            sequence=event.sequence,
            kind=event.kind,
            status=event.status,
            summary=event.summary,
            step_number=event.step_number,
            tool_name=event.tool_name,
            replay_of_step_number=event.replay_of_step_number,
        )
        for event in snapshot.audit_events
        if isinstance(event, RunAuditEvent)
    )
    if len(audit_events) != len(snapshot.audit_events):
        raise TypeError("snapshot audit events must contain RunAuditEvent values")
    return RunHttpResponse(
        run_id=snapshot.run_id,
        status=snapshot.status,
        version=snapshot.version,
        start_url=snapshot.start_url,
        task=snapshot.task,
        step_count=snapshot.step_count,
        question=snapshot.question,
        interaction_id=snapshot.interaction_id,
        final_result=snapshot.final_result,
        error=error,
        secret_fields=snapshot.secret_fields,
        secret_targets=snapshot.secret_targets,
        files=snapshot.files,
        audit_events=audit_events,
    )


def _safe_known_error_message(exc: Exception) -> str:
    message = str(exc).replace("\r", " ").replace("\n", " ")
    return message[:_ERROR_MESSAGE_LIMIT]


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    payload = HttpErrorResponse(error=HttpErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def create_http_app(run_manager: RunManager) -> FastAPI:
    """Create an HTTP application around one externally owned RunManager."""
    required_methods = (
        "create_run", "get_run", "get_download", "respond", "confirm", "submit_secret", "cancel", "close"
    )
    if run_manager is None or any(
        not callable(getattr(run_manager, method, None))
        for method in required_methods
    ):
        raise TypeError("run_manager must provide the RunManager public API")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.run_manager.close()

    app = FastAPI(lifespan=lifespan)
    app.state.run_manager = run_manager

    async def handle_validation_error(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "Request validation failed.",
        )

    app.add_exception_handler(RequestValidationError, handle_validation_error)

    error_mappings = (
        (RunFileNotFoundError, status.HTTP_404_NOT_FOUND, "run_file_not_found"),
        (RunNotFoundError, status.HTTP_404_NOT_FOUND, "run_not_found"),
        (
            RunIdempotencyConflictError,
            status.HTTP_409_CONFLICT,
            "idempotency_conflict",
        ),
        (RunConflictError, status.HTTP_409_CONFLICT, "run_conflict"),
        (
            RunManagerClosedError,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "run_manager_closed",
        ),
        (
            RunConstructionError,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "run_construction_error",
        ),
        (RunManagerError, status.HTTP_400_BAD_REQUEST, "run_manager_error"),
    )
    for exception_type, status_code, code in error_mappings:
        async def handler(
            _request: Request,
            exc: Exception,
            *,
            _status_code: int = status_code,
            _code: str = code,
        ) -> JSONResponse:
            return _error_response(
                _status_code, _code, _safe_known_error_message(exc)
            )

        app.add_exception_handler(exception_type, handler)

    async def handle_unexpected_error(
        _request: Request, _exc: Exception
    ) -> JSONResponse:
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Internal server error.",
        )

    app.add_exception_handler(Exception, handle_unexpected_error)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def operator_page() -> HTMLResponse:
        headers = dict(SECURITY_HEADERS)
        headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return HTMLResponse(OPERATOR_HTML, headers=headers)

    @app.get("/operator.js", include_in_schema=False)
    async def operator_javascript() -> Response:
        return Response(
            OPERATOR_JAVASCRIPT,
            media_type="application/javascript",
            headers=dict(SECURITY_HEADERS),
        )

    @app.get("/operator.css", include_in_schema=False)
    async def operator_stylesheet() -> Response:
        return Response(
            OPERATOR_CSS, media_type="text/css", headers=dict(SECURITY_HEADERS)
        )

    @app.post(
        "/runs",
        response_model=RunHttpResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_run(request: CreateRunRequest) -> RunHttpResponse:
        snapshot = await app.state.run_manager.create_run(
            request.start_url, request.task
        )
        return snapshot_to_http_response(snapshot)

    @app.get(
        "/runs/{run_id}",
        response_model=RunHttpResponse,
        status_code=status.HTTP_200_OK,
    )
    async def get_run(run_id: str) -> RunHttpResponse:
        snapshot = await app.state.run_manager.get_run(run_id)
        return snapshot_to_http_response(snapshot)

    @app.get(
        "/runs/{run_id}/files/{file_id}",
        response_class=FileResponse,
        status_code=status.HTTP_200_OK,
    )
    async def get_download(run_id: str, file_id: str) -> FileResponse:
        download = await app.state.run_manager.get_download(run_id, file_id)
        return FileResponse(
            path=download.path,
            filename=download.metadata.filename,
            media_type="application/octet-stream",
        )

    @app.post(
        "/runs/{run_id}/responses",
        response_model=RunHttpResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def respond(
        run_id: str, request: RunResponseRequest
    ) -> RunHttpResponse:
        if isinstance(request, UserInputRunResponseRequest):
            snapshot = await app.state.run_manager.respond(
                run_id,
                request.interaction_id,
                request.request_id,
                request.text,
            )
        elif isinstance(request, ConfirmationRunResponseRequest):
            snapshot = await app.state.run_manager.confirm(
                run_id,
                request.interaction_id,
                request.request_id,
                request.approved,
            )
        else:
            values = {
                SecretField(field): value.get_secret_value()
                for field, value in request.values.items()
            }
            snapshot = await app.state.run_manager.submit_secret(
                run_id,
                request.interaction_id,
                request.request_id,
                values,
            )
        return snapshot_to_http_response(snapshot)

    @app.post(
        "/runs/{run_id}/cancel",
        response_model=RunHttpResponse,
        status_code=status.HTTP_200_OK,
    )
    async def cancel(
        run_id: str, request: CancelRunRequest
    ) -> RunHttpResponse:
        snapshot = await app.state.run_manager.cancel(
            run_id, request.request_id
        )
        return snapshot_to_http_response(snapshot)

    return app
