"""Application-owned controls for ending or pausing an agent task."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from .secret_store import SecretField


class AgentControlStatus(str, Enum):
    """Terminal orchestration states produced by agent-control tools."""

    FINISHED = "finished"
    AWAITING_USER = "awaiting_user"
    AWAITING_SECRET = "awaiting_secret"


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
    confirmation_for_step: int | None = None
    secret_fields: tuple[SecretField, ...] | None = None


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

_REQUEST_SECRET_DESCRIPTION = (
    "Request secret values through the application. Specify only the required "
    "field categories; raw credentials or OTP values must never be included in "
    "tool arguments. This control executes locally and never reaches MCP."
)


class AgentControlExecutor:
    """Validate and execute controls owned by the Python orchestrator."""

    def definitions(self) -> tuple[AgentControlDefinition, ...]:
        """Return fresh control definitions in deterministic alphabetical order."""

        definitions = []
        for name in sorted((*_CONTROL_SPECS, "request_secret")):
            if name == "request_secret":
                definitions.append(
                    AgentControlDefinition(
                        name=name,
                        description=_REQUEST_SECRET_DESCRIPTION,
                        input_schema={
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "Safe question shown to the user.",
                                },
                                "fields": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": ["username", "password", "otp"],
                                    },
                                    "minItems": 1,
                                    "uniqueItems": True,
                                },
                            },
                            "required": ["question", "fields"],
                            "additionalProperties": False,
                        },
                    )
                )
                continue
            spec = _CONTROL_SPECS[name]
            properties: dict[str, Any] = {
                spec.field_name: {
                    "type": "string",
                    "description": spec.field_description,
                }
            }
            if name == "ask_user":
                properties["confirmation_for_step"] = {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "Claim that this question asks approval for the "
                        "immediately preceding rejected step."
                    ),
                }
            definitions.append(
                AgentControlDefinition(
                    name=name,
                    description=spec.description,
                    input_schema={
                        "type": "object",
                        "properties": properties,
                        "required": [spec.field_name],
                        "additionalProperties": False,
                    },
                )
            )
        return tuple(definitions)

    def execute(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> AgentControlResult:
        """Strictly validate and execute one known application-owned control."""

        spec = _CONTROL_SPECS.get(tool_name)
        if spec is None and tool_name != "request_secret":
            raise UnknownAgentControlError(
                f"Unknown agent control {tool_name!r}."
            )

        if not isinstance(arguments, Mapping):
            raise InvalidAgentControlArgumentsError(
                f"Agent control {tool_name!r} arguments must be a mapping."
            )

        if tool_name == "request_secret":
            return self._execute_request_secret(arguments)

        assert spec is not None

        expected_fields = {spec.field_name}
        if tool_name == "ask_user":
            expected_fields.add("confirmation_for_step")
        actual_fields = set(arguments)
        missing_fields = {spec.field_name} - actual_fields
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
            confirmation_for_step = arguments.get("confirmation_for_step")
            if "confirmation_for_step" in arguments and (
                type(confirmation_for_step) is not int
                or confirmation_for_step <= 0
            ):
                raise InvalidAgentControlArgumentsError(
                    "Agent control 'ask_user' field "
                    "'confirmation_for_step' must be a positive integer."
                )
            return AgentControlResult(
                tool_name=tool_name,
                status=spec.status,
                text=value,
                final_result=None,
                question=value,
                confirmation_for_step=confirmation_for_step,
            )

        raise AgentControlError(
            f"Agent control {tool_name!r} is registered but has no implemented "
            "execution branch; refusing to execute."
        )

    @staticmethod
    def _execute_request_secret(
        arguments: Mapping[str, Any],
    ) -> AgentControlResult:
        actual_fields = set(arguments)
        if "question" not in actual_fields:
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' is missing required field 'question'."
            )
        if "fields" not in actual_fields:
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' is missing required field 'fields'."
            )
        if actual_fields != {"question", "fields"}:
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' received unexpected fields."
            )
        question = arguments["question"]
        if type(question) is not str or not question.strip():
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' field 'question' must be a non-empty string."
            )
        fields = arguments["fields"]
        if type(fields) is not list or not fields:
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' field 'fields' must be a non-empty list."
            )
        if any(type(field) is not str for field in fields):
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' field names must be strings."
            )
        if len(set(fields)) != len(fields):
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' field names must be unique."
            )
        allowed = {field.value: field for field in SecretField}
        if any(field not in allowed for field in fields):
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' contains an unsupported field category."
            )
        secret_fields = tuple(sorted((allowed[field] for field in fields), key=lambda item: item.value))
        return AgentControlResult(
            tool_name="request_secret",
            status=AgentControlStatus.AWAITING_SECRET,
            text=question,
            final_result=None,
            question=question,
            confirmation_for_step=None,
            secret_fields=secret_fields,
        )
