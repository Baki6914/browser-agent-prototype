"""Pure unit tests for the import-safe M11C live verifier."""

from __future__ import annotations

import importlib.util
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "m11c_live_http_verifier.py"
SPEC = importlib.util.spec_from_file_location("m11c_live_http_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = verifier
SPEC.loader.exec_module(verifier)


class ResolutionTests(unittest.TestCase):
    def test_repository_root_from_script(self):
        self.assertEqual(verifier.repository_root(SCRIPT), ROOT)

    def test_repository_root_rejects_unrelated_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(verifier.VerificationFailure):
                verifier.repository_root(Path(temporary))

    def test_mcp_cli_resolves_package_declared_bin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "node_modules" / "@playwright" / "mcp"
            package.mkdir(parents=True)
            (package / "package.json").write_text(json.dumps({"bin": {"playwright-mcp": "cli.js"}}))
            (package / "cli.js").write_text("synthetic")
            self.assertEqual(verifier.resolve_mcp_cli(root), (package / "cli.js").resolve())

    def test_mcp_cli_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "node_modules" / "@playwright" / "mcp"
            package.mkdir(parents=True)
            (root / "outside.js").write_text("synthetic")
            (package / "package.json").write_text(json.dumps({"bin": "../../../outside.js"}))
            with self.assertRaises(verifier.VerificationFailure):
                verifier.resolve_mcp_cli(root)


class ConfigTests(unittest.TestCase):
    def test_exact_origin_config(self):
        origin = "http://127.0.0.1:43210"
        config = verifier.build_exact_origin_config(origin)
        self.assertEqual(config["network"]["allowedOrigins"], [origin])
        self.assertEqual(config["capabilities"], ["core"])
        self.assertEqual(config["browser"], {"browserName": "chromium", "isolated": True, "launchOptions": {"headless": True}})
        self.assertEqual((config["imageResponses"], config["codegen"]), ("omit", "none"))
        self.assertFalse(config["allowUnrestrictedFileAccess"])
        self.assertFalse(config["saveSession"])
        self.assertFalse(config["sharedBrowserContext"])

    def test_rejects_wildcard_origin(self):
        with self.assertRaises(verifier.VerificationFailure):
            verifier.build_exact_origin_config("http://127.0.0.1:1234/*")

    def test_rejects_localhost_alias(self):
        with self.assertRaises(verifier.VerificationFailure):
            verifier.build_exact_origin_config("http://localhost:1234")

    def test_rejects_additional_origin(self):
        config = verifier.build_exact_origin_config("http://127.0.0.1:1234")
        config["network"]["allowedOrigins"].append("http://127.0.0.1:5678")
        with self.assertRaises(verifier.VerificationFailure):
            verifier.validate_exact_origin_config(config, "http://127.0.0.1:1234")


class EnvironmentTests(unittest.TestCase):
    BASE = {"BROWSER_AGENT_LLM_BASE_URL": "https://provider.example/v1", "BROWSER_AGENT_LLM_MODEL": "model"}

    def test_valid_environment_and_default_timeout(self):
        value = verifier.load_runtime_environment(self.BASE)
        self.assertEqual(value.timeout, 60.0)
        self.assertIsNone(value.api_key)

    def test_optional_api_key_and_timeout(self):
        value = verifier.load_runtime_environment({**self.BASE, "BROWSER_AGENT_LLM_API_KEY": "private-marker", "BROWSER_AGENT_LLM_TIMEOUT": "2.5"})
        self.assertEqual(value.timeout, 2.5)

    def test_missing_required_environment_is_safe(self):
        with self.assertRaisesRegex(verifier.VerificationFailure, "INVALID_ENVIRONMENT"):
            verifier.load_runtime_environment({})

    def test_invalid_timeout_is_safe(self):
        with self.assertRaisesRegex(verifier.VerificationFailure, "INVALID_ENVIRONMENT"):
            verifier.load_runtime_environment({**self.BASE, "BROWSER_AGENT_LLM_TIMEOUT": "nan"})

    def test_error_does_not_contain_environment_value(self):
        marker = "do-not-report-this-value"
        with self.assertRaises(verifier.VerificationFailure) as caught:
            verifier.load_runtime_environment({"BROWSER_AGENT_LLM_BASE_URL": marker, "BROWSER_AGENT_LLM_MODEL": "m"})
        self.assertNotIn(marker, str(caught.exception))

    def test_worker_environment_removes_provider_values_only(self):
        source = {**self.BASE, "BROWSER_AGENT_LLM_API_KEY": "key", "BROWSER_AGENT_LLM_TIMEOUT": "2", "PATH": "/bin"}
        config = verifier.load_runtime_environment(source)
        sanitized = verifier.sanitized_worker_environment(source)
        self.assertEqual(sanitized, {"PATH": "/bin"})
        self.assertEqual((config.base_url, config.model, config.api_key, config.timeout), (self.BASE["BROWSER_AGENT_LLM_BASE_URL"], "model", "key", 2.0))


class SyntheticAndStateTests(unittest.TestCase):
    def test_download_response_is_fixed_and_safe(self):
        headers = verifier.synthetic_headers()
        self.assertEqual(headers["Content-Length"], str(len(verifier.DOWNLOAD_BYTES)))
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="m11c-report.txt"')
        self.assertNotIn(b"secret", verifier.DOWNLOAD_BYTES.lower())

    def test_synthetic_html_has_accessible_controls_and_no_external_url(self):
        html = verifier._synthetic_html()
        for label in (b"Public note", b"Report type", b"Synthetic password", b"Apply synthetic choices", b"Download synthetic report", b"WORKFLOW COMPLETE"):
            self.assertIn(label, html)
        self.assertNotIn(b"https://", html)
        self.assertIn(b"replaceWith", html)

    def test_all_documented_statuses_classify(self):
        for status in verifier.KNOWN_STATUSES:
            expected = "terminal" if status in verifier.TERMINAL_STATUSES else "active"
            self.assertEqual(verifier.classify_status(status), expected)

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(verifier.VerificationFailure):
            verifier.classify_status("mystery")

    def test_user_payload(self):
        payload = verifier.interaction_payload("awaiting_user", "i", "choice", "r1")
        self.assertEqual(payload, {"request_id": "r1", "interaction_id": "i", "type": "user_input", "text": "choice"})

    def test_confirmation_payload(self):
        self.assertEqual(verifier.interaction_payload("awaiting_confirmation", "i", True, "r2")["type"], "confirmation")

    def test_secret_payload(self):
        payload = verifier.interaction_payload("awaiting_secret", "i", "sentinel", "r3")
        self.assertEqual(payload["values"], {"password": "sentinel"})

    def test_interaction_mismatch_is_rejected(self):
        with self.assertRaises(verifier.VerificationFailure):
            verifier.interaction_payload("awaiting_secret", "", "value", "r")


class DownloadAndDisclosureTests(unittest.TestCase):
    def test_download_metadata_validation(self):
        item = {"file_id": "opaque", "filename": verifier.DOWNLOAD_FILENAME, "size": len(verifier.DOWNLOAD_BYTES)}
        self.assertIs(verifier.validate_download_metadata([item]), item)

    def test_download_metadata_rejects_extra_or_multiple(self):
        item = {"file_id": "opaque", "filename": verifier.DOWNLOAD_FILENAME, "size": len(verifier.DOWNLOAD_BYTES), "path": "/private"}
        with self.assertRaises(verifier.VerificationFailure):
            verifier.validate_download_metadata([item])

    def test_marker_scan_handles_text_and_bytes(self):
        self.assertTrue(verifier.contains_prohibited_marker("prefix sentinel suffix", ["sentinel"]))
        self.assertTrue(verifier.contains_prohibited_marker(b"api-key", ["api-key"]))
        self.assertFalse(verifier.contains_prohibited_marker("public", ["sentinel", ""]))

    def test_provider_body_scan_detects_sentinel_and_api_key(self):
        record = verifier.AuditRecord(["sentinel", "api-key"])
        self.assertTrue(verifier.contains_prohibited_marker(json.dumps({"x": "sentinel"}), record.prohibited_markers))
        self.assertTrue(verifier.contains_prohibited_marker(json.dumps({"x": "api-key"}), record.prohibited_markers))

    def test_download_base_cleanup_requires_empty_base(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary) / "downloads"
            base.mkdir()
            verifier.validate_download_base_cleanup(base)
            (base / "run-success" / "managed").mkdir(parents=True)
            with self.assertRaisesRegex(verifier.VerificationFailure, "CLEANUP_FAILURE"):
                verifier.validate_download_base_cleanup(base)

    def test_safe_failure_categories_are_fixed(self):
        for category in verifier.FailureCategory:
            output = verifier.safe_failure_output(category)
            self.assertEqual(output.splitlines()[0], "M11C_RESULT=FAIL")
            self.assertIn(category.value, output)


class ParentContractTests(unittest.TestCase):
    def _success(self):
        checks = {field: True for field in verifier.SUCCESS_FIELDS[1:]}
        return verifier.WorkerResult(True, None, None, checks, 7, 12)

    def test_safe_success_output_only_has_fixed_fields_and_counts(self):
        output = verifier.safe_success_output(self._success())
        self.assertIn("M11C_RESULT=PASS", output)
        self.assertIn("PROVIDER_CALL_COUNT=7", output)
        self.assertNotIn("{", output)

    def test_safe_success_rejects_missing_check(self):
        result = self._success()
        checks = dict(result.checks)
        checks["CLEANUP"] = False
        with self.assertRaises(verifier.VerificationFailure):
            verifier.safe_success_output(verifier.WorkerResult(True, None, None, checks))

    def test_parent_parses_exact_worker_contract(self):
        result = self._success()
        raw = json.dumps(result.__dict__).encode()
        self.assertTrue(verifier.parse_worker_result(raw, 0).ok)

    def test_parent_rejects_worker_noise(self):
        with self.assertRaises(verifier.VerificationFailure):
            verifier.parse_worker_result(b"log line\n{}", 0)

    def test_parent_interprets_safe_worker_failure(self):
        checks = {field: False for field in verifier.CHECK_FIELDS}
        raw = json.dumps({"ok": False, "category": "DOWNLOAD_MISMATCH", "cleanup_category": None, "checks": checks, "provider_calls": 0, "http_responses": 0}).encode()
        result = verifier.parse_worker_result(raw, 1)
        self.assertEqual(result.category, "DOWNLOAD_MISMATCH")

    def test_parent_rejects_nonzero_worker(self):
        with self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
            verifier.parse_worker_result(b"{}", 1)

    def test_strict_schema_rejects_bad_types_counts_and_extras(self):
        base = self._success().__dict__
        mutations = [
            {**base, "ok": "true"},
            {**base, "provider_calls": True},
            {**base, "provider_calls": -1},
            {**base, "http_responses": verifier.MAX_EVIDENCE_COUNT + 1},
            {**base, "checks": {**base["checks"], "EXTRA": True}},
            {**base, "extra": "value"},
        ]
        for payload in mutations:
            with self.subTest(payload=payload), self.assertRaises(verifier.VerificationFailure):
                verifier.parse_worker_result(json.dumps(payload).encode(), 0)

    def test_numeric_and_newline_injection_are_rejected(self):
        payload = json.dumps(self._success().__dict__).encode() + b"\nM11C_RESULT=PASS"
        with self.assertRaises(verifier.VerificationFailure):
            verifier.parse_worker_result(payload, 0)
        bad = {**self._success().__dict__, "provider_calls": 1.5}
        with self.assertRaises(verifier.VerificationFailure):
            verifier.parse_worker_result(json.dumps(bad).encode(), 0)

    def test_primary_cleanup_and_combined_failure_output(self):
        primary = verifier.safe_failure_output(verifier.FailureCategory.PROVIDER)
        cleanup = verifier.safe_failure_output(verifier.FailureCategory.CLEANUP)
        combined = verifier.safe_failure_output(verifier.FailureCategory.PROVIDER, verifier.FailureCategory.CLEANUP)
        self.assertNotIn("CLEANUP_FAILURE_CATEGORY", primary)
        self.assertEqual(cleanup, "M11C_RESULT=FAIL\nFAILURE_CATEGORY=CLEANUP_FAILURE")
        self.assertIn("CLEANUP_FAILURE_CATEGORY=CLEANUP_FAILURE", combined)


class ConfirmationTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = verifier.ConfirmationActionTracker()

    @staticmethod
    def decision(step, tool, arguments):
        return verifier.DecisionRecord(step, tool, arguments)

    def confirm(self, action):
        decisions = [action, self.decision(action.step + 1, "ask_user", {"question": "confirm", "confirmation_for_step": action.step})]
        return self.tracker.resolve(decisions, action.step + 1)

    def test_cumulative_replay_gaps_resolve_all_four_confirmations(self):
        decisions = []
        pairs = [
            (self.decision(3, "browser_fill_form", {"fields": [{"name": "Public note", "type": "textbox", "ref": "r1", "value": "public verification note"}]}), 4),
            (self.decision(6, "browser_select_option", {"element": "Report type", "ref": "r2", "values": ["Detailed"]}), 7),
            (self.decision(9, "browser_click", {"element": "Apply synthetic choices", "ref": "r3"}), 10),
            (self.decision(12, "browser_click", {"element": "Download synthetic report", "ref": "r4"}), 13),
        ]
        for action, ask_step in pairs:
            decisions.extend([action, self.decision(ask_step, "ask_user", {"question": "confirm", "confirmation_for_step": action.step})])
            self.assertIs(self.tracker.resolve(decisions, ask_step), action)
        self.assertTrue(self.tracker.complete)

    def test_missing_bool_forward_wrong_unrelated_and_duplicate_references(self):
        valid = self.decision(1, "browser_fill_form", {"fields": [{"name": "Public note", "ref": "r", "value": "public verification note"}]})
        ask = self.decision(2, "ask_user", {"confirmation_for_step": 1})
        cases = [None, True, 1, 99]
        for reference in cases:
            with self.subTest(reference=reference), self.assertRaises(verifier.VerificationFailure):
                verifier.ConfirmationActionTracker().resolve([valid, ask], reference)
        unrelated = self.decision(1, "browser_snapshot", {})
        with self.assertRaises(verifier.VerificationFailure):
            verifier.ConfirmationActionTracker().resolve([unrelated, ask], 2)
        stale = self.decision(2, "browser_fill_form", valid.arguments)
        with self.assertRaises(verifier.VerificationFailure):
            verifier.ConfirmationActionTracker().resolve([valid, stale, self.decision(3, "ask_user", {"confirmation_for_step": 1})], 3)
        self.tracker.resolve([valid, ask], 2)
        with self.assertRaises(verifier.VerificationFailure):
            self.tracker.resolve([valid, ask], 2)

    def test_http_snapshot_has_no_confirmation_field_and_step_count_is_authority(self):
        action = self.decision(1, "browser_fill_form", {"fields": [{"name": "Public note", "ref": "r", "value": "public verification note"}]})
        ask = self.decision(2, "ask_user", {"confirmation_for_step": 1})
        snapshot = {"status": "awaiting_confirmation", "step_count": 2}
        self.assertNotIn("confirmation_for_step", snapshot)
        self.assertIs(self.tracker.resolve([action, ask], snapshot["step_count"]), action)

    def test_missing_invalid_or_mismatched_step_count_is_rejected(self):
        action = self.decision(3, "browser_fill_form", {"fields": [{"name": "Public note", "ref": "r", "value": "public verification note"}]})
        ask = self.decision(4, "ask_user", {"confirmation_for_step": 3})
        for count in (None, True, 0, -1, 5):
            with self.subTest(count=count), self.assertRaises(verifier.VerificationFailure):
                verifier.ConfirmationActionTracker().resolve([action, ask], count)

    def test_reference_must_be_adjacent_to_snapshot_step_count(self):
        action = self.decision(3, "browser_fill_form", {"fields": [{"name": "Public note", "ref": "r", "value": "public verification note"}]})
        ask = self.decision(5, "ask_user", {"confirmation_for_step": 3})
        with self.assertRaises(verifier.VerificationFailure):
            self.tracker.resolve([action, ask], 5)

    def test_rejects_arbitrary_wrong_password_duplicate_and_out_of_order_actions(self):
        invalid = [
            self.decision(1, "browser_click", {"element": "Other", "ref": "r"}),
            self.decision(1, "browser_fill_form", {"fields": [{"name": "Public note", "ref": "r", "value": "wrong"}]}),
            self.decision(1, "browser_fill_form", {"fields": [{"name": "Synthetic password", "ref": "r", "value": "secret"}]}),
            self.decision(1, "browser_select_option", {"element": "Report type", "ref": "r", "values": ["Summary"]}),
        ]
        for action in invalid:
            with self.subTest(action=action), self.assertRaises(verifier.VerificationFailure):
                verifier.ConfirmationActionTracker().resolve([action, self.decision(2, "ask_user", {"confirmation_for_step": 1})], 2)


class AuditStepTests(unittest.TestCase):
    def test_provider_decision_count_cannot_substitute_for_agent_step_count(self):
        record = verifier.AuditRecord()

        class Real:
            def _request_body(self, context):
                return {"safe": True}

            async def next_decision(self, context):
                return Mock(tool_name="browser_snapshot", arguments={})

        audited = verifier.make_audit_source(Real, record)()
        context = Mock(steps=tuple(Mock(step_number=value) for value in range(1, 6)), tools=())
        asyncio.run(audited.next_decision(context))
        self.assertEqual(record.provider_calls, 1)
        self.assertEqual(record.decisions[0].step, 6)

    def test_audit_rejects_invalid_prior_or_duplicate_pending_steps(self):
        record = verifier.AuditRecord()

        class Real:
            def _request_body(self, context): return {}
            async def next_decision(self, context): return Mock(tool_name="browser_snapshot", arguments={})

        audited = verifier.make_audit_source(Real, record)()
        for steps in ((Mock(step_number=True),), (Mock(step_number=0),), "not-steps"):
            with self.subTest(steps=steps), self.assertRaises(verifier.VerificationFailure):
                asyncio.run(audited.next_decision(Mock(steps=steps, tools=())))
        record.decisions.append(verifier.DecisionRecord(2, "browser_snapshot", {}))
        with self.assertRaises(verifier.VerificationFailure):
            asyncio.run(audited.next_decision(Mock(steps=(Mock(step_number=1),), tools=())))


class ProcessLifecycleTests(unittest.TestCase):
    def process(self, effects, returncode=0):
        process = Mock(returncode=returncode)
        process.communicate.side_effect = effects
        process.poll.return_value = None
        return process

    @patch.object(verifier.subprocess, "Popen")
    def test_normal_completion_suppresses_raw_streams(self, popen):
        popen.return_value = self.process([(b'{"safe":true}', b"raw stderr")])
        self.assertEqual(verifier.run_worker(["python"], {"PATH": "/bin"}), (b'{"safe":true}', b"raw stderr", 0))
        popen.return_value.communicate.assert_called_once_with(timeout=verifier.WORKER_TIMEOUT_SECONDS)

    @patch.object(verifier.subprocess, "Popen")
    def test_timeout_terminates(self, popen):
        popen.return_value = self.process([verifier.subprocess.TimeoutExpired("worker", 1), (b"", b"")])
        with self.assertRaises(verifier.VerificationFailure):
            verifier.run_worker(["python"], {}, 1)
        popen.return_value.terminate.assert_called_once()

    @patch.object(verifier.subprocess, "Popen")
    def test_timeout_uses_kill_fallback(self, popen):
        popen.return_value = self.process([verifier.subprocess.TimeoutExpired("worker", 1), verifier.subprocess.TimeoutExpired("worker", 1), (b"", b"")])
        with self.assertRaises(verifier.VerificationFailure):
            verifier.run_worker(["python"], {}, 1)
        popen.return_value.kill.assert_called_once()

    @patch.object(verifier.subprocess, "Popen")
    def test_keyboard_interrupt_cleans_up_and_propagates(self, popen):
        popen.return_value = self.process([KeyboardInterrupt(), (b"", b"")])
        with self.assertRaises(KeyboardInterrupt):
            verifier.run_worker(["python"], {})
        popen.return_value.terminate.assert_called_once()

    @patch.object(verifier.subprocess, "Popen")
    def test_terminate_raising_attempts_kill_fallback(self, popen):
        process = self.process([verifier.subprocess.TimeoutExpired("worker", 1), (b"", b"")])
        process.terminate.side_effect = OSError("fixed test failure")
        popen.return_value = process
        with self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
            verifier.run_worker(["python"], {}, 1)
        process.kill.assert_called_once()

    @patch.object(verifier.subprocess, "Popen")
    def test_second_communicate_timeout_after_kill_is_worker_crash(self, popen):
        process = self.process([
            verifier.subprocess.TimeoutExpired("worker", 1),
            verifier.subprocess.TimeoutExpired("worker", 1),
            verifier.subprocess.TimeoutExpired("worker", 1),
        ])
        popen.return_value = process
        with self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
            verifier.run_worker(["python"], {}, 1)
        process.kill.assert_called_once()

    @patch.object(verifier.subprocess, "Popen")
    def test_already_exited_process_is_only_drained(self, popen):
        process = self.process([verifier.subprocess.TimeoutExpired("worker", 1), (b"", b"")])
        process.poll.return_value = 1
        popen.return_value = process
        with self.assertRaises(verifier.VerificationFailure):
            verifier.run_worker(["python"], {}, 1)
        process.terminate.assert_not_called()
        process.kill.assert_not_called()

    @patch.object(verifier.subprocess, "Popen")
    def test_keyboard_interrupt_during_cleanup_is_fixed_worker_crash(self, popen):
        process = self.process([KeyboardInterrupt(), KeyboardInterrupt(), KeyboardInterrupt()])
        popen.return_value = process
        with self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
            verifier.run_worker(["python"], {})
        process.kill.assert_called_once()

    @patch.object(verifier.subprocess, "Popen")
    def test_run_worker_never_writes_raw_streams(self, popen):
        process = self.process([(b"private stdout", b"private stderr")])
        popen.return_value = process
        with patch.object(verifier.sys.stdout, "write") as write, patch.object(verifier.sys.stderr, "write") as err_write:
            verifier.run_worker(["python"], {})
        write.assert_not_called()
        err_write.assert_not_called()


class ServerLifecycleTests(unittest.TestCase):
    def server(self, started):
        server = verifier._Server.__new__(verifier._Server)
        server.httpd = Mock()
        server.thread = Mock()
        server.thread.is_alive.return_value = False
        server._started = started
        server._closed = False
        return server

    def test_close_before_start_skips_shutdown_and_join(self):
        server = self.server(False)
        server.close()
        server.httpd.shutdown.assert_not_called()
        server.httpd.server_close.assert_called_once()
        server.thread.join.assert_not_called()
        server.close()
        server.httpd.server_close.assert_called_once()

    def test_started_close_attempts_all_cleanup_and_reports_failure(self):
        server = self.server(True)
        server.httpd.shutdown.side_effect = OSError("fixed")
        server.httpd.server_close.side_effect = OSError("fixed")
        server.thread.is_alive.return_value = True
        with self.assertRaisesRegex(verifier.VerificationFailure, "CLEANUP_FAILURE"):
            server.close()
        server.httpd.server_close.assert_called_once()
        server.thread.join.assert_called_once_with(timeout=5)


class ExitCodeContractTests(unittest.TestCase):
    def payload(self, category, ok=False):
        return json.dumps({
            "ok": ok, "category": category, "cleanup_category": None,
            "checks": {field: ok for field in verifier.CHECK_FIELDS},
            "provider_calls": 0, "http_responses": 0,
        }).encode()

    def test_safe_precondition_failures_accept_exit_two(self):
        for category in ("INVALID_ENVIRONMENT", "MISSING_NODE_OR_MCP_CLI"):
            self.assertEqual(verifier.parse_worker_result(self.payload(category), 2).category, category)

    def test_return_code_category_mismatches_are_worker_crash(self):
        cases = [
            (self.payload("INVALID_ENVIRONMENT"), 1),
            (self.payload("DOWNLOAD_MISMATCH"), 2),
            (self.payload(None), 2),
        ]
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(verifier.VerificationFailure, "WORKER_CRASH"):
                verifier.parse_worker_result(raw, code)


class ImportSafetyTests(unittest.TestCase):
    def test_import_does_not_read_environment_or_start_runtime(self):
        spec = importlib.util.spec_from_file_location("m11c_import_safety", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        with patch.dict(os.environ, {}, clear=True), patch("subprocess.Popen") as popen, patch("threading.Thread.start") as start:
            spec.loader.exec_module(module)
        popen.assert_not_called()
        start.assert_not_called()

    def test_main_dispatch_is_not_invoked_on_import(self):
        self.assertTrue(callable(verifier.main))
        self.assertTrue(callable(verifier.worker_main))


if __name__ == "__main__":
    unittest.main()
