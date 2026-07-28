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

__all__ = [
    "InvalidToolArgumentsError",
    "McpGatewayError",
    "McpSessionProtocol",
    "McpToolCatalogError",
    "McpToolGateway",
    "McpToolGatewayNotDiscoveredError",
    "ToolDefinition",
    "ToolObservation",
    "UnknownMcpToolError",
    "UnsupportedToolSchemaError",
]
