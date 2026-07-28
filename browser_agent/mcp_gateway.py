"""Typed, SDK-neutral boundary for discovering and invoking MCP tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolDefinition:
    """Stable repository-owned description of one discovered MCP tool."""

    name: str
    description: str | None
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolObservation:
    """Normalized result of an attempted MCP tool invocation."""

    tool_name: str
    status: Literal["success", "mcp_error", "transport_error"]
    text: str
    structured_content: object | None
    error: str | None


class McpSessionProtocol(Protocol):
    """Minimum asynchronous MCP session interface used by the gateway."""

    async def list_tools(self) -> object:
        """Return an MCP tool-list response."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> object:
        """Invoke a named MCP tool."""


class McpGatewayError(Exception):
    """Base class for errors owned by the MCP tool gateway."""


class McpToolCatalogError(McpGatewayError):
    """The MCP tool catalog could not be retrieved or normalized."""


class McpToolGatewayNotDiscoveredError(McpGatewayError):
    """The gateway was invoked before successful discovery."""


class UnknownMcpToolError(McpGatewayError):
    """The requested tool was not present in the discovered catalog."""


class InvalidToolArgumentsError(McpGatewayError):
    """Arguments do not satisfy the gateway's supported schema subset."""


class UnsupportedToolSchemaError(InvalidToolArgumentsError):
    """A schema uses validation behavior the gateway does not implement."""


_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "oneOf",
        "anyOf",
        "allOf",
        "$ref",
        "not",
        "if",
        "then",
        "else",
        "patternProperties",
        "dependentSchemas",
        "prefixItems",
        "contains",
    }
)
_SUPPORTED_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
_ANNOTATION_KEYWORDS = frozenset(
    {"$schema", "description", "title", "default", "examples", "deprecated"}
)
_SUPPORTED_KEYWORDS = frozenset(
    {"type", "properties", "required", "additionalProperties", "items", "enum"}
)
_MISSING = object()


def _value(source: object, *names: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return default
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return default


def _response_tools(response: object) -> object:
    tools = _value(response, "tools", default=_MISSING)
    if tools is not _MISSING:
        return tools
    if isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return response
    raise McpToolCatalogError("MCP list_tools response has no tools collection")


def _copy_definition(definition: ToolDefinition) -> ToolDefinition:
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        input_schema=deepcopy(definition.input_schema),
    )


def _path_property(path: str, name: object) -> str:
    return f"{path}.{name}"


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        )
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _json_equal(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return type(left) is type(right) and left == right


def _require_schema(value: object, path: str, keyword: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UnsupportedToolSchemaError(
            f"{path}: schema keyword {keyword!r} must contain a schema mapping"
        )
    return value


def _check_schema(schema: Mapping[str, Any], path: str) -> None:
    unknown = sorted(
        set(schema) - _SUPPORTED_KEYWORDS - _ANNOTATION_KEYWORDS
    )
    if unknown:
        keyword = unknown[0]
        category = (
            "unsupported schema keyword"
            if keyword in _UNSUPPORTED_KEYWORDS
            else "unimplemented schema keyword"
        )
        raise UnsupportedToolSchemaError(f"{path}: {category} {keyword!r}")

    expected_type = schema.get("type")
    if expected_type is not None and (
        not isinstance(expected_type, str)
        or expected_type not in _SUPPORTED_TYPES
    ):
        raise UnsupportedToolSchemaError(
            f"{path}: unsupported schema type {expected_type!r}"
        )

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, Sequence) or isinstance(
            enum, (str, bytes, bytearray)
        ):
            raise UnsupportedToolSchemaError(
                f"{path}: schema keyword 'enum' must be an array"
            )

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise UnsupportedToolSchemaError(
            f"{path}: schema keyword 'properties' must be a mapping"
        )
    for name, child in properties.items():
        if not isinstance(name, str):
            raise UnsupportedToolSchemaError(
                f"{path}: property names in a schema must be strings"
            )
        child_path = _path_property(path, name)
        _check_schema(_require_schema(child, child_path, "properties"), child_path)

    required = schema.get("required", [])
    if not isinstance(required, Sequence) or isinstance(
        required, (str, bytes, bytearray)
    ):
        raise UnsupportedToolSchemaError(
            f"{path}: schema keyword 'required' must be an array"
        )
    if any(not isinstance(name, str) for name in required):
        raise UnsupportedToolSchemaError(
            f"{path}: required property names must be strings"
        )

    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if isinstance(additional, Mapping):
            _check_schema(additional, path)
        elif not isinstance(additional, bool):
            raise UnsupportedToolSchemaError(
                f"{path}: 'additionalProperties' must be boolean or a schema"
            )

    if "items" in schema:
        item_schema = _require_schema(schema["items"], path, "items")
        _check_schema(item_schema, f"{path}[]")


def _validate(value: object, schema: Mapping[str, Any], path: str) -> None:
    unsupported = sorted(_UNSUPPORTED_KEYWORDS.intersection(schema))
    if unsupported:
        raise UnsupportedToolSchemaError(
            f"{path}: unsupported schema keyword {unsupported[0]!r}"
        )

    expected_type = schema.get("type")
    if expected_type is not None:
        if not isinstance(expected_type, str) or expected_type not in _SUPPORTED_TYPES:
            raise UnsupportedToolSchemaError(
                f"{path}: unsupported schema type {expected_type!r}"
            )
        if not _matches_type(value, expected_type):
            raise InvalidToolArgumentsError(
                f"{path}: expected {expected_type}, got {type(value).__name__}"
            )

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, Sequence) or isinstance(enum, (str, bytes, bytearray)):
            raise UnsupportedToolSchemaError(
                f"{path}: schema keyword 'enum' must be an array"
            )
        if not any(_json_equal(value, candidate) for candidate in enum):
            raise InvalidToolArgumentsError(f"{path}: value is not in enum")

    object_keywords = {"properties", "required", "additionalProperties"}
    if object_keywords.intersection(schema):
        if not isinstance(value, Mapping):
            if expected_type is None:
                raise InvalidToolArgumentsError(
                    f"{path}: expected object, got {type(value).__name__}"
                )
            return

        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise UnsupportedToolSchemaError(
                f"{path}: schema keyword 'properties' must be a mapping"
            )
        required = schema.get("required", [])
        if not isinstance(required, Sequence) or isinstance(
            required, (str, bytes, bytearray)
        ):
            raise UnsupportedToolSchemaError(
                f"{path}: schema keyword 'required' must be an array"
            )
        for name in required:
            if not isinstance(name, str):
                raise UnsupportedToolSchemaError(
                    f"{path}: required property names must be strings"
                )
            if name not in value:
                raise InvalidToolArgumentsError(
                    f"{_path_property(path, name)}: required property is missing"
                )

        for name, item in value.items():
            item_path = _path_property(path, name)
            if name in properties:
                child_schema = _require_schema(
                    properties[name], item_path, "properties"
                )
                _validate(item, child_schema, item_path)
                continue
            additional = schema.get("additionalProperties", True)
            if additional is False:
                raise InvalidToolArgumentsError(
                    f"{item_path}: additional property is not allowed"
                )
            if isinstance(additional, Mapping):
                _validate(item, additional, item_path)
            elif additional is not True:
                raise UnsupportedToolSchemaError(
                    f"{path}: 'additionalProperties' must be boolean or a schema"
                )

    if "items" in schema:
        if not isinstance(value, Sequence) or isinstance(
            value, (str, bytes, bytearray)
        ):
            if expected_type is None:
                raise InvalidToolArgumentsError(
                    f"{path}: expected array, got {type(value).__name__}"
                )
            return
        item_schema = _require_schema(schema["items"], path, "items")
        for index, item in enumerate(value):
            _validate(item, item_schema, f"{path}[{index}]")


def _plain_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _plain_value(model_dump())
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _plain_value(getattr(value, field.name))
            for field in fields(value)
        }
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, Mapping):
        return {
            str(key): _plain_value(item)
            for key, item in attributes.items()
            if not str(key).startswith("_")
        }
    return str(value)


def _normalize_result(tool_name: str, result: object) -> ToolObservation:
    content = _value(result, "content", default=[])
    text_parts: list[str] = []
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        for block in content:
            text = _value(block, "text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
    text = "\n".join(text_parts)
    structured = _value(
        result, "structuredContent", "structured_content", default=None
    )
    structured_content = (
        None if structured is None else _plain_value(structured)
    )
    is_error = bool(_value(result, "isError", "is_error", default=False))
    if is_error:
        return ToolObservation(
            tool_name=tool_name,
            status="mcp_error",
            text=text,
            structured_content=structured_content,
            error=text or "MCP tool reported an error without a message",
        )
    return ToolObservation(
        tool_name=tool_name,
        status="success",
        text=text,
        structured_content=structured_content,
        error=None,
    )


class McpToolGateway:
    """Discover MCP capabilities, validate arguments, and normalize calls."""

    def __init__(self, session: McpSessionProtocol) -> None:
        self._session = session
        self._catalog: dict[str, ToolDefinition] | None = None

    @property
    def tools(self) -> tuple[ToolDefinition, ...]:
        """Return sorted defensive copies of the discovered catalog."""
        if self._catalog is None:
            raise McpToolGatewayNotDiscoveredError(
                "MCP tools have not been discovered"
            )
        return tuple(
            _copy_definition(self._catalog[name]) for name in sorted(self._catalog)
        )

    async def discover(self) -> tuple[ToolDefinition, ...]:
        """Dynamically retrieve and store the MCP session's tool catalog."""
        try:
            response = await self._session.list_tools()
        except Exception as exc:
            raise McpToolCatalogError(
                f"MCP tool discovery failed: {type(exc).__name__}: {exc}"
            ) from exc

        tools = _response_tools(response)
        if not isinstance(tools, Sequence) or isinstance(
            tools, (str, bytes, bytearray)
        ):
            raise McpToolCatalogError("MCP tools collection must be a sequence")

        catalog: dict[str, ToolDefinition] = {}
        for tool in tools:
            name = _value(tool, "name")
            if not isinstance(name, str) or not name.strip():
                raise McpToolCatalogError(
                    "discovered MCP tool has an empty or non-string name"
                )
            if name in catalog:
                raise McpToolCatalogError(
                    f"duplicate MCP tool name discovered: {name!r}"
                )
            description = _value(tool, "description")
            if description is not None and not isinstance(description, str):
                description = None
            schema = _value(tool, "inputSchema", "input_schema", default={})
            if not isinstance(schema, Mapping):
                raise McpToolCatalogError(
                    f"MCP tool {name!r} has a non-mapping input schema"
                )
            catalog[name] = ToolDefinition(
                name=name,
                description=description,
                input_schema=deepcopy(dict(schema)),
            )

        self._catalog = catalog
        return self.tools

    async def invoke(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolObservation:
        """Validate and invoke one previously discovered MCP tool."""
        if self._catalog is None:
            raise McpToolGatewayNotDiscoveredError(
                "cannot invoke an MCP tool before discovery"
            )
        definition = self._catalog.get(tool_name)
        if definition is None:
            raise UnknownMcpToolError(
                f"unknown MCP tool {tool_name!r}; rediscover if the catalog changed"
            )
        if not isinstance(arguments, Mapping):
            raise InvalidToolArgumentsError("arguments: expected a mapping")

        copied_arguments = dict(arguments)
        _check_schema(definition.input_schema, "arguments")
        _validate(copied_arguments, definition.input_schema, "arguments")
        try:
            result = await self._session.call_tool(tool_name, copied_arguments)
        except Exception as exc:
            message = str(exc)
            detail = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
            return ToolObservation(
                tool_name=tool_name,
                status="transport_error",
                text="",
                structured_content=None,
                error=detail,
            )
        return _normalize_result(tool_name, result)
