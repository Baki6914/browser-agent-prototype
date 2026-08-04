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
        self.assertEqual(body["tool_choice"], "auto")
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

    async def test_secret_applied_event_adds_fixed_safe_guidance(self) -> None:
        context = _context()
        context = AgentLoopContext(
            context.task,
            context.tools,
            context.steps,
            application_events=(
                AgentApplicationEvent(
                    1,
                    AgentApplicationEventKind.SECRET_APPLIED,
                    (SecretField.PASSWORD, SecretField.USERNAME),
                ),
            ),
        )

        _, request = await self._request(
            httpx.Response(200, json=_payload()), context=context
        )

        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"])
        event = user["application_events"][0]
        self.assertEqual(
            set(event), {"after_step_number", "kind", "fields", "guidance"}
        )
        self.assertEqual(event["after_step_number"], 1)
        self.assertEqual(event["kind"], "secret_applied")
        self.assertEqual(event["fields"], ["password", "username"])
        guidance = event["guidance"]
        for phrase in (
            "not evidence that authentication succeeded",
            "does not complete the user's task",
            "inspect, infer, or reveal raw secret values",
            "do not take a snapshot merely to inspect the filled secret fields",
            "prior safe page observation and previously supplied element references",
            "inspect the resulting state",
            "Do not call finish until the requested outcome is visibly verified",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, guidance)
        system_message = body["messages"][0]["content"]
        for phrase in (
            "do not inspect the secret values",
            "snapshot the filled form merely to verify them",
            "Continue from prior safe observations and element references",
            "inspect the resulting page after the intended action",
            "do not call finish until the requested result is visibly verified",
            "neither proves authentication success nor completes the task",
        ):
            with self.subTest(system_phrase=phrase):
                self.assertIn(phrase, system_message)

    async def test_context_without_application_events_preserves_shape(self) -> None:
        context = _context()

        _, request = await self._request(
            httpx.Response(200, json=_payload()), context=context
        )

        body = json.loads(request.content)
        user = json.loads(body["messages"][1]["content"])
        self.assertNotIn("application_events", user)
        self.assertEqual(user["task"], context.task)
        self.assertEqual(user["previous_steps"][0]["step_number"], 1)
        self.assertEqual(
            [item["function"]["name"] for item in body["tools"]],
            [definition.name for definition in context.tools],
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
            "Immediately call ask_user",
            "confirmation_for_step",
            "rejected step's step_number",
            "approves that exact action",
        ):
            with self.subTest(guidance=guidance):
                self.assertIn(guidance, system_message)
        self.assertEqual(result, AgentToolCall("browser_snapshot", {}))

    async def test_valid_tool_call_returns_agent_tool_call(self) -> None:
        result, _ = await self._request(
            httpx.Response(200, json=_payload(arguments='{"value":1}'))
        )
        self.assertEqual(result.tool_name, "browser_snapshot")
        self.assertEqual(result.arguments, {"value": 1})

    async def test_unknown_syntactically_valid_name_is_returned(self) -> None:
        result, _ = await self._request(
            httpx.Response(200, json=_payload("unknown_tool"))
        )
        self.assertEqual(result.tool_name, "unknown_tool")

    async def test_text_only_response_is_rejected(self) -> None:
        with self.assertRaises(OpenAIProviderResponseError):
            await self._request(
                httpx.Response(
                    200, json={"choices": [{"message": {"content": "done"}}]}
                )
            )

    async def test_missing_empty_or_multiple_tool_calls_are_rejected(self) -> None:
        messages = ({}, {"tool_calls": []}, {"tool_calls": [1, 2]})
        for message in messages:
            with self.subTest(message=message):
                with self.assertRaises(OpenAIProviderResponseError):
                    await self._request(
                        httpx.Response(
                            200, json={"choices": [{"message": message}]}
                        )
                    )

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
                with self.assertRaises(OpenAIProviderResponseError):
                    await self._request(
                        httpx.Response(
                            200, json=_payload(arguments=arguments)  # type: ignore[arg-type]
                        )
                    )

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
        with self.assertRaises(OpenAIProviderResponseError):
            await self._request(httpx.Response(200, content=b"\xff"))

    async def test_timeout_maps_to_provider_timeout_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderTimeoutError):
            await source.next_decision(_context())

    async def test_transport_failure_maps_to_provider_transport_error(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("failed", request=request)

        source = OpenAICompatibleDecisionSource(
            _config(), httpx.MockTransport(handler)
        )
        with self.assertRaises(OpenAIProviderTransportError):
            await source.next_decision(_context())

    async def test_http_failure_maps_and_redacts_echoed_key(self) -> None:
        key = "synthetic-secret"
        source = OpenAICompatibleDecisionSource(
            _config(key),
            httpx.MockTransport(
                lambda request: httpx.Response(
                    401, text=f"echo {key} " + ("x" * 500)
                )
            ),
        )
        with self.assertRaises(OpenAIProviderHTTPError) as caught:
            await source.next_decision(_context())
        text = str(caught.exception)
        self.assertIn("401", text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn(key, text)
        self.assertLess(len(text), 400)

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
        event = json.loads(rendered)["application_events"][0]
        self.assertEqual(event["after_step_number"], 1)
        self.assertEqual(event["fields"], ["otp"])
        self.assertEqual(event["kind"], "secret_applied")
        self.assertIn("not evidence that authentication succeeded", event["guidance"])
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
            [("after_step_number", "fields", "guidance", "kind")] * 2,
        )
        first_event, second_event = user["application_events"]
        self.assertEqual(first_event["guidance"], second_event["guidance"])
        self.assertEqual(first_event["fields"], ["password", "username"])
        self.assertEqual(second_event["fields"], ["otp"])
        for event in (first_event, second_event):
            self.assertNotIn("target_ref", event)
            self.assertNotIn("target_label", event)
            self.assertNotIn("handle", event)
            self.assertNotIn("raw_value", event)
        self.assertEqual(user["user_interactions"][0]["response"], "Answer")
        rendered = json.dumps(body, sort_keys=True)
        for forbidden in ("synthetic-user", "synthetic-pass", "synthetic-otp",
                          "target-ref", "target-name", "SecretReference",
                          "secret_id", "expires_at", "digest", "hmac"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
