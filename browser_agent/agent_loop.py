"""Deterministic, policy-enforced agent-loop integration."""

from __future__ import annotations

import asyncio
import json
import math
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
from .tool_policy import ToolConfirmationRequiredError, ToolPolicyError
from .secret_store import SecretField


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
    AWAITING_SECRET = "awaiting_secret"


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
    confirmation_required: bool = False
    confirmation_for_step: int | None = None
    secret_fields: tuple[SecretField, ...] | None = None


@dataclass(frozen=True)
class AgentStepRecord:
    step_number: int
    decision: AgentToolCall
    observation: AgentStepObservation
    replay_of_step_number: int | None = None


class AgentPauseKind(str, Enum):
    USER_INPUT = "user_input"
    CONFIRMATION = "confirmation"
    SECRET = "secret"


class AgentSessionError(Exception):
    """Base error for resumable in-memory session use."""


class AgentSessionConstructionError(AgentSessionError):
    """The session was constructed with invalid configuration."""


class AgentSessionStateError(AgentSessionError):
    """The requested transition is invalid for the current session state."""


class AgentUserResponseError(AgentSessionError):
    """A user response does not satisfy the strict interaction contract."""


def _validate_json_native(
    value: Any,
    *,
    require_object_root: bool = False,
    active_container_ids: set[int] | None = None,
) -> None:
    """Validate exact JSON-native types without coercing Python containers."""
    if require_object_root and type(value) is not dict:
        raise AgentSessionConstructionError(
            "canonical tool arguments must be an exact dict"
        )
    if active_container_ids is None:
        active_container_ids = set()
    if type(value) is dict:
        container_id = id(value)
        if container_id in active_container_ids:
            raise AgentSessionConstructionError(
                "tool arguments cannot contain circular containers"
            )
        active_container_ids.add(container_id)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise AgentSessionConstructionError(
                        "tool argument object keys must be strings"
                    )
                _validate_json_native(
                    item, active_container_ids=active_container_ids
                )
        finally:
            active_container_ids.remove(container_id)
        return
    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            raise AgentSessionConstructionError(
                "tool arguments cannot contain circular containers"
            )
        active_container_ids.add(container_id)
        try:
            for item in value:
                _validate_json_native(
                    item, active_container_ids=active_container_ids
                )
        finally:
            active_container_ids.remove(container_id)
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise AgentSessionConstructionError(
                "tool argument floats must be finite"
            )
        return
    if value is None or type(value) in (str, bool, int):
        return
    raise AgentSessionConstructionError(
        "tool arguments must use exact JSON-native types"
    )


def _reject_json_constant(value: str) -> None:
    raise AgentSessionConstructionError(
        f"non-finite JSON constant is not allowed: {value}"
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AgentSessionConstructionError(
                f"duplicate JSON object key is not allowed: {key!r}"
            )
        result[key] = value
    return result


def _canonicalize_arguments(arguments: Any) -> str:
    _validate_json_native(arguments, require_object_root=True)
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


@dataclass(frozen=True)
class AgentUserInput:
    after_step_number: int
    kind: AgentPauseKind
    question: str
    response: str | bool

    def __post_init__(self) -> None:
        if type(self.after_step_number) is not int or self.after_step_number <= 0:
            raise AgentUserResponseError(
                "after_step_number must be a positive integer"
            )
        if not isinstance(self.kind, AgentPauseKind):
            raise AgentUserResponseError("kind must be an AgentPauseKind")
        if not isinstance(self.question, str) or not self.question.strip():
            raise AgentUserResponseError("question must be a non-empty string")
        if self.kind is AgentPauseKind.USER_INPUT:
            if not isinstance(self.response, str) or not self.response.strip():
                raise AgentUserResponseError(
                    "normal user response must be a non-empty string"
                )
        elif self.kind is AgentPauseKind.CONFIRMATION and type(self.response) is not bool:
            raise AgentUserResponseError(
                "confirmation response must be a bool"
            )
        elif self.kind is AgentPauseKind.SECRET:
            raise AgentUserResponseError(
                "secret pauses do not accept AgentUserInput"
            )


@dataclass(frozen=True)
class PendingConfirmation:
    rejected_step_number: int
    tool_name: str
    _canonical_arguments: str

    def __post_init__(self) -> None:
        if (
            type(self.rejected_step_number) is not int
            or self.rejected_step_number <= 0
        ):
            raise AgentSessionConstructionError(
                "rejected_step_number must be a positive integer"
            )
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise AgentSessionConstructionError(
                "tool_name must be a non-empty string"
            )
        if not isinstance(self._canonical_arguments, str):
            raise AgentSessionConstructionError(
                "canonical arguments must be a string"
            )
        value = self._decode_arguments()
        if _canonicalize_arguments(value) != self._canonical_arguments:
            raise AgentSessionConstructionError(
                "canonical arguments text is not canonical"
            )

    @classmethod
    def from_tool_call(
        cls, rejected_step_number: int, decision: AgentToolCall
    ) -> PendingConfirmation:
        try:
            canonical = _canonicalize_arguments(decision.arguments)
        except (AgentSessionConstructionError, TypeError, ValueError) as exc:
            raise AgentSessionConstructionError(
                "tool arguments cannot be normalized safely"
            ) from exc
        return cls(rejected_step_number, decision.tool_name, canonical)

    def _decode_arguments(self) -> dict[str, Any]:
        try:
            value = json.loads(
                self._canonical_arguments,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (json.JSONDecodeError, TypeError) as exc:
            raise AgentSessionConstructionError(
                "canonical arguments are invalid"
            ) from exc
        _validate_json_native(value, require_object_root=True)
        return value

    @property
    def arguments(self) -> dict[str, Any]:
        return self._decode_arguments()


@dataclass(frozen=True)
class AgentLoopContext:
    task: str
    tools: tuple[AgentToolDefinition, ...]
    steps: tuple[AgentStepRecord, ...]
    user_interactions: tuple[AgentUserInput, ...] = ()


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
    pause_kind: AgentPauseKind | None = None
    pending_confirmation: PendingConfirmation | None = None
    user_interactions: tuple[AgentUserInput, ...] = ()
    secret_fields: tuple[SecretField, ...] | None = None


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

    async def route(
        self,
        decision: AgentToolCall,
        *,
        confirmation_granted: bool = False,
    ) -> AgentStepObservation:
        if type(confirmation_granted) is not bool:
            raise TypeError("confirmation_granted must be a bool")
        try:
            if decision.tool_name in self._control_names:
                result = self._agent_controls.execute(
                    decision.tool_name, decision.arguments
                )
                status = {
                    AgentControlStatus.FINISHED: AgentStepStatus.FINISHED,
                    AgentControlStatus.AWAITING_USER: AgentStepStatus.AWAITING_USER,
                    AgentControlStatus.AWAITING_SECRET: AgentStepStatus.AWAITING_SECRET,
                }[result.status]
                return AgentStepObservation(
                    tool_name=decision.tool_name,
                    source=AgentToolSource.AGENT_CONTROL,
                    status=status,
                    text=result.text,
                    error=None,
                    confirmation_for_step=result.confirmation_for_step,
                    secret_fields=result.secret_fields,
                )

            if decision.tool_name in self._mcp_names:
                result = await self._mcp_executor.invoke(
                    decision.tool_name,
                    decision.arguments,
                    confirmation_granted=confirmation_granted,
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
                confirmation_required=isinstance(
                    exc, ToolConfirmationRequiredError
                ),
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
        return await ResumableAgentSession(
            self._decision_source, self._router, self._max_steps
        ).start(task)


class ResumableAgentSession:
    """One dependency-free in-memory run with serialized state transitions."""

    def __init__(
        self,
        decision_source: AgentDecisionSourceProtocol,
        router: AgentToolRouter,
        max_steps: int,
    ) -> None:
        if type(max_steps) is not int:
            raise AgentSessionConstructionError("max_steps must be an int")
        if max_steps <= 0:
            raise AgentSessionConstructionError(
                "max_steps must be greater than zero"
            )
        self._decision_source = decision_source
        self._router = router
        self._max_steps = max_steps
        self._lock = asyncio.Lock()
        self._task: str | None = None
        self._steps: list[AgentStepRecord] = []
        self._user_interactions: list[AgentUserInput] = []
        self._candidate: PendingConfirmation | None = None
        self._pending_confirmation: PendingConfirmation | None = None
        self._pause_kind: AgentPauseKind | None = None
        self._question: str | None = None
        self._secret_fields: tuple[SecretField, ...] | None = None
        self._terminal_result: AgentRunResult | None = None
        self._started = False
        self._failed = False

    @property
    def steps(self) -> tuple[AgentStepRecord, ...]:
        return tuple(self._steps)

    @property
    def user_interactions(self) -> tuple[AgentUserInput, ...]:
        return tuple(self._user_interactions)

    @property
    def pause_kind(self) -> AgentPauseKind | None:
        return self._pause_kind

    @property
    def pending_confirmation(self) -> PendingConfirmation | None:
        return self._pending_confirmation

    async def start(self, task: str) -> AgentRunResult:
        async with self._lock:
            if self._started:
                raise AgentSessionStateError("session has already been started")
            if not isinstance(task, str):
                raise TypeError("task must be a string")
            if not task.strip():
                raise ValueError("task must be non-empty and non-whitespace")
            self._started = True
            self._task = task
            return await self._continue()

    async def respond(self, text: str) -> AgentRunResult:
        async with self._lock:
            self._require_pause(AgentPauseKind.USER_INPUT)
            if not isinstance(text, str) or not text.strip():
                raise AgentUserResponseError(
                    "normal user response must be a non-empty string"
                )
            self._user_interactions.append(
                AgentUserInput(
                    len(self._steps),
                    AgentPauseKind.USER_INPUT,
                    self._question or "",
                    text,
                )
            )
            self._clear_pause()
            return await self._continue()

    async def confirm(self, approved: bool) -> AgentRunResult:
        async with self._lock:
            self._require_pause(AgentPauseKind.CONFIRMATION)
            if type(approved) is not bool:
                raise AgentUserResponseError(
                    "confirmation response must be a bool"
                )
            pending = self._pending_confirmation
            if pending is None:
                raise AgentSessionStateError("no pending confirmation")
            question = self._question or ""
            self._pending_confirmation = None
            self._candidate = None
            self._clear_pause()
            self._user_interactions.append(
                AgentUserInput(
                    len(self._steps),
                    AgentPauseKind.CONFIRMATION,
                    question,
                    approved,
                )
            )
            if not approved:
                return await self._continue()
            if len(self._steps) >= self._max_steps:
                return self._terminate(AgentRunStatus.STEP_LIMIT_REACHED)
            decision = AgentToolCall(pending.tool_name, pending.arguments)
            try:
                observation = await self._router.route(
                    decision, confirmation_granted=True
                )
            except BaseException:
                self._failed = True
                raise
            self._steps.append(
                AgentStepRecord(
                    len(self._steps) + 1,
                    decision,
                    observation,
                    replay_of_step_number=pending.rejected_step_number,
                )
            )
            if len(self._steps) >= self._max_steps:
                return self._terminate(AgentRunStatus.STEP_LIMIT_REACHED)
            return await self._continue()

    def _require_pause(self, kind: AgentPauseKind) -> None:
        if not self._started:
            raise AgentSessionStateError("session has not been started")
        if self._failed:
            raise AgentSessionStateError("session failed and cannot resume")
        if self._terminal_result is not None:
            raise AgentSessionStateError("terminal session cannot resume")
        if self._pause_kind is not kind:
            raise AgentSessionStateError(
                f"session is not paused for {kind.value}"
            )

    def _clear_pause(self) -> None:
        self._pause_kind = None
        self._question = None
        self._secret_fields = None

    def _result(
        self,
        status: AgentRunStatus,
        final_result: str | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            status,
            tuple(self._steps),
            final_result,
            self._question,
            self._pause_kind,
            self._pending_confirmation,
            tuple(self._user_interactions),
            self._secret_fields,
        )

    def _terminate(
        self,
        status: AgentRunStatus,
        final_result: str | None = None,
    ) -> AgentRunResult:
        self._candidate = None
        self._pending_confirmation = None
        self._clear_pause()
        self._terminal_result = self._result(status, final_result)
        return self._terminal_result

    async def _continue(self) -> AgentRunResult:
        while len(self._steps) < self._max_steps:
            context = AgentLoopContext(
                task=self._task or "",
                tools=self._router.definitions(),
                steps=tuple(self._steps),
                user_interactions=tuple(self._user_interactions),
            )
            decision = await self._decision_source.next_decision(context)
            if decision is None:
                return self._terminate(
                    AgentRunStatus.DECISION_SOURCE_EXHAUSTED
                )
            previous_candidate = self._candidate
            self._candidate = None
            observation = await self._router.route(
                decision, confirmation_granted=False
            )
            step_number = len(self._steps) + 1
            self._steps.append(
                AgentStepRecord(step_number, decision, observation)
            )
            if (
                observation.confirmation_required
                and observation.source is AgentToolSource.MCP
            ):
                try:
                    self._candidate = PendingConfirmation.from_tool_call(
                        step_number, decision
                    )
                except AgentSessionConstructionError:
                    self._candidate = None
            if observation.status is AgentStepStatus.FINISHED:
                return self._terminate(
                    AgentRunStatus.FINISHED, observation.text
                )
            if observation.status is AgentStepStatus.AWAITING_USER:
                reference = observation.confirmation_for_step
                if (
                    previous_candidate is not None
                    and reference == previous_candidate.rejected_step_number
                    and reference == step_number - 1
                ):
                    self._pending_confirmation = previous_candidate
                    self._pause_kind = AgentPauseKind.CONFIRMATION
                else:
                    self._pending_confirmation = None
                    self._pause_kind = AgentPauseKind.USER_INPUT
                self._candidate = None
                self._question = observation.text
                return self._result(AgentRunStatus.AWAITING_USER)
            if observation.status is AgentStepStatus.AWAITING_SECRET:
                fields = observation.secret_fields
                if (
                    type(observation.text) is not str
                    or not observation.text.strip()
                    or type(fields) is not tuple
                    or not fields
                    or any(type(item) is not SecretField for item in fields)
                    or fields
                    != tuple(sorted(fields, key=lambda item: item.value))
                    or len(set(fields)) != len(fields)
                    or observation.confirmation_required is not False
                    or observation.confirmation_for_step is not None
                    or self._pending_confirmation is not None
                ):
                    self._failed = True
                    raise AgentSessionStateError(
                        "secret control returned invalid field metadata"
                    )
                self._candidate = None
                self._pending_confirmation = None
                self._pause_kind = AgentPauseKind.SECRET
                self._question = observation.text
                self._secret_fields = fields
                return self._result(AgentRunStatus.AWAITING_USER)
        return self._terminate(AgentRunStatus.STEP_LIMIT_REACHED)
