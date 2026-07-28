"""Application-owned controls for ending or pausing an agent task."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any


class AgentControlStatus(str, Enum):
    """Terminal orchestration states produced by agent-control tools."""

    FINISHED = "finished"
    AWAITING_USER = "awaiting_user"


@dataclass(frozen=True)
class AgentControlDefinition:
    """Public description and input contract for an agent control."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class AgentControlResult:
    """Typed outcome returned after an agent control executes."""

    tool_name: str
    status: AgentControlStatus
    text: str
    final_result: str | None
    question: str | None


class AgentControlError(Exception):
    """Base error for application-owned agent controls."""


class UnknownAgentControlError(AgentControlError):
    """Raised when a requested control is not registered."""


class InvalidAgentControlArgumentsError(AgentControlError):
    """Raised when a control receives arguments outside its strict contract."""


@dataclass(frozen=True)
class _ControlSpec:
    description: str
    field_name: str
    field_description: str
    status: AgentControlStatus


_CONTROL_SPECS: Mapping[str, _ControlSpec] = MappingProxyType(
    {
        "ask_user": _ControlSpec(
            description=(
                "Pause progress and request missing information or clarification "
                "from the user."
            ),
            field_name="question",
            field_description="Question that must be answered before continuing.",
            status=AgentControlStatus.AWAITING_USER,
        ),
        "finish": _ControlSpec(
            description=(
                "End the agent task and return the final result to the user."
            ),
            field_name="result",
            field_description="Final result to return to the user.",
            status=AgentControlStatus.FINISHED,
        ),
    }
)


class AgentControlExecutor:
    """Validate and execute controls owned by the Python orchestrator."""

    def definitions(self) -> tuple[AgentControlDefinition, ...]:
        """Return fresh control definitions in deterministic alphabetical order."""

        return tuple(
            AgentControlDefinition(
                name=name,
                description=spec.description,
                input_schema={
                    "type": "object",
                    "properties": {
                        spec.field_name: {
                            "type": "string",
                            "description": spec.field_description,
                        }
                    },
                    "required": [spec.field_name],
                    "additionalProperties": False,
                },
            )
            for name, spec in sorted(_CONTROL_SPECS.items())
        )

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> AgentControlResult:
        """Strictly validate and execute one known application-owned control."""

        spec = _CONTROL_SPECS.get(tool_name)
        if spec is None:
            raise UnknownAgentControlError(
                f"Unknown agent control {tool_name!r}."
            )

        if not isinstance(arguments, Mapping):
            raise InvalidAgentControlArgumentsError(
                f"Agent control {tool_name!r} arguments must be a mapping."
            )

        expected_fields = {spec.field_name}
        actual_fields = set(arguments)
        missing_fields = expected_fields - actual_fields
        if missing_fields:
            raise InvalidAgentControlArgumentsError(
                f"Agent control {tool_name!r} is missing required field "
                f"{spec.field_name!r}."
            )

        unexpected_fields = actual_fields - expected_fields
        if unexpected_fields:
            rendered_fields = ", ".join(
                sorted(repr(field) for field in unexpected_fields)
            )
            raise InvalidAgentControlArgumentsError(
                f"Agent control {tool_name!r} received unexpected field(s): "
                f"{rendered_fields}."
            )

        value = arguments[spec.field_name]
        if not isinstance(value, str):
            raise InvalidAgentControlArgumentsError(
                f"Agent control {tool_name!r} field {spec.field_name!r} "
                "must be a string."
            )
        if not value.strip():
            raise InvalidAgentControlArgumentsError(
                f"Agent control {tool_name!r} field {spec.field_name!r} "
                "must be a non-empty, non-whitespace string."
            )

        if tool_name == "finish":
            return AgentControlResult(
                tool_name=tool_name,
                status=spec.status,
                text=value,
                final_result=value,
                question=None,
            )

        if tool_name == "ask_user":
            return AgentControlResult(
                tool_name=tool_name,
                status=spec.status,
                text=value,
                final_result=None,
                question=value,
            )

        raise AgentControlError(
            f"Agent control {tool_name!r} is registered but has no implemented "
            "execution branch; refusing to execute."
        )
