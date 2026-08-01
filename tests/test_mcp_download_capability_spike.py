"""Focused offline tests for the Milestone 10A download spike helpers."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mcp_download_capability_spike.py"
SPEC = importlib.util.spec_from_file_location("mcp_download_capability_spike", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
spike = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = spike
SPEC.loader.exec_module(spike)


class DownloadSpikeHelperTests(unittest.TestCase):
    def test_safe_basename_accepts_expected_name(self) -> None:
        self.assertEqual(
            spike.validate_safe_basename(spike.SUGGESTED_FILENAME),
            spike.SUGGESTED_FILENAME,
        )

    def test_safe_basename_rejects_traversal_and_separators(self) -> None:
        for value in ("..", "../file.txt", "folder/file.txt", "folder\\file.txt"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                spike.validate_safe_basename(value)

    def test_verify_download_checks_exact_content_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / spike.SUGGESTED_FILENAME
            path.write_bytes(spike.EXPECTED_PAYLOAD)
            self.assertEqual(
                spike.verify_download(path, directory),
                (spike.SUGGESTED_FILENAME, len(spike.EXPECTED_PAYLOAD)),
            )
            path.write_bytes(spike.EXPECTED_PAYLOAD + b"!")
            with self.assertRaisesRegex(spike.SpikeError, "size mismatch"):
                spike.verify_download(path, directory)
            path.write_bytes(b"X" * len(spike.EXPECTED_PAYLOAD))
            with self.assertRaisesRegex(spike.SpikeError, "content did not match"):
                spike.verify_download(path, directory)

    def test_scan_distinguishes_completed_and_partial_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "final.txt").write_bytes(b"done")
            (directory / "pending.crdownload").write_bytes(b"partial")
            scan = spike.scan_download_directory(directory)
            self.assertEqual([path.name for path in scan.completed], ["final.txt"])
            self.assertEqual(
                [path.name for path in scan.partial], ["pending.crdownload"]
            )

    def test_single_completed_selection_is_deterministic(self) -> None:
        path = Path("only.txt")
        scan = spike.DirectoryScan((path,), ())
        self.assertEqual(spike.select_single_completed(scan), path)

    def test_multiple_completed_files_are_rejected(self) -> None:
        scan = spike.DirectoryScan((Path("a.txt"), Path("b.txt")), ())
        with self.assertRaisesRegex(spike.SpikeError, "multiple completed"):
            spike.select_single_completed(scan)

    def test_poll_requires_two_stable_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            completed = directory / "done.txt"
            completed.write_bytes(b"done")
            scans = iter(
                [
                    spike.DirectoryScan((), (directory / "done.txt.part",)),
                    spike.DirectoryScan((completed,), ()),
                    spike.DirectoryScan((completed,), ()),
                ]
            )
            ticks = iter((0.0, 0.0, 0.1, 0.2, 0.3))
            result = spike.poll_for_stable_download(
                directory,
                expected_filename="done.txt",
                timeout=1.0,
                interval=0.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
                scan=lambda _directory: next(scans),
            )
            self.assertEqual(result.file, completed)
            self.assertTrue(result.partial_seen)
            self.assertFalse(result.partial_remaining)

    def test_expected_download_is_selected_with_page_yaml_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = directory / spike.SUGGESTED_FILENAME
            expected.write_bytes(spike.EXPECTED_PAYLOAD)
            page = directory / "page-2026-08-01T11-49-04-857Z.yml"
            page.write_text("snapshot", encoding="utf-8")
            scans = iter(
                [
                    spike.DirectoryScan((expected, page), ()),
                    spike.DirectoryScan((expected, page), ()),
                ]
            )
            ticks = iter((0.0, 0.0, 0.1))
            result = spike.poll_for_stable_download(
                directory,
                timeout=1.0,
                interval=0.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
                scan=lambda _directory: next(scans),
            )
            self.assertEqual(result.file, expected)

    def test_relevant_partial_prevents_expected_file_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = directory / spike.SUGGESTED_FILENAME
            expected.write_bytes(spike.EXPECTED_PAYLOAD)
            partial = directory / f"{spike.SUGGESTED_FILENAME}.part"
            scans = iter(
                [
                    spike.DirectoryScan((expected,), (partial,)),
                    spike.DirectoryScan((expected,), (partial,)),
                    spike.DirectoryScan((expected,), (partial,)),
                ]
            )
            ticks = iter((0.0, 0.0, 0.1, 0.2, 0.3))
            result = spike.poll_for_stable_download(
                directory,
                timeout=0.15,
                interval=0.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
                scan=lambda _directory: next(scans),
            )
            self.assertIsNone(result.file)
            self.assertTrue(result.partial_seen)
            self.assertTrue(result.partial_remaining)

    def test_absent_expected_filename_is_unsupported(self) -> None:
        ticks = iter((0.0, 0.0, 1.0))
        result = spike.poll_for_stable_download(
            Path("unused"),
            timeout=0.5,
            interval=0.0,
            clock=lambda: next(ticks),
            sleep=lambda _seconds: None,
            scan=lambda _directory: spike.DirectoryScan((Path("page.yml"),), ()),
        )
        self.assertEqual(spike.classify_wait_result(result), "UNSUPPORTED")

    def test_selected_expected_file_still_requires_exact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            expected = directory / spike.SUGGESTED_FILENAME
            expected.write_bytes(b"X" * len(spike.EXPECTED_PAYLOAD))
            ticks = iter((0.0, 0.0, 0.1))
            result = spike.poll_for_stable_download(
                directory,
                timeout=1.0,
                interval=0.0,
                clock=lambda: next(ticks),
                sleep=lambda _seconds: None,
            )
            self.assertEqual(result.file, expected)
            with self.assertRaisesRegex(spike.SpikeError, "content did not match"):
                spike.verify_download(result.file, directory)

    def test_timeout_classifies_as_unsupported(self) -> None:
        ticks = iter((0.0, 0.0, 1.0, 2.0))
        result = spike.poll_for_stable_download(
            Path("unused"),
            timeout=0.5,
            interval=0.0,
            clock=lambda: next(ticks),
            sleep=lambda _seconds: None,
            scan=lambda _directory: spike.DirectoryScan(
                (), (Path(f"{spike.SUGGESTED_FILENAME}.tmp"),)
            ),
        )
        self.assertEqual(spike.classify_wait_result(result), "UNSUPPORTED")
        self.assertTrue(result.partial_remaining)

    def test_chromium_permission_failure_is_blocked(self) -> None:
        detail = "sandbox_host_linux.cc shutdown: Operation not permitted"
        self.assertTrue(spike.is_browser_permission_blocker(detail))

    def test_unrelated_error_is_not_blocked(self) -> None:
        self.assertFalse(spike.is_browser_permission_blocker("invalid JSON config"))

    def test_invalid_tool_arguments_is_an_implementation_error(self) -> None:
        report = spike.SpikeReport(mcp_startup="initialized", stage="browser_click")
        spike.classify_failure(
            report,
            "InvalidToolArgumentsError: arguments.target: required property is missing",
        )
        self.assertEqual(report.result, "ERROR")
        self.assertEqual(report.stage, "browser_click")
        self.assertIn("spike implementation/validation error", report.detail)

    def test_later_failure_preserves_existing_report_state(self) -> None:
        report = spike.SpikeReport(
            mcp_startup="initialized",
            navigation="success",
            stage="browser_snapshot",
        )
        spike.classify_failure(report, "RuntimeError: snapshot validation failed")
        self.assertEqual(report.mcp_startup, "initialized")
        self.assertEqual(report.navigation, "success")
        self.assertEqual(report.stage, "browser_snapshot")

    def test_browser_click_arguments_match_installed_schema(self) -> None:
        self.assertEqual(
            spike.browser_click_arguments("e7"),
            {"element": "Download demo file", "target": "e7"},
        )

    def test_subprocess_permission_failure_remains_blocked(self) -> None:
        report = spike.SpikeReport(stage="MCP subprocess startup")
        spike.classify_failure(
            report, "PermissionError: [Errno 1] Operation not permitted"
        )
        self.assertEqual(report.result, "BLOCKED")
        self.assertEqual(report.stage, "MCP subprocess startup")

    def test_report_redacts_absolute_temporary_paths(self) -> None:
        private = "/tmp/m10-download-secret"
        report = spike.SpikeReport(
            result="UNSUPPORTED",
            detail=f"no file at {private}/item.txt",
            private_paths=[private],
        )
        rendered = spike.render_report(report)
        self.assertNotIn(private, rendered)
        self.assertIn("<temporary-path>/item.txt", rendered)


if __name__ == "__main__":
    unittest.main()
