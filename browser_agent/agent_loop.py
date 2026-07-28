"""Deterministic, policy-enforced agent-loop integration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .agent_controls import (
    AgentControlError,
    AgentControlExecutor,
    AgentControlStatus,
)
from .mcp_gateway import McpGatewayError, ToolDefinition, ToolObservation
from .tool_policy import ToolPolicyError


class AgentToolSource(str, Enum):
    MCP = "mcp"
    AGENT_CONTROL = "agent_control"


class AgentStepStatus(str, Enum):
    SUCCESS = "success"
    MCP_ERROR = "mcp_error"
    TRANSPORT_ERROR = "transport_error"
    REJECTED = "rejected"
    FINISHED = "finished"
    AWAITING_USER = "awaiting_user"


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    description: str | None
    input_schema: dict[str, Any]
    source: AgentToolSource


@dataclass(frozen=True, init=False)
class AgentToolCall:
    """One immutable decision with defensively copied nested arguments."""

    tool_name: str
    _arguments: dict[str, Any]

    def __init__(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if not isinstance(arguments, Mapping):
            raise TypeError("arguments must be a mapping")
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "_arguments", deepcopy(dict(arguments)))

    @property
    def arguments(self) -> dict[str, Any]:
        return deepcopy(self._arguments)


@dataclass(frozen=True)
class AgentStepObservation:
    tool_name: str
    source: AgentToolSource | None
    status: AgentStepStatus
    text: str
    error: str | None


@dataclass(frozen=True)
class AgentStepRecord:
    step_number: int
    decision: AgentToolCall
    observation: AgentStepObservation


@dataclass(frozen=True)
class AgentLoopContext:
    task: str
    tools: tuple[AgentToolDefinition, ...]
    steps: tuple[AgentStepRecord, ...]


class AgentRunStatus(str, Enum):
    FINISHED = "finished"
    AWAITING_USER = "awaiting_user"
    STEP_LIMIT_REACHED = "step_limit_reached"
    DECISION_SOURCE_EXHAUSTED = "decision_source_exhausted"


@dataclass(frozen=True)
class AgentRunResult:
    status: AgentRunStatus
    steps: tuple[AgentStepRecord, ...]
    final_result: str | None
    question: str | None


class AgentDecisionSourceProtocol(Protocol):
    async def next_decision(
        self,
        context: AgentLoopContext,
    ) -> AgentToolCall | None:
        """Return the next decision, or None when the finite source is exhausted."""


class ScriptedDecisionSource:
    """Finite in-memory decisions for deterministic integration tests and smoke."""

    def __init__(self, decisions: Sequence[AgentToolCall]) -> None:
        self._decisions = tuple(decisions)
        self._index = 0

    async def next_decision(
        self,
        context: AgentLoopContext,
    ) -> AgentToolCall | None:
        del context
        if self._index >= len(self._decisions):
            return None
        decision = self._decisions[self._index]
        self._index += 1
        return decision


class McpToolExecutorProtocol(Protocol):
    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> ToolObservation:
        """Invoke a policy-enforced MCP tool."""


class AgentToolCatalogError(Exception):
    """The combined MCP and agent-control catalog is ambiguous."""


def _copy_agent_definition(
    definition: AgentToolDefinition,
) -> AgentToolDefinition:
    return AgentToolDefinition(
        name=definition.name,
        description=definition.description,
        input_schema=deepcopy(definition.input_schema),
        source=definition.source,
    )


class AgentToolRouter:
    """Route controls locally and browser tools only through policy enforcement."""

    def __init__(
        self,
        mcp_executor: McpToolExecutorProtocol,
        mcp_definitions: Sequence[ToolDefinition],
        agent_controls: AgentControlExecutor,
    ) -> None:
        self._mcp_executor = mcp_executor
        self._agent_controls = agent_controls

        catalog: dict[str, AgentToolDefinition] = {}
        mcp_names: set[str] = set()
        for definition in mcp_definitions:
            if definition.name in mcp_names:
                raise AgentToolCatalogError(
                    f"duplicate MCP tool name: {definition.name!r}"
                )
            mcp_names.add(definition.name)
            catalog[definition.name] = AgentToolDefinition(
                name=definition.name,
                description=definition.description,
                input_schema=deepcopy(definition.input_schema),
                source=AgentToolSource.MCP,
            )

        control_names: set[str] = set()
        for definition in agent_controls.definitions():
            if definition.name in control_names:
                raise AgentToolCatalogError(
                    f"duplicate agent-control name: {definition.name!r}"
                )
            if definition.name in mcp_names:
                raise AgentToolCatalogError(
                    f"MCP/agent-control tool name collision: {definition.name!r}"
                )
            control_names.add(definition.name)
            catalog[definition.name] = AgentToolDefinition(
                name=definition.name,
                description=definition.description,
                input_schema=deepcopy(definition.input_schema),
                source=AgentToolSource.AGENT_CONTROL,
            )

        self._definitions = tuple(
            catalog[name] for name in sorted(catalog)
        )
        self._mcp_names = frozenset(mcp_names)
        self._control_names = frozenset(control_names)

    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return tuple(
            _copy_agent_definition(definition)
            for definition in self._definitions
        )

    async def route(self, decision: AgentToolCall) -> AgentStepObservation:
        try:
            if decision.tool_name in self._control_names:
                result = self._agent_controls.execute(
                    decision.tool_name, decision.arguments
                )
                status = (
                    AgentStepStatus.FINISHED
                    if result.status is AgentControlStatus.FINISHED
                    else AgentStepStatus.AWAITING_USER
                )
                return AgentStepObservation(
                    tool_name=decision.tool_name,
                    source=AgentToolSource.AGENT_CONTROL,
                    status=status,
                    text=result.text,
                    error=None,
                )

            if decision.tool_name in self._mcp_names:
                result = await self._mcp_executor.invoke(
                    decision.tool_name,
                    decision.arguments,
                    confirmation_granted=False,
                )
                return AgentStepObservation(
                    tool_name=decision.tool_name,
                    source=AgentToolSource.MCP,
                    status=AgentStepStatus(result.status),
                    text=result.text,
                    error=result.error,
                )
        except (AgentControlError, ToolPolicyError, McpGatewayError) as exc:
            return AgentStepObservation(
                tool_name=decision.tool_name,
                source=(
                    AgentToolSource.AGENT_CONTROL
                    if decision.tool_name in self._control_names
                    else AgentToolSource.MCP
                ),
                status=AgentStepStatus.REJECTED,
                text="",
                error=f"{type(exc).__name__}: {exc}",
            )

        return AgentStepObservation(
            tool_name=decision.tool_name,
            source=None,
            status=AgentStepStatus.REJECTED,
            text="",
            error=f"Unknown tool: {decision.tool_name!r}",
        )


class DeterministicAgentLoop:
    """Run one scripted decision at a time with observation feedback."""

    def __init__(
        self,
        decision_source: AgentDecisionSourceProtocol,
        router: AgentToolRouter,
        max_steps: int,
    ) -> None:
        if type(max_steps) is not int:
            raise TypeError("max_steps must be an int")
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        self._decision_source = decision_source
        self._router = router
        self._max_steps = max_steps

    async def run(self, task: str) -> AgentRunResult:
        if not isinstance(task, str):
            raise TypeError("task must be a string")
        if not task.strip():
            raise ValueError("task must be non-empty and non-whitespace")

        steps: list[AgentStepRecord] = []
        for step_number in range(1, self._max_steps + 1):
            context = AgentLoopContext(
                task=task,
                tools=self._router.definitions(),
                steps=tuple(steps),
            )
            decision = await self._decision_source.next_decision(context)
            if decision is None:
                return AgentRunResult(
                    AgentRunStatus.DECISION_SOURCE_EXHAUSTED,
                    tuple(steps),
                    None,
                    None,
                )
            observation = await self._router.route(decision)
            steps.append(AgentStepRecord(step_number, decision, observation))
            if observation.status is AgentStepStatus.FINISHED:
                return AgentRunResult(
                    AgentRunStatus.FINISHED,
                    tuple(steps),
                    observation.text,
                    None,
                )
            if observation.status is AgentStepStatus.AWAITING_USER:
                return AgentRunResult(
                    AgentRunStatus.AWAITING_USER,
                    tuple(steps),
                    None,
                    observation.text,
                )

        return AgentRunResult(
            AgentRunStatus.STEP_LIMIT_REACHED,
            tuple(steps),
            None,
            None,
        )
