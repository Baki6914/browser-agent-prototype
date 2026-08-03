"""Deterministic full-HTTP tests for the real production composition wiring."""

from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import httpx

import browser_agent.application as application_module
from browser_agent.agent_loop import AgentLoopContext, AgentToolCall
from browser_agent.application import (
    BrowserAgentApplicationConfig,
    PlaywrightMcpRunSessionFactory,
)
from browser_agent.http_api import create_http_app
from browser_agent.openai_provider import OpenAICompatibleProviderConfig
from browser_agent.run_manager import RunManager
from browser_agent.secret_store import TransientSecretStore


START_URL = "https://public.example.test/start"
TASK = "Inspect the synthetic page"
SECRET = "distinctive-test-secret-value"
SECRET_REF = "password-element-reference"
DOWNLOAD_BYTES = b"synthetic report bytes\n"


def _tool(name: str, schema: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"Synthetic {name}",
        inputSchema=schema or {
            "type": "object", "properties": {}, "additionalProperties": False,
        },
    )


TOOLS = (
    _tool("browser_navigate", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"], "additionalProperties": False}),
    _tool("browser_snapshot"),
    _tool("browser_click", {"type": "object", "properties": {"element": {"type": "string"}, "ref": {"type": "string"}}, "required": ["element", "ref"], "additionalProperties": False}),
    _tool("browser_fill_form", {"type": "object", "properties": {"fields": {"type": "array", "items": {"type": "object"}}}, "required": ["fields"], "additionalProperties": False}),
    _tool("browser_close"),
    _tool("browser_file_upload"),
    _tool("browser_evaluate"),
    _tool("browser_run_code_unsafe"),
)


@dataclass
class RuntimeRecord:
    parameters: object
    events: list[str] = field(default_factory=list)
    calls: list[tuple[str, dict]] = field(default_factory=list)
    contexts: list[AgentLoopContext] = field(default_factory=list)
    output_directory: Path | None = None
    runtime_config: Path | None = None
    blocking: bool = False
    decision_started: asyncio.Event = field(default_factory=asyncio.Event)
    decision_cancelled: bool = False
    secret_seen: bool = False


class _Context(AbstractAsyncContextManager):
    def __init__(self, value: object, record: RuntimeRecord, enter: str, exit: str):
        self.value, self.record, self.enter, self.exit = value, record, enter, exit

    async def __aenter__(self):
        self.record.events.append(self.enter)
        return self.value

    async def __aexit__(self, *_args):
        self.record.events.append(self.exit)


class FakeMcpSession:
    def __init__(self, record: RuntimeRecord, download_on_click: bool):
        self.record = record
        self.download_on_click = download_on_click

    async def initialize(self):
        self.record.events.append("initialize")

    async def list_tools(self):
        self.record.events.append("discover")
        return SimpleNamespace(tools=TOOLS)

    async def call_tool(self, name, arguments):
        text = "ok"
        if name == "browser_fill_form":
            values = [item.get("value") for item in arguments["fields"]]
            self.record.secret_seen = values == [SECRET]
            retained = {"fields": [{"name": item["name"], "type": item["type"]} for item in arguments["fields"]]}
        else:
            retained = dict(arguments)
        if name == "browser_click" and self.download_on_click:
            assert self.record.output_directory is not None
            (self.record.output_directory / "report.txt").write_bytes(DOWNLOAD_BYTES)
            text = '- Downloaded file report.txt to "report.txt"'
        self.record.calls.append((name, retained))
        return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False, structuredContent=None)


class Harness:
    def __init__(self):
        self.records: list[RuntimeRecord] = []
        self.record_created = asyncio.Event()
        self.scripts: list[dict] = []

    def queue(self, decisions: list[AgentToolCall], *, download=False, blocking=False):
        self.scripts.append({"decisions": decisions, "download": download, "blocking": blocking})

    def stdio(self, parameters):
        record = RuntimeRecord(parameters)
        args = parameters.args
        record.output_directory = Path(args[args.index("--output-dir") + 1])
        record.runtime_config = Path(args[args.index("--config") + 1])
        assert record.runtime_config.is_file()
        self.records.append(record)
        self.record_created.set()
        return _Context((object(), object()), record, "stdio_enter", "stdio_exit")

    def client(self, *_streams):
        record = self.records[-1]
        script = self.scripts[len(self.records) - 1]
        record.blocking = script["blocking"]
        return _Context(FakeMcpSession(record, script["download"]), record, "client_enter", "client_exit")

    def decision_type(self):
        harness = self

        class DecisionSource:
            def __init__(self, _config, *, trace_recorder=None):
                index = len([r for r in harness.records if hasattr(r, "decision_source")])
                self.record = harness.records[index]
                self.record.decision_source = self
                self.decisions = list(harness.scripts[index]["decisions"])
                self.index = 0
                self.trace_recorder = trace_recorder

            async def next_decision(self, context):
                self.record.contexts.append(context)
                if self.record.blocking:
                    self.record.decision_started.set()
                    try:
                        await asyncio.Future()
                    except asyncio.CancelledError:
                        self.record.decision_cancelled = True
                        raise
                decision = self.decisions[self.index]
                self.index += 1
                return decision

        return DecisionSource


class HttpWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.cli, self.mcp_config = root / "cli.js", root / "mcp.json"
        self.cli.write_text("synthetic")
        self.mcp_config.write_text(json.dumps({
            "browser": {"browserName": "chromium"}
        }))
        self.download_base = root / "downloads"
        config = BrowserAgentApplicationConfig(
            provider=OpenAICompatibleProviderConfig("https://provider.invalid/v1", "synthetic-model", None, 1),
            download_base_directory=self.download_base,
            node_command="synthetic-node",
            mcp_cli_path=self.cli,
            mcp_config_path=self.mcp_config,
            max_steps=8,
        )
        self.harness = Harness()
        factory = PlaywrightMcpRunSessionFactory(config, stdio_factory=self.harness.stdio, client_session_factory=self.harness.client)
        self.manager = RunManager(factory, secret_store=TransientSecretStore())
        self.app = create_http_app(self.manager)
        self.source_patch = patch.object(application_module, "OpenAICompatibleDecisionSource", self.harness.decision_type())
        self.source_patch.start()
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        await self.client.__aenter__()

    async def asyncTearDown(self):
        await self.client.__aexit__(None, None, None)
        await self.lifespan.__aexit__(None, None, None)
        self.source_patch.stop()
        self.temp.cleanup()

    async def create(self):
        response = await self.client.post("/runs", json={"start_url": START_URL, "task": TASK})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "queued")
        return response.json()["run_id"], response

    async def test_operator_resources_have_safe_media_types_and_headers(self):
        page = await self.client.get("/")
        javascript = await self.client.get("/operator.js")
        stylesheet = await self.client.get("/operator.css")
        for response in (page, javascript, stylesheet):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertTrue(page.headers["content-type"].startswith("text/html"))
        self.assertTrue(javascript.headers["content-type"].startswith("application/javascript"))
        self.assertTrue(stylesheet.headers["content-type"].startswith("text/css"))
        csp = page.headers["content-security-policy"]
        for directive in (
            "default-src", "script-src", "style-src", "connect-src", "img-src",
            "object-src", "base-uri", "frame-ancestors", "form-action",
        ):
            self.assertIn(directive, csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)

    async def poll(self, run_id, status, limit=80):
        last = None
        for _ in range(limit):
            last = await self.client.get(f"/runs/{run_id}")
            if last.json()["status"] == status:
                return last
            await asyncio.sleep(0)
        self.fail(f"status {status!r} not reached; last response={last!r} body={last.text if last else None}")

    async def respond(self, run_id, paused, payload):
        interaction_id = paused.json()["interaction_id"]
        body = {"request_id": f"request-{interaction_id}", "interaction_id": interaction_id, **payload}
        response = await self.client.post(f"/runs/{run_id}/responses", json=body)
        self.assertEqual(response.status_code, 202)
        return response

    async def approve_completion(self, run_id, proposal):
        paused = await self.poll(run_id, "awaiting_completion")
        body = paused.json()
        self.assertEqual(body["status"], "awaiting_completion")
        self.assertIsNone(body["final_result"])
        self.assertEqual(body["completion_proposal"], proposal)
        self.assertIsInstance(body["interaction_id"], str)
        self.assertTrue(body["interaction_id"])
        await self.respond(
            run_id, paused, {"type": "completion", "approved": True}
        )
        finished = await self.poll(run_id, "finished")
        self.assertEqual(finished.json()["final_result"], proposal)
        self.assertIsNone(finished.json()["completion_proposal"])
        return paused, finished

    async def wait_for_cleanup(self, record, limit=80):
        for _ in range(limit):
            if record.events[-2:] == ["client_exit", "stdio_exit"]:
                return
            await asyncio.sleep(0)
        self.fail(f"runtime cleanup did not complete; events={record.events!r}")

    async def test_finished_http_run_and_composition_security_wiring(self):
        self.harness.queue([AgentToolCall("browser_snapshot", {}), AgentToolCall("finish", {"result": "done"})])
        run_id, created = await self.create()
        _, finished = await self.approve_completion(run_id, "done")
        record = self.harness.records[0]
        await self.wait_for_cleanup(record)
        self.assertEqual(record.calls[0], ("browser_navigate", {"url": START_URL}))
        self.assertEqual([name for name, _ in record.calls], ["browser_navigate", "browser_snapshot", "browser_close"])
        self.assertEqual(record.contexts[0].task, TASK)
        visible = {tool.name for tool in record.contexts[0].tools}
        self.assertTrue({"browser_navigate", "browser_snapshot", "browser_click", "browser_fill_form"} <= visible)
        self.assertTrue({"browser_close", "browser_file_upload", "browser_evaluate", "browser_run_code_unsafe"}.isdisjoint(visible))
        self.assertEqual(record.events, ["stdio_enter", "client_enter", "initialize", "discover", "client_exit", "stdio_exit"])
        self.assertTrue(record.runtime_config.exists())
        self.assertEqual(finished.json()["final_result"], "done")
        self.assertEqual(set(created.json()), {"run_id", "status", "version", "start_url", "task", "step_count", "question", "interaction_id", "final_result", "completion_proposal", "error", "secret_fields", "secret_targets", "files", "audit_events"})
        self.assertEqual(
            [event["summary"] for event in finished.json()["audit_events"]],
            [
                "Initial page opened",
                "Page inspected",
                "Agent proposed completion",
                "User finished the run",
            ],
        )
        self.assertEqual(
            {route.path for route in self.app.routes if route.path.startswith("/runs")},
            {"/runs", "/runs/{run_id}", "/runs/{run_id}/responses", "/runs/{run_id}/cancel", "/runs/{run_id}/files/{file_id}", "/runs/{run_id}/provider-traces"},
        )

    async def test_user_input_http_pause_and_resume(self):
        self.harness.queue([AgentToolCall("ask_user", {"question": "Which account?"}), AgentToolCall("finish", {"result": "resumed"})])
        run_id, _ = await self.create()
        paused = await self.poll(run_id, "awaiting_user")
        self.assertEqual(paused.json()["question"], "Which account?")
        self.assertTrue(paused.json()["interaction_id"])
        await self.respond(run_id, paused, {"type": "user_input", "text": "Personal"})
        await self.approve_completion(run_id, "resumed")
        await self.wait_for_cleanup(self.harness.records[0])
        self.assertEqual(len(self.harness.records), 1)
        interaction = self.harness.records[0].contexts[-1].user_interactions[-1]
        self.assertEqual(interaction.response, "Personal")

    async def test_urlless_http_run_skips_initial_navigation_and_starts_agent(self):
        task = "Open https://example.com and inspect the page."
        self.harness.queue([AgentToolCall("ask_user", {"question": "Which target?"})])

        created = await self.client.post("/runs", json={"task": task})
        self.assertEqual(created.status_code, 202)
        self.assertIsNone(created.json()["start_url"])
        paused = await self.poll(created.json()["run_id"], "awaiting_user")

        record = self.harness.records[0]
        self.assertEqual(record.calls, [])
        self.assertEqual(record.contexts[0].task, task)
        visible = {tool.name for tool in record.contexts[0].tools}
        self.assertIn("browser_navigate", visible)
        self.assertIn("ask_user", visible)
        self.assertEqual(paused.json()["audit_events"][0]["summary"], "Conversation started")
        self.assertIsNone(paused.json()["start_url"])

    async def test_trusted_confirmation_pause_and_exact_replay(self):
        click = {"element": "Download", "ref": "button-7"}
        self.harness.queue([AgentToolCall("browser_click", click), AgentToolCall("ask_user", {"question": "Approve click?", "confirmation_for_step": 1}), AgentToolCall("finish", {"result": "clicked"})])
        run_id, _ = await self.create()
        paused = await self.poll(run_id, "awaiting_confirmation")
        record = self.harness.records[0]
        self.assertNotIn("browser_click", [name for name, _ in record.calls])
        click_definition = next(tool for tool in record.contexts[0].tools if tool.name == "browser_click")
        self.assertNotIn("confirmation_granted", click_definition.input_schema.get("properties", {}))
        await self.respond(run_id, paused, {"type": "confirmation", "approved": True})
        await self.approve_completion(run_id, "clicked")
        await self.wait_for_cleanup(record)
        clicks = [args for name, args in record.calls if name == "browser_click"]
        self.assertEqual(clicks, [click])

    async def test_secret_request_secure_application_and_non_disclosure(self):
        decision = AgentToolCall("request_secret", {"question": "Enter password", "fields": ["password"], "targets": [{"field": "password", "name": "Account password", "ref": SECRET_REF}]})
        self.harness.queue([decision, AgentToolCall("finish", {"result": "signed in"})])
        run_id, created = await self.create()
        paused = await self.poll(run_id, "awaiting_secret")
        body = paused.json()
        self.assertEqual(body["secret_fields"], ["password"])
        self.assertEqual(body["secret_targets"], [{"field": "password", "name": "Account password"}])
        self.assertNotIn(SECRET_REF, paused.text)
        submitted = await self.respond(run_id, paused, {"type": "secret", "values": {"password": SECRET}})
        _, finished = await self.approve_completion(run_id, "signed in")
        record = self.harness.records[0]
        await self.wait_for_cleanup(record)
        self.assertTrue(record.secret_seen)
        self.assertEqual([name for name, _ in record.calls].count("browser_fill_form"), 1)
        event = record.contexts[-1].application_events[-1]
        self.assertEqual([field.value for field in event.fields], ["password"])
        retained = [created.text, paused.text, submitted.text, finished.text, repr(record.calls), repr(record.contexts), repr(await self.manager.get_run(run_id))]
        self.assertTrue(all(SECRET not in value for value in retained))
        self.assertTrue(all(SECRET_REF not in value for value in [created.text, paused.text, submitted.text, finished.text, repr(await self.manager.get_run(run_id))]))

    async def test_scripted_login_guards_secret_application_and_exact_confirmation(self):
        raw_secret = "SuperSecretPassword!"
        raw_username = "alice@example.test"
        click = {"element": "Login", "ref": "login-button"}
        self.harness.queue([
            AgentToolCall("browser_fill_form", {"fields": [
                {"name": "username", "type": "text", "ref": "user-ref", "value": raw_username},
                {"name": "password", "type": "password", "ref": SECRET_REF, "value": raw_secret},
            ]}),
            AgentToolCall("request_secret", {
                "question": "Enter username and password securely",
                "fields": ["username", "password"],
                "targets": [
                    {"field": "username", "name": "Username", "ref": "user-ref"},
                    {"field": "password", "name": "Password", "ref": SECRET_REF},
                ],
            }),
            AgentToolCall("ask_user", {"question": "May I click the Login button?"}),
            AgentToolCall("browser_click", click),
            AgentToolCall("ask_user", {
                "question": "Approve this exact Login click?", "confirmation_for_step": 4,
            }),
            AgentToolCall("finish", {"result": "Login verified"}),
        ])
        run_id, created = await self.create()
        secret_pause = await self.poll(run_id, "awaiting_secret")
        record = self.harness.records[0]
        self.assertNotIn("browser_fill_form", [name for name, _ in record.calls])
        retained_before_secret = repr((record.contexts, await self.manager.get_run(run_id)))
        self.assertNotIn(raw_secret, retained_before_secret)
        self.assertNotIn(raw_username, retained_before_secret)
        self.assertIn("Sensitive form values must be supplied", retained_before_secret)

        submitted = await self.respond(run_id, secret_pause, {
            "type": "secret", "values": {
                "username": raw_username, "password": raw_secret,
            },
        })
        confirmation = await self.poll(run_id, "awaiting_confirmation")
        self.assertNotEqual(submitted.json()["status"], "awaiting_user")
        self.assertEqual([name for name, _ in record.calls].count("browser_fill_form"), 1)
        await self.respond(run_id, confirmation, {"type": "confirmation", "approved": True})
        proposal, finished = await self.approve_completion(run_id, "Login verified")
        await self.wait_for_cleanup(record)
        self.assertEqual([args for name, args in record.calls if name == "browser_click"], [click])
        retained = repr((created.json(), secret_pause.json(), submitted.json(), proposal.json(), finished.json(), record.contexts, await self.manager.get_run(run_id)))
        self.assertNotIn(raw_secret, retained)
        self.assertNotIn(raw_username, retained)

    async def test_download_tracking_http_retrieval_and_run_isolation(self):
        click = {"element": "Report", "ref": "download-1"}
        script = [AgentToolCall("browser_click", click), AgentToolCall("ask_user", {"question": "Download?", "confirmation_for_step": 1}), AgentToolCall("finish", {"result": "downloaded"})]
        self.harness.queue(script, download=True)
        self.harness.queue([AgentToolCall("finish", {"result": "other"})])
        first, _ = await self.create()
        paused = await self.poll(first, "awaiting_confirmation")
        await self.respond(first, paused, {"type": "confirmation", "approved": True})
        awaiting = await self.poll(first, "awaiting_completion")
        self.assertEqual(awaiting.json()["status"], "awaiting_completion")
        self.assertIsNone(awaiting.json()["final_result"])
        self.assertEqual(awaiting.json()["completion_proposal"], "downloaded")
        self.assertIsInstance(awaiting.json()["interaction_id"], str)
        self.assertTrue(awaiting.json()["interaction_id"])
        metadata = awaiting.json()["files"][0]
        self.assertEqual(set(metadata), {"file_id", "filename", "size"})
        self.assertEqual((metadata["filename"], metadata["size"]), ("report.txt", len(DOWNLOAD_BYTES)))
        self.assertNotIn("browser_close", [name for name, _ in self.harness.records[0].calls])
        record = self.harness.records[0]
        evidence_context = record.contexts[-1]
        evidence_text = evidence_context.steps[-1].observation.text
        self.assertIn(
            f"Application recorded download: report.txt ({len(DOWNLOAD_BYTES)} bytes)",
            evidence_text,
        )
        self.assertNotIn(str(record.output_directory), evidence_text)
        self.assertNotIn(metadata["file_id"], evidence_text)
        self.assertEqual(
            [name for name, _ in record.calls].count("browser_click"), 1
        )

        async def run_sync(function, *args, **_kwargs):
            return function(*args)

        with patch("starlette.responses.anyio.to_thread.run_sync", run_sync):
            while_awaiting = await self.client.get(
                f"/runs/{first}/files/{metadata['file_id']}"
            )
        self.assertEqual(while_awaiting.status_code, 200)
        self.assertEqual(while_awaiting.content, DOWNLOAD_BYTES)

        await self.respond(
            first, awaiting, {"type": "completion", "approved": True}
        )
        finished = await self.poll(first, "finished")
        self.assertEqual(finished.json()["final_result"], "downloaded")
        self.assertIsNone(finished.json()["completion_proposal"])
        await self.wait_for_cleanup(self.harness.records[0])
        second, _ = await self.create()
        await self.approve_completion(second, "other")
        await self.wait_for_cleanup(self.harness.records[1])
        all_json = awaiting.text + finished.text + (await self.client.get(f"/runs/{second}")).text
        self.assertNotIn(str(self.download_base), all_json)

        with patch("starlette.responses.anyio.to_thread.run_sync", run_sync):
            downloaded = await self.client.get(f"/runs/{first}/files/{metadata['file_id']}")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.content, DOWNLOAD_BYTES)
        self.assertIn("report.txt", downloaded.headers["content-disposition"])
        isolated = await self.client.get(f"/runs/{second}/files/{metadata['file_id']}")
        self.assertEqual(isolated.status_code, 404)
        one, two = self.harness.records
        self.assertIsNot(one, two)
        self.assertNotEqual(one.output_directory, two.output_directory)
        self.assertNotEqual(one.output_directory.parent, two.output_directory.parent)

    async def test_declined_completion_feedback_continues_to_second_proposal(self):
        self.harness.queue([
            AgentToolCall("finish", {"result": "first proposal"}),
            AgentToolCall("finish", {"result": "second proposal"}),
        ])
        run_id, _ = await self.create()
        first = await self.poll(run_id, "awaiting_completion")
        self.assertEqual(first.json()["completion_proposal"], "first proposal")
        await self.respond(
            run_id,
            first,
            {
                "type": "completion",
                "approved": False,
                "feedback": "The export is still missing",
            },
        )
        second, finished = await self.approve_completion(run_id, "second proposal")
        self.assertNotEqual(
            first.json()["interaction_id"], second.json()["interaction_id"]
        )
        self.assertEqual(finished.json()["final_result"], "second proposal")
        feedback = self.harness.records[0].contexts[-1].user_interactions[-1]
        self.assertEqual(feedback.response, "The export is still missing")
        await self.wait_for_cleanup(self.harness.records[0])

    async def test_http_cancellation_and_cleanup(self):
        self.harness.queue([], blocking=True)
        run_id, _ = await self.create()
        if not self.harness.records:
            await asyncio.wait_for(self.harness.record_created.wait(), 1)
        record = self.harness.records[0]
        await asyncio.wait_for(record.decision_started.wait(), 1)
        root = record.output_directory.parent
        response = await self.client.post(f"/runs/{run_id}/cancel", json={"request_id": "cancel-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertTrue(record.decision_cancelled)
        self.assertEqual([name for name, _ in record.calls].count("browser_close"), 1)
        self.assertEqual(record.events[-2:], ["client_exit", "stdio_exit"])
        self.assertTrue(record.runtime_config.exists())
        self.assertFalse(root.exists())
        await self.manager.close()
        self.assertEqual([name for name, _ in record.calls].count("browser_close"), 1)

    async def test_fastapi_lifespan_shutdown(self):
        self.harness.queue([AgentToolCall("ask_user", {"question": "Remain paused?"})])
        run_id, _ = await self.create()
        await self.poll(run_id, "awaiting_user")
        record = self.harness.records[0]
        root = record.output_directory.parent
        await self.lifespan.__aexit__(None, None, None)
        self.lifespan = _AlreadyExited()
        self.assertEqual([name for name, _ in record.calls].count("browser_close"), 1)
        self.assertEqual(record.events[-2:], ["client_exit", "stdio_exit"])
        self.assertTrue(record.runtime_config.exists())
        self.assertFalse(root.exists())
        self.assertEqual(record.events.count("client_exit"), 1)
        self.assertEqual(record.events.count("stdio_exit"), 1)


class _AlreadyExited:
    async def __aexit__(self, *_args):
        return None


if __name__ == "__main__":
    unittest.main()
