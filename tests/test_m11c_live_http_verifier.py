"""Offline contract tests for the import-safe M11C live verifier."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "m11c_live_http_verifier.py"
SPEC = importlib.util.spec_from_file_location("m11c_live_http_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class ResolutionAndConfigTests(unittest.TestCase):
    def test_bootstrap_returns_and_inserts_repository_root_once(self):
        path = ["/unrelated"]
        with patch.object(verifier.sys, "path", path):
            self.assertEqual(verifier.bootstrap_repository_import_path(), ROOT)
            self.assertEqual(path, [str(ROOT), "/unrelated"])
            self.assertEqual(verifier.bootstrap_repository_import_path(), ROOT)
            self.assertEqual(path.count(str(ROOT)), 1)
        existing = ["/unrelated", str(ROOT)]
        with patch.object(verifier.sys, "path", existing):
            self.assertEqual(verifier.bootstrap_repository_import_path(), ROOT)
            self.assertEqual(existing, ["/unrelated", str(ROOT)])

    def test_bootstrap_is_independent_of_current_working_directory(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(verifier.sys, "path", []):
            previous = Path.cwd()
            try:
                os.chdir(temporary)
                self.assertEqual(verifier.bootstrap_repository_import_path(), ROOT)
                self.assertEqual(verifier.sys.path, [str(ROOT)])
            finally:
                os.chdir(previous)

    def test_repository_and_package_cli_resolution(self):
        self.assertEqual(verifier.repository_root(SCRIPT), ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "node_modules" / "@playwright" / "mcp"
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"bin": "cli.js"}))
            (package / "cli.js").write_text("public")
            self.assertEqual(verifier.resolve_mcp_cli(root), (package / "cli.js").resolve())

    def test_repository_and_cli_escape_fail_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(verifier.VerificationFailure):
                verifier.repository_root(root)
            package = root / "node_modules" / "@playwright" / "mcp"
            package.mkdir(parents=True)
            (root / "outside.js").write_text("public")
            (package / "package.json").write_text(json.dumps({"bin": "../../../outside.js"}))
            with self.assertRaises(verifier.VerificationFailure):
                verifier.resolve_mcp_cli(root)

    def test_exact_origin_is_single_loopback_origin(self):
        origin = "http://127.0.0.1:43210"
        config = verifier.build_exact_origin_config(origin)
        self.assertEqual(config["network"]["allowedOrigins"], [origin])
        self.assertEqual(config["capabilities"], ["core"])
        self.assertFalse(config["allowUnrestrictedFileAccess"])
        for invalid in ("http://localhost:1", "http://127.0.0.1:2/*", "https://127.0.0.1:3"):
            with self.subTest(invalid=invalid), self.assertRaises(verifier.VerificationFailure):
                verifier.build_exact_origin_config(invalid)


class EnvironmentTests(unittest.TestCase):
    BASE = {"BROWSER_AGENT_LLM_BASE_URL": "https://provider.example/v1", "BROWSER_AGENT_LLM_MODEL": "model"}

    def test_validation_defaults_and_optional_values(self):
        default = verifier.load_runtime_environment(self.BASE)
        supplied = verifier.load_runtime_environment({**self.BASE, "BROWSER_AGENT_LLM_API_KEY": "key", "BROWSER_AGENT_LLM_TIMEOUT": "2.5"})
        self.assertEqual((default.timeout, default.api_key), (60.0, None))
        self.assertEqual((supplied.timeout, supplied.api_key), (2.5, "key"))

    def test_invalid_environment_is_fixed_and_does_not_disclose(self):
        marker = "do-not-report"
        for environment in ({}, {**self.BASE, "BROWSER_AGENT_LLM_TIMEOUT": "nan"}, {"BROWSER_AGENT_LLM_BASE_URL": marker, "BROWSER_AGENT_LLM_MODEL": "m"}):
            with self.subTest(environment=environment), self.assertRaises(verifier.VerificationFailure) as caught:
                verifier.load_runtime_environment(environment)
            self.assertNotIn(marker, str(caught.exception))

    def test_provider_import_failure_is_missing_runtime_without_disclosure(self):
        marker = "private-import-detail"
        api_key = "private-api-key"
        real_import = __import__

        def fail_provider_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "browser_agent.openai_provider":
                raise ModuleNotFoundError(marker)
            return real_import(name, globals, locals, fromlist, level)

        with patch("builtins.__import__", side_effect=fail_provider_import):
            with self.assertRaises(verifier.VerificationFailure) as caught:
                verifier.load_runtime_environment({**self.BASE, "BROWSER_AGENT_LLM_API_KEY": api_key})
        self.assertEqual(caught.exception.category, verifier.FailureCategory.MISSING_RUNTIME)
        self.assertEqual(
            str(caught.exception),
            verifier.FailureCategory.MISSING_RUNTIME.value,
        )
        self.assertNotIn(marker, str(caught.exception))
        self.assertNotIn(api_key, str(caught.exception))

    def test_malformed_provider_values_remain_invalid_without_disclosure(self):
        marker = "not-a-provider-url"
        with self.assertRaises(verifier.VerificationFailure) as caught:
            verifier.load_runtime_environment({**self.BASE, "BROWSER_AGENT_LLM_BASE_URL": marker})
        self.assertEqual(caught.exception.category, verifier.FailureCategory.INVALID_ENVIRONMENT)
        self.assertEqual(str(caught.exception), "INVALID_ENVIRONMENT")
        self.assertNotIn(marker, str(caught.exception))

    def test_worker_environment_removes_only_provider_configuration(self):
        source = {**self.BASE, "BROWSER_AGENT_LLM_API_KEY": "secret", "PATH": "/bin"}
        self.assertEqual(verifier.sanitized_worker_environment(source), {"PATH": "/bin"})


class SyntheticAndPayloadTests(unittest.TestCase):
    def test_synthetic_page_and_download_are_public_and_fixed(self):
        html = verifier._synthetic_html()
        for label in (b"Synthetic password", b"Download synthetic report", b"Synthetic secret applied"):
            self.assertIn(label, html)
        self.assertNotIn(b"https://", html)
        self.assertEqual(verifier.synthetic_headers()["Content-Length"], str(len(verifier.DOWNLOAD_BYTES)))
        self.assertNotIn(b"secret", verifier.DOWNLOAD_BYTES.lower())

    def test_http_interaction_payloads_are_exact(self):
        self.assertEqual(verifier.interaction_payload("awaiting_user", "i", "blue", "r"), {
            "request_id": "r", "interaction_id": "i", "type": "user_input", "text": "blue",
        })
        self.assertEqual(verifier.interaction_payload("awaiting_confirmation", "i", True, "r")["approved"], True)
        self.assertEqual(verifier.interaction_payload("awaiting_secret", "i", "sentinel", "r")["values"], {"password": "sentinel"})
        for bad in (("awaiting_secret", "", "x"), ("awaiting_confirmation", "i", 1), ("mystery", "i", "x")):
            with self.subTest(bad=bad), self.assertRaises(verifier.VerificationFailure):
                verifier.interaction_payload(*bad, "r")

    def test_status_classifier_rejects_malformed_and_unexpected_states(self):
        for status in verifier.KNOWN_STATUSES:
            expected = "terminal" if status in verifier.TERMINAL_STATUSES else "active"
            self.assertEqual(verifier.classify_status(status), expected)
        for value in (None, True, "mystery"):
            with self.assertRaises(verifier.VerificationFailure):
                verifier.classify_status(value)


class TargetDerivationTests(unittest.TestCase):
    SNAPSHOT = '''- main "M11C synthetic application" [ref=e1]:
  - textbox "Synthetic password" [ref=e17]
  - link "Download synthetic report" [ref=e29]'''

    def test_derives_current_refs_from_realistic_observation(self):
        self.assertEqual(verifier.derive_target(self.SNAPSHOT, "Synthetic password"), "e17")
        self.assertEqual(verifier.derive_target(self.SNAPSHOT, "Download synthetic report"), "e29")

    def test_snapshot_ref_becomes_browser_click_target(self):
        snapshot = '- link "Download synthetic report" [ref=e17]'
        derived_ref = verifier.derive_target(snapshot, "Download synthetic report")
        self.assertEqual(derived_ref, "e17")

        steps = [_step(number, "browser_snapshot") for number in range(1, 4)]
        steps.append(_step(4, "browser_snapshot", text=f"Synthetic secret applied\n{snapshot}"))
        source = verifier.DeterministicDecisionSource(object(), verifier.AuditRecord())
        click = asyncio.run(source.next_decision(_context(verifier.DETERMINISTIC_TASK, steps)))

        self.assertEqual(click.arguments["target"], "e17")
        self.assertNotIn("ref", click.arguments)

    def test_missing_duplicate_and_malformed_refs_are_rejected(self):
        for snapshot in ("public", self.SNAPSHOT + '\n- textbox "Synthetic password" [ref=e99]', '- textbox "Synthetic password"'):
            with self.subTest(snapshot=snapshot), self.assertRaises(verifier.VerificationFailure):
                verifier.derive_target(snapshot, "Synthetic password")

    def test_decision_source_has_no_hard_coded_ephemeral_reference(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotRegex(source, r'["\']e\d+["\']')


def _step(number, tool, *, text="public", status="success", replay=None):
    from browser_agent.agent_loop import AgentStepStatus, AgentToolCall
    observation = Mock(status=AgentStepStatus(status), text=text)
    return Mock(step_number=number, decision=AgentToolCall(tool, {}), observation=observation, replay_of_step_number=replay)


def _context(task, steps):
    tools = [Mock(name=name) for name in ("ask_user", "request_secret", "finish", "browser_snapshot", "browser_click")]
    return Mock(task=task, steps=tuple(steps), tools=tools)


class DeterministicDecisionTests(unittest.TestCase):
    def setUp(self):
        self.record = verifier.AuditRecord(["sentinel", "api-key"])
        self.source = verifier.DeterministicDecisionSource(object(), self.record)

    def decide(self, steps):
        return asyncio.run(self.source.next_decision(_context(verifier.DETERMINISTIC_TASK, steps)))

    def test_complete_sequence_derives_refs_and_has_one_confirmation(self):
        first_snapshot = '- textbox "Synthetic password" [ref=p-current]\n- link "Download synthetic report" [ref=d-old]'
        second_snapshot = 'Synthetic secret applied\n- link "Download synthetic report" [ref=d-current]'
        steps = []
        self.assertEqual(self.decide(steps).tool_name, "ask_user")
        steps.append(_step(1, "ask_user"))
        self.assertEqual(self.decide(steps).tool_name, "browser_snapshot")
        steps.append(_step(2, "browser_snapshot", text=first_snapshot))
        secret = self.decide(steps)
        self.assertEqual(secret.arguments["targets"][0]["ref"], "p-current")
        self.assertNotIn("sentinel", json.dumps(secret.arguments))
        steps.append(_step(3, "request_secret"))
        self.assertEqual(self.decide(steps).tool_name, "browser_snapshot")
        steps.append(_step(4, "browser_snapshot", text=second_snapshot))
        click = self.decide(steps)
        self.assertEqual(click.arguments, {
            "element": "Download synthetic report",
            "target": "d-current",
        })
        steps.append(Mock(step_number=5, decision=click, observation=Mock(status=Mock(value="rejected"), text="confirmation"), replay_of_step_number=None))
        confirmation = self.decide(steps)
        self.assertEqual(confirmation.arguments["confirmation_for_step"], 5)
        steps.append(Mock(step_number=6, decision=confirmation, observation=Mock(status=Mock(value="awaiting_user"), text="public"), replay_of_step_number=None))
        steps.append(Mock(step_number=7, decision=click, observation=Mock(status=Mock(value="success"), text="downloaded"), replay_of_step_number=5))
        finish = self.decide(steps)
        self.assertEqual(finish.arguments, {"result": verifier.PUBLIC_RESULT})
        self.assertEqual([item.tool_name for item in self.record.decisions].count("ask_user"), 2)

    def test_replay_must_be_exact_and_successful(self):
        click = Mock(tool_name="browser_click", arguments={"element": "Download synthetic report", "target": "r"})
        steps = [_step(i, "browser_snapshot") for i in range(1, 5)]
        steps.append(Mock(decision=click, observation=Mock(status=Mock(value="rejected")), replay_of_step_number=None))
        steps.append(_step(6, "ask_user"))
        steps.append(Mock(decision=Mock(tool_name="browser_click", arguments={"element": "Download synthetic report", "target": "other"}), observation=Mock(status=Mock(value="success")), replay_of_step_number=5))
        with self.assertRaises(verifier.VerificationFailure):
            self.decide(steps)

    def test_replay_with_fabricated_ref_argument_is_rejected(self):
        click = Mock(tool_name="browser_click", arguments={"element": "Download synthetic report", "target": "r"})
        steps = [_step(i, "browser_snapshot") for i in range(1, 5)]
        steps.append(Mock(decision=click, observation=Mock(status=Mock(value="rejected")), replay_of_step_number=None))
        steps.append(_step(6, "ask_user"))
        fabricated = Mock(tool_name="browser_click", arguments={"element": "Download synthetic report", "ref": "r"})
        steps.append(Mock(decision=fabricated, observation=Mock(status=Mock(value="success")), replay_of_step_number=5))
        with self.assertRaisesRegex(verifier.VerificationFailure, "INTERACTION_MISMATCH"):
            self.decide(steps)

    def test_cancellation_source_only_pauses(self):
        call = asyncio.run(self.source.next_decision(_context(verifier.CANCELLATION_TASK, [])))
        self.assertEqual(call.tool_name, "ask_user")
        self.assertNotIn("confirmation_for_step", call.arguments)

    def test_prohibited_tool_exposure_is_rejected(self):
        context = _context(verifier.DETERMINISTIC_TASK, [])
        context.tools = [SimpleNamespace(name="browser_evaluate")]
        self.assertEqual(context.tools[0].name, "browser_evaluate")
        with self.assertRaisesRegex(verifier.VerificationFailure, "UNEXPECTED_TOOL_EXPOSURE"):
            asyncio.run(self.source.next_decision(context))


class SmokeAndRoutingContractTests(unittest.TestCase):
    def test_smoke_task_is_short_observe_then_fixed_finish(self):
        self.assertIn("browser_snapshot", verifier.SMOKE_TASK)
        self.assertIn("provider smoke complete", verifier.SMOKE_TASK)
        for forbidden in ("secret", "download", "confirm", "fill", "select"):
            self.assertNotIn(forbidden, verifier.SMOKE_TASK.lower())

    def test_routing_uses_real_source_only_for_smoke(self):
        calls = []
        class Real:
            def __init__(self, _config): pass
            def _request_body(self, _context): return {"public": True}
            async def next_decision(self, _context):
                calls.append("provider")
                return Mock(tool_name="browser_snapshot", arguments={})
        source = verifier.make_routing_source(Real, verifier.AuditRecord())(object())
        asyncio.run(source.next_decision(_context(verifier.SMOKE_TASK, [])))
        deterministic = asyncio.run(source.next_decision(_context(verifier.CANCELLATION_TASK, [])))
        self.assertEqual((calls, deterministic.tool_name), (["provider"], "ask_user"))


class DownloadDisclosureAndCleanupTests(unittest.TestCase):
    def test_path_free_exact_download_metadata(self):
        item = {"file_id": "0123456789abcdef0123456789abcdef", "filename": verifier.DOWNLOAD_FILENAME, "size": len(verifier.DOWNLOAD_BYTES)}
        self.assertIs(verifier.validate_download_metadata([item]), item)
        for bad in ([{**item, "path": "/private"}], [item, item], [{**item, "size": 1}]):
            with self.subTest(bad=bad), self.assertRaises(verifier.VerificationFailure):
                verifier.validate_download_metadata(bad)

    def test_download_file_id_requires_lowercase_uuid4_hex_contract(self):
        valid = {"file_id": "0123456789abcdef0123456789abcdef", "filename": verifier.DOWNLOAD_FILENAME, "size": len(verifier.DOWNLOAD_BYTES)}
        invalid_ids = (
            "",
            "abc",
            "0123456789abcdef0123456789abcde",
            "0123456789abcdef0123456789abcdef0",
            "0123456789ABCDEF0123456789ABCDEF",
            "01234567-89ab-cdef-0123-456789abcdef",
            "../report.txt",
            "/private/run/report.txt",
            "downloads/report.txt",
            "0123456789abcdef0123456789abcdeg",
        )
        for file_id in invalid_ids:
            with self.subTest(file_id=file_id), self.assertRaises(verifier.VerificationFailure) as caught:
                verifier.validate_download_metadata([{**valid, "file_id": file_id}])
            self.assertEqual(caught.exception.category, verifier.FailureCategory.DOWNLOAD)
            self.assertEqual(str(caught.exception), "DOWNLOAD_MISMATCH")
            if file_id:
                self.assertNotIn(file_id, str(caught.exception))

    def test_disclosure_scan_detects_secret_and_api_key_in_text_and_bytes(self):
        markers = ["unique-sentinel", "api-key"]
        self.assertTrue(verifier.contains_prohibited_marker("x unique-sentinel", markers))
        self.assertTrue(verifier.contains_prohibited_marker(b"x api-key", markers))
        self.assertFalse(verifier.contains_prohibited_marker("public", markers))

    def test_cleanup_requires_empty_download_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "downloads"
            base.mkdir()
            verifier.validate_download_base_cleanup(base)
            (base / "run" / "managed").mkdir(parents=True)
            with self.assertRaisesRegex(verifier.VerificationFailure, "CLEANUP_FAILURE"):
                verifier.validate_download_base_cleanup(base)

    def test_failure_output_never_includes_sensitive_details(self):
        marker = "unique-sentinel"
        output = verifier.safe_failure_output(verifier.FailureCategory.DISCLOSURE)
        self.assertEqual(output, "M11C_RESULT=FAIL\nFAILURE_CATEGORY=SECRET_DISCLOSURE")
        self.assertNotIn(marker, output)


class ParentProtocolTests(unittest.TestCase):
    def success(self):
        return verifier.WorkerResult(True, None, None, {field: True for field in verifier.CHECK_FIELDS}, 2, 20)

    def test_safe_success_has_component_evidence_and_counts(self):
        output = verifier.safe_success_output(self.success())
        for field in verifier.SUCCESS_FIELDS:
            self.assertIn(f"{field}=PASS", output)
        self.assertIn("PROVIDER_CALL_COUNT=2", output)
        self.assertNotIn("{", output)

    def test_safe_success_never_passes_skipped_component(self):
        checks = dict(self.success().checks)
        checks["CANCELLATION"] = False
        with self.assertRaises(verifier.VerificationFailure):
            verifier.safe_success_output(verifier.WorkerResult(True, None, None, checks))

    def test_worker_result_parser_accepts_exact_contract(self):
        raw = json.dumps(self.success().__dict__).encode()
        self.assertTrue(verifier.parse_worker_result(raw, 0).ok)

    def test_worker_result_parser_rejects_noise_bad_schema_and_codes(self):
        valid = self.success().__dict__
        cases = [
            (json.dumps(valid).encode() + b"\nnoise", 0),
            (json.dumps({**valid, "extra": True}).encode(), 0),
            (json.dumps({**valid, "provider_calls": True}).encode(), 0),
            (json.dumps(valid).encode(), 1),
        ]
        for raw, code in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
                verifier.parse_worker_result(raw, code)

    def test_safe_failure_category_and_cleanup_category(self):
        output = verifier.safe_failure_output(verifier.FailureCategory.PROVIDER, verifier.FailureCategory.CLEANUP)
        self.assertIn("FAILURE_CATEGORY=PROVIDER_FAILURE", output)
        self.assertIn("CLEANUP_FAILURE_CATEGORY=CLEANUP_FAILURE", output)


class HttpDriverContractTests(unittest.TestCase):
    def test_cancellation_crosses_http_boundary_and_checks_isolation(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('POST", f"/runs/{run_id}/cancel"', source)
        self.assertIn('GET", f"/runs/{cancellation_run}/files/{file_id}"', source)
        self.assertNotIn("run_manager.cancel", source)

    def test_all_user_operations_use_http_payload_builder(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('POST", f"/runs/{run_id}/responses"', source)
        self.assertIn("interaction_payload(status", source)
        self.assertIn('GET", f"/runs/{deterministic_run}/files/{file_id}"', source)


class ProcessAndImportSafetyTests(unittest.TestCase):
    @patch.object(verifier.subprocess, "Popen")
    def test_worker_output_is_captured_and_not_printed(self, popen):
        process = Mock(returncode=0)
        process.communicate.return_value = (b'{"safe":true}', b"private")
        popen.return_value = process
        with patch.object(verifier.sys.stdout, "write") as out, patch.object(verifier.sys.stderr, "write") as err:
            result = verifier.run_worker(["python"], {"PATH": "/bin"})
        self.assertEqual(result, (b'{"safe":true}', b"private", 0))
        out.assert_not_called()
        err.assert_not_called()

    @patch.object(verifier.subprocess, "Popen")
    def test_worker_timeout_terminates_process(self, popen):
        process = Mock(returncode=1)
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired("worker", 1), (b"", b"")]
        popen.return_value = process
        with self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
            verifier.run_worker(["python"], {}, 1)
        process.terminate.assert_called_once()

    def test_import_is_inert(self):
        spec = importlib.util.spec_from_file_location("m11c_inert", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        original_path = list(sys.path)
        with patch.dict(os.environ, {}, clear=True), patch("subprocess.Popen") as popen, patch("threading.Thread.start") as start:
            spec.loader.exec_module(module)
        self.assertEqual(sys.path, original_path)
        popen.assert_not_called()
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
