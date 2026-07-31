"""Application-owned controls for ending or pausing an agent task."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from .secret_store import SecretField
from .secret_application import SecretFieldTarget


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
    secret_targets: tuple[SecretFieldTarget, ...] | None = field(default=None, repr=False)


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
    "Request secret values through the application. The model identifies only "
    "field categories and current-page targets from the current page snapshot; "
    "raw username, password, or OTP values must never appear in arguments. "
    "This control executes locally and does not invoke MCP."
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
                                "targets": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "field": {"type": "string", "enum": ["username", "password", "otp"]},
                                            "name": {"type": "string", "description": "Safe human-readable field name."},
                                            "ref": {"type": "string", "description": "Exact element reference from the current page snapshot."},
                                        },
                                        "required": ["field", "name", "ref"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": ["question", "fields", "targets"],
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
        if "targets" not in actual_fields:
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' is missing required field 'targets'."
            )
        if actual_fields != {"question", "fields", "targets"}:
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
        targets = arguments["targets"]
        if type(targets) is not list or not targets:
            raise InvalidAgentControlArgumentsError(
                "Agent control 'request_secret' targets must be a non-empty list."
            )
        parsed: list[SecretFieldTarget] = []
        for target in targets:
            if type(target) is not dict or set(target) != {"field", "name", "ref"}:
                raise InvalidAgentControlArgumentsError(
                    "Agent control 'request_secret' target is invalid."
                )
            category, name, ref = target["field"], target["name"], target["ref"]
            if type(category) is not str or category not in allowed:
                raise InvalidAgentControlArgumentsError("Agent control 'request_secret' target field is invalid.")
            if type(name) is not str or not name.strip():
                raise InvalidAgentControlArgumentsError("Agent control 'request_secret' target name is invalid.")
            if type(ref) is not str or not ref.strip():
                raise InvalidAgentControlArgumentsError("Agent control 'request_secret' target reference is invalid.")
            parsed.append(SecretFieldTarget(allowed[category], name, ref))
        parsed.sort(key=lambda item: item.field.value)
        secret_targets = tuple(parsed)
        if len({target.field for target in secret_targets}) != len(secret_targets):
            raise InvalidAgentControlArgumentsError("Agent control 'request_secret' target fields must be unique.")
        if len({target.ref for target in secret_targets}) != len(secret_targets):
            raise InvalidAgentControlArgumentsError("Agent control 'request_secret' target references must be unique.")
        if tuple(target.field for target in secret_targets) != secret_fields:
            raise InvalidAgentControlArgumentsError("Agent control 'request_secret' fields and targets do not match.")
        return AgentControlResult(
            tool_name="request_secret",
            status=AgentControlStatus.AWAITING_SECRET,
            text=question,
            final_result=None,
            question=question,
            confirmation_for_step=None,
            secret_fields=secret_fields,
            secret_targets=secret_targets,
        )
