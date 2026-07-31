"""Deterministic, in-process tests for the FastAPI RunManager boundary."""

from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from browser_agent import (
    CancelRunRequest,
    CreateRunRequest,
    RunConflictError,
    RunConstructionError,
    RunIdempotencyConflictError,
    RunManagerClosedError,
    RunManagerError,
    RunNotFoundError,
    RunSnapshot,
    RunStatus,
    create_http_app,
)
from browser_agent.http_api import (
    ConfirmationRunResponseRequest,
    RunHttpResponse,
    UserInputRunResponseRequest,
    snapshot_to_http_response,
)


def snapshot(
    *,
    status: RunStatus = RunStatus.QUEUED,
    question: str | None = None,
    interaction_id: str | None = None,
    final_result: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> RunSnapshot:
    return RunSnapshot(
        run_id="run-1",
        status=status,
        version=1,
        start_url=" https://example.com ",
        task=" Find the report ",
        step_count=2,
        question=question,
        interaction_id=interaction_id,
        final_result=final_result,
        error_type=error_type,
        error_message=error_message,
    )


class FakeRunManager:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.result = snapshot()
        self.error: Exception | None = None
        self.close_count = 0
        self.close_completed = False

    def _return_or_raise(self, call: tuple[object, ...]) -> RunSnapshot:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return self.result

    async def create_run(self, start_url: str, task: str) -> RunSnapshot:
        return self._return_or_raise(("create_run", start_url, task))

    async def get_run(self, run_id: str) -> RunSnapshot:
        return self._return_or_raise(("get_run", run_id))

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

    async def cancel(self, run_id: str, request_id: str) -> RunSnapshot:
        return self._return_or_raise(("cancel", run_id, request_id))

    async def close(self) -> None:
        self.close_count += 1
        await asyncio.sleep(0)
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

    def test_get_returns_snapshot(self) -> None:
        response = self.client.get("/runs/run-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.calls, [("get_run", "run-1")])
        self.assertEqual(response.json()["run_id"], "run-1")

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
            {"start_url": None, "task": "task"},
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
                "error",
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
            },
        )
        forbidden = (
            "file",
            "download",
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
        self.assertIn(
            "202", paths["/runs/{run_id}/responses"]["post"]["responses"]
        )
        self.assertIn(
            "200", paths["/runs/{run_id}/cancel"]["post"]["responses"]
        )


if __name__ == "__main__":
    unittest.main()
