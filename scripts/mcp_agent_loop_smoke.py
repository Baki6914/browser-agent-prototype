"""Live smoke check for the deterministic MCP-backed agent loop."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class AgentLoopSmokeError(RuntimeError):
    """A clear live agent-loop smoke failure."""


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
    raise AgentLoopSmokeError(
        f"local @playwright/mcp CLI not found under {package}"
    )


def _exception_details(exc: Exception) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(
            _exception_details(nested)
            for nested in exc.exceptions
            if isinstance(nested, Exception)
        )
    return f"{type(exc).__name__}: {exc}"


class RecordingPolicyExecutor:
    """Record only normal policy calls while preserving the real executor."""

    def __init__(self, executor: object) -> None:
        self._executor = executor
        self.calls: list[str] = []

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> object:
        self.calls.append(tool_name)
        return await self._executor.invoke(  # type: ignore[attr-defined,no-any-return]
            tool_name,
            arguments,
            confirmation_granted=confirmation_granted,
        )


async def _run_loop(executor: object, definitions: tuple[object, ...]) -> None:
    from browser_agent.agent_controls import AgentControlExecutor
    from browser_agent.agent_loop import (
        AgentRunStatus,
        AgentStepStatus,
        AgentToolCall,
        AgentToolRouter,
        AgentToolSource,
        DeterministicAgentLoop,
        ScriptedDecisionSource,
    )

    recording_executor = RecordingPolicyExecutor(executor)
    router = AgentToolRouter(
        recording_executor,
        definitions,  # type: ignore[arg-type]
        AgentControlExecutor(),
    )
    source = ScriptedDecisionSource(
        [
            AgentToolCall(
                "browser_navigate", {"url": "https://example.com"}
            ),
            AgentToolCall("browser_snapshot", {}),
            AgentToolCall(
                "finish",
                {"result": "Example Domain inspected successfully."},
            ),
        ]
    )
    result = await DeterministicAgentLoop(source, router, 3).run(
        "Inspect Example Domain."
    )

    if len(result.steps) != 3:
        raise AgentLoopSmokeError(
            f"loop recorded {len(result.steps)} steps, expected 3"
        )
    if recording_executor.calls != ["browser_navigate", "browser_snapshot"]:
        raise AgentLoopSmokeError(
            "normal MCP calls were not exactly browser_navigate and "
            f"browser_snapshot: {recording_executor.calls!r}"
        )
    for step, expected_tool_name in zip(
        result.steps[:2],
        ("browser_navigate", "browser_snapshot"),
        strict=True,
    ):
        if step.decision.tool_name != expected_tool_name:
            raise AgentLoopSmokeError(
                f"step {step.step_number} decision was "
                f"{step.decision.tool_name!r}, expected {expected_tool_name!r}"
            )
        if step.observation.source is not AgentToolSource.MCP:
            raise AgentLoopSmokeError(
                f"{expected_tool_name} observation source was "
                f"{step.observation.source!r}, expected MCP"
            )
        if step.observation.status is not AgentStepStatus.SUCCESS:
            error_detail = (
                f"; error: {step.observation.error}"
                if step.observation.error is not None
                else ""
            )
            raise AgentLoopSmokeError(
                f"{expected_tool_name} observation status was "
                f"{step.observation.status.value}, expected success"
                f"{error_detail}"
            )
    if result.status is not AgentRunStatus.FINISHED:
        raise AgentLoopSmokeError(
            f"loop status was {result.status.value}, expected finished"
        )
    if result.final_result != "Example Domain inspected successfully.":
        raise AgentLoopSmokeError(
            f"unexpected final result: {result.final_result!r}"
        )
    final_observation = result.steps[-1].observation
    if final_observation.source is not AgentToolSource.AGENT_CONTROL:
        raise AgentLoopSmokeError("finish was not recorded as an agent control")
    if final_observation.status is not AgentStepStatus.FINISHED:
        raise AgentLoopSmokeError("final loop step was not finished")


async def run_smoke() -> None:
    """Run navigate, snapshot, local finish, and internal cleanup-close."""
    root = _repository_root()
    sys.path.insert(0, str(root))

    from browser_agent.mcp_gateway import McpToolGateway
    from browser_agent.tool_policy import (
        McpToolPolicy,
        PolicyEnforcedToolExecutor,
    )
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    node = shutil.which("node")
    if node is None:
        raise AgentLoopSmokeError("Node.js executable was not found")
    config = root / "config" / "playwright-mcp.spike.json"
    if not config.is_file():
        raise AgentLoopSmokeError(f"MCP configuration not found: {config}")
    server = StdioServerParameters(
        command=node,
        args=[str(_playwright_mcp_cli(root)), "--config", str(config)],
        cwd=str(root),
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            gateway = McpToolGateway(session)
            discovered = await gateway.discover()
            policy = McpToolPolicy(discovered)
            executor = PolicyEnforcedToolExecutor(gateway, policy)
            primary_error: Exception | None = None
            cleanup_error: Exception | None = None
            try:
                await _run_loop(executor, policy.llm_visible_tools())
            except Exception as exc:
                primary_error = exc
            finally:
                try:
                    close_observation = await executor.invoke_internal(
                        "browser_close", {}
                    )
                    if close_observation.status != "success":
                        raise AgentLoopSmokeError(
                            "browser_close failed with "
                            f"{close_observation.status}: "
                            f"{close_observation.error}"
                        )
                except Exception as exc:
                    cleanup_error = exc

            if primary_error is not None:
                if cleanup_error is not None:
                    raise AgentLoopSmokeError(
                        "primary failure: "
                        f"{_exception_details(primary_error)}; cleanup failure: "
                        f"{_exception_details(cleanup_error)}"
                    ) from primary_error
                raise primary_error
            if cleanup_error is not None:
                raise cleanup_error


def main() -> int:
    try:
        asyncio.run(run_smoke())
    except Exception as exc:
        print(
            f"MCP agent loop smoke failed: {_exception_details(exc)}",
            file=sys.stderr,
        )
        return 1
    print(
        "MCP agent loop smoke succeeded: navigate, snapshot, finish, and close"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
