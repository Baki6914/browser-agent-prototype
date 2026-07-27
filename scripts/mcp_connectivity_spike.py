"""Narrow local stdio connectivity spike for Playwright MCP.

Pure helper functions in this module intentionally avoid importing the MCP SDK
so their unit tests can run before Phase 2 dependency installation.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REQUIRED_TOOLS = frozenset({"browser_navigate", "browser_snapshot"})
TARGET_URL = "https://example.com"
STARTUP_TIMEOUT_SECONDS = 30.0
OPERATION_TIMEOUT_SECONDS = 60.0
SHUTDOWN_TIMEOUT_SECONDS = 15.0


class SpikeError(RuntimeError):
    """A connectivity-spike failure with a user-facing category."""


def repository_root(start: Path | None = None) -> Path:
    """Return the repository root containing this script's known directories."""
    candidate = (start or Path(__file__)).resolve()
    current = candidate if candidate.is_dir() else candidate.parent
    for directory in (current, *current.parents):
        if (directory / "scripts").is_dir() and (directory / "config").is_dir():
            return directory
    raise SpikeError(f"startup error: repository root not found from {candidate}")


def playwright_mcp_cli(root: Path) -> Path:
    """Return the local Playwright MCP Node entry point or raise a clear error."""
    package_dir = root / "node_modules" / "@playwright" / "mcp"
    package_json = package_dir / "package.json"
    candidates = [package_dir / "cli.js"]

    if package_json.is_file():
        try:
            metadata = json.loads(package_json.read_text(encoding="utf-8"))
            package_bin = metadata.get("bin")
            if isinstance(package_bin, str):
                candidates.insert(0, package_dir / package_bin)
            elif isinstance(package_bin, Mapping):
                for value in package_bin.values():
                    if isinstance(value, str):
                        candidates.insert(0, package_dir / value)
        except (OSError, json.JSONDecodeError) as exc:
            raise SpikeError(
                f"missing CLI error: cannot read {package_json}: {exc}"
            ) from exc

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(package_dir.resolve()):
            return resolved
    raise SpikeError(
        "missing CLI error: local @playwright/mcp CLI was not found under "
        f"{package_dir}; run npm install first"
    )


def summarize_schema(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Produce a stable, JSON-compatible summary of a tool input schema."""
    source = schema or {}
    properties = source.get("properties", {})
    property_summary: dict[str, dict[str, Any]] = {}
    if isinstance(properties, Mapping):
        for name in sorted(str(key) for key in properties):
            value = properties.get(name)
            detail: dict[str, Any] = {}
            if isinstance(value, Mapping):
                for field in ("type", "description", "default"):
                    if field in value and isinstance(
                        value[field], (str, int, float, bool, type(None))
                    ):
                        detail[field] = value[field]
                enum = value.get("enum")
                if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)):
                    detail["enum"] = list(enum)
            property_summary[name] = detail

    required = source.get("required", [])
    required_names = (
        sorted(str(item) for item in required)
        if isinstance(required, Sequence) and not isinstance(required, (str, bytes))
        else []
    )
    summary: dict[str, Any] = {
        "type": source.get("type") if isinstance(source.get("type"), str) else None,
        "properties": property_summary,
        "required": required_names,
    }
    if isinstance(source.get("additionalProperties"), bool):
        summary["additionalProperties"] = source["additionalProperties"]
    return summary


def _tool_value(tool: object, attribute: str, default: Any = None) -> Any:
    if isinstance(tool, Mapping):
        return tool.get(attribute, default)
    return getattr(tool, attribute, default)


def build_inventory(tools: Sequence[object]) -> dict[str, Any]:
    """Build a deterministic inventory from a real MCP list_tools response."""
    entries = []
    for tool in tools:
        name = _tool_value(tool, "name")
        if not isinstance(name, str) or not name:
            raise SpikeError("inventory error: discovered a tool without a valid name")
        description = _tool_value(tool, "description")
        schema = _tool_value(tool, "inputSchema", {})
        entries.append(
            {
                "name": name,
                "description": description if isinstance(description, str) else None,
                "input_schema": summarize_schema(
                    schema if isinstance(schema, Mapping) else {}
                ),
            }
        )
    entries.sort(key=lambda item: item["name"])
    return {"tool_count": len(entries), "tools": entries}


def validate_required_tools(tool_names: Sequence[str]) -> None:
    """Require the minimum safe interaction tools."""
    missing = sorted(REQUIRED_TOOLS.difference(tool_names))
    if missing:
        raise SpikeError(f"missing-tool error: required tools absent: {', '.join(missing)}")


def extract_result_text(result: object) -> str:
    """Extract useful text from MCP content blocks and structured content."""
    parts: list[str] = []
    content = _tool_value(result, "content", [])
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        for block in content:
            text = _tool_value(block, "text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())

    structured = _tool_value(result, "structuredContent")
    if structured is None:
        structured = _tool_value(result, "structured_content")
    if structured is not None:
        parts.append(json.dumps(structured, sort_keys=True, ensure_ascii=False))
    return "\n".join(parts)


def ensure_usable_result(operation: str, result: object) -> str:
    """Reject MCP-declared errors and empty results without brittle matching."""
    is_error = _tool_value(result, "isError", _tool_value(result, "is_error", False))
    text = extract_result_text(result)
    if is_error:
        raise SpikeError(f"invocation error: {operation} returned an MCP error: {text}")
    if not text.strip():
        raise SpikeError(f"invocation error: {operation} returned no usable content")
    return text


def raise_preserving_failures(
    primary_error: Exception | None,
    shutdown_error: Exception | None,
) -> None:
    """Raise a sole failure unchanged, or report primary and shutdown failures."""
    if primary_error is not None and shutdown_error is not None:
        raise SpikeError(
            f"primary operation failure: {primary_error}; "
            f"additional shutdown failure: {shutdown_error}"
        ) from primary_error
    if primary_error is not None:
        raise primary_error
    if shutdown_error is not None:
        raise shutdown_error


def extract_exception_messages(exc: Exception) -> list[str]:
    """Return distinct leaf messages from nested exception groups."""
    messages: list[str] = []
    seen: set[str] = set()

    def visit(current: Exception) -> None:
        if isinstance(current, ExceptionGroup):
            for nested in current.exceptions:
                visit(nested)
            return

        message = str(current).strip()
        if message and message not in seen:
            seen.add(message)
            messages.append(message)

    visit(exc)
    return messages


async def _with_timeout(awaitable: Any, label: str, seconds: float) -> Any:
    try:
        async with asyncio.timeout(seconds):
            return await awaitable
    except TimeoutError as exc:
        raise SpikeError(f"timeout error: {label} exceeded {seconds:.0f}s") from exc


async def run_connectivity_spike() -> None:
    """Start MCP over stdio, discover tools, and perform two safe browser calls."""
    root = repository_root()
    config_path = root / "config" / "playwright-mcp.spike.json"
    if not config_path.is_file():
        raise SpikeError(f"startup error: configuration is missing: {config_path}")
    node = shutil.which("node")
    if node is None:
        raise SpikeError("missing dependency error: Node.js executable was not found")
    cli_path = playwright_mcp_cli(root)

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        raise SpikeError(
            "missing dependency error: mcp==1.28.1 is not installed"
        ) from exc

    server = StdioServerParameters(
        command=node,
        args=[str(cli_path), "--config", str(config_path)],
        cwd=str(root),
    )

    try:
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                try:
                    await _with_timeout(
                        session.initialize(),
                        "MCP initialization",
                        STARTUP_TIMEOUT_SECONDS,
                    )
                except SpikeError:
                    raise
                except Exception as exc:
                    raise SpikeError(f"initialization error: {exc}") from exc

                response = await _with_timeout(
                    session.list_tools(),
                    "list_tools",
                    OPERATION_TIMEOUT_SECONDS,
                )
                tools = response.tools
                validate_required_tools([tool.name for tool in tools])
                inventory = build_inventory(tools)
                inventory_path = root / "docs" / "mcp-tool-inventory.json"
                inventory_path.write_text(
                    json.dumps(inventory, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )

                names = {tool.name for tool in tools}
                primary_error: Exception | None = None
                shutdown_error: Exception | None = None
                try:
                    try:
                        navigate = await _with_timeout(
                            session.call_tool(
                                "browser_navigate", arguments={"url": TARGET_URL}
                            ),
                            "browser_navigate",
                            OPERATION_TIMEOUT_SECONDS,
                        )
                        ensure_usable_result("browser_navigate", navigate)

                        snapshot = await _with_timeout(
                            session.call_tool("browser_snapshot", arguments={}),
                            "browser_snapshot",
                            OPERATION_TIMEOUT_SECONDS,
                        )
                        ensure_usable_result("browser_snapshot", snapshot)
                    except Exception as exc:
                        primary_error = exc
                finally:
                    if "browser_close" in names:
                        try:
                            close_result = await _with_timeout(
                                session.call_tool("browser_close", arguments={}),
                                "browser_close",
                                SHUTDOWN_TIMEOUT_SECONDS,
                            )
                            if _tool_value(
                                close_result,
                                "isError",
                                _tool_value(close_result, "is_error", False),
                            ):
                                raise SpikeError(
                                    "shutdown error: browser_close returned an MCP "
                                    "error: "
                                    + extract_result_text(close_result)
                                )
                        except Exception as exc:
                            shutdown_error = exc
                raise_preserving_failures(primary_error, shutdown_error)
    except SpikeError:
        raise
    except Exception as exc:
        messages = extract_exception_messages(exc)
        details = "; ".join(messages) if messages else str(exc)
        raise SpikeError(
            f"startup, invocation, or shutdown error: {details}"
        ) from exc


def main() -> int:
    """Run the spike and translate failures into a process exit code."""
    try:
        asyncio.run(run_connectivity_spike())
    except SpikeError as exc:
        print(f"Playwright MCP connectivity spike failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Playwright MCP connectivity spike interrupted", file=sys.stderr)
        return 130
    print("Playwright MCP connectivity spike succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
