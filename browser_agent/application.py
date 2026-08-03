"""Import-safe composition root for the browser-agent HTTP application."""

from __future__ import annotations

import asyncio
import math
import os
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .agent_controls import AgentControlExecutor
from .agent_loop import AgentToolRouter, ResumableAgentSession
from .downloads import DownloadTrackingExecutor, RunDownloadStore
from .http_api import create_http_app
from .mcp_gateway import McpToolGateway, ToolObservation
from .openai_provider import (
    OpenAICompatibleDecisionSource,
    OpenAICompatibleProviderConfig,
)
from .run_manager import RunManager
from .secret_application import SecretFormApplier
from .secret_store import TransientSecretStore
from .tool_policy import McpToolPolicy, PolicyEnforcedToolExecutor


_REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
class BrowserAgentApplicationError(Exception):
    """Base class for safe application-composition errors."""


class BrowserAgentApplicationConfigurationError(BrowserAgentApplicationError):
    """Application configuration is invalid."""


class BrowserAgentApplicationConstructionError(BrowserAgentApplicationError):
    """A per-run application composition could not be constructed."""


class BrowserAgentApplicationCleanupError(BrowserAgentApplicationError):
    """Owned browser resources could not be completely cleaned up."""


def _validate_start_url(value: str) -> None:
    """Validate basic URL syntax without making a site-authorization decision."""
    if type(value) is not str or not value.strip():
        raise BrowserAgentApplicationConstructionError("start_url is invalid")
    if (
        value != value.strip()
        or any(unicodedata.category(char).startswith("C") for char in value)
    ):
        raise BrowserAgentApplicationConstructionError("start_url is invalid")
    try:
        parsed = urlsplit(value)
        parsed.port
        hostname = parsed.hostname
    except (TypeError, UnicodeError, ValueError):
        raise BrowserAgentApplicationConstructionError("start_url is invalid") from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or any(char.isspace() for char in parsed.netloc)
    ):
        raise BrowserAgentApplicationConstructionError("start_url is invalid")


@dataclass(frozen=True)
class BrowserAgentApplicationConfig:
    """Validated immutable configuration for the real application boundary."""

    provider: OpenAICompatibleProviderConfig
    download_base_directory: Path
    node_command: str
    mcp_cli_path: Path
    mcp_config_path: Path
    max_steps: int

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.provider, OpenAICompatibleProviderConfig):
                raise ValueError("provider configuration is invalid")
            if type(self.max_steps) is not int or self.max_steps <= 0:
                raise ValueError("max_steps must be an exact positive int")
            if type(self.node_command) is not str or not self.node_command.strip():
                raise ValueError("node_command must be a non-empty string")
            for name in ("mcp_cli_path", "mcp_config_path"):
                path = getattr(self, name)
                if not isinstance(path, Path) or not path.is_file():
                    raise ValueError(f"{name} must identify an existing regular file")
            base = self.download_base_directory
            if not isinstance(base, Path):
                raise ValueError("download_base_directory must be a Path")
            if base.is_symlink():
                raise ValueError("download_base_directory must not be a symlink")
            if base.exists() and not base.is_dir():
                raise ValueError(
                    "download_base_directory must be a directory when it exists"
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise BrowserAgentApplicationConfigurationError(str(exc)) from None


def _environment_value(name: str, *, required: bool = False) -> str | None:
    value = os.environ.get(name)
    if value is None:
        if required:
            raise BrowserAgentApplicationConfigurationError(
                f"required environment variable {name} is missing"
            )
        return None
    if not value.strip():
        raise BrowserAgentApplicationConfigurationError(
            f"environment variable {name} must not be empty"
        )
    return value


def load_application_config_from_environment() -> BrowserAgentApplicationConfig:
    """Load configuration without creating directories or starting resources."""
    root = Path(__file__).resolve().parent.parent
    base_url = _environment_value("BROWSER_AGENT_LLM_BASE_URL", required=True)
    model = _environment_value("BROWSER_AGENT_LLM_MODEL", required=True)
    download_base = _environment_value(
        "BROWSER_AGENT_DOWNLOAD_BASE_DIR", required=True
    )
    api_key = _environment_value("BROWSER_AGENT_LLM_API_KEY")
    timeout_text = _environment_value("BROWSER_AGENT_LLM_TIMEOUT")
    max_steps_text = _environment_value("BROWSER_AGENT_MAX_STEPS")
    node = _environment_value("BROWSER_AGENT_NODE_COMMAND")
    cli = _environment_value("BROWSER_AGENT_MCP_CLI_PATH")
    mcp_config = _environment_value("BROWSER_AGENT_MCP_CONFIG_PATH")

    if node is None:
        node = shutil.which("node")
        if node is None:
            raise BrowserAgentApplicationConfigurationError(
                "Node.js executable was not found"
            )
    try:
        timeout = float(timeout_text) if timeout_text is not None else 60.0
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except ValueError:
        raise BrowserAgentApplicationConfigurationError(
            "BROWSER_AGENT_LLM_TIMEOUT must be a finite positive number"
        ) from None
    try:
        max_steps = int(max_steps_text) if max_steps_text is not None else 10
        if max_steps_text is not None and str(max_steps) != max_steps_text.strip():
            raise ValueError
        if max_steps <= 0:
            raise ValueError
    except ValueError:
        raise BrowserAgentApplicationConfigurationError(
            "BROWSER_AGENT_MAX_STEPS must be a positive integer"
        ) from None

    try:
        provider = OpenAICompatibleProviderConfig(
            base_url=base_url or "",
            model=model or "",
            api_key=api_key,
            timeout_seconds=timeout,
        )
        return BrowserAgentApplicationConfig(
            provider=provider,
            download_base_directory=Path(download_base or ""),
            node_command=node,
            mcp_cli_path=Path(cli) if cli is not None else root / "node_modules" / "@playwright" / "mcp" / "cli.js",
            mcp_config_path=Path(mcp_config) if mcp_config is not None else root / "config" / "playwright-mcp.spike.json",
            max_steps=max_steps,
        )
    except BrowserAgentApplicationConfigurationError:
        raise
    except Exception:
        raise BrowserAgentApplicationConfigurationError(
            "application provider configuration is invalid"
        ) from None


async def _cleanup_owned_resources(
    tracking_executor: DownloadTrackingExecutor | None,
    client_context: object | None,
    stdio_context: object | None,
    download_store: RunDownloadStore | None,
) -> BaseException | None:
    first_error: BaseException | None = None
    operations = []
    if tracking_executor is not None:
        async def close_browser() -> None:
            observation = await tracking_executor.invoke_internal(
                "browser_close", {}
            )
            if (
                type(observation) is not ToolObservation
                or observation.status != "success"
            ):
                raise RuntimeError("internal browser cleanup failed")

        operations.append(close_browser)
    if client_context is not None:
        operations.append(lambda: client_context.__aexit__(None, None, None))
    if stdio_context is not None:
        operations.append(lambda: stdio_context.__aexit__(None, None, None))
    for operation in operations:
        try:
            await operation()
        except asyncio.CancelledError as exc:
            if first_error is None:
                first_error = exc
        except (KeyboardInterrupt, SystemExit) as exc:
            if first_error is None:
                first_error = exc
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if download_store is not None:
        try:
            download_store.close()
        except asyncio.CancelledError as exc:
            if first_error is None:
                first_error = exc
        except (KeyboardInterrupt, SystemExit) as exc:
            if first_error is None:
                first_error = exc
        except Exception as exc:
            if first_error is None:
                first_error = exc
    return first_error


@dataclass(frozen=True)
class _RunSessionStartup:
    session: ResumableAgentSession
    secret_applier: SecretFormApplier
    download_store: RunDownloadStore
    tracking_executor: DownloadTrackingExecutor


@dataclass(frozen=True)
class _RunSessionLifecycle:
    owner_task: asyncio.Task[BaseException | None]
    close_requested: asyncio.Event


class PlaywrightMcpRunSessionHandle:
    """Own one live browser/MCP session; RunManager owns its download store."""

    def __init__(
        self,
        session: ResumableAgentSession,
        secret_applier: SecretFormApplier,
        download_store: RunDownloadStore,
        tracking_executor: DownloadTrackingExecutor,
        lifecycle: _RunSessionLifecycle,
    ) -> None:
        self.session = session
        self.secret_applier = secret_applier
        self.download_store = download_store
        self._tracking_executor = tracking_executor
        self._lifecycle = lifecycle

    async def close(self) -> None:
        self._lifecycle.close_requested.set()
        try:
            error = await asyncio.shield(self._lifecycle.owner_task)
        except asyncio.CancelledError:
            await asyncio.shield(self._lifecycle.owner_task)
            raise
        if error is not None:
            if isinstance(
                error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
            ):
                raise error
            raise BrowserAgentApplicationCleanupError(
                "browser session cleanup failed safely"
            ) from None


class PlaywrightMcpRunSessionFactory:
    """Create one fully wired, isolated browser-agent run session."""

    def __init__(
        self,
        config: BrowserAgentApplicationConfig,
        *,
        stdio_factory: Callable[..., object] = stdio_client,
        client_session_factory: Callable[..., object] = ClientSession,
    ) -> None:
        if not isinstance(config, BrowserAgentApplicationConfig):
            raise BrowserAgentApplicationConfigurationError(
                "application configuration is invalid"
            )
        self._config = config
        self._stdio_factory = stdio_factory
        self._client_session_factory = client_session_factory

    async def create(
        self, start_url: str, task: str
    ) -> PlaywrightMcpRunSessionHandle:
        _validate_start_url(start_url)
        if type(task) is not str or not task.strip():
            raise BrowserAgentApplicationConstructionError(
                "task must be a non-empty string"
            )

        loop = asyncio.get_running_loop()
        startup: asyncio.Future[_RunSessionStartup] = loop.create_future()
        close_requested = asyncio.Event()
        startup_abandoned = asyncio.Event()
        owner_task = asyncio.create_task(
            self._own_run_session(
                start_url, startup, close_requested, startup_abandoned
            )
        )
        try:
            ready = await asyncio.shield(startup)
        except asyncio.CancelledError:
            startup_abandoned.set()
            startup.cancel()
            owner_task.cancel()
            try:
                await asyncio.shield(owner_task)
            except asyncio.CancelledError:
                pass
            raise
        except (KeyboardInterrupt, SystemExit):
            await asyncio.shield(owner_task)
            raise
        except Exception:
            await asyncio.shield(owner_task)
            raise
        lifecycle = _RunSessionLifecycle(owner_task, close_requested)
        return PlaywrightMcpRunSessionHandle(
            ready.session,
            ready.secret_applier,
            ready.download_store,
            ready.tracking_executor,
            lifecycle,
        )

    async def _own_run_session(
        self,
        start_url: str,
        startup: asyncio.Future[_RunSessionStartup],
        close_requested: asyncio.Event,
        startup_abandoned: asyncio.Event,
    ) -> BaseException | None:
        store: RunDownloadStore | None = None
        stdio_context: object | None = None
        client_context: object | None = None
        tracking: DownloadTrackingExecutor | None = None
        startup_succeeded = False
        try:
            _validate_start_url(start_url)
            store = RunDownloadStore(self._config.download_base_directory)
            parameters = StdioServerParameters(
                command=self._config.node_command,
                args=[
                    str(self._config.mcp_cli_path),
                    "--config",
                    str(self._config.mcp_config_path),
                    "--output-dir",
                    str(store.output_directory),
                ],
                cwd=_REPOSITORY_ROOT,
            )
            pending_stdio_context = self._stdio_factory(parameters)
            streams = await pending_stdio_context.__aenter__()
            stdio_context = pending_stdio_context
            pending_client_context = self._client_session_factory(*streams)
            mcp_session = await pending_client_context.__aenter__()
            client_context = pending_client_context
            await mcp_session.initialize()
            gateway = McpToolGateway(mcp_session)
            definitions = await gateway.discover()
            policy = McpToolPolicy(definitions)
            enforced = PolicyEnforcedToolExecutor(gateway, policy)
            tracking = DownloadTrackingExecutor(enforced, store)
            navigation = await tracking.invoke(
                "browser_navigate",
                {"url": start_url},
                confirmation_granted=False,
            )
            if type(navigation) is not ToolObservation or navigation.status != "success":
                raise RuntimeError("initial browser navigation failed")
            router = AgentToolRouter(
                tracking, policy.llm_visible_tools(), AgentControlExecutor()
            )
            decision_source = OpenAICompatibleDecisionSource(self._config.provider)
            agent_session = ResumableAgentSession(
                decision_source, router, self._config.max_steps
            )
            secret_applier = SecretFormApplier(tracking, definitions)
            startup.set_result(
                _RunSessionStartup(
                    agent_session, secret_applier, store, tracking
                )
            )
            startup_succeeded = True
            await close_requested.wait()
        except asyncio.CancelledError as exc:
            failure: BaseException | None = exc
        except (KeyboardInterrupt, SystemExit) as exc:
            failure = exc
        except Exception:
            failure = BrowserAgentApplicationConstructionError(
                "browser run construction failed safely"
            )
        else:
            failure = None

        cleanup_error = await _cleanup_owned_resources(
            tracking,
            client_context,
            stdio_context,
            None if startup_succeeded and not startup_abandoned.is_set() else store,
        )
        if isinstance(
            cleanup_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
        ):
            failure = cleanup_error
        if not startup.done():
            startup.set_exception(
                failure
                or BrowserAgentApplicationConstructionError(
                    "browser run construction failed safely"
                )
            )
        if failure is not None:
            return failure
        return cleanup_error


def create_application(
    config: BrowserAgentApplicationConfig | None = None,
) -> FastAPI:
    """Compose the import-safe process application without starting a run."""
    resolved = (
        load_application_config_from_environment() if config is None else config
    )
    if not isinstance(resolved, BrowserAgentApplicationConfig):
        raise BrowserAgentApplicationConfigurationError(
            "application configuration is invalid"
        )
    secret_store = TransientSecretStore()
    factory = PlaywrightMcpRunSessionFactory(resolved)
    manager = RunManager(factory, secret_store=secret_store)
    return create_http_app(manager)
