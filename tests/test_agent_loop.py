"""Focused unit tests for the deterministic MCP-backed agent loop."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import Any

from browser_agent.agent_controls import AgentControlExecutor
from browser_agent.agent_loop import (
    AgentLoopContext,
    AgentRunStatus,
    AgentStepStatus,
    AgentToolCall,
    AgentToolCatalogError,
    AgentToolRouter,
    AgentToolSource,
    DeterministicAgentLoop,
    ScriptedDecisionSource,
)
from browser_agent.mcp_gateway import ToolDefinition, ToolObservation
from browser_agent.tool_policy import ToolConfirmationRequiredError
from scripts.mcp_agent_loop_smoke import AgentLoopSmokeError, _run_loop


def tool_definition(name: str = "browser_snapshot") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"{name} description",
        input_schema={
            "type": "object",
            "properties": {"nested": {"type": "object"}},
        },
    )


class FakePolicyExecutor:
    def __init__(
        self,
        observation: ToolObservation | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.observation = observation or ToolObservation(
            "browser_snapshot", "success", "page snapshot", None, None
        )
        self.error = error

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> ToolObservation:
        self.calls.append(
            (tool_name, dict(arguments), confirmation_granted)
        )
        if self.error is not None:
            raise self.error
        return self.observation


class SequentialFakePolicyExecutor:
    def __init__(self, observations: list[ToolObservation]) -> None:
        self.observations = observations
        self.index = 0

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> ToolObservation:
        del tool_name, arguments, confirmation_granted
        observation = self.observations[self.index]
        self.index += 1
        return observation


class RecordingControls(AgentControlExecutor):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, tool_name: str, arguments: Mapping[str, Any]):
        self.calls.append((tool_name, dict(arguments)))
        return super().execute(tool_name, arguments)


class RecordingDecisionSource:
    def __init__(self, decisions: list[AgentToolCall]) -> None:
        self.decisions = decisions
        self.contexts: list[AgentLoopContext] = []
        self.index = 0

    async def next_decision(
        self, context: AgentLoopContext
    ) -> AgentToolCall | None:
        self.contexts.append(context)
        if self.index == len(self.decisions):
            return None
        decision = self.decisions[self.index]
        self.index += 1
        return decision


class AgentToolCallTests(unittest.TestCase):
    def test_constructor_rejects_non_mapping_arguments(self) -> None:
        with self.assertRaises(TypeError):
            AgentToolCall("browser_snapshot", [])  # type: ignore[arg-type]

    def test_constructor_deep_copies_nested_arguments(self) -> None:
        original = {"nested": {"values": [1]}}
        call = AgentToolCall("browser_snapshot", original)
        original["nested"]["values"].append(2)
        self.assertEqual(call.arguments, {"nested": {"values": [1]}})

    def test_arguments_access_returns_defensive_nested_copies(self) -> None:
        call = AgentToolCall("browser_snapshot", {"nested": {"values": [1]}})
        returned = call.arguments
        returned["nested"]["values"].append(2)
        self.assertEqual(call.arguments, {"nested": {"values": [1]}})


class AgentToolRouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.executor = FakePolicyExecutor()
        self.controls = RecordingControls()
        self.router = AgentToolRouter(
            self.executor, [tool_definition()], self.controls
        )

    def test_combined_definitions_are_alphabetical_and_typed(self) -> None:
        definitions = self.router.definitions()
        self.assertEqual(
            [definition.name for definition in definitions],
            ["ask_user", "browser_snapshot", "finish"],
        )
        self.assertEqual(
            [definition.source for definition in definitions],
            [
                AgentToolSource.AGENT_CONTROL,
                AgentToolSource.MCP,
                AgentToolSource.AGENT_CONTROL,
            ],
        )

    def test_duplicate_mcp_name_fails_closed(self) -> None:
        with self.assertRaises(AgentToolCatalogError):
            AgentToolRouter(
                self.executor,
                [tool_definition(), tool_definition()],
                self.controls,
            )

    def test_mcp_control_collision_fails_closed(self) -> None:
        with self.assertRaises(AgentToolCatalogError):
            AgentToolRouter(
                self.executor, [tool_definition("finish")], self.controls
            )

    def test_returned_schemas_are_defensively_isolated(self) -> None:
        source = tool_definition()
        router = AgentToolRouter(self.executor, [source], self.controls)
        source.input_schema["properties"]["nested"]["changed"] = True
        returned = next(
            item
            for item in router.definitions()
            if item.name == "browser_snapshot"
        )
        self.assertNotIn(
            "changed", returned.input_schema["properties"]["nested"]
        )
        returned.input_schema["properties"]["nested"]["later"] = True
        fresh = next(
            item
            for item in router.definitions()
            if item.name == "browser_snapshot"
        )
        self.assertNotIn("later", fresh.input_schema["properties"]["nested"])

    async def test_finish_routes_only_to_agent_controls(self) -> None:
        observation = await self.router.route(
            AgentToolCall("finish", {"result": "done"})
        )
        self.assertEqual(observation.status, AgentStepStatus.FINISHED)
        self.assertEqual(self.controls.calls, [("finish", {"result": "done"})])
        self.assertEqual(self.executor.calls, [])

    async def test_ask_user_routes_only_to_agent_controls(self) -> None:
        observation = await self.router.route(
            AgentToolCall("ask_user", {"question": "Which account?"})
        )
        self.assertEqual(observation.status, AgentStepStatus.AWAITING_USER)
        self.assertEqual(observation.source, AgentToolSource.AGENT_CONTROL)
        self.assertEqual(self.executor.calls, [])

    async def test_mcp_routes_only_to_policy_with_false_confirmation(self) -> None:
        observation = await self.router.route(
            AgentToolCall(
                "browser_snapshot",
                {"confirmation_granted": True},
            )
        )
        self.assertEqual(observation.status, AgentStepStatus.SUCCESS)
        self.assertEqual(self.controls.calls, [])
        self.assertEqual(
            self.executor.calls,
            [
                (
                    "browser_snapshot",
                    {"confirmation_granted": True},
                    False,
                )
            ],
        )

    async def test_unknown_tool_calls_neither_executor(self) -> None:
        observation = await self.router.route(AgentToolCall("unknown", {}))
        self.assertEqual(observation.status, AgentStepStatus.REJECTED)
        self.assertIsNone(observation.source)
        self.assertEqual(self.controls.calls, [])
        self.assertEqual(self.executor.calls, [])

    async def test_policy_rejection_is_visible(self) -> None:
        error = ToolConfirmationRequiredError("trusted approval required")
        router = AgentToolRouter(
            FakePolicyExecutor(error=error),
            [tool_definition()],
            self.controls,
        )
        observation = await router.route(
            AgentToolCall("browser_snapshot", {})
        )
        self.assertEqual(observation.status, AgentStepStatus.REJECTED)
        self.assertIn("ToolConfirmationRequiredError", observation.error or "")
        self.assertIn("trusted approval required", observation.error or "")

    async def test_unexpected_programming_error_propagates(self) -> None:
        router = AgentToolRouter(
            FakePolicyExecutor(error=RuntimeError("bug")),
            [tool_definition()],
            self.controls,
        )
        with self.assertRaisesRegex(RuntimeError, "bug"):
            await router.route(AgentToolCall("browser_snapshot", {}))


class DeterministicAgentLoopTests(unittest.IsolatedAsyncioTestCase):
    def make_loop(
        self,
        decisions: list[AgentToolCall],
        *,
        max_steps: int = 5,
        executor: FakePolicyExecutor | None = None,
    ) -> tuple[
        DeterministicAgentLoop,
        RecordingDecisionSource,
        FakePolicyExecutor,
    ]:
        actual_executor = executor or FakePolicyExecutor()
        source = RecordingDecisionSource(decisions)
        router = AgentToolRouter(
            actual_executor,
            [tool_definition()],
            RecordingControls(),
        )
        return (
            DeterministicAgentLoop(source, router, max_steps),
            source,
            actual_executor,
        )

    async def test_rejection_can_be_followed_by_ask_user_without_retry(self) -> None:
        executor = FakePolicyExecutor(
            error=ToolConfirmationRequiredError("approval required")
        )
        loop, _, _ = self.make_loop(
            [
                AgentToolCall("browser_snapshot", {}),
                AgentToolCall("ask_user", {"question": "Approve?"}),
            ],
            executor=executor,
        )
        result = await loop.run("Inspect")
        self.assertEqual(result.status, AgentRunStatus.AWAITING_USER)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(
            [step.observation.status for step in result.steps],
            [AgentStepStatus.REJECTED, AgentStepStatus.AWAITING_USER],
        )

    async def test_browser_observation_reaches_next_context(self) -> None:
        loop, source, _ = self.make_loop(
            [
                AgentToolCall("browser_snapshot", {}),
                AgentToolCall("finish", {"result": "done"}),
            ]
        )
        await loop.run("  preserve me  ")
        self.assertEqual(source.contexts[1].task, "  preserve me  ")
        self.assertEqual(len(source.contexts[1].steps), 1)
        self.assertEqual(
            source.contexts[1].steps[0].observation.text, "page snapshot"
        )

    async def test_finish_stops_before_later_decisions(self) -> None:
        loop, source, _ = self.make_loop(
            [
                AgentToolCall("finish", {"result": "done"}),
                AgentToolCall("browser_snapshot", {}),
            ]
        )
        result = await loop.run("Inspect")
        self.assertEqual(result.status, AgentRunStatus.FINISHED)
        self.assertEqual(result.final_result, "done")
        self.assertEqual(source.index, 1)

    async def test_ask_user_stops_before_later_decisions(self) -> None:
        loop, source, _ = self.make_loop(
            [
                AgentToolCall("ask_user", {"question": "Which one?"}),
                AgentToolCall("finish", {"result": "unused"}),
            ]
        )
        result = await loop.run("Inspect")
        self.assertEqual(result.status, AgentRunStatus.AWAITING_USER)
        self.assertEqual(result.question, "Which one?")
        self.assertEqual(source.index, 1)

    async def test_script_exhaustion_returns_typed_status(self) -> None:
        router = AgentToolRouter(
            FakePolicyExecutor(), [tool_definition()], RecordingControls()
        )
        loop = DeterministicAgentLoop(ScriptedDecisionSource([]), router, 2)
        result = await loop.run("Inspect")
        self.assertEqual(
            result.status, AgentRunStatus.DECISION_SOURCE_EXHAUSTED
        )
        self.assertEqual(result.steps, ())

    async def test_max_steps_returns_typed_status(self) -> None:
        loop, _, _ = self.make_loop(
            [
                AgentToolCall("browser_snapshot", {}),
                AgentToolCall("finish", {"result": "unused"}),
            ],
            max_steps=1,
        )
        result = await loop.run("Inspect")
        self.assertEqual(result.status, AgentRunStatus.STEP_LIMIT_REACHED)
        self.assertEqual(len(result.steps), 1)

    async def test_invalid_task_is_rejected(self) -> None:
        loop, _, _ = self.make_loop([])
        for task in ("", "   ", None):
            with self.subTest(task=task):
                with self.assertRaises((TypeError, ValueError)):
                    await loop.run(task)  # type: ignore[arg-type]

    def test_invalid_step_limits_are_rejected(self) -> None:
        router = AgentToolRouter(
            FakePolicyExecutor(), [tool_definition()], RecordingControls()
        )
        for limit in (True, False, 0, -1, "1", 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises((TypeError, ValueError)):
                    DeterministicAgentLoop(
                        ScriptedDecisionSource([]),
                        router,
                        limit,  # type: ignore[arg-type]
                    )


class AgentLoopSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_navigate_mcp_error_before_later_finish(self) -> None:
        executor = SequentialFakePolicyExecutor(
            [
                ToolObservation(
                    "browser_navigate",
                    "mcp_error",
                    "",
                    None,
                    "navigation failed",
                ),
                ToolObservation(
                    "browser_snapshot",
                    "success",
                    "snapshot",
                    None,
                    None,
                ),
            ]
        )

        with self.assertRaises(AgentLoopSmokeError) as raised:
            await _run_loop(
                executor,
                (
                    tool_definition("browser_navigate"),
                    tool_definition("browser_snapshot"),
                ),
            )

        message = str(raised.exception)
        self.assertIn("browser_navigate", message)
        self.assertIn("mcp_error", message)
        self.assertIn("navigation failed", message)

    async def test_rejects_snapshot_transport_error_before_later_finish(
        self,
    ) -> None:
        executor = SequentialFakePolicyExecutor(
            [
                ToolObservation(
                    "browser_navigate",
                    "success",
                    "navigated",
                    None,
                    None,
                ),
                ToolObservation(
                    "browser_snapshot",
                    "transport_error",
                    "",
                    None,
                    "connection closed",
                ),
            ]
        )

        with self.assertRaises(AgentLoopSmokeError) as raised:
            await _run_loop(
                executor,
                (
                    tool_definition("browser_navigate"),
                    tool_definition("browser_snapshot"),
                ),
            )

        message = str(raised.exception)
        self.assertIn("browser_snapshot", message)
        self.assertIn("transport_error", message)
        self.assertIn("connection closed", message)
