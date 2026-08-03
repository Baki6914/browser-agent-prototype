"""Dependency-free unit tests for MCP tool classification and enforcement."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from browser_agent.mcp_gateway import ToolDefinition, ToolObservation
from browser_agent.tool_policy import (
    TOOL_POLICY_REGISTRY,
    InternalOnlyToolError,
    McpToolPolicy,
    PolicyAction,
    PolicyEnforcedToolExecutor,
    ToolCategory,
    ToolConfirmationRequiredError,
    ToolDeniedError,
    ToolPolicyError,
)


def definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {}},
    )


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolObservation:
        self.calls.append((tool_name, dict(arguments)))
        return ToolObservation(tool_name, "success", "ok", None, None)


class ToolPolicyTests(unittest.TestCase):
    def test_base_policy_distribution(self) -> None:
        counts = {action: 0 for action in PolicyAction}
        for rule in TOOL_POLICY_REGISTRY.values():
            counts[rule.action] += 1
        self.assertEqual(counts[PolicyAction.ALLOW], 11)
        self.assertEqual(counts[PolicyAction.REQUIRE_CONFIRMATION], 8)
        self.assertEqual(counts[PolicyAction.INTERNAL_ONLY], 1)
        self.assertEqual(counts[PolicyAction.DENY], 4)

    def test_browser_navigate_is_automatic_and_remains_visible(self) -> None:
        policy = McpToolPolicy([definition("browser_navigate")])
        decision = policy.decide("browser_navigate", {})
        self.assertEqual(decision.action, PolicyAction.ALLOW)
        self.assertEqual(
            [tool.name for tool in policy.llm_visible_tools()],
            ["browser_navigate"],
        )
    def test_inventory_has_an_explicit_registry_entry_for_every_tool(self) -> None:
        inventory_path = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "mcp-tool-inventory.json"
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        names = {tool["name"] for tool in inventory["tools"]}
        self.assertEqual(names, set(TOOL_POLICY_REGISTRY))
        self.assertEqual(inventory["tool_count"], len(TOOL_POLICY_REGISTRY))

    def test_registry_is_immutable_and_preserves_denied_decision(self) -> None:
        original = TOOL_POLICY_REGISTRY["browser_evaluate"]
        with self.assertRaises(TypeError):
            TOOL_POLICY_REGISTRY["browser_evaluate"] = TOOL_POLICY_REGISTRY[
                "browser_find"
            ]  # type: ignore[index]
        self.assertIs(TOOL_POLICY_REGISTRY["browser_evaluate"], original)
        self.assertEqual(original.action, PolicyAction.DENY)

    def test_decisions_are_alphabetical(self) -> None:
        policy = McpToolPolicy(
            [definition("browser_type"), definition("browser_find")]
        )
        self.assertEqual(
            [decision.tool_name for decision in policy.decisions()],
            ["browser_find", "browser_type"],
        )

    def test_duplicate_discovered_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ToolPolicyError, "duplicate"):
            McpToolPolicy([definition("browser_find"), definition("browser_find")])

    def test_definitions_are_defensively_isolated(self) -> None:
        source = definition("browser_find")
        policy = McpToolPolicy([source])
        source.input_schema["changed"] = True
        returned = policy.llm_visible_tools()[0]
        self.assertNotIn("changed", returned.input_schema)
        returned.input_schema["later"] = True
        self.assertNotIn("later", policy.llm_visible_tools()[0].input_schema)

    def test_unknown_tool_defaults_to_deny_and_unknown_category(self) -> None:
        decision = McpToolPolicy([]).decide("browser_future", {})
        self.assertEqual(decision.action, PolicyAction.DENY)
        self.assertEqual(decision.category, ToolCategory.UNKNOWN)

    def test_unknown_discovered_tool_is_reported_unclassified(self) -> None:
        policy = McpToolPolicy(
            [definition("browser_future"), definition("browser_find")]
        )
        self.assertEqual(policy.unclassified_tool_names(), ("browser_future",))

    def test_approved_boundary_tools_are_denied(self) -> None:
        policy = McpToolPolicy([])
        for name in (
            "browser_run_code_unsafe",
            "browser_evaluate",
            "browser_file_upload",
            "browser_drop",
        ):
            with self.subTest(name=name):
                self.assertEqual(policy.decide(name, {}).action, PolicyAction.DENY)

    def test_browser_close_is_internal_only(self) -> None:
        decision = McpToolPolicy([]).decide("browser_close", {})
        self.assertEqual(decision.action, PolicyAction.INTERNAL_ONLY)
        self.assertEqual(decision.category, ToolCategory.INTERNAL_CONTROL)

    def test_internal_and_denied_tools_are_not_llm_visible(self) -> None:
        policy = McpToolPolicy(
            [
                definition("browser_close"),
                definition("browser_evaluate"),
                definition("browser_find"),
            ]
        )
        self.assertEqual(
            [tool.name for tool in policy.llm_visible_tools()],
            ["browser_find"],
        )

    def test_allow_and_confirmation_tools_are_llm_visible(self) -> None:
        policy = McpToolPolicy(
            [definition("browser_type"), definition("browser_find")]
        )
        self.assertEqual(
            [tool.name for tool in policy.llm_visible_tools()],
            ["browser_find", "browser_type"],
        )

    def test_observation_is_allowed_without_filename(self) -> None:
        decision = McpToolPolicy([]).decide("browser_snapshot", {})
        self.assertEqual(decision.action, PolicyAction.ALLOW)

    def test_non_empty_filename_escalates_allow_tool(self) -> None:
        decision = McpToolPolicy([]).decide(
            "browser_snapshot", {"filename": "snapshot.md"}
        )
        self.assertEqual(decision.action, PolicyAction.REQUIRE_CONFIRMATION)

    def test_empty_or_none_filename_does_not_escalate(self) -> None:
        policy = McpToolPolicy([])
        for filename in ("", "   ", None):
            with self.subTest(filename=filename):
                self.assertEqual(
                    policy.decide(
                        "browser_snapshot", {"filename": filename}
                    ).action,
                    PolicyAction.ALLOW,
                )

    def test_non_mapping_arguments_are_rejected(self) -> None:
        with self.assertRaisesRegex(ToolPolicyError, "mapping"):
            McpToolPolicy([]).decide("browser_find", [])  # type: ignore[arg-type]


class PolicyExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.gateway = FakeGateway()
        self.policy = McpToolPolicy(
            [
                definition("browser_find"),
                definition("browser_snapshot"),
                definition("browser_type"),
                definition("browser_close"),
                definition("browser_evaluate"),
                definition("browser_navigate"),
            ]
        )
        self.executor = PolicyEnforcedToolExecutor(self.gateway, self.policy)

    async def test_navigation_invokes_without_confirmation(self) -> None:
        await self.executor.invoke(
            "browser_navigate", {"url": "https://example.test"}
        )
        self.assertEqual(self.gateway.calls, [
            ("browser_navigate", {"url": "https://example.test"})
        ])

    async def test_allow_invokes_gateway(self) -> None:
        result = await self.executor.invoke("browser_find", {"text": "ready"})
        self.assertEqual(result.status, "success")
        self.assertEqual(self.gateway.calls, [("browser_find", {"text": "ready"})])

    async def test_confirmation_tool_is_blocked_without_confirmation(self) -> None:
        with self.assertRaises(ToolConfirmationRequiredError):
            await self.executor.invoke("browser_type", {"text": "value"})
        self.assertEqual(self.gateway.calls, [])

    async def test_confirmation_tool_invokes_after_confirmation(self) -> None:
        await self.executor.invoke(
            "browser_type", {"text": "value"}, confirmation_granted=True
        )
        self.assertEqual(
            self.gateway.calls, [("browser_type", {"text": "value"})]
        )

    async def test_explicit_false_blocks_confirmation_tool(self) -> None:
        with self.assertRaises(ToolConfirmationRequiredError):
            await self.executor.invoke(
                "browser_type", {"text": "value"}, confirmation_granted=False
            )
        self.assertEqual(self.gateway.calls, [])

    async def test_confirmation_string_is_rejected_before_gateway(self) -> None:
        with self.assertRaises(ToolPolicyError):
            await self.executor.invoke(
                "browser_type",
                {"text": "value"},
                confirmation_granted="true",  # type: ignore[arg-type]
            )
        self.assertEqual(self.gateway.calls, [])

    async def test_confirmation_integer_is_rejected_before_gateway(self) -> None:
        with self.assertRaises(ToolPolicyError):
            await self.executor.invoke(
                "browser_type",
                {"text": "value"},
                confirmation_granted=1,  # type: ignore[arg-type]
            )
        self.assertEqual(self.gateway.calls, [])

    async def test_other_truthy_non_booleans_are_rejected_before_gateway(
        self,
    ) -> None:
        for value in ("yes", [1], {"confirmed": True}):
            with self.subTest(value=value):
                with self.assertRaises(ToolPolicyError):
                    await self.executor.invoke(
                        "browser_type",
                        {"text": "value"},
                        confirmation_granted=value,  # type: ignore[arg-type]
                    )
                self.assertEqual(self.gateway.calls, [])

    async def test_allow_tool_rejects_non_boolean_confirmation_before_gateway(
        self,
    ) -> None:
        with self.assertRaises(ToolPolicyError):
            await self.executor.invoke(
                "browser_find",
                {"text": "ready"},
                confirmation_granted="yes",  # type: ignore[arg-type]
            )
        self.assertEqual(self.gateway.calls, [])

    async def test_deny_does_not_invoke_gateway(self) -> None:
        with self.assertRaises(ToolDeniedError):
            await self.executor.invoke("browser_evaluate", {"function": "() => 1"})
        self.assertEqual(self.gateway.calls, [])

    async def test_internal_only_is_blocked_on_normal_path(self) -> None:
        with self.assertRaises(InternalOnlyToolError):
            await self.executor.invoke("browser_close", {})
        self.assertEqual(self.gateway.calls, [])

    async def test_internal_path_invokes_browser_close(self) -> None:
        await self.executor.invoke_internal("browser_close", {})
        self.assertEqual(self.gateway.calls, [("browser_close", {})])

    async def test_internal_path_cannot_bypass_deny(self) -> None:
        with self.assertRaises(ToolDeniedError):
            await self.executor.invoke_internal(
                "browser_evaluate", {"function": "() => 1"}
            )
        self.assertEqual(self.gateway.calls, [])

    async def test_internal_path_rejects_normal_allow_tool(self) -> None:
        with self.assertRaisesRegex(ToolPolicyError, "not internal-only"):
            await self.executor.invoke_internal("browser_find", {})
        self.assertEqual(self.gateway.calls, [])

    async def test_confirmation_flag_is_not_forwarded_as_tool_argument(self) -> None:
        await self.executor.invoke(
            "browser_type", {"text": "value"}, confirmation_granted=True
        )
        self.assertNotIn("confirmation_granted", self.gateway.calls[0][1])

    async def test_filename_escalation_is_enforced(self) -> None:
        arguments = {"filename": "snapshot.md"}
        with self.assertRaises(ToolConfirmationRequiredError):
            await self.executor.invoke("browser_snapshot", arguments)
        self.assertEqual(self.gateway.calls, [])
        await self.executor.invoke(
            "browser_snapshot", arguments, confirmation_granted=True
        )
        self.assertEqual(
            self.gateway.calls, [("browser_snapshot", arguments)]
        )

    async def test_non_mapping_arguments_fail_before_gateway(self) -> None:
        with self.assertRaises(ToolPolicyError):
            await self.executor.invoke("browser_find", [])  # type: ignore[arg-type]
        self.assertEqual(self.gateway.calls, [])
