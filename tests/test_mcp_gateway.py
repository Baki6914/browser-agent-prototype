"""Dependency-free unit tests for the dynamic MCP tool gateway."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from browser_agent.mcp_gateway import (
    InvalidToolArgumentsError,
    McpToolCatalogError,
    McpToolGateway,
    McpToolGatewayNotDiscoveredError,
    UnknownMcpToolError,
    UnsupportedToolSchemaError,
)
from scripts.mcp_gateway_smoke import SmokeError, _exception_details, _run_browser_sequence


class FakeSession:
    def __init__(self, tools: list[object], result: object | None = None) -> None:
        self.response: object = SimpleNamespace(tools=tools)
        self.result = result or {"content": [{"text": "ok"}]}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.call_exception: BaseException | None = None

    async def list_tools(self) -> object:
        return self.response

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> object:
        self.calls.append((name, arguments))
        if self.call_exception is not None:
            raise self.call_exception
        return self.result


class FakeSmokeGateway:
    def __init__(self, failures: dict[str, Exception] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, name: str, arguments: dict[str, Any]) -> object:
        self.calls.append((name, arguments))
        failure = self.failures.get(name)
        if failure is not None:
            raise failure
        return SimpleNamespace(status="success", error=None)


def tool(name: str = "sample", schema: object | None = None) -> object:
    return SimpleNamespace(
        name=name,
        description="Sample tool",
        inputSchema=schema if schema is not None else {"type": "object"},
    )


class SmokeSequenceTests(unittest.IsolatedAsyncioTestCase):
    tools = {"browser_navigate", "browser_snapshot", "browser_close"}

    async def test_close_is_attempted_when_another_required_tool_is_missing(self) -> None:
        gateway = FakeSmokeGateway()

        with self.assertRaisesRegex(SmokeError, "browser_snapshot"):
            await _run_browser_sequence(
                gateway, {"browser_navigate", "browser_close"}
            )

        self.assertEqual(gateway.calls, [("browser_close", {})])

    async def test_sole_primary_failure_remains_visible(self) -> None:
        gateway = FakeSmokeGateway(
            {"browser_navigate": RuntimeError("navigation leaf")}
        )

        with self.assertRaisesRegex(RuntimeError, "navigation leaf"):
            await _run_browser_sequence(gateway, self.tools)

        self.assertEqual(
            [name for name, _ in gateway.calls],
            ["browser_navigate", "browser_close"],
        )

    async def test_sole_cleanup_failure_remains_visible(self) -> None:
        gateway = FakeSmokeGateway(
            {"browser_close": RuntimeError("cleanup leaf")}
        )

        with self.assertRaisesRegex(RuntimeError, "cleanup leaf"):
            await _run_browser_sequence(gateway, self.tools)

    async def test_primary_and_cleanup_failure_details_remain_visible(self) -> None:
        gateway = FakeSmokeGateway(
            {
                "browser_navigate": RuntimeError("navigation leaf"),
                "browser_close": ValueError("cleanup leaf"),
            }
        )

        with self.assertRaises(SmokeError) as caught:
            await _run_browser_sequence(gateway, self.tools)

        message = str(caught.exception)
        self.assertIn("primary failure: RuntimeError: navigation leaf", message)
        self.assertIn("cleanup failure: ValueError: cleanup leaf", message)

    async def test_nested_exception_group_leaf_details_are_visible(self) -> None:
        nested = ExceptionGroup(
            "TaskGroup summary",
            [
                RuntimeError("first leaf"),
                ExceptionGroup("nested summary", [ValueError("second leaf")]),
            ],
        )
        gateway = FakeSmokeGateway({"browser_navigate": nested})

        with self.assertRaises(ExceptionGroup) as caught:
            await _run_browser_sequence(gateway, self.tools)

        details = _exception_details(caught.exception)
        self.assertIn("RuntimeError: first leaf", details)
        self.assertIn("ValueError: second leaf", details)
        self.assertNotIn("TaskGroup summary", details)
        self.assertNotIn("nested summary", details)


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_object_style_tools(self) -> None:
        gateway = McpToolGateway(FakeSession([tool("browser_snapshot")]))
        definitions = await gateway.discover()
        self.assertEqual(definitions[0].name, "browser_snapshot")
        self.assertEqual(definitions[0].description, "Sample tool")

    async def test_discovers_mapping_style_tools_and_response(self) -> None:
        session = FakeSession([])
        session.response = {
            "tools": [
                {
                    "name": "browser_navigate",
                    "description": "Navigate",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
        definitions = await McpToolGateway(session).discover()
        self.assertEqual(definitions[0].name, "browser_navigate")

    async def test_definitions_are_sorted_alphabetically(self) -> None:
        gateway = McpToolGateway(FakeSession([tool("zeta"), tool("alpha")]))
        self.assertEqual(
            [item.name for item in await gateway.discover()], ["alpha", "zeta"]
        )

    async def test_public_catalog_is_defensively_isolated(self) -> None:
        gateway = McpToolGateway(
            FakeSession([tool(schema={"type": "object", "properties": {}})])
        )
        definitions = await gateway.discover()
        definitions[0].input_schema["properties"]["changed"] = {"type": "string"}
        self.assertNotIn("changed", gateway.tools[0].input_schema["properties"])

    async def test_duplicate_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(McpToolCatalogError, "duplicate"):
            await McpToolGateway(FakeSession([tool(), tool()])).discover()

    async def test_empty_and_malformed_names_are_rejected(self) -> None:
        for bad_name in ("", "   ", 7, None):
            with self.subTest(name=bad_name):
                with self.assertRaises(McpToolCatalogError):
                    await McpToolGateway(FakeSession([tool(bad_name)])).discover()  # type: ignore[arg-type]

    async def test_non_mapping_schema_is_rejected(self) -> None:
        with self.assertRaisesRegex(McpToolCatalogError, "non-mapping"):
            await McpToolGateway(FakeSession([tool(schema=[])] )).discover()


class InvocationGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_before_discovery_is_rejected(self) -> None:
        with self.assertRaises(McpToolGatewayNotDiscoveredError):
            await McpToolGateway(FakeSession([])).invoke("sample", {})

    async def test_unknown_tool_is_rejected(self) -> None:
        gateway = McpToolGateway(FakeSession([tool()]))
        await gateway.discover()
        with self.assertRaises(UnknownMcpToolError):
            await gateway.invoke("missing", {})

    async def test_arguments_must_be_mapping(self) -> None:
        gateway = McpToolGateway(FakeSession([tool()]))
        await gateway.discover()
        with self.assertRaisesRegex(InvalidToolArgumentsError, "mapping"):
            await gateway.invoke("sample", [])  # type: ignore[arg-type]


class ValidationTests(unittest.IsolatedAsyncioTestCase):
    async def gateway(self, schema: dict[str, Any]) -> tuple[McpToolGateway, FakeSession]:
        session = FakeSession([tool(schema=schema)])
        gateway = McpToolGateway(session)
        await gateway.discover()
        return gateway, session

    async def test_missing_required_property_is_rejected_with_path(self) -> None:
        gateway, _ = await self.gateway(
            {"type": "object", "required": ["url"], "properties": {}}
        )
        with self.assertRaisesRegex(InvalidToolArgumentsError, "arguments.url"):
            await gateway.invoke("sample", {})

    async def test_schema_meta_keyword_allows_valid_arguments_and_call(self) -> None:
        gateway, session = await self.gateway(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}},
                "additionalProperties": False,
            }
        )

        await gateway.invoke("sample", {"url": "https://example.com"})

        self.assertEqual(
            session.calls,
            [("sample", {"url": "https://example.com"})],
        )

    async def test_schema_meta_keyword_does_not_disable_validation(self) -> None:
        gateway, session = await self.gateway(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "required": ["url"],
                "properties": {"url": {"type": "string"}},
            }
        )

        with self.assertRaisesRegex(InvalidToolArgumentsError, "arguments.url"):
            await gateway.invoke("sample", {"url": 42})

        self.assertEqual(session.calls, [])

    async def test_wrong_primitive_type_is_rejected(self) -> None:
        gateway, _ = await self.gateway(
            {"type": "object", "properties": {"url": {"type": "string"}}}
        )
        with self.assertRaisesRegex(InvalidToolArgumentsError, "arguments.url"):
            await gateway.invoke("sample", {"url": 42})

    async def test_bool_is_neither_integer_nor_number(self) -> None:
        for kind in ("integer", "number"):
            with self.subTest(kind=kind):
                gateway, _ = await self.gateway(
                    {"type": "object", "properties": {"value": {"type": kind}}}
                )
                with self.assertRaises(InvalidToolArgumentsError):
                    await gateway.invoke("sample", {"value": True})

    async def test_additional_properties_false_rejects_unknown_key(self) -> None:
        gateway, _ = await self.gateway(
            {"type": "object", "properties": {}, "additionalProperties": False}
        )
        with self.assertRaisesRegex(InvalidToolArgumentsError, "arguments.extra"):
            await gateway.invoke("sample", {"extra": 1})

    async def test_additional_properties_schema_is_applied(self) -> None:
        gateway, _ = await self.gateway(
            {
                "type": "object",
                "properties": {},
                "additionalProperties": {"type": "integer"},
            }
        )
        with self.assertRaisesRegex(InvalidToolArgumentsError, "arguments.extra"):
            await gateway.invoke("sample", {"extra": "wrong"})

    async def test_enum_rejects_value(self) -> None:
        gateway, _ = await self.gateway(
            {"type": "object", "properties": {"mode": {"enum": ["safe"]}}}
        )
        with self.assertRaisesRegex(InvalidToolArgumentsError, "enum"):
            await gateway.invoke("sample", {"mode": "unsafe"})

    async def test_recursive_nested_object_validation(self) -> None:
        gateway, _ = await self.gateway(
            {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    }
                },
            }
        )
        with self.assertRaisesRegex(
            InvalidToolArgumentsError, r"arguments\.fields\.value"
        ):
            await gateway.invoke("sample", {"fields": {"value": 3}})

    async def test_recursive_array_items_have_indexed_paths(self) -> None:
        gateway, _ = await self.gateway(
            {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                        },
                    }
                },
            }
        )
        with self.assertRaisesRegex(
            InvalidToolArgumentsError, r"arguments\.fields\[0\]\.value"
        ):
            await gateway.invoke("sample", {"fields": [{"value": 3}]})

    async def test_unsupported_schema_keyword_is_rejected(self) -> None:
        gateway, _ = await self.gateway(
            {"type": "object", "properties": {"value": {"oneOf": []}}}
        )
        with self.assertRaisesRegex(UnsupportedToolSchemaError, "oneOf"):
            await gateway.invoke("sample", {"value": "anything"})

    async def test_unsupported_optional_branch_is_rejected_before_call(self) -> None:
        gateway, session = await self.gateway(
            {"type": "object", "properties": {"optional": {"pattern": "x"}}}
        )
        with self.assertRaisesRegex(UnsupportedToolSchemaError, "pattern"):
            await gateway.invoke("sample", {})
        self.assertEqual(session.calls, [])

    async def test_validation_failure_does_not_call_session(self) -> None:
        gateway, session = await self.gateway(
            {"type": "object", "required": ["url"]}
        )
        with self.assertRaises(InvalidToolArgumentsError):
            await gateway.invoke("sample", {})
        self.assertEqual(session.calls, [])


class NormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def invoke_result(self, result: object) -> object:
        gateway = McpToolGateway(FakeSession([tool()], result=result))
        await gateway.discover()
        return await gateway.invoke("sample", {})

    async def test_successful_result_joins_text_blocks(self) -> None:
        observation = await self.invoke_result(
            {"content": [{"text": " first "}, SimpleNamespace(text="second")]}
        )
        self.assertEqual(observation.status, "success")
        self.assertEqual(observation.text, "first\nsecond")
        self.assertIsNone(observation.error)

    async def test_structured_content_is_normalized(self) -> None:
        observation = await self.invoke_result(
            SimpleNamespace(
                content=[], structured_content=SimpleNamespace(answer=42)
            )
        )
        self.assertEqual(observation.structured_content, {"answer": 42})

    async def test_mcp_declared_error_preserves_text(self) -> None:
        observation = await self.invoke_result(
            {"content": [{"text": "navigation failed"}], "isError": True}
        )
        self.assertEqual(observation.status, "mcp_error")
        self.assertEqual(observation.error, "navigation failed")

    async def test_empty_mcp_error_has_clear_fallback(self) -> None:
        observation = await self.invoke_result({"content": [], "is_error": True})
        self.assertEqual(
            observation.error, "MCP tool reported an error without a message"
        )

    async def test_transport_exception_is_normalized(self) -> None:
        session = FakeSession([tool()])
        session.call_exception = RuntimeError("connection closed")
        gateway = McpToolGateway(session)
        await gateway.discover()
        observation = await gateway.invoke("sample", {})
        self.assertEqual(observation.status, "transport_error")
        self.assertEqual(observation.error, "RuntimeError: connection closed")

    async def test_base_exception_is_not_swallowed(self) -> None:
        session = FakeSession([tool()])
        session.call_exception = KeyboardInterrupt("stop")
        gateway = McpToolGateway(session)
        await gateway.discover()
        with self.assertRaises(KeyboardInterrupt):
            await gateway.invoke("sample", {})


if __name__ == "__main__":
    unittest.main()
