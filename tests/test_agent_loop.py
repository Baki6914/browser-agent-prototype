"""Focused unit tests for the deterministic MCP-backed agent loop."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from collections.abc import Mapping
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_agent.agent_controls import AgentControlExecutor
from browser_agent.agent_loop import (
    AgentLoopContext,
    AgentPauseKind,
    AgentRunStatus,
    AgentSessionConstructionError,
    AgentSessionStateError,
    AgentStepObservation,
    AgentStepStatus,
    AgentToolCall,
    AgentToolCatalogError,
    AgentToolRouter,
    AgentToolSource,
    AgentUserResponseError,
    DeterministicAgentLoop,
    PendingConfirmation,
    ResumableAgentSession,
    ScriptedDecisionSource,
    SecretField,
    SecretFieldTarget,
)
from browser_agent.mcp_gateway import (
    InvalidToolArgumentsError,
    ToolDefinition,
    ToolObservation,
)
from browser_agent.tool_policy import (
    ToolConfirmationRequiredError,
    ToolDeniedError,
)
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


class ConfirmationExecutor:
    def __init__(self, *, replay_error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []
        self.replay_error = replay_error
        self.replay_started = asyncio.Event()
        self.release_replay = asyncio.Event()
        self.block_replay = False

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> ToolObservation:
        self.calls.append((tool_name, dict(arguments), confirmation_granted))
        if not confirmation_granted:
            raise ToolConfirmationRequiredError("approval required")
        self.replay_started.set()
        if self.block_replay:
            await self.release_replay.wait()
        if self.replay_error is not None:
            raise self.replay_error
        return ToolObservation(tool_name, "success", "replayed", None, None)


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
            ["ask_user", "browser_snapshot", "finish", "request_secret"],
        )
        self.assertEqual(
            [definition.source for definition in definitions],
            [
                AgentToolSource.AGENT_CONTROL,
                AgentToolSource.MCP,
                AgentToolSource.AGENT_CONTROL,
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
        self.assertIs(observation.confirmation_required, True)
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


class ResumableAgentSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_session(
        self,
        decisions: list[AgentToolCall],
        *,
        executor: Any | None = None,
        max_steps: int = 8,
    ) -> tuple[ResumableAgentSession, RecordingDecisionSource, Any]:
        actual_executor = executor or FakePolicyExecutor()
        source = RecordingDecisionSource(decisions)
        router = AgentToolRouter(
            actual_executor,
            [tool_definition()],
            RecordingControls(),
        )
        return (
            ResumableAgentSession(source, router, max_steps),
            source,
            actual_executor,
        )

    def confirmation_session(
        self,
        *,
        executor: ConfirmationExecutor | None = None,
        max_steps: int = 8,
        arguments: dict[str, Any] | None = None,
        reference: Any = 1,
        later: list[AgentToolCall] | None = None,
    ) -> tuple[
        ResumableAgentSession, RecordingDecisionSource, ConfirmationExecutor
    ]:
        actual_executor = executor or ConfirmationExecutor()
        session, source, _ = self.make_session(
            [
                AgentToolCall(
                    "browser_snapshot",
                    (
                        arguments
                        if arguments is not None
                        else {"nested": {"values": [1]}, "café": True}
                    ),
                ),
                AgentToolCall(
                    "ask_user",
                    {
                        "question": "Approve this exact action?",
                        "confirmation_for_step": reference,
                    },
                ),
                *(later or []),
            ],
            executor=actual_executor,
            max_steps=max_steps,
        )
        return session, source, actual_executor

    async def test_start_finish_and_terminal_transitions(self) -> None:
        session, _, _ = self.make_session(
            [AgentToolCall("finish", {"result": "done"})]
        )
        result = await session.start("Inspect")
        self.assertEqual(result.status, AgentRunStatus.FINISHED)
        with self.assertRaises(AgentSessionStateError):
            await session.start("again")
        with self.assertRaises(AgentSessionStateError):
            await session.respond("again")

    async def test_normal_response_resumes_history_and_context(self) -> None:
        session, source, _ = self.make_session(
            [
                AgentToolCall("ask_user", {"question": "Which period?"}),
                AgentToolCall("finish", {"result": "done"}),
            ]
        )
        paused = await session.start("Inspect")
        self.assertEqual(paused.pause_kind, AgentPauseKind.USER_INPUT)
        self.assertIsNone(paused.pending_confirmation)
        result = await session.respond("2026")
        self.assertEqual(result.status, AgentRunStatus.FINISHED)
        self.assertEqual([step.step_number for step in result.steps], [1, 2])
        interaction = source.contexts[1].user_interactions[0]
        self.assertEqual(interaction.after_step_number, 1)
        self.assertEqual(interaction.response, "2026")
        with self.assertRaises(AgentSessionStateError):
            await session.confirm(True)

    async def test_invalid_normal_response_is_rejected(self) -> None:
        session, _, _ = self.make_session(
            [AgentToolCall("ask_user", {"question": "Which?"})]
        )
        await session.start("Inspect")
        for response in ("", " ", True, 1):
            with self.subTest(response=response):
                with self.assertRaises(AgentUserResponseError):
                    await session.respond(response)  # type: ignore[arg-type]

    async def test_step_limit_is_shared_across_response(self) -> None:
        session, source, _ = self.make_session(
            [
                AgentToolCall("ask_user", {"question": "Which?"}),
                AgentToolCall("browser_snapshot", {}),
                AgentToolCall("finish", {"result": "unused"}),
            ],
            max_steps=2,
        )
        await session.start("Inspect")
        result = await session.respond("one")
        self.assertEqual(result.status, AgentRunStatus.STEP_LIMIT_REACHED)
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(source.index, 2)

    async def test_matching_confirmation_binds_and_replays_exact_call(self) -> None:
        original = {"nested": {"values": [1]}, "café": True}
        session, source, executor = self.confirmation_session(
            arguments=original,
            later=[AgentToolCall("finish", {"result": "done"})],
        )
        paused = await session.start("Inspect")
        self.assertEqual(paused.pause_kind, AgentPauseKind.CONFIRMATION)
        self.assertEqual(len(executor.calls), 1)
        pending = paused.pending_confirmation
        self.assertIsNotNone(pending)
        original["nested"]["values"].append(2)
        returned = pending.arguments  # type: ignore[union-attr]
        returned["nested"]["values"].append(3)
        result = await session.confirm(True)
        self.assertEqual(result.status, AgentRunStatus.FINISHED)
        self.assertEqual(
            executor.calls,
            [
                (
                    "browser_snapshot",
                    {"nested": {"values": [1]}, "café": True},
                    False,
                ),
                (
                    "browser_snapshot",
                    {"café": True, "nested": {"values": [1]}},
                    True,
                ),
            ],
        )
        self.assertEqual(result.steps[2].replay_of_step_number, 1)
        self.assertEqual(result.steps[2].step_number, 3)
        self.assertIs(
            source.contexts[-1].user_interactions[0].response, True
        )

    async def test_confirmation_rejection_does_not_replay(self) -> None:
        session, source, executor = self.confirmation_session(
            later=[AgentToolCall("finish", {"result": "safe"})]
        )
        await session.start("Inspect")
        result = await session.confirm(False)
        self.assertEqual(result.status, AgentRunStatus.FINISHED)
        self.assertEqual(len(executor.calls), 1)
        self.assertIs(
            source.contexts[-1].user_interactions[0].response, False
        )

    async def test_wrong_or_unsafe_associations_fall_back_to_user_input(self) -> None:
        for arguments, reference in (
            ({}, 2),
            ({"value": float("nan")}, 1),
            ({1: "not a string key"}, 1),
            ({"nested": ("not", "a", "list")}, 1),
        ):
            with self.subTest(arguments=arguments, reference=reference):
                session, _, _ = self.confirmation_session(
                    arguments=arguments, reference=reference
                )
                result = await session.start("Inspect")
                self.assertEqual(result.pause_kind, AgentPauseKind.USER_INPUT)
                self.assertIsNone(result.pending_confirmation)

    def test_direct_pending_confirmation_rejects_unsafe_or_noncanonical_json(
        self,
    ) -> None:
        for canonical in (
            '{"value":NaN}',
            '{"value":Infinity}',
            '{"value":-Infinity}',
            '{"value": 1}',
            '{"z":1,"a":2}',
            '{"value":1,"value":2}',
            '[]',
        ):
            with self.subTest(canonical=canonical):
                with self.assertRaises(AgentSessionConstructionError):
                    PendingConfirmation(1, "browser_snapshot", canonical)

    def test_valid_pending_arguments_are_fresh_independent_mappings(self) -> None:
        pending = PendingConfirmation(
            1,
            "browser_snapshot",
            '{"nested":{"items":[{"value":1}]},"text":"café"}',
        )
        first = pending.arguments
        second = pending.arguments
        self.assertIsNot(first, second)
        self.assertIsNot(first["nested"], second["nested"])
        first["nested"]["items"][0]["value"] = 2
        self.assertEqual(second["nested"]["items"][0]["value"], 1)
        self.assertEqual(pending.arguments["nested"]["items"][0]["value"], 1)

    async def test_generic_deny_does_not_create_confirmation(self) -> None:
        session, _, _ = self.make_session(
            [
                AgentToolCall("browser_snapshot", {}),
                AgentToolCall(
                    "ask_user",
                    {"question": "Approve?", "confirmation_for_step": 1},
                ),
            ],
            executor=FakePolicyExecutor(error=ToolDeniedError("denied")),
        )
        result = await session.start("Inspect")
        self.assertEqual(result.pause_kind, AgentPauseKind.USER_INPUT)

    async def test_invalid_tool_arguments_do_not_create_confirmation(self) -> None:
        session, _, _ = self.make_session(
            [
                AgentToolCall("browser_snapshot", {}),
                AgentToolCall(
                    "ask_user",
                    {"question": "Approve?", "confirmation_for_step": 1},
                ),
            ],
            executor=FakePolicyExecutor(
                error=InvalidToolArgumentsError("invalid")
            ),
        )
        result = await session.start("Inspect")
        self.assertEqual(result.pause_kind, AgentPauseKind.USER_INPUT)

    async def test_intervening_call_invalidates_older_candidate(self) -> None:
        executor = ConfirmationExecutor()
        session, _, _ = self.make_session(
            [
                AgentToolCall("browser_snapshot", {"attempt": 1}),
                AgentToolCall("browser_snapshot", {"attempt": 2}),
                AgentToolCall(
                    "ask_user",
                    {"question": "Approve first?", "confirmation_for_step": 1},
                ),
            ],
            executor=executor,
        )
        result = await session.start("Inspect")
        self.assertEqual(result.pause_kind, AgentPauseKind.USER_INPUT)

    async def test_rejected_control_call_invalidates_candidate(self) -> None:
        session, _, _ = self.make_session(
            [
                AgentToolCall("browser_snapshot", {"attempt": 1}),
                AgentToolCall(
                    "ask_user",
                    {"question": "Invalid", "confirmation_for_step": True},
                ),
                AgentToolCall(
                    "ask_user",
                    {"question": "Approve?", "confirmation_for_step": 1},
                ),
            ],
            executor=ConfirmationExecutor(),
        )
        result = await session.start("Inspect")
        self.assertEqual(result.pause_kind, AgentPauseKind.USER_INPUT)
        self.assertIsNone(result.pending_confirmation)

    async def test_confirmation_and_response_state_are_distinct(self) -> None:
        session, _, _ = self.confirmation_session()
        await session.start("Inspect")
        with self.assertRaises(AgentSessionStateError):
            await session.respond("yes")
        with self.assertRaises(AgentUserResponseError):
            await session.confirm(1)  # type: ignore[arg-type]

    async def test_concurrent_and_sequential_confirm_are_single_use(self) -> None:
        executor = ConfirmationExecutor()
        executor.block_replay = True
        session, _, _ = self.confirmation_session(executor=executor)
        await session.start("Inspect")
        first = asyncio.create_task(session.confirm(True))
        await executor.replay_started.wait()
        self.assertIsNone(session.pending_confirmation)
        second = asyncio.create_task(session.confirm(True))
        executor.release_replay.set()
        await first
        with self.assertRaises(AgentSessionStateError):
            await second
        with self.assertRaises(AgentSessionStateError):
            await session.confirm(True)
        self.assertEqual(sum(call[2] for call in executor.calls), 1)

    async def test_replay_failure_and_exception_cannot_retry(self) -> None:
        for error in (
            ToolDeniedError("denied"),
            RuntimeError("bug"),
            TimeoutError("timed out"),
        ):
            with self.subTest(error=error):
                executor = ConfirmationExecutor(replay_error=error)
                session, _, _ = self.confirmation_session(executor=executor)
                await session.start("Inspect")
                if isinstance(error, (RuntimeError, TimeoutError)):
                    with self.assertRaises(type(error)):
                        await session.confirm(True)
                else:
                    await session.confirm(True)
                with self.assertRaises(AgentSessionStateError):
                    await session.confirm(True)
                self.assertEqual(sum(call[2] for call in executor.calls), 1)

    async def test_normalized_replay_errors_cannot_be_confirmed_again(
        self,
    ) -> None:
        for status in ("mcp_error", "transport_error"):
            with self.subTest(status=status):
                executor = ConfirmationExecutor()
                session, _, _ = self.confirmation_session(executor=executor)
                await session.start("Inspect")
                executor.replay_error = None

                async def replay_error(
                    tool_name: str,
                    arguments: Mapping[str, Any],
                    *,
                    confirmation_granted: bool = False,
                ) -> ToolObservation:
                    executor.calls.append(
                        (tool_name, dict(arguments), confirmation_granted)
                    )
                    return ToolObservation(
                        tool_name, status, "", None, f"{status} detail"
                    )

                executor.invoke = replay_error  # type: ignore[method-assign]
                await session.confirm(True)
                with self.assertRaises(AgentSessionStateError):
                    await session.confirm(True)
                self.assertEqual(sum(call[2] for call in executor.calls), 1)

    async def test_rejection_consumes_confirmation_permanently(self) -> None:
        session, _, executor = self.confirmation_session(
            later=[AgentToolCall("ask_user", {"question": "Continue?"})]
        )
        await session.start("Inspect")
        result = await session.confirm(False)
        self.assertEqual(result.pause_kind, AgentPauseKind.USER_INPUT)
        with self.assertRaises(AgentSessionStateError):
            await session.confirm(True)
        self.assertEqual(sum(call[2] for call in executor.calls), 0)

    async def test_cancellation_during_replay_cannot_retry(self) -> None:
        executor = ConfirmationExecutor()
        executor.block_replay = True
        session, _, _ = self.confirmation_session(executor=executor)
        await session.start("Inspect")
        replay = asyncio.create_task(session.confirm(True))
        await executor.replay_started.wait()
        replay.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await replay
        with self.assertRaises(AgentSessionStateError):
            await session.confirm(True)
        self.assertEqual(sum(call[2] for call in executor.calls), 1)

    async def test_replay_respects_full_and_final_step_limits(self) -> None:
        full, _, full_executor = self.confirmation_session(max_steps=2)
        await full.start("Inspect")
        full_result = await full.confirm(True)
        self.assertEqual(
            full_result.status, AgentRunStatus.STEP_LIMIT_REACHED
        )
        self.assertEqual(sum(call[2] for call in full_executor.calls), 0)

        final, source, final_executor = self.confirmation_session(max_steps=3)
        await final.start("Inspect")
        final_result = await final.confirm(True)
        self.assertEqual(
            final_result.status, AgentRunStatus.STEP_LIMIT_REACHED
        )
        self.assertEqual(sum(call[2] for call in final_executor.calls), 1)
        self.assertEqual(source.index, 2)
        self.assertEqual(len(final_result.steps), 3)


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


class SecretPauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_and_session_pause_with_metadata_only(self) -> None:
        router = AgentToolRouter(FakePolicyExecutor(), (), AgentControlExecutor())
        decision = AgentToolCall(
            "request_secret",
            {"question": "Enter credentials", "fields": ["username", "password"], "targets": [
                {"field": "username", "name": "Email", "ref": "u"},
                {"field": "password", "name": "Password", "ref": "p"},
            ]},
        )
        observation = await router.route(decision)
        self.assertEqual(observation.status, AgentStepStatus.AWAITING_SECRET)
        self.assertEqual(observation.secret_fields, (SecretField.PASSWORD, SecretField.USERNAME))
        session = ResumableAgentSession(ScriptedDecisionSource([decision]), router, 3)
        paused = await session.start("task")
        self.assertEqual(paused.status, AgentRunStatus.AWAITING_USER)
        self.assertEqual(paused.pause_kind, AgentPauseKind.SECRET)
        self.assertEqual(paused.secret_fields, (SecretField.PASSWORD, SecretField.USERNAME))
        self.assertEqual(paused.user_interactions, ())
        with self.assertRaises(AgentSessionStateError):
            await session.respond("synthetic")
        with self.assertRaises(AgentSessionStateError):
            await session.confirm(True)

    async def test_secret_application_event_resumes_without_user_input(self) -> None:
        router = AgentToolRouter(FakePolicyExecutor(), (), AgentControlExecutor())
        decisions = [
            AgentToolCall("request_secret", {"question": "Enter code", "fields": ["otp"],
                "targets": [{"field": "otp", "name": "Code", "ref": "otp-ref"}]}),
            AgentToolCall("finish", {"result": "done"}),
        ]
        source = ScriptedDecisionSource(decisions)
        session = ResumableAgentSession(source, router, 4)
        paused = await session.start("task")
        resumed = await session.resume_after_secret_application((SecretField.OTP,))
        self.assertEqual(resumed.status, AgentRunStatus.FINISHED)
        self.assertEqual(resumed.user_interactions, ())
        self.assertEqual(len(resumed.application_events), 1)
        event = resumed.application_events[0]
        self.assertEqual(event.fields, (SecretField.OTP,))
        self.assertFalse(hasattr(event, "secret_targets"))
        self.assertNotIn("otp-ref", repr(event))
        with self.assertRaises(TypeError):
            await session.resume_after_secret_application((SecretField.OTP,), "raw")  # type: ignore[call-arg]

    def test_agent_user_input_rejects_secret_kind(self) -> None:
        from browser_agent import AgentUserInput
        with self.assertRaises(AgentUserResponseError):
            AgentUserInput(1, AgentPauseKind.SECRET, "Question", "synthetic")

    def test_provider_definition_has_categories_only(self) -> None:
        router = AgentToolRouter(FakePolicyExecutor(), (), AgentControlExecutor())
        definition = next(item for item in router.definitions() if item.name == "request_secret")
        rendered = str(definition.input_schema)
        self.assertIn("username", rendered)
        self.assertNotIn("otp_value", rendered)

    async def test_invalid_secret_pause_metadata_fails_without_resume_state(self) -> None:
        decision = AgentToolCall("request_secret", {})
        cases = (
            {"text": "Question", "secret_fields": ("password",)},
            {"text": "Question", "secret_fields": (SecretField.USERNAME, SecretField.PASSWORD)},
            {"text": "Question", "secret_fields": (SecretField.OTP, SecretField.OTP)},
            {"text": "Question", "secret_fields": ()},
            {"text": None, "secret_fields": (SecretField.OTP,)},
            {"text": "   ", "secret_fields": (SecretField.OTP,)},
            {
                "text": "Question",
                "secret_fields": (SecretField.OTP,),
                "confirmation_required": True,
            },
            {
                "text": "Question",
                "secret_fields": (SecretField.OTP,),
                "confirmation_for_step": 1,
            },
        )

        class InvalidSecretRouter:
            def __init__(self, fields: dict[str, object]) -> None:
                self.fields = fields

            def definitions(self):
                return ()

            async def route(self, _decision, *, confirmation_granted=False):
                return AgentStepObservation(
                    tool_name="request_secret",
                    source=AgentToolSource.AGENT_CONTROL,
                    status=AgentStepStatus.AWAITING_SECRET,
                    error=None,
                    **self.fields,
                )

        for metadata in cases:
            with self.subTest(metadata=tuple(metadata)):
                source = ScriptedDecisionSource([decision])
                session = ResumableAgentSession(
                    source, InvalidSecretRouter(metadata), 2  # type: ignore[arg-type]
                )
                with self.assertRaises(AgentSessionStateError):
                    await session.start("task")
                self.assertIsNone(session.pause_kind)
                self.assertIsNone(session.pending_confirmation)
                self.assertEqual(session.user_interactions, ())
                self.assertEqual(source._index, 1)
                with self.assertRaises(AgentSessionStateError):
                    await session.respond("answer")

    async def test_secret_target_shapes_and_confirmation_metadata_fail_closed(self) -> None:
        decision = AgentToolCall("request_secret", {})
        password = SecretFieldTarget(SecretField.PASSWORD, "Password", "p")
        username = SecretFieldTarget(SecretField.USERNAME, "Username", "u")
        invalid = (
            None,
            (),
            (username,),
            (username, password),
            (password, SecretFieldTarget(SecretField.USERNAME, "Username", "p")),
        )

        class Router:
            def __init__(self, targets, *, confirmation=False):
                self.targets = targets
                self.confirmation = confirmation
            def definitions(self):
                return ()
            async def route(self, *_args, **_kwargs):
                return AgentStepObservation(
                    "request_secret", AgentToolSource.AGENT_CONTROL,
                    AgentStepStatus.AWAITING_SECRET, "Question", None,
                    confirmation_required=self.confirmation,
                    secret_fields=(SecretField.PASSWORD, SecretField.USERNAME),
                    secret_targets=self.targets,
                )

        for targets in invalid:
            with self.subTest(targets=targets):
                session = ResumableAgentSession(
                    ScriptedDecisionSource([decision]), Router(targets), 2  # type: ignore[arg-type]
                )
                with self.assertRaises(AgentSessionStateError):
                    await session.start("task")
                self.assertIsNone(session.pause_kind)
        session = ResumableAgentSession(
            ScriptedDecisionSource([decision]),
            Router((password, username), confirmation=True), 2,  # type: ignore[arg-type]
        )
        with self.assertRaises(AgentSessionStateError):
            await session.start("task")

    async def test_resume_validation_safe_context_and_independent_otp_pause(self) -> None:
        router = AgentToolRouter(FakePolicyExecutor(), (), AgentControlExecutor())
        source = RecordingDecisionSource([
            AgentToolCall("request_secret", {
                "question": "Credentials", "fields": ["username", "password"],
                "targets": [
                    {"field": "username", "name": "Email", "ref": "u-ref"},
                    {"field": "password", "name": "Password", "ref": "p-ref"},
                ],
            }),
            AgentToolCall("request_secret", {
                "question": "OTP", "fields": ["otp"],
                "targets": [{"field": "otp", "name": "Code", "ref": "o-ref"}],
            }),
            AgentToolCall("finish", {"result": "done"}),
        ])
        session = ResumableAgentSession(source, router, 6)
        first = await session.start("task")
        for fields in (
            (SecretField.USERNAME,),
            (SecretField.USERNAME, SecretField.PASSWORD),
            (SecretField.PASSWORD, SecretField.PASSWORD),
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(AgentSessionStateError):
                    await session.resume_after_secret_application(fields)
        second = await session.resume_after_secret_application(
            (SecretField.PASSWORD, SecretField.USERNAME)
        )
        self.assertEqual(second.pause_kind, AgentPauseKind.SECRET)
        self.assertEqual(second.secret_fields, (SecretField.OTP,))
        self.assertEqual(second.user_interactions, ())
        self.assertEqual(len(second.application_events), 1)
        self.assertEqual(source.contexts[1].application_events, second.application_events)
        event_text = repr(second.application_events)
        for forbidden in ("Email", "Password", "u-ref", "p-ref", "SecretReference", "value"):
            self.assertNotIn(forbidden, event_text)
        finished = await session.resume_after_secret_application((SecretField.OTP,))
        self.assertEqual(finished.status, AgentRunStatus.FINISHED)
        self.assertEqual(len(finished.application_events), 2)
        self.assertIsNone(finished.secret_fields)
        self.assertIsNone(finished.secret_targets)
        self.assertEqual(finished.user_interactions, ())
        self.assertEqual(first.user_interactions, ())


if __name__ == "__main__":
    unittest.main()
