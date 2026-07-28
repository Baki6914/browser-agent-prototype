"""Public package interface for the browser-agent prototype."""

from .mcp_gateway import (
    InvalidToolArgumentsError,
    McpGatewayError,
    McpSessionProtocol,
    McpToolCatalogError,
    McpToolGateway,
    McpToolGatewayNotDiscoveredError,
    ToolDefinition,
    ToolObservation,
    UnknownMcpToolError,
    UnsupportedToolSchemaError,
)
from .tool_policy import (
    InternalOnlyToolError,
    McpToolPolicy,
    PolicyAction,
    PolicyEnforcedToolExecutor,
    ToolCategory,
    ToolConfirmationRequiredError,
    ToolDeniedError,
    ToolPolicyDecision,
    ToolPolicyError,
)

__all__ = [
    "InvalidToolArgumentsError",
    "InternalOnlyToolError",
    "McpGatewayError",
    "McpSessionProtocol",
    "McpToolCatalogError",
    "McpToolGateway",
    "McpToolGatewayNotDiscoveredError",
    "McpToolPolicy",
    "PolicyAction",
    "PolicyEnforcedToolExecutor",
    "ToolDefinition",
    "ToolCategory",
    "ToolConfirmationRequiredError",
    "ToolDeniedError",
    "ToolObservation",
    "ToolPolicyDecision",
    "ToolPolicyError",
    "UnknownMcpToolError",
    "UnsupportedToolSchemaError",
]
