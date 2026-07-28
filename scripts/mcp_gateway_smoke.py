"""Live stdio smoke check for the dynamic MCP tool gateway."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

REQUIRED_TOOLS = frozenset(
    {"browser_navigate", "browser_snapshot", "browser_close"}
)


class SmokeError(RuntimeError):
    """A clear live-smoke failure."""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _playwright_mcp_cli(root: Path) -> Path:
    package = root / "node_modules" / "@playwright" / "mcp"
    metadata_path = package / "package.json"
    candidates = [package / "cli.js"]
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        package_bin = metadata.get("bin")
        if isinstance(package_bin, str):
            candidates.insert(0, package / package_bin)
        elif isinstance(package_bin, Mapping):
            candidates[0:0] = [
                package / value
                for value in package_bin.values()
                if isinstance(value, str)
            ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(package.resolve()):
            return resolved
    raise SmokeError(f"local @playwright/mcp CLI not found under {package}")


async def _require_success(gateway: object, name: str, arguments: dict) -> None:
    observation = await gateway.invoke(name, arguments)  # type: ignore[attr-defined]
    if observation.status != "success":
        raise SmokeError(
            f"{name} failed with {observation.status}: {observation.error}"
        )


def _exception_details(exc: Exception) -> str:
    """Return useful leaf details, including for nested exception groups."""
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(
            _exception_details(nested)
            for nested in exc.exceptions
            if isinstance(nested, Exception)
        )
    return f"{type(exc).__name__}: {exc}"


async def _run_browser_sequence(gateway: object, names: set[str]) -> None:
    """Validate discovery, run primary operations, and close when available."""
    primary_error: Exception | None = None
    cleanup_error: Exception | None = None
    try:
        missing = sorted(REQUIRED_TOOLS - names)
        if missing:
            raise SmokeError(
                f"required dynamically discovered tools missing: {', '.join(missing)}"
            )
        await _require_success(
            gateway,
            "browser_navigate",
            {"url": "https://example.com"},
        )
        await _require_success(gateway, "browser_snapshot", {})
    except Exception as exc:
        primary_error = exc
    finally:
        if "browser_close" in names:
            try:
                await _require_success(gateway, "browser_close", {})
            except Exception as exc:
                cleanup_error = exc

    if primary_error is not None:
        if cleanup_error is not None:
            raise SmokeError(
                f"primary failure: {_exception_details(primary_error)}; "
                f"cleanup failure: {_exception_details(cleanup_error)}"
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error


async def run_smoke() -> None:
    """Run the approved navigate, snapshot, and cleanup-close sequence."""
    root = _repository_root()
    sys.path.insert(0, str(root))

    from browser_agent.mcp_gateway import McpToolGateway
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    node = shutil.which("node")
    if node is None:
        raise SmokeError("Node.js executable was not found")
    config = root / "config" / "playwright-mcp.spike.json"
    if not config.is_file():
        raise SmokeError(f"MCP configuration not found: {config}")
    server = StdioServerParameters(
        command=node,
        args=[str(_playwright_mcp_cli(root)), "--config", str(config)],
        cwd=str(root),
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            gateway = McpToolGateway(session)
            definitions = await gateway.discover()
            names = {definition.name for definition in definitions}
            await _run_browser_sequence(gateway, names)


def main() -> int:
    try:
        asyncio.run(run_smoke())
    except Exception as exc:
        print(f"MCP gateway smoke failed: {_exception_details(exc)}", file=sys.stderr)
        return 1
    print("MCP gateway smoke succeeded: navigate, snapshot, and close")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
