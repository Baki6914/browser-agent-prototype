from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import browser_agent.application as application_module
from browser_agent.agent_loop import ResumableAgentSession
from browser_agent.application import (
    BrowserAgentApplicationCleanupError,
    BrowserAgentApplicationConfig,
    BrowserAgentApplicationConfigurationError,
    BrowserAgentApplicationConstructionError,
    PlaywrightMcpRunSessionFactory,
    PlaywrightMcpRunSessionHandle,
    create_application,
    load_application_config_from_environment,
)
from browser_agent.downloads import DownloadTrackingExecutor, RunDownloadStore
from browser_agent.mcp_gateway import ToolObservation
from browser_agent.openai_provider import (
    OpenAICompatibleDecisionSource,
    OpenAICompatibleProviderConfig,
)
from browser_agent.secret_application import SecretFormApplier
from browser_agent.secret_store import TransientSecretStore


API_KEY = "synthetic-api-key-marker"
PRIVATE_MARKER = "synthetic-private-path-marker"


def _tool(name: str, schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=name, inputSchema=schema or {
        "type": "object", "properties": {}, "additionalProperties": False
    })


TOOLS = (
    _tool("browser_navigate", {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
        "additionalProperties": False,
    }),
    _tool("browser_snapshot"),
    _tool("browser_close"),
    _tool("browser_file_upload"),
    _tool("browser_fill_form", {
        "type": "object",
        "properties": {"fields": {"type": "array", "items": {"type": "object"}}},
        "required": ["fields"],
        "additionalProperties": False,
    }),
)


class FakeMcpSession:
    def __init__(self, events: list[str], *, navigation_status: str = "success"):
        self.events = events
        self.navigation_status = navigation_status

    async def initialize(self):
        self.events.append("initialize")

    async def list_tools(self):
        self.events.append("discover")
        return SimpleNamespace(tools=TOOLS)

    async def call_tool(self, name, arguments):
        self.events.append(f"call:{name}")
        failed = name == "browser_navigate" and self.navigation_status != "success"
        return SimpleNamespace(
            content=[SimpleNamespace(text="failed" if failed else "ok")],
            isError=failed,
            structuredContent=None,
        )


class FakeContext(AbstractAsyncContextManager):
    def __init__(self, value, events, enter_name, exit_name, *, exit_error=None):
        self.value = value
        self.events = events
        self.enter_name = enter_name
        self.exit_name = exit_name
        self.exit_error = exit_error

    async def __aenter__(self):
        self.events.append(self.enter_name)
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        self.events.append(self.exit_name)
        if self.exit_error:
            raise self.exit_error


class Harness:
    def __init__(self, *, navigation_status="success", client_exit_error=None):
        self.events: list[str] = []
        self.parameters = []
        self.session = FakeMcpSession(
            self.events, navigation_status=navigation_status
        )
        self.client_exit_error = client_exit_error

    def stdio(self, parameters):
        self.parameters.append(parameters)
        return FakeContext(
            (object(), object()), self.events, "stdio_enter", "stdio_exit"
        )

    def client(self, *streams):
        self.events.append("client_construct")
        return FakeContext(
            self.session,
            self.events,
            "client_enter",
            "client_exit",
            exit_error=self.client_exit_error,
        )


class ApplicationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cli = self.root / "cli.js"
        self.mcp_config = self.root / "mcp.json"
        self.cli.write_text("cli")
        self.mcp_config.write_text("{}")
        self.downloads = self.root / "downloads"
        self.provider = OpenAICompatibleProviderConfig(
            "https://example.test/v1", "model", API_KEY, 7
        )

    def tearDown(self):
        self.temp.cleanup()

    def config(self, **changes):
        values = dict(
            provider=self.provider,
            download_base_directory=self.downloads,
            node_command="synthetic-node",
            mcp_cli_path=self.cli,
            mcp_config_path=self.mcp_config,
            max_steps=4,
        )
        values.update(changes)
        return BrowserAgentApplicationConfig(**values)

    def test_valid_config_is_immutable_and_does_not_create_download_base(self):
        config = self.config()
        self.assertFalse(self.downloads.exists())
        with self.assertRaises((AttributeError, TypeError)):
            config.max_steps = 5
        self.assertNotIn(API_KEY, repr(config))

    def test_config_rejects_invalid_max_steps_and_node(self):
        for value in (0, -1, True, "1", 1.5):
            with self.subTest(value=value), self.assertRaises(
                BrowserAgentApplicationConfigurationError
            ):
                self.config(max_steps=value)
        for value in ("", "  ", None):
            with self.subTest(value=value), self.assertRaises(
                BrowserAgentApplicationConfigurationError
            ):
                self.config(node_command=value)

    def test_config_rejects_bad_resource_paths_without_sensitive_text(self):
        existing_file = self.root / PRIVATE_MARKER
        existing_file.write_text(API_KEY)
        for changes in (
            {"mcp_cli_path": self.root / "missing"},
            {"mcp_cli_path": self.root},
            {"mcp_config_path": self.root / "missing"},
            {"download_base_directory": existing_file},
        ):
            with self.assertRaises(BrowserAgentApplicationConfigurationError) as cm:
                self.config(**changes)
            self.assertNotIn(API_KEY, str(cm.exception))
            self.assertNotIn(PRIVATE_MARKER, str(cm.exception))
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(BrowserAgentApplicationConfigurationError):
            self.config(download_base_directory=link)

    def test_environment_full_contract(self):
        env = {
            "BROWSER_AGENT_LLM_BASE_URL": "https://provider.test/v1",
            "BROWSER_AGENT_LLM_MODEL": "chosen-model",
            "BROWSER_AGENT_LLM_API_KEY": API_KEY,
            "BROWSER_AGENT_LLM_TIMEOUT": "12.5",
            "BROWSER_AGENT_DOWNLOAD_BASE_DIR": str(self.downloads),
            "BROWSER_AGENT_MAX_STEPS": "6",
            "BROWSER_AGENT_NODE_COMMAND": "node-custom",
            "BROWSER_AGENT_MCP_CLI_PATH": str(self.cli),
            "BROWSER_AGENT_MCP_CONFIG_PATH": str(self.mcp_config),
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_application_config_from_environment()
        self.assertEqual(config.provider.model, "chosen-model")
        self.assertEqual(config.provider.timeout_seconds, 12.5)
        self.assertEqual(config.max_steps, 6)
        self.assertEqual(config.node_command, "node-custom")
        self.assertFalse(self.downloads.exists())
        self.assertNotIn(API_KEY, repr(config))

    def test_environment_defaults_are_repository_relative_and_cwd_independent(self):
        env = {
            "BROWSER_AGENT_LLM_BASE_URL": "https://provider.test/v1",
            "BROWSER_AGENT_LLM_MODEL": "model",
            "BROWSER_AGENT_DOWNLOAD_BASE_DIR": str(self.downloads),
        }
        old_cwd = Path.cwd()
        with patch.dict(os.environ, env, clear=True), patch(
            "browser_agent.application.shutil.which", return_value="/node"
        ):
            os.chdir(self.root)
            try:
                config = load_application_config_from_environment()
            finally:
                os.chdir(old_cwd)
        repository = Path(__file__).resolve().parent.parent
        self.assertEqual(
            config.mcp_cli_path,
            repository / "node_modules" / "@playwright" / "mcp" / "cli.js",
        )
        self.assertEqual(
            config.mcp_config_path,
            repository / "config" / "playwright-mcp.spike.json",
        )
        self.assertEqual(config.max_steps, 10)

    def test_environment_rejects_missing_invalid_and_empty_values(self):
        required = {
            "BROWSER_AGENT_LLM_BASE_URL": "https://provider.test/v1",
            "BROWSER_AGENT_LLM_MODEL": "model",
            "BROWSER_AGENT_DOWNLOAD_BASE_DIR": str(self.downloads),
            "BROWSER_AGENT_NODE_COMMAND": "node",
            "BROWSER_AGENT_MCP_CLI_PATH": str(self.cli),
            "BROWSER_AGENT_MCP_CONFIG_PATH": str(self.mcp_config),
        }
        for missing in (
            "BROWSER_AGENT_LLM_BASE_URL",
            "BROWSER_AGENT_LLM_MODEL",
            "BROWSER_AGENT_DOWNLOAD_BASE_DIR",
        ):
            env = dict(required)
            env.pop(missing)
            with patch.dict(os.environ, env, clear=True), self.assertRaises(
                BrowserAgentApplicationConfigurationError
            ):
                load_application_config_from_environment()
        for name, value in (
            ("BROWSER_AGENT_LLM_TIMEOUT", "nan"),
            ("BROWSER_AGENT_LLM_TIMEOUT", "0"),
            ("BROWSER_AGENT_MAX_STEPS", "1.0"),
            ("BROWSER_AGENT_MAX_STEPS", "0"),
            ("BROWSER_AGENT_LLM_API_KEY", ""),
            ("BROWSER_AGENT_NODE_COMMAND", " "),
        ):
            env = dict(required, **{name: value})
            with self.subTest(name=name, value=value), patch.dict(
                os.environ, env, clear=True
            ), self.assertRaises(BrowserAgentApplicationConfigurationError):
                load_application_config_from_environment()

    def test_application_composes_existing_boundary_without_runtime_resources(self):
        config = self.config()
        with patch("browser_agent.application.stdio_client") as stdio, patch.object(
            OpenAICompatibleDecisionSource, "next_decision"
        ) as provider_call:
            app = create_application(config)
        paths = {route.path for route in app.routes if route.path.startswith("/runs")}
        self.assertEqual(paths, {
            "/runs", "/runs/{run_id}", "/runs/{run_id}/responses",
            "/runs/{run_id}/cancel", "/runs/{run_id}/files/{file_id}",
        })
        self.assertFalse(self.downloads.exists())
        stdio.assert_not_called()
        provider_call.assert_not_called()
        manager = app.state.run_manager
        self.assertIsInstance(manager._secret_store, TransientSecretStore)
        self.assertIsInstance(manager._factory, PlaywrightMcpRunSessionFactory)


class FactoryTests(ApplicationTestCase, unittest.IsolatedAsyncioTestCase):
    async def make_handle(self, harness=None):
        harness = harness or Harness()
        factory = PlaywrightMcpRunSessionFactory(
            self.config(), stdio_factory=harness.stdio,
            client_session_factory=harness.client,
        )
        return await factory.create("https://example.test", "inspect"), harness

    async def test_factory_wires_run_and_fresh_output_directories(self):
        handle1, harness1 = await self.make_handle()
        handle2, harness2 = await self.make_handle()
        self.assertTrue(callable(handle1.close))
        self.assertTrue(hasattr(handle1, "session"))
        self.assertTrue(hasattr(handle1, "secret_applier"))
        self.assertTrue(hasattr(handle1, "download_store"))
        self.assertIsInstance(handle1.session, ResumableAgentSession)
        self.assertIsInstance(handle1.secret_applier, SecretFormApplier)
        self.assertIsInstance(handle1._tracking_executor, DownloadTrackingExecutor)
        self.assertIsInstance(
            handle1.session._decision_source, OpenAICompatibleDecisionSource
        )
        self.assertIs(handle1.session._decision_source._config, self.provider)
        self.assertEqual(handle1.session._max_steps, 4)
        self.assertIs(
            handle1.secret_applier._executor, handle1._tracking_executor
        )
        self.assertIs(
            handle1.session._router._mcp_executor, handle1._tracking_executor
        )
        self.assertIsNot(handle1.download_store, handle2.download_store)
        self.assertNotEqual(
            handle1.download_store.output_directory,
            handle2.download_store.output_directory,
        )
        parameters = harness1.parameters[0]
        self.assertEqual(parameters.command, "synthetic-node")
        self.assertEqual(parameters.args, [
            str(self.cli), "--config", str(self.mcp_config), "--output-dir",
            str(handle1.download_store.output_directory),
        ])
        self.assertEqual(
            parameters.cwd, Path(application_module.__file__).resolve().parent.parent
        )
        self.assertEqual(harness1.events[:6], [
            "stdio_enter", "client_construct", "client_enter", "initialize",
            "discover", "call:browser_navigate",
        ])
        visible = {item.name for item in handle1.session._router.definitions()}
        self.assertIn("browser_navigate", visible)
        self.assertNotIn("browser_close", visible)
        self.assertNotIn("browser_file_upload", visible)
        self.assertFalse(handle1.session._started)
        await handle1.close()
        await handle2.close()
        handle1.download_store.close()
        handle2.download_store.close()

    async def test_navigation_failure_cleans_everything_and_returns_safe_error(self):
        harness = Harness(navigation_status="mcp_error")
        factory = PlaywrightMcpRunSessionFactory(
            self.config(), stdio_factory=harness.stdio,
            client_session_factory=harness.client,
        )
        with self.assertRaises(BrowserAgentApplicationConstructionError) as cm:
            await factory.create("https://example.test", "inspect")
        self.assertEqual(harness.events[-3:], [
            "call:browser_close", "client_exit", "stdio_exit"
        ])
        self.assertNotIn(API_KEY, str(cm.exception))
        self.assertNotIn(PRIVATE_MARKER, str(cm.exception))
        self.assertEqual(list(self.downloads.iterdir()), [])

    async def test_handle_close_is_ordered_idempotent_and_does_not_close_store(self):
        handle, harness = await self.make_handle()
        await handle.close()
        await handle.close()
        self.assertEqual(harness.events[-3:], [
            "call:browser_close", "client_exit", "stdio_exit"
        ])
        self.assertEqual(harness.events.count("call:browser_close"), 1)
        self.assertTrue(handle.download_store.output_directory.exists())
        handle.download_store.close()

    async def test_cleanup_is_exhaustive_when_browser_or_client_exit_fails(self):
        handle, harness = await self.make_handle(
            Harness(client_exit_error=RuntimeError(PRIVATE_MARKER))
        )
        handle._tracking_executor.invoke_internal = self._failing_close(harness.events)
        with self.assertRaises(BrowserAgentApplicationCleanupError) as cm:
            await handle.close()
        self.assertEqual(harness.events[-3:], [
            "browser_close_failed", "client_exit", "stdio_exit"
        ])
        self.assertNotIn(PRIVATE_MARKER, str(cm.exception))
        handle.download_store.close()

    async def test_mcp_error_close_result_fails_safely_and_exits_contexts(self):
        handle, harness = await self.make_handle()

        async def mcp_error(name, arguments):
            harness.events.append("browser_close_mcp_error")
            return ToolObservation(
                name, "mcp_error", PRIVATE_MARKER, None, API_KEY
            )

        handle._tracking_executor.invoke_internal = mcp_error
        with self.assertRaises(BrowserAgentApplicationCleanupError) as cm:
            await handle.close()
        self.assertEqual(harness.events[-3:], [
            "browser_close_mcp_error", "client_exit", "stdio_exit"
        ])
        self.assertEqual(harness.events.count("browser_close_mcp_error"), 1)
        self.assertNotIn(PRIVATE_MARKER, str(cm.exception))
        self.assertNotIn(API_KEY, str(cm.exception))
        with self.assertRaises(BrowserAgentApplicationCleanupError):
            await handle.close()
        self.assertEqual(harness.events.count("browser_close_mcp_error"), 1)
        self.assertTrue(handle.download_store.output_directory.exists())
        handle.download_store.close()

    async def test_transport_error_and_invalid_close_results_fail_safely(self):
        results = (
            ToolObservation(
                "browser_close", "transport_error", PRIVATE_MARKER, None, API_KEY
            ),
            SimpleNamespace(status="success", detail=PRIVATE_MARKER),
        )
        for result in results:
            with self.subTest(result=type(result).__name__):
                handle, harness = await self.make_handle()

                async def close_result(name, arguments, value=result):
                    harness.events.append("close_result")
                    return value

                handle._tracking_executor.invoke_internal = close_result
                with self.assertRaises(BrowserAgentApplicationCleanupError) as cm:
                    await handle.close()
                self.assertEqual(harness.events[-3:], [
                    "close_result", "client_exit", "stdio_exit"
                ])
                self.assertNotIn(PRIVATE_MARKER, str(cm.exception))
                self.assertNotIn(API_KEY, str(cm.exception))
                handle.download_store.close()

    @staticmethod
    def _failing_close(events):
        async def fail(name, arguments):
            events.append("browser_close_failed")
            raise RuntimeError(API_KEY)
        return fail

    async def test_invalid_inputs_do_not_create_store(self):
        factory = PlaywrightMcpRunSessionFactory(self.config())
        for start_url, task in (("", "task"), (True, "task"), ("url", " ")):
            with self.assertRaises(BrowserAgentApplicationConstructionError):
                await factory.create(start_url, task)
        self.assertFalse(self.downloads.exists())

    async def test_cancellation_after_policy_setup_cleans_and_reraises(self):
        harness = Harness()

        async def cancelled_call(name, arguments):
            harness.events.append(f"call:{name}")
            if name == "browser_navigate":
                raise asyncio.CancelledError
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok")], isError=False,
                structuredContent=None,
            )

        harness.session.call_tool = cancelled_call
        factory = PlaywrightMcpRunSessionFactory(
            self.config(), stdio_factory=harness.stdio,
            client_session_factory=harness.client,
        )
        with self.assertRaises(asyncio.CancelledError):
            await factory.create("https://example.test", "inspect")
        self.assertEqual(harness.events[-3:], [
            "call:browser_close", "client_exit", "stdio_exit"
        ])
        self.assertEqual(list(self.downloads.iterdir()), [])

    async def test_failure_before_policy_closes_entered_contexts_and_store(self):
        harness = Harness()

        async def failing_discovery():
            harness.events.append("discover_failed")
            raise RuntimeError(PRIVATE_MARKER)

        harness.session.list_tools = failing_discovery
        factory = PlaywrightMcpRunSessionFactory(
            self.config(), stdio_factory=harness.stdio,
            client_session_factory=harness.client,
        )
        with self.assertRaises(BrowserAgentApplicationConstructionError) as cm:
            await factory.create("https://example.test", "inspect")
        self.assertEqual(harness.events[-3:], [
            "discover_failed", "client_exit", "stdio_exit"
        ])
        self.assertNotIn(PRIVATE_MARKER, str(cm.exception))
        self.assertEqual(list(self.downloads.iterdir()), [])

    async def test_cancellation_before_policy_cleans_and_reraises(self):
        harness = Harness()

        async def cancelled_discovery():
            harness.events.append("discover_cancelled")
            raise asyncio.CancelledError

        harness.session.list_tools = cancelled_discovery
        factory = PlaywrightMcpRunSessionFactory(
            self.config(), stdio_factory=harness.stdio,
            client_session_factory=harness.client,
        )
        with self.assertRaises(asyncio.CancelledError):
            await factory.create("https://example.test", "inspect")
        self.assertEqual(harness.events[-3:], [
            "discover_cancelled", "client_exit", "stdio_exit"
        ])
        self.assertEqual(list(self.downloads.iterdir()), [])

    async def test_factory_control_signals_are_cleaned_and_not_converted(self):
        for signal in (KeyboardInterrupt, SystemExit):
            with self.subTest(signal=signal.__name__):
                harness = Harness()

                async def interrupted_initialize(signal_type=signal):
                    harness.events.append("initialize_interrupted")
                    raise signal_type(PRIVATE_MARKER)

                harness.session.initialize = interrupted_initialize
                factory = PlaywrightMcpRunSessionFactory(
                    self.config(), stdio_factory=harness.stdio,
                    client_session_factory=harness.client,
                )
                with self.assertRaises(signal):
                    await factory.create("https://example.test", "inspect")
                self.assertEqual(harness.events[-3:], [
                    "initialize_interrupted", "client_exit", "stdio_exit"
                ])
                self.assertEqual(list(self.downloads.iterdir()), [])

    async def test_handle_control_signals_are_not_converted(self):
        for signal in (KeyboardInterrupt, SystemExit):
            with self.subTest(signal=signal.__name__):
                handle, _ = await self.make_handle()

                async def cleanup(*args, signal_type=signal):
                    return signal_type(PRIVATE_MARKER)

                with patch.object(
                    application_module, "_cleanup_owned_resources", cleanup
                ), self.assertRaises(signal):
                    await handle.close()
                handle.download_store.close()


if __name__ == "__main__":
    unittest.main()
