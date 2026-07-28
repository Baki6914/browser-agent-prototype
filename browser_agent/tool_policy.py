"""Typed authorization policy for dynamically discovered MCP tools."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol

from .mcp_gateway import ToolDefinition, ToolObservation


class ToolCategory(str, Enum):
    READ_ONLY = "read_only"
    NAVIGATION = "navigation"
    PAGE_INTERACTION = "page_interaction"
    FILE_IO = "file_io"
    CODE_EXECUTION = "code_execution"
    INTERNAL_CONTROL = "internal_control"
    UNKNOWN = "unknown"


class PolicyAction(str, Enum):
    ALLOW = "allow"
    REQUIRE_CONFIRMATION = "require_confirmation"
    INTERNAL_ONLY = "internal_only"
    DENY = "deny"


@dataclass(frozen=True)
class ToolPolicyDecision:
    tool_name: str
    category: ToolCategory
    action: PolicyAction
    reason: str


class ToolPolicyError(Exception):
    """Base class for errors owned by the MCP tool policy."""


class ToolDeniedError(ToolPolicyError):
    """A denied tool was requested."""


class ToolConfirmationRequiredError(ToolPolicyError):
    """A tool requires trusted-orchestrator confirmation."""


class InternalOnlyToolError(ToolPolicyError):
    """An internal-only tool was requested through the normal path."""


def _rule(
    name: str,
    category: ToolCategory,
    action: PolicyAction,
    reason: str,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(name, category, action, reason)


# Explicit registry for the committed Playwright MCP inventory.
TOOL_POLICY_REGISTRY: Mapping[str, ToolPolicyDecision] = MappingProxyType({
    rule.tool_name: rule
    for rule in (
        _rule("browser_click", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "Clicking may change page state."),
        _rule("browser_close", ToolCategory.INTERNAL_CONTROL, PolicyAction.INTERNAL_ONLY, "Closing the browser is reserved for orchestrator cleanup."),
        _rule("browser_console_messages", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Observes console output without changing page state."),
        _rule("browser_drag", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "Dragging may change page state."),
        _rule("browser_drop", ToolCategory.FILE_IO, PolicyAction.DENY, "Dropping files or external data crosses the approved file boundary."),
        _rule("browser_evaluate", ToolCategory.CODE_EXECUTION, PolicyAction.DENY, "Arbitrary page-context code execution is not approved."),
        _rule("browser_file_upload", ToolCategory.FILE_IO, PolicyAction.DENY, "File upload lifecycle and authorization are out of scope."),
        _rule("browser_fill_form", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "Filling forms changes page state."),
        _rule("browser_find", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Searches the page snapshot without changing page state."),
        _rule("browser_handle_dialog", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "Handling a dialog may accept or reject a page action."),
        _rule("browser_hover", ToolCategory.PAGE_INTERACTION, PolicyAction.ALLOW, "Hover is an approved low-impact page interaction."),
        _rule("browser_navigate", ToolCategory.NAVIGATION, PolicyAction.ALLOW, "Navigation is an approved browser operation."),
        _rule("browser_navigate_back", ToolCategory.NAVIGATION, PolicyAction.ALLOW, "Back navigation is an approved browser operation."),
        _rule("browser_network_request", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Observes a previously recorded network request."),
        _rule("browser_network_requests", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Observes recorded network requests."),
        _rule("browser_press_key", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "A key press may change page state."),
        _rule("browser_resize", ToolCategory.PAGE_INTERACTION, PolicyAction.ALLOW, "Viewport resizing is an approved low-impact interaction."),
        _rule("browser_run_code_unsafe", ToolCategory.CODE_EXECUTION, PolicyAction.DENY, "Arbitrary unsafe code execution is prohibited."),
        _rule("browser_select_option", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "Selecting an option changes form state."),
        _rule("browser_snapshot", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Observes the current page."),
        _rule("browser_tabs", ToolCategory.NAVIGATION, PolicyAction.REQUIRE_CONFIRMATION, "Tab operations change browser context."),
        _rule("browser_take_screenshot", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Observes the rendered page."),
        _rule("browser_type", ToolCategory.PAGE_INTERACTION, PolicyAction.REQUIRE_CONFIRMATION, "Typing changes page state and may submit data."),
        _rule("browser_wait_for", ToolCategory.READ_ONLY, PolicyAction.ALLOW, "Waits for observable page state."),
    )
})

_UNKNOWN_REASON = "Tool has no explicit policy registry entry and is denied by default."
_FILENAME_REASON = "A non-empty filename writes output and requires confirmation."


def _copy_definition(definition: ToolDefinition) -> ToolDefinition:
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        input_schema=deepcopy(definition.input_schema),
    )


class McpToolPolicy:
    """Classify a discovered catalog and produce an LLM-visible subset."""

    def __init__(self, definitions: Sequence[ToolDefinition]) -> None:
        catalog: dict[str, ToolDefinition] = {}
        for definition in definitions:
            copied = _copy_definition(definition)
            if copied.name in catalog:
                raise ToolPolicyError(
                    f"duplicate discovered tool name: {copied.name!r}"
                )
            catalog[copied.name] = copied
        self._catalog = catalog

    def decide(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolPolicyDecision:
        if not isinstance(arguments, Mapping):
            raise ToolPolicyError("policy arguments must be a mapping")
        base = TOOL_POLICY_REGISTRY.get(tool_name)
        if base is None:
            return ToolPolicyDecision(
                tool_name,
                ToolCategory.UNKNOWN,
                PolicyAction.DENY,
                _UNKNOWN_REASON,
            )
        filename = arguments.get("filename")
        if (
            base.action is PolicyAction.ALLOW
            and isinstance(filename, str)
            and bool(filename.strip())
        ):
            return ToolPolicyDecision(
                tool_name,
                base.category,
                PolicyAction.REQUIRE_CONFIRMATION,
                _FILENAME_REASON,
            )
        return base

    def llm_visible_tools(self) -> tuple[ToolDefinition, ...]:
        visible_actions = {
            PolicyAction.ALLOW,
            PolicyAction.REQUIRE_CONFIRMATION,
        }
        return tuple(
            _copy_definition(self._catalog[name])
            for name in sorted(self._catalog)
            if self.decide(name, {}).action in visible_actions
        )

    def decisions(self) -> tuple[ToolPolicyDecision, ...]:
        return tuple(self.decide(name, {}) for name in sorted(self._catalog))

    def unclassified_tool_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in sorted(self._catalog) if name not in TOOL_POLICY_REGISTRY
        )


class ToolGatewayProtocol(Protocol):
    async def invoke(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolObservation:
        """Invoke a discovered tool through the transport gateway."""


class PolicyEnforcedToolExecutor:
    """Enforce policy before delegating any invocation to the gateway."""

    def __init__(self, gateway: ToolGatewayProtocol, policy: McpToolPolicy) -> None:
        self._gateway = gateway
        self._policy = policy

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> ToolObservation:
        if type(confirmation_granted) is not bool:
            raise ToolPolicyError("confirmation_granted must be a bool")
        decision = self._policy.decide(tool_name, arguments)
        if decision.action is PolicyAction.ALLOW:
            return await self._gateway.invoke(tool_name, arguments)
        if decision.action is PolicyAction.REQUIRE_CONFIRMATION:
            if confirmation_granted is True:
                return await self._gateway.invoke(tool_name, arguments)
            raise ToolConfirmationRequiredError(
                f"tool {tool_name!r} requires confirmation: {decision.reason}"
            )
        if decision.action is PolicyAction.INTERNAL_ONLY:
            raise InternalOnlyToolError(
                f"tool {tool_name!r} is internal-only: {decision.reason}"
            )
        raise ToolDeniedError(f"tool {tool_name!r} is denied: {decision.reason}")

    async def invoke_internal(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolObservation:
        decision = self._policy.decide(tool_name, arguments)
        if decision.action is PolicyAction.INTERNAL_ONLY:
            return await self._gateway.invoke(tool_name, arguments)
        if decision.action is PolicyAction.DENY:
            raise ToolDeniedError(
                f"tool {tool_name!r} remains denied on the internal path: "
                f"{decision.reason}"
            )
        raise ToolPolicyError(
            f"tool {tool_name!r} is not internal-only and cannot use "
            "the internal cleanup path"
        )
