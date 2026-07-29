"""Live provider-neutral Milestone 7 evaluation runner.

Importing this module does not import MCP packages, read provider environment
variables, start processes, or perform network access.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


class MvpEvaluationScriptError(RuntimeError):
    """A live evaluation orchestration or configuration failure."""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_repository_import_path() -> None:
    root = str(_repository_root())
    if root not in sys.path:
        sys.path.insert(0, root)


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
    raise MvpEvaluationScriptError(
        f"local @playwright/mcp CLI not found under {package}"
    )


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise MvpEvaluationScriptError(
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
        raise MvpEvaluationScriptError(
            "BROWSER_AGENT_LLM_TIMEOUT must be a finite number greater than zero"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise MvpEvaluationScriptError(
            "BROWSER_AGENT_LLM_TIMEOUT must be a finite number greater than zero"
        )
    return value


def _safe_exception_details(exc: Exception, api_key: str | None) -> str:
    from browser_agent import OpenAIProviderHTTPError

    if isinstance(exc, OpenAIProviderHTTPError):
        return (
            "OpenAIProviderHTTPError: external provider returned an HTTP "
            "error; response body omitted"
        )
    if isinstance(exc, BaseExceptionGroup):
        details = [
            _safe_exception_details(nested, api_key)
            for nested in exc.exceptions
            if isinstance(nested, Exception)
        ]
        return "; ".join(details) or type(exc).__name__
    return _sanitize(f"{type(exc).__name__}: {exc}", api_key)


def _sanitize(text: str, api_key: str | None) -> str:
    if api_key:
        return text.replace(api_key, "[REDACTED]")
    return text


def _provider_settings() -> tuple[str, str, str | None, float]:
    base_url = _required_environment("BROWSER_AGENT_LLM_BASE_URL")
    model = _required_environment("BROWSER_AGENT_LLM_MODEL")
    api_key = os.environ.get("BROWSER_AGENT_LLM_API_KEY")
    if api_key is not None and not api_key.strip():
        raise MvpEvaluationScriptError(
            "BROWSER_AGENT_LLM_API_KEY must be non-empty when set"
        )
    return base_url, model, api_key, _timeout_from_environment()


async def _run_one_scenario(
    scenario: object,
    *,
    base_url: str,
    model: str,
    api_key: str | None,
    timeout: float,
) -> object:
    # MCP SDK imports stay inside the executable orchestration path.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from browser_agent import (
        AgentControlExecutor,
        AgentToolRouter,
        DeterministicAgentLoop,
        McpToolGateway,
        McpToolPolicy,
        OpenAICompatibleDecisionSource,
        OpenAICompatibleProviderConfig,
        PolicyEnforcedToolExecutor,
        create_mvp_error_result,
        evaluate_mvp_scenario,
    )

    root = _repository_root()
    started = monotonic()
    run_result = None
    primary_error: str | None = None
    cleanup_error: str | None = None
    node = shutil.which("node")
    if node is None:
        raise MvpEvaluationScriptError("Node.js executable was not found")
    config = root / "config" / "playwright-mcp.spike.json"
    if not config.is_file():
        raise MvpEvaluationScriptError(f"MCP configuration not found: {config}")
    server = StdioServerParameters(
        command=node,
        args=[str(_playwright_mcp_cli(root)), "--config", str(config)],
        cwd=str(root),
    )

    try:
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                gateway = McpToolGateway(session)
                discovered = await gateway.discover()
                policy = McpToolPolicy(discovered)
                executor = PolicyEnforcedToolExecutor(gateway, policy)
                try:
                    router = AgentToolRouter(
                        executor,
                        policy.llm_visible_tools(),
                        AgentControlExecutor(),
                    )
                    source = OpenAICompatibleDecisionSource(
                        OpenAICompatibleProviderConfig(
                            base_url, model, api_key, timeout
                        )
                    )
                    run_result = await DeterministicAgentLoop(
                        source, router, scenario.max_steps
                    ).run(scenario.task)
                except Exception as exc:
                    primary_error = _safe_exception_details(exc, api_key)
                finally:
                    try:
                        close = await executor.invoke_internal(
                            "browser_close", {}
                        )
                        if close.status != "success":
                            detail = close.error or close.status
                            raise MvpEvaluationScriptError(
                                f"browser_close failed: {detail}"
                            )
                    except Exception as exc:
                        cleanup_error = _safe_exception_details(exc, api_key)
    except Exception as exc:
        outside_error = _safe_exception_details(exc, api_key)
        if primary_error is None:
            primary_error = outside_error
        else:
            cleanup_error = (
                f"{cleanup_error}; {outside_error}"
                if cleanup_error
                else outside_error
            )

    duration = monotonic() - started
    errors: list[str] = []
    if primary_error is not None:
        errors.append(f"primary failure: {primary_error}")
    if cleanup_error is not None:
        errors.append(f"cleanup failure: {cleanup_error}")
    if errors:
        return create_mvp_error_result(
            scenario,
            "; ".join(errors),
            duration_seconds=duration,
            run_result=run_result,
        )
    if run_result is None:
        return create_mvp_error_result(
            scenario,
            "primary failure: scenario produced no run result",
            duration_seconds=duration,
        )
    return evaluate_mvp_scenario(
        run_result, scenario, duration_seconds=duration
    )


async def run_live_evaluation() -> tuple[object, str | None]:
    """Run each scenario in a fresh MCP/browser session."""

    _ensure_repository_import_path()
    from browser_agent import (
        create_mvp_error_result,
        default_mvp_evaluation_scenarios,
        summarize_mvp_results,
    )

    scenarios = default_mvp_evaluation_scenarios()
    api_key = os.environ.get("BROWSER_AGENT_LLM_API_KEY")
    try:
        base_url, model, api_key, timeout = _provider_settings()
    except Exception as exc:
        detail = _safe_exception_details(exc, api_key)
        return (
            summarize_mvp_results(
                create_mvp_error_result(
                    scenario, f"primary failure: {detail}"
                )
                for scenario in scenarios
            ),
            api_key,
        )

    results = []
    for scenario in scenarios:
        try:
            result = await _run_one_scenario(
                scenario,
                base_url=base_url,
                model=model,
                api_key=api_key,
                timeout=timeout,
            )
        except Exception as exc:
            result = create_mvp_error_result(
                scenario,
                "primary failure: "
                + _safe_exception_details(exc, api_key),
            )
        results.append(result)
    return summarize_mvp_results(results), api_key


def _redact_json_value(value: Any, api_key: str | None) -> Any:
    if isinstance(value, str):
        return _sanitize(value, api_key)
    if isinstance(value, list):
        return [_redact_json_value(item, api_key) for item in value]
    if isinstance(value, dict):
        return {
            key: _redact_json_value(item, api_key)
            for key, item in value.items()
        }
    return value


def _write_report(summary: object, api_key: str | None) -> Path:
    from browser_agent import mvp_summary_to_json

    generated = datetime.now(timezone.utc)
    report = {
        "generated_at": generated.isoformat(),
        **mvp_summary_to_json(summary),
    }
    safe_report = _redact_json_value(report, api_key)
    directory = _repository_root() / "artifacts" / "mvp-evaluation"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / generated.strftime(
        "mvp-evaluation-%Y%m%dT%H%M%S.%fZ.json"
    )
    path.write_text(
        json.dumps(safe_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _print_summary(summary: object, report_path: str) -> None:
    for result in summary.results:
        print(f"{result.scenario_name}: {result.outcome.value.upper()}")
    print(f"Total: {summary.total}")
    print(f"Passed: {summary.passed}")
    print(f"Failed: {summary.failed}")
    print(f"Errors: {summary.errored}")
    print(f"Pass rate: {summary.pass_rate:.2%}")
    print(f"Report path: {report_path}")


def main() -> int:
    _ensure_repository_import_path()
    from browser_agent import mvp_evaluation_exit_code

    summary, api_key = asyncio.run(run_live_evaluation())
    try:
        report_path = str(_write_report(summary, api_key))
    except Exception as exc:
        detail = _safe_exception_details(exc, api_key)
        _print_summary(summary, f"ERROR ({detail})")
        return 2
    _print_summary(summary, report_path)
    return mvp_evaluation_exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
