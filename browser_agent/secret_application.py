"""Application-owned, sanitized secret application through browser_fill_form."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .mcp_gateway import ToolDefinition, ToolObservation
from .secret_store import SecretField


class SecretApplicationError(Exception):
    """Base class for safe secret-application failures."""


class SecretApplicationConstructionError(SecretApplicationError):
    """The applier cannot be safely constructed."""


class SecretApplicationValidationError(SecretApplicationError):
    """Application metadata or values do not satisfy the strict contract."""


class SecretApplicationExecutionError(SecretApplicationError):
    """The single browser fill failed without exposing dependency data."""


@dataclass(frozen=True, slots=True)
class SecretFieldTarget:
    field: SecretField
    name: str
    ref: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.field) is not SecretField:
            raise SecretApplicationValidationError("target field is invalid")
        if type(self.name) is not str or not self.name.strip():
            raise SecretApplicationValidationError("target name is invalid")
        if type(self.ref) is not str or not self.ref.strip():
            raise SecretApplicationValidationError("target reference is invalid")

    def __repr__(self) -> str:
        return (
            f"SecretFieldTarget(field={self.field!r}, name={self.name!r}, "
            "ref='[REDACTED]')"
        )


@dataclass(frozen=True, slots=True)
class SecretTargetSummary:
    field: SecretField
    name: str

    def __post_init__(self) -> None:
        if type(self.field) is not SecretField:
            raise SecretApplicationValidationError("summary field is invalid")
        if type(self.name) is not str or not self.name.strip():
            raise SecretApplicationValidationError("summary name is invalid")

    @classmethod
    def from_target(cls, target: SecretFieldTarget) -> "SecretTargetSummary":
        if type(target) is not SecretFieldTarget:
            raise SecretApplicationValidationError("target is invalid")
        return cls(target.field, target.name)


_SUCCESS_MESSAGE = "Requested secret fields were applied by the application."


@dataclass(frozen=True, slots=True)
class SecretApplicationResult:
    fields: tuple[SecretField, ...]
    message: str = field(default=_SUCCESS_MESSAGE, init=False)

    def __post_init__(self) -> None:
        if (
            type(self.fields) is not tuple
            or not self.fields
            or any(type(item) is not SecretField for item in self.fields)
            or self.fields != tuple(sorted(set(self.fields), key=lambda item: item.value))
        ):
            raise SecretApplicationValidationError("result fields are invalid")


class SecretFormApplierProtocol(Protocol):
    async def apply(
        self,
        targets: tuple[SecretFieldTarget, ...],
        values: Mapping[SecretField, str],
    ) -> SecretApplicationResult: ...


class SecretFormApplier:
    """Perform exactly one trusted, policy-enforced browser_fill_form call."""

    def __init__(self, executor: object, definitions: Sequence[ToolDefinition]) -> None:
        invoke = getattr(executor, "invoke", None)
        if not callable(invoke):
            raise SecretApplicationConstructionError("executor is invalid")
        if not isinstance(definitions, Sequence) or isinstance(definitions, (str, bytes)):
            raise SecretApplicationConstructionError("tool definitions are invalid")
        names: list[str] = []
        fill: ToolDefinition | None = None
        for definition in definitions:
            if type(definition) is not ToolDefinition:
                raise SecretApplicationConstructionError("tool definition is invalid")
            if definition.name in names:
                raise SecretApplicationConstructionError("tool definitions contain duplicates")
            names.append(definition.name)
            if definition.name == "browser_fill_form":
                fill = definition
        if fill is None:
            raise SecretApplicationConstructionError("browser_fill_form is unavailable")
        schema = fill.input_schema
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        fields_schema = properties.get("fields") if isinstance(properties, Mapping) else None
        required = schema.get("required") if isinstance(schema, Mapping) else None
        if (
            not isinstance(schema, Mapping)
            or schema.get("type") != "object"
            or not isinstance(properties, Mapping)
            or not isinstance(fields_schema, Mapping)
            or fields_schema.get("type") != "array"
            or not isinstance(required, list)
            or "fields" not in required
            or schema.get("additionalProperties") is not False
        ):
            raise SecretApplicationConstructionError("browser_fill_form schema is invalid")
        self._executor = executor

    async def apply(
        self,
        targets: tuple[SecretFieldTarget, ...],
        values: Mapping[SecretField, str],
    ) -> SecretApplicationResult:
        self._validate(targets, values)
        fields_payload: list[dict[str, Any]] = []
        arguments: dict[str, Any] = {}
        try:
            for target in targets:
                fields_payload.append({
                    "name": target.name,
                    "type": "textbox",
                    "target": target.ref,
                    "value": values[target.field],
                })
            arguments["fields"] = fields_payload
            invocation_failed = False
            try:
                observation = await self._executor.invoke(
                    "browser_fill_form", arguments, confirmation_granted=True
                )
            except Exception:
                invocation_failed = True
                observation = None
            if invocation_failed:
                raise SecretApplicationExecutionError(
                    "Secret application failed safely."
                ) from None
            if type(observation) is not ToolObservation or observation.status != "success":
                raise SecretApplicationExecutionError("Secret application failed safely.")
            return SecretApplicationResult(tuple(target.field for target in targets))
        finally:
            for item in fields_payload:
                item["value"] = None
                item.clear()
            fields_payload.clear()
            arguments.clear()

    @staticmethod
    def _validate(
        targets: object, values: object
    ) -> None:
        if type(targets) is not tuple or not targets:
            raise SecretApplicationValidationError("targets must be a non-empty tuple")
        if any(type(item) is not SecretFieldTarget for item in targets):
            raise SecretApplicationValidationError("targets contain an invalid item")
        fields = tuple(item.field for item in targets)
        if fields != tuple(sorted(fields, key=lambda item: item.value)):
            raise SecretApplicationValidationError("targets are not deterministic")
        if len(set(fields)) != len(fields):
            raise SecretApplicationValidationError("target fields are duplicated")
        if len({item.ref for item in targets}) != len(targets):
            raise SecretApplicationValidationError("target references are duplicated")
        if not isinstance(values, Mapping) or not values:
            raise SecretApplicationValidationError("values must be a non-empty mapping")
        if any(type(key) is not SecretField for key in values):
            raise SecretApplicationValidationError("value field is invalid")
        if any(type(value) is not str or not value.strip() for value in values.values()):
            raise SecretApplicationValidationError("secret value is invalid")
        if tuple(sorted(values, key=lambda item: item.value)) != fields:
            raise SecretApplicationValidationError("value fields do not match targets")
