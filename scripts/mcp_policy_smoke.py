"""Live discovery-only smoke check for the MCP tool policy."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path


class PolicySmokeError(RuntimeError):
    """A clear live policy-smoke failure."""


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
    raise PolicySmokeError(f"local @playwright/mcp CLI not found under {package}")


def _validate_policy(policy: object) -> None:
    from browser_agent.tool_policy import PolicyAction

    unclassified = policy.unclassified_tool_names()
    if unclassified:
        raise PolicySmokeError(
            "discovered tools without explicit registry rules: "
            + ", ".join(unclassified)
        )
    decisions = policy.decisions()
    by_name = {decision.tool_name: decision for decision in decisions}
    expected = {
        "browser_run_code_unsafe": PolicyAction.DENY,
        "browser_evaluate": PolicyAction.DENY,
        "browser_file_upload": PolicyAction.DENY,
        "browser_drop": PolicyAction.DENY,
        "browser_close": PolicyAction.INTERNAL_ONLY,
    }
    for name, action in expected.items():
        if name in by_name and by_name[name].action is not action:
            raise PolicySmokeError(
                f"{name} must be {action.value}, got {by_name[name].action.value}"
            )
    visible_names = {tool.name for tool in policy.llm_visible_tools()}
    hidden = {
        decision.tool_name
        for decision in decisions
        if decision.action in {PolicyAction.DENY, PolicyAction.INTERNAL_ONLY}
    }
    leaked = sorted(visible_names & hidden)
    if leaked:
        raise PolicySmokeError(
            "denied or internal-only tools leaked into visible catalog: "
            + ", ".join(leaked)
        )
    for decision in decisions:
        print(
            f"{decision.tool_name}: {decision.category.value} / "
            f"{decision.action.value}"
        )
    counts = Counter(decision.action.value for decision in decisions)
    print(
        "action counts: "
        + ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
    )


async def run_smoke() -> None:
    """Start MCP, discover tools, classify locally, and shut down."""
    root = _repository_root()
    sys.path.insert(0, str(root))

    from browser_agent.mcp_gateway import McpToolGateway
    from browser_agent.tool_policy import McpToolPolicy
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    node = shutil.which("node")
    if node is None:
        raise PolicySmokeError("Node.js executable was not found")
    config = root / "config" / "playwright-mcp.spike.json"
    if not config.is_file():
        raise PolicySmokeError(f"MCP configuration not found: {config}")
    server = StdioServerParameters(
        command=node,
        args=[str(_playwright_mcp_cli(root)), "--config", str(config)],
        cwd=str(root),
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            gateway = McpToolGateway(session)
            policy = McpToolPolicy(await gateway.discover())
            _validate_policy(policy)


def _exception_details(exc: Exception) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(
            _exception_details(nested)
            for nested in exc.exceptions
            if isinstance(nested, Exception)
        )
    return f"{type(exc).__name__}: {exc}"


def main() -> int:
    try:
        asyncio.run(run_smoke())
    except Exception as exc:
        print(f"MCP policy smoke failed: {_exception_details(exc)}", file=sys.stderr)
        return 1
    print("MCP policy smoke succeeded: discovery and classification only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
