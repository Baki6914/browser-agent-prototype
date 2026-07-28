"""Focused tests for the provider-neutral OpenAI-compatible decision source."""

from __future__ import annotations

import json
import math
import unittest
from typing import Any

import httpx

from browser_agent.agent_loop import (
    AgentLoopContext,
    AgentStepObservation,
    AgentStepRecord,
    AgentStepStatus,
    AgentToolCall,
    AgentToolDefinition,
    AgentToolSource,
)
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
                    "error": None,
                    "source": "mcp",
                    "status": "success",
                    "text": "Exact observation café",
                },
                "step_number": 1,
                "tool_name": "browser_navigate",
            },
        )

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
