"""Focused tests for the provider-neutral OpenAI-compatible decision source."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import unittest
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_agent.agent_loop import (
    AgentLoopContext,
    AgentPauseKind,
    AgentStepObservation,
    AgentStepRecord,
    AgentStepStatus,
    AgentToolCall,
    AgentToolDefinition,
    AgentToolSource,
    AgentUserInput,
    AgentApplicationEvent,
    AgentApplicationEventKind,
)
from browser_agent.secret_store import SecretField
from browser_agent.agent_controls import AgentControlExecutor
from browser_agent.openai_provider import (
    OpenAICompatibleDecisionSource,
    OpenAICompatibleProviderConfig,
    OpenAIProviderConfigurationError,
    OpenAIProviderHTTPError,
    OpenAIProviderResponseError,
    OpenAIProviderTimeoutError,
    OpenAIProviderTransportError,
)
from browser_agent.provider_trace import ProviderTraceStore


def _config(api_key: str | None = None) -> OpenAICompatibleProviderConfig:
    return OpenAICompatibleProviderConfig(
        "https://api.example.com/v1/", "synthetic-model", api_key, 10
    )


def _context() -> AgentLoopContext:
    schema = {
        "type": "object",
        "properties": {"nested": {"type": "array", "items": {"type": "string"}}},
    }
    return AgentLoopContext(
        task="Inspect café exactly.",
        tools=(
            AgentToolDefinition(
                "browser_snapshot", None, schema, AgentToolSource.MCP
            ),
            AgentToolDefinition(
                "finish",
                "Finish the task.",
                {"type": "object"},
                AgentToolSource.AGENT_CONTROL,
            ),
        ),
        steps=(
            AgentStepRecord(
                1,
                AgentToolCall(
                    "browser_navigate", {"url": "https://example.com"}
                ),
                AgentStepObservation(
                    "browser_navigate",
                    AgentToolSource.MCP,
                    AgentStepStatus.SUCCESS,
                    "Exact observation café",
                    None,
                ),
            ),
        ),
    )


def _payload(name: str = "browser_snapshot", arguments: str = "{}") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ]
                }
            }
        ]
    }


class ConfigTests(unittest.TestCase):
    def test_valid_config_normalizes_and_derives_chat_url(self) -> None:
        config = _config()
        self.assertEqual(config.base_url, "https://api.example.com/v1")
        self.assertEqual(
            config.chat_completions_url,
            "https://api.example.com/v1/chat/completions",
        )

    def test_invalid_base_urls_are_rejected(self) -> None:
        invalid = (
            "",
            "relative/v1",
            "ftp://example.com/v1",
            "https:///v1",
            "https://user@example.com/v1",
            "https://example.com/v1?x=1",
            "https://example.com/v1#fragment",
            "https://example.com:bad/v1",
            " https://example.com/v1",
            "https://example.com/v1 ",
            "https://exam ple.com/v1",
            "https://example.com/v 1",
            "https://example.com/v1\tsegment",
            "https://example.com/v1\nsegment",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(OpenAIProviderConfigurationError):
                    OpenAICompatibleProviderConfig(value, "model")

        self.assertEqual(
            OpenAICompatibleProviderConfig(
                "https://example.com/v1%20segment", "model"
            ).base_url,
            "https://example.com/v1%20segment",
        )

    def test_empty_model_is_rejected_and_accepted_text_is_preserved(self) -> None:
        for value in ("", " \t"):
            with self.assertRaises(OpenAIProviderConfigurationError):
                OpenAICompatibleProviderConfig("https://example.com/v1", value)
        self.assertEqual(
            OpenAICompatibleProviderConfig(
                "https://example.com/v1", " model "
            ).model,
            " model ",
        )

    def test_invalid_api_keys_are_rejected(self) -> None:
        for value in ("", " ", 3):
            with self.subTest(value=value):
                with self.assertRaises(OpenAIProviderConfigurationError):
                    OpenAICompatibleProviderConfig(
                        "https://example.com/v1", "model", value  # type: ignore[arg-type]
                    )

    def test_invalid_timeout_values_are_rejected(self) -> None:
        for value in (True, False, 0, -1, math.inf, -math.inf, math.nan, "1"):
            with self.subTest(value=value):
                with self.assertRaises(OpenAIProviderConfigurationError):
                    OpenAICompatibleProviderConfig(
                        "https://example.com/v1",
                        "model",
                        timeout_seconds=value,  # type: ignore[arg-type]
                    )

    def test_repr_does_not_expose_api_key(self) -> None:
        config = _config("super-secret")
        self.assertNotIn("super-secret", repr(config))
        self.assertNotIn("api_key", repr(config))


class DecisionSourceTests(unittest.IsolatedAsyncioTestCase):
    async def _request(
        self,
        response: httpx.Response,
        *,
        api_key: str | None = None,
        context: AgentLoopContext | None = None,
    ) -> tuple[AgentToolCall, httpx.Request]:
        captured: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return response

        result = await OpenAICompatibleDecisionSource(
            _config(api_key), httpx.MockTransport(handler)
        ).next_decision(context or _context())
        return result, captured[0]

    async def test_authorization_header_added_when_key_exists(self) -> None:
        _, request = await self._request(
            httpx.Response(200, json=_payload()), api_key="synthetic-key"
        )
        self.assertEqual(request.headers["Authorization"], "Bearer synthetic-key")
        self.assertEqual(request.headers["Content-Type"], "application/json")

    async def test_success_records_only_safe_structural_trace(self) -> None:
        store = ProviderTraceStore(enabled=True)
        payload = _payload("browser_snapshot", '{"private":"tool argument value"}')
        payload["choices"][0]["finish_reason"] = "tool_calls"
        payload["choices"][0]["message"]["content"] = "raw assistant response"
        source = OpenAICompatibleDecisionSource(
            _config("api-secret"),
            httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
            store,
        )
        decision = await source.next_decision(_context())
        self.assertEqual(decision.tool_name, "browser_snapshot")
        trace = store.snapshot()[0]
        self.assertEqual(trace.result_status, "success")
        self.assertEqual(trace.tool_call_count, 1)
        self.assertTrue(trace.assistant_content_present)
        self.assertEqual(trace.selected_tool_name, "browser_snapshot")
        rendered = repr(trace)
        for forbidden in (
            "api-secret", "Inspect café", "Exact observation", "tool argument value",
            "raw assistant response",
        ):
            self.assertNotIn(forbidden, rendered)

    async def test_untrusted_finish_reason_and_unoffered_tool_name_are_not_traced(self) -> None:
        store = ProviderTraceStore(enabled=True)
        payload = _payload("unoffered_tool")
        payload["choices"][0]["finish_reason"] = "raw provider-controlled text"
        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)), store
        )
        decision = await source.next_decision(_context())
        self.assertEqual(decision.tool_name, "unoffered_tool")
        trace = store.snapshot()[0]
        self.assertIsNone(trace.finish_reason)
        self.assertIsNone(trace.selected_tool_name)

    async def test_cardinality_attempts_are_structurally_traced_without_new_retry(self) -> None:
        store = ProviderTraceStore(enabled=True)
        multiple = _payload()
        multiple["choices"][0]["message"]["tool_calls"].append(
            multiple["choices"][0]["message"]["tool_calls"][0]
        )
        responses = iter((httpx.Response(200, json=multiple), httpx.Response(200, json=_payload())))
        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(lambda _request: next(responses)), store
        )
        await source.next_decision(_context())
        self.assertEqual(
            [(item.attempt_number, item.tool_call_count, item.result_status) for item in store.snapshot()],
            [(1, 2, "tool_call_cardinality_error"), (2, 1, "success")],
        )

    async def test_safe_failure_categories_are_traced(self) -> None:
        cases = (
            (lambda _request: httpx.Response(503, text="raw provider secret"), OpenAIProviderHTTPError, "http_error"),
            (lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("late", request=request)), OpenAIProviderTimeoutError, "timeout"),
            (lambda request: (_ for _ in ()).throw(httpx.ConnectError("private transport", request=request)), OpenAIProviderTransportError, "transport_error"),
            (lambda _request: httpx.Response(200, content=b"not-json"), OpenAIProviderResponseError, "invalid_json"),
        )
        for handler, error, status_name in cases:
            with self.subTest(status=status_name):
                store = ProviderTraceStore(enabled=True)
                source = OpenAICompatibleDecisionSource(
                    _config(), httpx.MockTransport(handler), store
                )
                with self.assertRaises(error):
                    await source.next_decision(_context())
                self.assertEqual(store.snapshot()[0].result_status, status_name)
                self.assertNotIn("raw provider secret", repr(store.snapshot()[0]))

    async def test_recorder_failure_does_not_change_provider_decision(self) -> None:
        class BrokenRecorder:
            def record(self, **_metadata: object) -> None:
                raise RuntimeError("recorder failed")

        source = OpenAICompatibleDecisionSource(
            _config(),
            httpx.MockTransport(lambda _request: httpx.Response(200, json=_payload())),
            BrokenRecorder(),
        )
        self.assertEqual((await source.next_decision(_context())).tool_name, "browser_snapshot")

    async def test_authorization_header_absent_without_key(self) -> None:
        _, request = await self._request(httpx.Response(200, json=_payload()))
        self.assertNotIn("Authorization", request.headers)

    async def test_request_contract_and_exact_url(self) -> None:
        _, request = await self._request(httpx.Response(200, json=_payload()))
        body = json.loads(request.content)
        self.assertEqual(
            str(request.url), "https://api.example.com/v1/chat/completions"
        )
        self.assertEqual(body["model"], "synthetic-model")
        self.assertEqual(body["tool_choice"], "required")
        self.assertNotEqual(body["tool_choice"], "auto")
        self.assertIs(body["parallel_tool_calls"], False)
        self.assertEqual([item["function"]["name"] for item in body["tools"]],
                         ["browser_snapshot", "finish"])
        self.assertEqual(body["tools"][0]["function"]["description"], "")
        self.assertEqual(
            body["tools"][0]["function"]["parameters"],
            _context().tools[0].input_schema,
        )

    async def test_user_json_contains_exact_context_history(self) -> None:
        _, request = await self._request(httpx.Response(200, json=_payload()))
        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"])
        self.assertEqual(user["task"], "Inspect café exactly.")
        self.assertEqual(
            user["previous_steps"][0],
            {
                "arguments": {"url": "https://example.com"},
                "observation": {
                    "confirmation_required": False,
                    "error": None,
                    "source": "mcp",
                    "status": "success",
                    "text": "Exact observation café",
                },
                "step_number": 1,
                "tool_name": "browser_navigate",
            },
        )
        self.assertNotIn("user_interactions", user)

    async def test_user_interactions_serialize_deterministically(self) -> None:
        context = _context()
        context = AgentLoopContext(
            context.task,
            context.tools,
            context.steps,
            (
                AgentUserInput(
                    1,
                    AgentPauseKind.USER_INPUT,
                    "Which period?",
                    "2026",
                ),
                AgentUserInput(
                    3,
                    AgentPauseKind.CONFIRMATION,
                    "Approve exact action?",
                    True,
                ),
            ),
        )
        _, request = await self._request(
            httpx.Response(200, json=_payload()), context=context
        )
        content = json.loads(request.content)["messages"][1]["content"]
        self.assertEqual(
            content,
            json.dumps(
                {
                    "task": context.task,
                    "previous_steps": [
                        {
                            "arguments": {"url": "https://example.com"},
                            "observation": {
                                "confirmation_required": False,
                                "error": None,
                                "source": "mcp",
                                "status": "success",
                                "text": "Exact observation café",
                            },
                            "step_number": 1,
                            "tool_name": "browser_navigate",
                        }
                    ],
                    "user_interactions": [
                        {
                            "after_step_number": 1,
                            "kind": "user_input",
                            "question": "Which period?",
                            "response": "2026",
                        },
                        {
                            "after_step_number": 3,
                            "kind": "confirmation",
                            "question": "Approve exact action?",
                            "response": True,
                        },
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    async def test_confirmation_required_step_is_explicit_in_context(self) -> None:
        context = _context()
        context = AgentLoopContext(
            task="Submit the exact form.",
            tools=context.tools,
            steps=(
                AgentStepRecord(
                    step_number=4,
                    decision=AgentToolCall(
                        "browser_fill_form",
                        {"fields": [{"name": "email", "value": "user@example.com"}]},
                    ),
                    observation=AgentStepObservation(
                        tool_name="browser_fill_form",
                        source=AgentToolSource.MCP,
                        status=AgentStepStatus.REJECTED,
                        text="Exact rejected observation",
                        error="ToolConfirmationRequiredError: approval required",
                        confirmation_required=True,
                    ),
                ),
            ),
        )

        result, request = await self._request(
            httpx.Response(200, json=_payload()), context=context
        )

        body = json.loads(request.content)
        previous_step = json.loads(body["messages"][1]["content"])[
            "previous_steps"
        ][0]
        self.assertEqual(
            previous_step,
            {
                "arguments": {
                    "fields": [
                        {"name": "email", "value": "user@example.com"}
                    ]
                },
                "observation": {
                    "confirmation_required": True,
                    "error": "ToolConfirmationRequiredError: approval required",
                    "source": "mcp",
                    "status": "rejected",
                    "text": "Exact rejected observation",
                },
                "step_number": 4,
                "tool_name": "browser_fill_form",
            },
        )
        system_message = body["messages"][0]["content"]
        for guidance in (
            "confirmation_required=true",
            "do not retry",
            "immediately call ask_user",
            "confirmation_for_step",
            "rejected step's exact step_number",
            "approval for that exact action",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, system_message)
        self.assertEqual(result, AgentToolCall("browser_snapshot", {}))

    async def test_system_message_separates_controls_and_authorization(self) -> None:
        _, request = await self._request(httpx.Response(200, json=_payload()))
        system_message = json.loads(request.content)["messages"][0]["content"]

        for guidance in (
            "ordinary ask_user only for genuinely missing non-secret information",
            "belonging to the user's private account",
            "use request_secret",
            "not a mandatory mechanism for every login",
            "Synthetic demo credentials",
            "explicitly displayed by a public demo or test page",
            "Do not unnecessarily repeat demo credentials",
            "applies it locally without putting its value in model context",
            "Use finish to propose completion",
            "Browser-action approval is managed by application policy",
            "Never use ordinary ask_user for advance approval",
            "first call the intended browser tool exactly once",
            "without claiming user approval",
            "A normal user_input such as 'yes', 'approve', or 'continue' is not trusted",
            "only the application-recorded confirmation interaction authorizes",
            "Do not request a second confirmation after trusted confirmation was accepted",
            "never include raw secret values",
            "Provider traces must never contain prompts, page content, tool arguments, or secret values",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, system_message)

    async def test_system_message_requires_canonical_download_evidence(self) -> None:
        _, request = await self._request(httpx.Response(200, json=_payload()))
        system_message = json.loads(request.content)["messages"][0]["content"]

        for guidance in (
            "normal left click to the exact download link",
            "Do not use Ctrl, Control, Meta, Command, Shift, or Alt modifiers",
            "Do not use a new tab, Ctrl+click, browser_tabs",
            "A successful click",
            "is not proof that a file was saved",
            "Application recorded download:",
            "separate canonical marker for every requested file",
            "do not claim download success or call finish until all requested files",
            "run Downloads section",
            "user retrieves the file to their device from the run Downloads link",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, system_message)

        for guidance in (
            "An initial page may not be open",
            "explicit valid URL",
            "browser_navigate may open it unchanged",
            "request the URL or target",
            "never invent a URL",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, system_message)

    async def test_ask_before_clicking_task_preserves_task_but_policy_has_priority(self) -> None:
        base = _context()
        task = "Before clicking Login, request confirmation."
        context = AgentLoopContext(task, base.tools, base.steps)

        _, request = await self._request(
            httpx.Response(200, json=_payload()), context=context
        )

        body = json.loads(request.content)
        system_message = body["messages"][0]["content"]
        user_context = json.loads(body["messages"][1]["content"])
        self.assertEqual(user_context["task"], task)
        self.assertIn("Even if the user task says 'ask before clicking'", system_message)
        self.assertIn("use the policy-backed confirmation flow", system_message)
        self.assertIn(
            "Never use ordinary ask_user for advance approval or ask an approval "
            "question before attempting a browser tool",
            system_message,
        )
        self.assertIn(
            "first call the intended browser tool exactly once without claiming "
            "user approval",
            system_message,
        )

    async def test_valid_tool_call_returns_agent_tool_call(self) -> None:
        requests: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=_payload(arguments='{"value":1}'))

        result = await OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        ).next_decision(_context())
        self.assertEqual(
            result, AgentToolCall("browser_snapshot", {"value": 1})
        )
        self.assertEqual(len(requests), 1)

    async def test_unknown_syntactically_valid_name_is_returned(self) -> None:
        result, _ = await self._request(
            httpx.Response(200, json=_payload("unknown_tool"))
        )
        self.assertEqual(result.tool_name, "unknown_tool")

    async def test_text_only_response_retries_once_without_copying_content(self) -> None:
        requests: list[httpx.Request] = []
        responses = iter((
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": "raw private reply"}}]},
            ),
            httpx.Response(200, json=_payload(arguments='{"value":1}')),
        ))

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return next(responses)

        result = await OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        ).next_decision(_context())

        self.assertEqual(result, AgentToolCall("browser_snapshot", {"value": 1}))
        self.assertEqual(len(requests), 2)
        second_body = json.loads(requests[1].content)
        self.assertIn(
            "The previous response violated the contract. Return exactly one "
            "supplied tool call and no ordinary text.",
            second_body["messages"][0]["content"],
        )
        self.assertNotIn("raw private reply", requests[1].content.decode())
        self.assertEqual(second_body["tool_choice"], "required")
        self.assertIs(second_body["parallel_tool_calls"], False)

    async def test_multiple_tool_calls_retries_once(self) -> None:
        requests: list[httpx.Request] = []
        multiple = _payload()
        multiple["choices"][0]["message"]["tool_calls"].append(
            _payload("finish")["choices"][0]["message"]["tool_calls"][0]
        )
        responses = iter((
            httpx.Response(200, json=multiple),
            httpx.Response(200, json=_payload("finish", '{"reason":"done"}')),
        ))

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return next(responses)

        result = await OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        ).next_decision(_context())

        self.assertEqual(result, AgentToolCall("finish", {"reason": "done"}))
        self.assertEqual(len(requests), 2)

    async def test_zero_tool_calls_twice_exhausts_retry_safely(self) -> None:
        requests: list[httpx.Request] = []
        raw_content = "raw assistant content must stay private"

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": raw_content}}]}
            )

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderResponseError) as caught:
            await source.next_decision(_context())

        self.assertEqual(len(requests), 2)
        self.assertIn("received 0", str(caught.exception))
        self.assertNotIn(raw_content, str(caught.exception))

    async def test_non_array_tool_calls_is_rejected_without_retry(self) -> None:
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"tool_calls": "invalid"}}]},
            )

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderResponseError):
            await source.next_decision(_context())
        self.assertEqual(requests, 1)

    async def test_non_function_tool_call_is_rejected(self) -> None:
        payload = _payload()
        payload["choices"][0]["message"]["tool_calls"][0]["type"] = "custom"
        with self.assertRaises(OpenAIProviderResponseError):
            await self._request(httpx.Response(200, json=payload))

    async def test_missing_function_object_is_rejected(self) -> None:
        payload = _payload()
        del payload["choices"][0]["message"]["tool_calls"][0]["function"]
        with self.assertRaises(OpenAIProviderResponseError):
            await self._request(httpx.Response(200, json=payload))

    async def test_empty_or_non_string_tool_name_is_rejected(self) -> None:
        for name in ("", " ", 1):
            with self.subTest(name=name):
                with self.assertRaises(OpenAIProviderResponseError):
                    await self._request(
                        httpx.Response(200, json=_payload(name))  # type: ignore[arg-type]
                    )

    async def test_non_string_or_malformed_argument_json_is_rejected(self) -> None:
        for arguments in ({"x": 1}, "{"):
            with self.subTest(arguments=arguments):
                requests = 0

                async def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal requests
                    requests += 1
                    return httpx.Response(
                        200,
                        json=_payload(arguments=arguments),  # type: ignore[arg-type]
                    )

                source = OpenAICompatibleDecisionSource(
                    _config(), httpx.MockTransport(handler)
                )
                with self.assertRaises(OpenAIProviderResponseError):
                    await source.next_decision(_context())
                self.assertEqual(requests, 1)

    async def test_non_object_parsed_arguments_are_rejected(self) -> None:
        for arguments in ("[]", '"text"', "1", "true", "null"):
            with self.subTest(arguments=arguments):
                with self.assertRaises(OpenAIProviderResponseError):
                    await self._request(
                        httpx.Response(
                            200, json=_payload(arguments=arguments)
                        )
                    )

    async def test_invalid_top_level_json_and_shapes_are_rejected(self) -> None:
        responses = (
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, json=[]),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"choices": []}),
            httpx.Response(200, json={"choices": [1]}),
            httpx.Response(200, json={"choices": [{}]}),
        )
        for response in responses:
            with self.subTest(content=response.content):
                with self.assertRaises(OpenAIProviderResponseError):
                    await self._request(response)

    async def test_invalid_utf8_response_is_rejected(self) -> None:
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, content=b"\xff")

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderResponseError):
            await source.next_decision(_context())
        self.assertEqual(requests, 1)

    async def test_invalid_json_is_rejected_without_retry(self) -> None:
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(200, content=b"not-json")

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderResponseError):
            await source.next_decision(_context())
        self.assertEqual(requests, 1)

    async def test_timeout_maps_to_provider_timeout_error(self) -> None:
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            raise httpx.ReadTimeout("timed out", request=request)

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderTimeoutError):
            await source.next_decision(_context())
        self.assertEqual(requests, 1)

    async def test_transport_failure_maps_to_provider_transport_error(self) -> None:
        requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            raise httpx.ConnectError("failed", request=request)

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderTransportError):
            await source.next_decision(_context())
        self.assertEqual(requests, 1)

    async def test_http_failure_maps_and_redacts_echoed_key(self) -> None:
        key = "synthetic-secret"
        requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(401, text=f"echo {key} " + ("x" * 500))

        source = OpenAICompatibleDecisionSource(
            _config(key),
            httpx.MockTransport(handler),
        )
        with self.assertRaises(OpenAIProviderHTTPError) as caught:
            await source.next_decision(_context())
        text = str(caught.exception)
        self.assertIn("401", text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn(key, text)
        self.assertLess(len(text), 400)
        self.assertEqual(requests, 1)

    async def test_http_failure_redacts_key_crossing_excerpt_boundary(self) -> None:
        key = "DISTINCTIVE-BOUNDARY-SECRET-ALPHA-OMEGA"
        body = ("x" * 290) + key + ("y" * 500)
        source = OpenAICompatibleDecisionSource(
            _config(key),
            httpx.MockTransport(
                lambda request: httpx.Response(401, text=body)
            ),
        )

        with self.assertRaises(OpenAIProviderHTTPError) as caught:
            await source.next_decision(_context())

        text = str(caught.exception)
        self.assertNotIn(key, text)
        self.assertNotIn("DISTINCTIVE-BOUNDARY", text)
        self.assertNotIn("SECRET-ALPHA-OMEGA", text)
        self.assertIn("[REDACTED]", text)
        self.assertLess(len(text), 400)

    async def test_unexpected_runtime_error_propagates_unchanged(self) -> None:
        error = RuntimeError("programming failure")

        async def handler(request: httpx.Request) -> httpx.Response:
            raise error

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(RuntimeError) as caught:
            await source.next_decision(_context())
        self.assertIs(caught.exception, error)

    async def test_caller_owned_nested_schema_is_not_mutated_or_retained(self) -> None:
        context = _context()
        original = context.tools[0].input_schema
        captured: list[dict[str, Any]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            original["properties"]["nested"]["items"]["type"] = "number"
            return httpx.Response(200, json=_payload())

        await OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        ).next_decision(context)
        self.assertEqual(
            captured[0]["tools"][0]["function"]["parameters"]["properties"]
            ["nested"]["items"]["type"],
            "string",
        )


class SecretApplicationContextTests(unittest.TestCase):
    def test_actual_request_secret_schema_is_converted_exactly(self) -> None:
        control = AgentControlExecutor()
        definition = next(item for item in control.definitions() if item.name == "request_secret")
        context = _context()
        supplied = AgentLoopContext(
            context.task,
            context.tools + (AgentToolDefinition(
                definition.name, definition.description,
                definition.input_schema, AgentToolSource.AGENT_CONTROL,
            ),),
            context.steps,
        )
        body = OpenAICompatibleDecisionSource._context_request_body(supplied)
        schema = next(
            tool["function"]["parameters"] for tool in body["tools"]
            if tool["function"]["name"] == "request_secret"
        )
        self.assertEqual(schema["required"], ["question", "fields", "targets"])
        self.assertEqual(set(schema["properties"]), {"question", "fields", "targets"})
        target = schema["properties"]["targets"]["items"]
        self.assertEqual(set(target["properties"]), {"field", "name", "ref"})
        self.assertEqual(target["required"], ["field", "name", "ref"])
        self.assertIs(target["additionalProperties"], False)

    def test_application_events_are_safe_and_present_only_when_non_empty(self) -> None:
        context = _context()
        self.assertNotIn("application_events", OpenAICompatibleDecisionSource._context_request_body(context)["messages"][1]["content"])
        safe = AgentLoopContext(
            context.task, context.tools, context.steps, context.user_interactions,
            (AgentApplicationEvent(1, AgentApplicationEventKind.SECRET_APPLIED, (SecretField.OTP,)),),
        )
        rendered = OpenAICompatibleDecisionSource._context_request_body(safe)["messages"][1]["content"]
        self.assertIn('"application_events":[{"after_step_number":1,"fields":["otp"],"kind":"secret_applied"}]', rendered)
        for forbidden in ("synthetic-user", "synthetic-pass", "synthetic-otp", "target-ref", "secret_id"):
            self.assertNotIn(forbidden, rendered)

    def test_multiple_application_events_have_only_safe_deterministic_keys(self) -> None:
        context = _context()
        events = (
            AgentApplicationEvent(1, AgentApplicationEventKind.SECRET_APPLIED,
                                  (SecretField.PASSWORD, SecretField.USERNAME)),
            AgentApplicationEvent(2, AgentApplicationEventKind.SECRET_APPLIED,
                                  (SecretField.OTP,)),
        )
        supplied = AgentLoopContext(
            context.task, context.tools, context.steps,
            (AgentUserInput(1, AgentPauseKind.USER_INPUT, "Question", "Answer"),),
            events,
        )
        body = OpenAICompatibleDecisionSource._context_request_body(supplied)
        user = json.loads(body["messages"][1]["content"])
        self.assertEqual(
            [tuple(item) for item in user["application_events"]],
            [("after_step_number", "fields", "kind")] * 2,
        )
        self.assertEqual(user["user_interactions"][0]["response"], "Answer")
        rendered = json.dumps(body, sort_keys=True)
        for forbidden in ("synthetic-user", "synthetic-pass", "synthetic-otp",
                          "target-ref", "target-name", "SecretReference",
                          "secret_id", "expires_at", "digest", "hmac"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
