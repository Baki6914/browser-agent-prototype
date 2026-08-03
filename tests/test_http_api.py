"""Deterministic, in-process tests for the FastAPI RunManager boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_agent import (
    CancelRunRequest,
    CreateRunRequest,
    DownloadFile,
    DownloadMetadata,
    RunConflictError,
    RunConstructionError,
    RunIdempotencyConflictError,
    RunManagerClosedError,
    RunManagerError,
    RunFileNotFoundError,
    RunNotFoundError,
    RunSnapshot,
    RunStatus,
    RunAuditEvent,
    RunAuditKind,
    RunAuditStatus,
    ProviderTraceSnapshot,
    SecretField,
    SecretTargetSummary,
    create_http_app,
)
from browser_agent.http_api import (
    ConfirmationRunResponseRequest,
    RunHttpResponse,
    UserInputRunResponseRequest,
    SecretRunResponseRequest,
    snapshot_to_http_response,
)


class TestClient:
    """Synchronous facade over HTTPX ASGITransport for this test module."""

    __test__ = False

    def __init__(self, app, *, raise_server_exceptions: bool = True) -> None:
        self.app = app
        self.raise_server_exceptions = raise_server_exceptions
        self._loop = asyncio.new_event_loop()
        self._lifespan = None
        self._client = None

    def __enter__(self):
        self._lifespan = self.app.router.lifespan_context(self.app)
        self._loop.run_until_complete(self._lifespan.__aenter__())
        transport = httpx.ASGITransport(
            app=self.app,
            raise_app_exceptions=self.raise_server_exceptions,
        )
        self._client = httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        )
        self._loop.run_until_complete(self._client.__aenter__())
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        try:
            if self._client is not None:
                self._loop.run_until_complete(self._client.__aexit__(
                    _type, _value, _traceback
                ))
            if self._lifespan is not None:
                self._loop.run_until_complete(self._lifespan.__aexit__(
                    _type, _value, _traceback
                ))
        finally:
            self._loop.close()

    def get(self, path: str) -> httpx.Response:
        return self._loop.run_until_complete(self._request("GET", path))

    def post(self, path: str, *, json: object) -> httpx.Response:
        return self._loop.run_until_complete(self._request("POST", path, json=json))

    async def _request(
        self, method: str, path: str, *, json: object | None = None
    ) -> httpx.Response:
        if self._client is None:
            raise RuntimeError("test client is not entered")
        return await self._client.request(method, path, json=json)


def snapshot(
    *,
    status: RunStatus = RunStatus.QUEUED,
    question: str | None = None,
    interaction_id: str | None = None,
    final_result: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    secret_fields: tuple[SecretField, ...] | None = None,
    secret_targets: tuple[SecretTargetSummary, ...] | None = None,
    files: tuple[DownloadMetadata, ...] = (),
    audit_events: tuple[RunAuditEvent, ...] = (),
    start_url: str | None = " https://example.com ",
) -> RunSnapshot:
    return RunSnapshot(
        run_id="run-1",
        status=status,
        version=1,
        start_url=start_url,
        task=" Find the report ",
        step_count=2,
        question=question,
        interaction_id=interaction_id,
        final_result=final_result,
        error_type=error_type,
        error_message=error_message,
        secret_fields=secret_fields,
        secret_targets=secret_targets,
        files=files,
        audit_events=audit_events,
    )


class FakeRunManager:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.result = snapshot()
        self.error: Exception | None = None
        self.close_count = 0
        self.close_completed = False
        self.download = DownloadFile(
            DownloadMetadata("file-1", "report.pdf", 7), Path("/tmp/report.pdf")
        )

    def _return_or_raise(self, call: tuple[object, ...]) -> RunSnapshot:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return self.result

    async def create_run(self, start_url: str | None, task: str) -> RunSnapshot:
        return self._return_or_raise(("create_run", start_url, task))

    async def get_run(self, run_id: str) -> RunSnapshot:
        return self._return_or_raise(("get_run", run_id))

    async def get_provider_traces(self, run_id: str) -> ProviderTraceSnapshot:
        self.calls.append(("get_provider_traces", run_id))
        if self.error is not None:
            raise self.error
        return ProviderTraceSnapshot(False, ())

    async def get_download(self, run_id: str, file_id: str) -> DownloadFile:
        self.calls.append(("get_download", run_id, file_id))
        if self.error is not None:
            raise self.error
        return self.download

    async def respond(
        self, run_id: str, interaction_id: str, request_id: str, text: str
    ) -> RunSnapshot:
        return self._return_or_raise(
            ("respond", run_id, interaction_id, request_id, text)
        )

    async def confirm(
        self,
        run_id: str,
        interaction_id: str,
        request_id: str,
        approved: bool,
    ) -> RunSnapshot:
        return self._return_or_raise(
            ("confirm", run_id, interaction_id, request_id, approved)
        )

    async def resolve_completion(
        self, run_id: str, interaction_id: str, request_id: str,
        approved: bool, feedback: str | None = None,
    ) -> RunSnapshot:
        return self._return_or_raise(
            ("completion", run_id, interaction_id, request_id, approved, feedback)
        )

    async def submit_secret(
        self, run_id: str, interaction_id: str, request_id: str,
        values: dict[SecretField, str],
    ) -> RunSnapshot:
        return self._return_or_raise(
            ("submit_secret", run_id, interaction_id, request_id, values)
        )

    async def cancel(self, run_id: str, request_id: str) -> RunSnapshot:
        return self._return_or_raise(("cancel", run_id, request_id))

    async def close(self) -> None:
        self.close_count += 1
        self.close_completed = True


class ConstructionTests(unittest.TestCase):
    def test_retains_exact_manager_without_calling_it(self) -> None:
        manager = FakeRunManager()
        app = create_http_app(manager)  # type: ignore[arg-type]
        self.assertIs(app.state.run_manager, manager)
        self.assertEqual(manager.calls, [])
        self.assertEqual(manager.close_count, 0)

    def test_invalid_manager_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            create_http_app(object())  # type: ignore[arg-type]

    def test_manager_without_get_download_is_rejected(self) -> None:
        manager = FakeRunManager()
        manager.get_download = None  # type: ignore[method-assign]
        with self.assertRaises(TypeError):
            create_http_app(manager)  # type: ignore[arg-type]

    def test_models_forbid_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            CreateRunRequest(start_url="url", task="task", extra="x")
        with self.assertRaises(ValidationError):
            RunHttpResponse(
                **snapshot_to_http_response(snapshot()).model_dump(),
                internal="x",
            )


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = FakeRunManager()
        self.app = create_http_app(self.manager)  # type: ignore[arg-type]
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_provider_trace_endpoint_is_read_only_no_store(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/runs/run-a/provider-traces")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"enabled": False, "traces": []})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.manager.calls, [("get_provider_traces", "run-a")])

    def test_create_returns_queued_snapshot_and_preserves_strings(self) -> None:
        response = self.client.post(
            "/runs",
            json={
                "start_url": " https://example.com ",
                "task": " Find the report ",
            },
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.manager.calls,
            [
                (
                    "create_run",
                    " https://example.com ",
                    " Find the report ",
                )
            ],
        )
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(response.json()["error"], None)

    def test_create_accepts_omitted_and_null_start_url(self) -> None:
        self.manager.result = snapshot(start_url=None)
        for payload in ({"task": "Talk first"}, {"start_url": None, "task": "Talk first"}):
            with self.subTest(payload=payload):
                self.manager.calls.clear()
                response = self.client.post("/runs", json=payload)
                self.assertEqual(response.status_code, 202)
                self.assertEqual(self.manager.calls, [("create_run", None, "Talk first")])
                self.assertIsNone(response.json()["start_url"])

    def test_get_returns_snapshot(self) -> None:
        response = self.client.get("/runs/run-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.calls, [("get_run", "run-1")])
        self.assertEqual(response.json()["run_id"], "run-1")

    def test_get_file_returns_exact_bytes_and_safe_headers(self) -> None:
        async def run_sync(function, *args, **_kwargs):
            return function(*args)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-file-id"
            path.write_bytes(b"payload")
            self.manager.download = DownloadFile(
                DownloadMetadata("file-1", "report.pdf", 7), path
            )
            with patch("starlette.responses.anyio.to_thread.run_sync", run_sync):
                response = self.client.get("/runs/run-1/files/file-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"payload")
        self.assertEqual(response.headers["content-type"], "application/octet-stream")
        self.assertIn("report.pdf", response.headers["content-disposition"])
        self.assertNotIn(str(path), response.headers["content-disposition"])
        self.assertEqual(self.manager.calls, [("get_download", "run-1", "file-1")])

    def test_user_input_delegates_in_exact_order(self) -> None:
        response = self.client.post(
            "/runs/run-1/responses",
            json={
                "request_id": "request-1",
                "interaction_id": "interaction-1",
                "type": "user_input",
                "text": " Select 2025 ",
            },
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.manager.calls,
            [
                (
                    "respond",
                    "run-1",
                    "interaction-1",
                    "request-1",
                    " Select 2025 ",
                )
            ],
        )

    def test_confirmation_delegates_actual_bool(self) -> None:
        response = self.client.post(
            "/runs/run-1/responses",
            json={
                "request_id": "request-2",
                "interaction_id": "interaction-2",
                "type": "confirmation",
                "approved": True,
            },
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            self.manager.calls,
            [
                (
                    "confirm",
                    "run-1",
                    "interaction-2",
                    "request-2",
                    True,
                )
            ],
        )
        self.assertIs(type(self.manager.calls[0][-1]), bool)

    def test_completion_delegates_approval_and_feedback(self) -> None:
        approved = self.client.post("/runs/run-1/responses", json={
            "request_id": "request-c1", "interaction_id": "interaction-c1",
            "type": "completion", "approved": True,
        })
        self.assertEqual(approved.status_code, 202)
        declined = self.client.post("/runs/run-1/responses", json={
            "request_id": "request-c2", "interaction_id": "interaction-c2",
            "type": "completion", "approved": False, "feedback": "Keep working",
        })
        self.assertEqual(declined.status_code, 202)
        self.assertEqual(self.manager.calls, [
            ("completion", "run-1", "interaction-c1", "request-c1", True, None),
            ("completion", "run-1", "interaction-c2", "request-c2", False, "Keep working"),
        ])

    def test_cancel_delegates_and_returns_200(self) -> None:
        response = self.client.post(
            "/runs/run-1/cancel", json={"request_id": "request-3"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.manager.calls, [("cancel", "run-1", "request-3")]
        )

    def test_calls_only_direct_manager_method(self) -> None:
        self.client.get("/runs/run-1")
        self.assertEqual(self.manager.calls, [("get_run", "run-1")])


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = FakeRunManager()
        self.client = TestClient(
            create_http_app(self.manager),  # type: ignore[arg-type]
            raise_server_exceptions=False,
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def assert_invalid(self, payload: dict[str, object]) -> None:
        response = self.client.post("/runs/run-1/responses", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                }
            },
        )
        self.assertEqual(self.manager.calls, [])

    def test_cross_field_contract(self) -> None:
        base = {
            "request_id": "request",
            "interaction_id": "interaction",
        }
        cases = (
            {**base, "type": "user_input", "text": "x", "approved": True},
            {**base, "type": "confirmation", "approved": True, "text": "x"},
            {
                **base,
                "type": "user_input",
                "text": "x",
                "approved": False,
            },
            {**base, "type": "user_input"},
            {**base, "type": "confirmation"},
            {**base, "type": "unsupported", "text": "x"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_invalid(payload)

    def test_strict_confirmation_bool(self) -> None:
        for approved in (1, 0, "true", None):
            with self.subTest(approved=approved):
                self.assert_invalid(
                    {
                        "request_id": "request",
                        "interaction_id": "interaction",
                        "type": "confirmation",
                        "approved": approved,
                    }
                )

    def test_completion_cross_field_and_strict_contract(self) -> None:
        base = {"request_id": "request", "interaction_id": "interaction", "type": "completion"}
        cases = (
            {**base, "approved": True, "feedback": "not allowed"},
            {**base, "approved": False},
            {**base, "approved": False, "feedback": "   "},
            {**base, "approved": False, "feedback": 7},
            {**base, "approved": 1},
            {**base, "approved": True, "extra": "x"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_invalid(payload)

    def test_non_string_ids_are_not_coerced(self) -> None:
        for field in ("request_id", "interaction_id"):
            payload: dict[str, object] = {
                "request_id": "request",
                "interaction_id": "interaction",
                "type": "user_input",
                "text": "x",
            }
            payload[field] = 7
            with self.subTest(field=field):
                self.assert_invalid(payload)

    def test_empty_whitespace_null_and_extra_are_rejected(self) -> None:
        create_cases = (
            {"start_url": "", "task": "task"},
            {"start_url": "url", "task": "  "},
            {"start_url": "url", "task": None},
            {"start_url": "url", "task": "task", "unknown": "secret-value"},
        )
        for payload in create_cases:
            with self.subTest(payload=payload):
                response = self.client.post("/runs", json=payload)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manager.calls, [])

    def test_validation_never_echoes_submitted_values(self) -> None:
        secrets = (
            "SECRET-TASK",
            "SECRET-URL",
            "SECRET-TEXT",
            "SECRET-UNKNOWN",
        )
        responses = (
            self.client.post(
                "/runs",
                json={
                    "start_url": 7,
                    "task": "SECRET-TASK",
                    "unknown": "SECRET-UNKNOWN",
                },
            ),
            self.client.post(
                "/runs/run/responses",
                json={
                    "request_id": "id",
                    "interaction_id": "interaction",
                    "type": "user_input",
                    "text": 7,
                    "unknown": "SECRET-TEXT",
                },
            ),
        )
        combined = " ".join(response.text for response in responses)
        for secret in secrets:
            self.assertNotIn(secret, combined)

    def test_request_models_reject_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            CancelRunRequest(request_id="id", extra="x")
        with self.assertRaises(ValidationError):
            UserInputRunResponseRequest(
                request_id="id",
                interaction_id="interaction",
                type="user_input",
                text="text",
                approved=True,
            )
        with self.assertRaises(ValidationError):
            ConfirmationRunResponseRequest(
                request_id="id",
                interaction_id="interaction",
                type="confirmation",
                approved=True,
                text="text",
            )


class ErrorMappingTests(unittest.TestCase):
    def test_manager_error_mappings(self) -> None:
        cases = (
            (RunNotFoundError("missing"), 404, "run_not_found"),
            (
                RunIdempotencyConflictError("duplicate"),
                409,
                "idempotency_conflict",
            ),
            (RunConflictError("conflict"), 409, "run_conflict"),
            (
                RunManagerClosedError("closed"),
                503,
                "run_manager_closed",
            ),
            (
                RunConstructionError("invalid"),
                422,
                "run_construction_error",
            ),
            (RunManagerError("bad"), 400, "run_manager_error"),
        )
        for error, expected_status, expected_code in cases:
            with self.subTest(error=error):
                manager = FakeRunManager()
                manager.error = error
                client = TestClient(
                    create_http_app(manager),  # type: ignore[arg-type]
                    raise_server_exceptions=False,
                )
                with client:
                    response = client.get("/runs/run-1")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error"]["code"], expected_code)

    def test_unexpected_error_is_sanitized(self) -> None:
        manager = FakeRunManager()
        manager.error = RuntimeError("PRIVATE\ntraceback-like detail")
        client = TestClient(
            create_http_app(manager),  # type: ignore[arg-type]
            raise_server_exceptions=False,
        )
        with client:
            response = client.get("/runs/run-1")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error.",
                }
            },
        )
        self.assertNotIn("PRIVATE", response.text)

    def test_file_error_mappings_and_sanitized_unexpected_error(self) -> None:
        cases = (
            (RunFileNotFoundError("run file not found"), 404, "run_file_not_found"),
            (RunNotFoundError("run not found"), 404, "run_not_found"),
            (RuntimeError("PRIVATE-PATH"), 500, "internal_error"),
        )
        for error, expected_status, expected_code in cases:
            with self.subTest(error=error):
                manager = FakeRunManager()
                manager.error = error
                with TestClient(create_http_app(manager), raise_server_exceptions=False) as client:  # type: ignore[arg-type]
                    response = client.get("/runs/run/files/file")
                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(response.json()["error"]["code"], expected_code)
                self.assertNotIn("PRIVATE-PATH", response.text)

    def test_known_messages_are_normalized_and_bounded(self) -> None:
        manager = FakeRunManager()
        manager.error = RunManagerError("first\r\nsecond" + "x" * 600)
        client = TestClient(
            create_http_app(manager),  # type: ignore[arg-type]
            raise_server_exceptions=False,
        )
        with client:
            message = client.get("/runs/run-1").json()["error"]["message"]
        self.assertNotIn("\r", message)
        self.assertNotIn("\n", message)
        self.assertLessEqual(len(message), 500)

    def test_request_id_reaches_manager_before_manager_error(self) -> None:
        manager = FakeRunManager()
        manager.error = RunIdempotencyConflictError("conflict")
        client = TestClient(
            create_http_app(manager),  # type: ignore[arg-type]
            raise_server_exceptions=False,
        )
        with client:
            client.post(
                "/runs/run-1/cancel", json={"request_id": "exact-request"}
            )
        self.assertEqual(
            manager.calls, [("cancel", "run-1", "exact-request")]
        )


class SnapshotTests(unittest.TestCase):
    def test_audit_events_serialize_in_order_without_sensitive_markers(self) -> None:
        events = (
            RunAuditEvent(1, RunAuditKind.SYSTEM, RunAuditStatus.SUCCESS, "Initial page opened"),
            RunAuditEvent(
                2, RunAuditKind.TOOL, RunAuditStatus.PENDING,
                "Approval required: browser_click", 1, "browser_click", None,
            ),
        )
        payload = snapshot_to_http_response(snapshot(audit_events=events)).model_dump(mode="json")
        self.assertEqual([item["sequence"] for item in payload["audit_events"]], [1, 2])
        self.assertEqual(payload["audit_events"][0]["kind"], "system")
        self.assertEqual(payload["audit_events"][0]["status"], "success")
        self.assertIsNone(payload["audit_events"][0]["tool_name"])
        rendered = repr(payload)
        for marker in ("SECRET_MARKER", "ARGUMENT_MARKER", "OBSERVATION_MARKER"):
            self.assertNotIn(marker, rendered)

    def test_files_serialize_safe_metadata_without_path(self) -> None:
        response = snapshot_to_http_response(snapshot(
            files=(DownloadMetadata("file-1", "report.pdf", 7),)
        ))
        rendered = response.model_dump(mode="json")
        self.assertEqual(rendered["files"], [{"file_id": "file-1", "filename": "report.pdf", "size": 7}])
        self.assertNotIn("path", str(rendered))

    def test_secret_target_summary_json_contains_no_ref(self) -> None:
        response = snapshot_to_http_response(snapshot(
            status=RunStatus.AWAITING_SECRET,
            secret_fields=(SecretField.PASSWORD,),
            secret_targets=(SecretTargetSummary(SecretField.PASSWORD, "Password"),),
        ))
        rendered = response.model_dump(mode="json")
        self.assertEqual(rendered["secret_targets"], [{"field": "password", "name": "Password"}])
        self.assertNotIn("ref", str(rendered))
    def test_pause_finished_and_failed_fields(self) -> None:
        pause = snapshot_to_http_response(
            snapshot(
                status=RunStatus.AWAITING_USER,
                question="Which year?",
                interaction_id="interaction-1",
            )
        )
        self.assertEqual(
            (pause.status.value, pause.question, pause.interaction_id),
            ("awaiting_user", "Which year?", "interaction-1"),
        )
        finished = snapshot_to_http_response(
            snapshot(status=RunStatus.FINISHED, final_result="done")
        )
        self.assertEqual(finished.final_result, "done")
        failed = snapshot_to_http_response(
            snapshot(
                status=RunStatus.FAILED,
                error_type="SafeError",
                error_message="safe message",
            )
        )
        self.assertEqual(
            failed.error.model_dump() if failed.error else None,
            {"type": "SafeError", "message": "safe message"},
        )

    def test_partial_error_fails_safely(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_to_http_response(
                snapshot(error_type="SafeError", error_message=None)
            )

    def test_response_contains_no_manager_internals(self) -> None:
        fields = set(snapshot_to_http_response(snapshot()).model_dump())
        self.assertEqual(
            fields,
            {
                "run_id",
                "status",
                "version",
                "start_url",
                "task",
                "step_count",
                "question",
                "interaction_id",
                "final_result",
                "completion_proposal",
                "error",
                "secret_fields",
                "secret_targets",
                "files",
                "audit_events",
            },
        )
        self.assertTrue(
            fields.isdisjoint(
                {"session", "handle", "lock", "active_task", "requests", "factory"}
            )
        )


class LifespanTests(unittest.TestCase):
    def test_context_exit_awaits_close_once(self) -> None:
        manager = FakeRunManager()
        app = create_http_app(manager)  # type: ignore[arg-type]
        with TestClient(app):
            self.assertEqual(manager.close_count, 0)
        self.assertEqual(manager.close_count, 1)
        self.assertTrue(manager.close_completed)

    def test_repeated_lifespans_reuse_manager(self) -> None:
        manager = FakeRunManager()
        app = create_http_app(manager)  # type: ignore[arg-type]
        with TestClient(app):
            pass
        with TestClient(app):
            pass
        self.assertIs(app.state.run_manager, manager)
        self.assertEqual(manager.close_count, 2)


class OpenApiTests(unittest.TestCase):
    def test_only_approved_application_paths_exist(self) -> None:
        app = create_http_app(FakeRunManager())  # type: ignore[arg-type]
        paths = set(app.openapi()["paths"])
        self.assertEqual(
            paths,
            {
                "/runs",
                "/runs/{run_id}",
                "/runs/{run_id}/responses",
                "/runs/{run_id}/cancel",
                "/runs/{run_id}/files/{file_id}",
                "/runs/{run_id}/provider-traces",
            },
        )
        forbidden = (
            "login",
            "credential",
            "websocket",
            "sse",
        )
        self.assertFalse(any(word in str(paths).lower() for word in forbidden))

    def test_success_status_codes_are_declared(self) -> None:
        paths = create_http_app(FakeRunManager()).openapi()["paths"]  # type: ignore[arg-type]
        self.assertIn("202", paths["/runs"]["post"]["responses"])
        self.assertIn("200", paths["/runs/{run_id}"]["get"]["responses"])
        self.assertIn("200", paths["/runs/{run_id}/files/{file_id}"]["get"]["responses"])
        self.assertIn(
            "202", paths["/runs/{run_id}/responses"]["post"]["responses"]
        )
        self.assertIn(
            "200", paths["/runs/{run_id}/cancel"]["post"]["responses"]
        )


class SecretHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = FakeRunManager()
        self.client = TestClient(
            create_http_app(self.manager),  # type: ignore[arg-type]
            raise_server_exceptions=False,
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_secret_request_delegates_and_redacts(self) -> None:
        body = {
            "type": "secret", "request_id": "request-secret",
            "interaction_id": "interaction-secret",
            "values": {"username": "synthetic-user", "password": "synthetic-pass"},
        }
        model = SecretRunResponseRequest.model_validate(body)
        self.assertNotIn("synthetic-pass", repr(model))
        self.assertNotIn("synthetic-pass", str(model))
        self.assertNotIn("synthetic-user", repr(model))
        self.assertNotIn("synthetic-user", str(model))
        response = self.client.post("/runs/run-1/responses", json=body)
        self.assertEqual(response.status_code, 202)
        call = self.manager.calls[0]
        self.assertEqual(call[:4], ("submit_secret", "run-1", "interaction-secret", "request-secret"))
        self.assertEqual(set(call[4]), {SecretField.USERNAME, SecretField.PASSWORD})
        self.assertNotIn("synthetic-pass", response.text)

    def test_secret_snapshot_fields_are_safe(self) -> None:
        for run_status, question, interaction in (
            (RunStatus.AWAITING_SECRET, "Enter credentials", "interaction"),
            (RunStatus.AWAITING_SECRET_APPLICATION, None, None),
        ):
            self.manager.result = snapshot(
                status=run_status, question=question, interaction_id=interaction,
                secret_fields=(SecretField.PASSWORD, SecretField.USERNAME),
            )
            response = self.client.get("/runs/run-1")
            self.assertEqual(response.json()["secret_fields"], ["password", "username"])
            self.assertNotIn("secret_id", response.text)

    def test_secret_validation_is_generic_and_does_not_echo(self) -> None:
        invalid_values = ({}, {"token": "synthetic"}, {"otp": None},
                          {"otp": 1}, {"otp": True}, {"otp": "   "},
                          ["synthetic"], {"otp": {"nested": "synthetic"}})
        for values in invalid_values:
            with self.subTest(kind=type(values).__name__):
                response = self.client.post("/runs/run-1/responses", json={
                    "type": "secret", "request_id": "r", "interaction_id": "i",
                    "values": values,
                })
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json(), {"error": {"code": "validation_error", "message": "Request validation failed."}})
        response = self.client.post("/runs/run-1/responses", json={
            "type": "secret", "request_id": "r", "interaction_id": "i",
            "values": {"otp": "synthetic-otp"}, "extra": "synthetic-extra",
        })
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("synthetic-otp", response.text)

    def test_asgi_secret_pause_submission_and_later_completion(self) -> None:
        targets = (SecretTargetSummary(SecretField.PASSWORD, "Password"),)
        self.manager.result = snapshot(
            status=RunStatus.AWAITING_SECRET,
            question="Enter password",
            interaction_id="secret-interaction",
            secret_fields=(SecretField.PASSWORD,),
            secret_targets=targets,
        )
        paused = self.client.get("/runs/run-1")
        self.assertEqual(paused.status_code, 200)
        self.assertEqual(paused.json()["secret_fields"], ["password"])
        self.assertEqual(paused.json()["secret_targets"], [
            {"field": "password", "name": "Password"}
        ])
        self.assertNotIn("ref", paused.text)

        self.manager.result = snapshot(
            status=RunStatus.AWAITING_SECRET_APPLICATION,
            secret_fields=(SecretField.PASSWORD,), secret_targets=targets,
        )
        submitted = self.client.post("/runs/run-1/responses", json={
            "type": "secret", "request_id": "secret-request",
            "interaction_id": "secret-interaction",
            "values": {"password": "synthetic-password-value"},
        })
        self.assertEqual(submitted.status_code, 202)
        self.assertEqual(submitted.json()["status"], "awaiting_secret_application")
        self.assertEqual(submitted.json()["secret_targets"], [
            {"field": "password", "name": "Password"}
        ])
        self.assertNotIn("synthetic-password-value", submitted.text)

        self.manager.result = snapshot(
            status=RunStatus.FINISHED, final_result="done",
        )
        finished = self.client.get("/runs/run-1")
        self.assertEqual(finished.json()["status"], "finished")
        self.assertIsNone(finished.json()["secret_fields"])
        self.assertIsNone(finished.json()["secret_targets"])

    def test_asgi_later_application_failure_is_fixed_and_contains_no_metadata(self) -> None:
        self.manager.result = snapshot(
            status=RunStatus.FAILED,
            error_type="SecretApplicationError",
            error_message="Secret application failed safely.",
        )
        response = self.client.get("/runs/run-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"], {
            "type": "SecretApplicationError",
            "message": "Secret application failed safely.",
        })
        for forbidden in (
            "synthetic-password-value", "dependency detail", "target-ref",
            "SecretReference", "secret_id", "expires_at", "digest", "HMAC",
            "MCP payload", "application_events",
        ):
            self.assertNotIn(forbidden, response.text)


if __name__ == "__main__":
    unittest.main()
