"""Live external OpenAI-compatible provider plus local MCP integration smoke.

The temporary external provider is NVIDIA API, but this script and the provider
implementation are provider-neutral. Passing proves external integration only,
not offline, internal, local-vLLM, confidential-data, or production readiness.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class OpenAIAgentSmokeError(RuntimeError):
    """A clear live OpenAI-compatible agent smoke failure."""


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
    raise OpenAIAgentSmokeError(
        f"local @playwright/mcp CLI not found under {package}"
    )


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise OpenAIAgentSmokeError(
            f"required environment variable {name} is missing or empty"
        )
    return value


def _timeout_from_environment() -> float:
    raw = os.environ.get("BROWSER_AGENT_LLM_TIMEOUT")
    if raw is None:
        return 60.0
    try:
        value = float(raw)
    except ValueError as exc:
        raise OpenAIAgentSmokeError(
            "BROWSER_AGENT_LLM_TIMEOUT must be a finite number greater than zero"
        ) from exc
    return value


def _exception_details(exc: Exception) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(
            _exception_details(nested)
            for nested in exc.exceptions
            if isinstance(nested, Exception)
        )
    return f"{type(exc).__name__}: {exc}"


class RecordingPolicyExecutor:
    """Record normal policy calls while delegating to the real executor."""

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


async def _run_loop(
    executor: object,
    definitions: tuple[object, ...],
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    timeout: float,
) -> None:
    from browser_agent import (
        AgentControlExecutor,
        AgentPauseKind,
        AgentRunStatus,
        AgentStepStatus,
        AgentToolRouter,
        AgentToolSource,
        DeterministicAgentLoop,
        OpenAICompatibleDecisionSource,
        OpenAICompatibleProviderConfig,
    )

    recording_executor = RecordingPolicyExecutor(executor)
    router = AgentToolRouter(
        recording_executor,
        definitions,  # type: ignore[arg-type]
        AgentControlExecutor(),
    )
    source = OpenAICompatibleDecisionSource(
        OpenAICompatibleProviderConfig(base_url, model, api_key, timeout)
    )
    task = (
        "First call browser_navigate for https://example.com. Then inspect the "
        "page with browser_snapshot. Then call finish with a concise result "
        "containing the page title."
    )
    result = await DeterministicAgentLoop(source, router, max_steps=5).run(task)
    if result.status is not AgentRunStatus.AWAITING_USER:
        raise OpenAIAgentSmokeError(
            f"loop status was {result.status.value}, expected awaiting_user"
        )
    if result.pause_kind is not AgentPauseKind.COMPLETION:
        raise OpenAIAgentSmokeError(
            f"pause kind was {result.pause_kind!r}, expected completion"
        )
    if len(result.steps) != 3:
        raise OpenAIAgentSmokeError(
            f"loop recorded {len(result.steps)} steps, expected 3"
        )
    expected = (
        ("browser_navigate", AgentToolSource.MCP, AgentStepStatus.SUCCESS),
        ("browser_snapshot", AgentToolSource.MCP, AgentStepStatus.SUCCESS),
        (
            "finish",
            AgentToolSource.AGENT_CONTROL,
            AgentStepStatus.COMPLETION_PROPOSED,
        ),
    )
    for step, (name, source_kind, status) in zip(
        result.steps, expected, strict=True
    ):
        if (
            step.decision.tool_name != name
            or step.observation.source is not source_kind
            or step.observation.status is not status
        ):
            raise OpenAIAgentSmokeError(
                f"unexpected step {step.step_number}: "
                f"{step.decision.tool_name!r}, {step.observation.source!r}, "
                f"{step.observation.status.value!r}"
            )
    if recording_executor.calls != ["browser_navigate", "browser_snapshot"]:
        raise OpenAIAgentSmokeError(
            "normal MCP calls were not exactly browser_navigate and "
            f"browser_snapshot: {recording_executor.calls!r}"
        )
    if result.final_result is not None:
        raise OpenAIAgentSmokeError(
            f"final_result was set before approval: {result.final_result!r}"
        )
    if (
        not result.completion_proposal
        or "example domain" not in result.completion_proposal.lower()
    ):
        raise OpenAIAgentSmokeError(
            "completion_proposal was empty or did not contain 'Example Domain'"
        )


async def run_smoke() -> None:
    """Run model decisions, policy-routed MCP tools, and internal cleanup."""
    root = _repository_root()
    sys.path.insert(0, str(root))
    base_url = _required_environment("BROWSER_AGENT_LLM_BASE_URL")
    model = _required_environment("BROWSER_AGENT_LLM_MODEL")
    api_key = os.environ.get("BROWSER_AGENT_LLM_API_KEY")
    if api_key is not None and not api_key.strip():
        raise OpenAIAgentSmokeError(
            "BROWSER_AGENT_LLM_API_KEY must be non-empty when set"
        )
    timeout = _timeout_from_environment()

    from browser_agent import (
        McpToolGateway,
        McpToolPolicy,
        OpenAICompatibleProviderConfig,
        PolicyEnforcedToolExecutor,
    )
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Validate all generic provider settings before starting the local MCP stack.
    OpenAICompatibleProviderConfig(base_url, model, api_key, timeout)
    node = shutil.which("node")
    if node is None:
        raise OpenAIAgentSmokeError("Node.js executable was not found")
    config = root / "config" / "playwright-mcp.spike.json"
    if not config.is_file():
        raise OpenAIAgentSmokeError(f"MCP configuration not found: {config}")
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
                await _run_loop(
                    executor,
                    policy.llm_visible_tools(),
                    base_url=base_url,
                    model=model,
                    api_key=api_key,
                    timeout=timeout,
                )
            except Exception as exc:
                primary_error = exc
            finally:
                try:
                    close = await executor.invoke_internal("browser_close", {})
                    if close.status != "success":
                        raise OpenAIAgentSmokeError(
                            f"browser_close failed with {close.status}: "
                            f"{close.error}"
                        )
                except Exception as exc:
                    cleanup_error = exc
            if primary_error is not None:
                if cleanup_error is not None:
                    raise OpenAIAgentSmokeError(
                        f"primary failure: {_exception_details(primary_error)}; "
                        f"cleanup failure: {_exception_details(cleanup_error)}"
                    ) from primary_error
                raise primary_error
            if cleanup_error is not None:
                raise cleanup_error


def main() -> int:
    api_key = os.environ.get("BROWSER_AGENT_LLM_API_KEY")
    try:
        asyncio.run(run_smoke())
    except Exception as exc:
        detail = _exception_details(exc)
        if api_key:
            detail = detail.replace(api_key, "[REDACTED]")
        print(f"OpenAI-compatible agent smoke failed: {detail}", file=sys.stderr)
        return 1
    print(
        "OpenAI-compatible agent smoke succeeded: model navigate, snapshot, "
        "finish, and close"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
