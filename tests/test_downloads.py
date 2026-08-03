"""Focused offline tests for the controlled-download boundary."""

from __future__ import annotations

import asyncio
from dataclasses import fields
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_agent import (
    DownloadError,
    DownloadFile,
    DownloadMetadata,
    DownloadNotFoundError,
    DownloadTrackingError,
    DownloadTrackingExecutor,
    DownloadValidationError,
    RunDownloadStore,
    ToolObservation,
    extract_completed_download_paths,
)
from browser_agent.downloads import _completed_download_basename


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name) / "downloads"
        self.store = RunDownloadStore(self.base)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def write(self, name: str, payload: bytes = b"payload") -> Path:
        path = self.store.output_directory / name
        path.write_bytes(payload)
        return path

    def test_construction_is_isolated_beneath_base(self) -> None:
        output = self.store.output_directory.resolve()
        self.assertTrue(output.is_dir())
        self.assertTrue(output.is_relative_to(self.base.resolve()))
        self.assertNotEqual(output.parent, self.base.resolve())

    def test_configured_base_symlink_is_rejected(self) -> None:
        target = Path(self.temp.name) / "target"
        target.mkdir()
        link = Path(self.temp.name) / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(DownloadError):
            RunDownloadStore(link)

    def test_registration_returns_safe_exact_metadata(self) -> None:
        self.write("report.pdf", b"abc")
        metadata = self.store.record_completed("report.pdf")
        self.assertEqual((metadata.filename, metadata.size), ("report.pdf", 3))
        self.assertEqual([item.name for item in fields(metadata)], ["file_id", "filename", "size"])
        self.assertNotIn(str(self.base), repr(metadata))
        self.assertEqual(self.store.list_metadata(), (metadata,))

    def test_invalid_names_are_rejected(self) -> None:
        for value in ("/tmp/report.pdf", "../report.pdf", "dir/report.pdf", "dir\\report.pdf", ""):
            with self.subTest(value=value), self.assertRaises(DownloadValidationError):
                self.store.record_completed(value)

    def test_missing_directory_symlink_and_partial_are_rejected(self) -> None:
        with self.assertRaises(DownloadValidationError):
            self.store.record_completed("missing.pdf")
        (self.store.output_directory / "folder").mkdir()
        with self.assertRaises(DownloadValidationError):
            self.store.record_completed("folder")
        target = Path(self.temp.name) / "outside"
        target.write_bytes(b"secret")
        (self.store.output_directory / "linked.pdf").symlink_to(target)
        with self.assertRaises(DownloadValidationError):
            self.store.record_completed("linked.pdf")
        for name in ("pending.crdownload", "pending.part", "pending.tmp"):
            self.write(name)
            with self.assertRaises(DownloadValidationError):
                self.store.record_completed(name)

    def test_duplicate_names_keep_independent_bytes(self) -> None:
        self.write("report.pdf", b"first")
        first = self.store.record_completed("report.pdf")
        self.write("report.pdf", b"second")
        second = self.store.record_completed("report.pdf")
        self.assertNotEqual(first.file_id, second.file_id)
        self.assertEqual(self.store.get_file(first.file_id).path.read_bytes(), b"first")
        self.assertEqual(self.store.get_file(second.file_id).path.read_bytes(), b"second")

    def test_lookup_returns_private_file_and_unknown_is_not_found(self) -> None:
        self.write("file.txt")
        metadata = self.store.record_completed("file.txt")
        found = self.store.get_file(metadata.file_id)
        self.assertIsInstance(found, DownloadFile)
        self.assertEqual(found.metadata, metadata)
        self.assertNotIn(str(found.path), repr(found))
        with self.assertRaises(DownloadNotFoundError):
            self.store.get_file("unknown")

    def test_finalize_removes_all_incoming_and_retains_managed(self) -> None:
        self.write("kept.txt", b"kept")
        metadata = self.store.record_completed("kept.txt")
        for name in ("page-now.yml", "pending.crdownload", "other.bin"):
            self.write(name)
        nested = self.store.output_directory / "nested"
        nested.mkdir()
        (nested / "artifact").write_text("x")
        self.store.finalize()
        self.store.finalize()
        self.assertEqual(tuple(self.store.output_directory.iterdir()), ())
        self.assertEqual(self.store.get_file(metadata.file_id).path.read_bytes(), b"kept")

    def test_close_removes_run_root_and_closed_methods_are_safe(self) -> None:
        run_root = self.store.output_directory.parent
        self.store.close()
        self.store.close()
        self.assertFalse(run_root.exists())
        self.assertEqual(self.store.list_metadata(), ())
        with self.assertRaises(DownloadNotFoundError):
            self.store.get_file("file")
        with self.assertRaises(DownloadError):
            self.store.record_completed("file")
        with self.assertRaises(DownloadError):
            self.store.finalize()

    def test_metadata_rejects_bool_size(self) -> None:
        with self.assertRaises(DownloadValidationError):
            DownloadMetadata("id", "file", True)  # type: ignore[arg-type]


class ParserTests(unittest.TestCase):
    def test_completed_download_basename_rejects_non_string(self) -> None:
        with self.assertRaises(DownloadValidationError):
            _completed_download_basename(None)

    def test_plain_basename_completed_lines_are_ordered_and_deduplicated(self) -> None:
        first = '- Downloaded file report.pdf to "report.pdf"'
        second = '- Downloaded file data.csv to "data.csv"'
        self.assertEqual(
            extract_completed_download_paths(f"{first}\n{second}\n{first}"),
            ("report.pdf", "data.csv"),
        )

    def test_real_mcp_path_returns_only_final_basename(self) -> None:
        text = (
            '- Downloaded file m11c-report.txt to '
            '"some/run/scoped/path/incoming/m11c-report.txt"'
        )
        self.assertEqual(
            extract_completed_download_paths(text),
            ("m11c-report.txt",),
        )

    def test_paths_resolving_to_same_basename_are_deduplicated(self) -> None:
        first = '- Downloaded file report.pdf to "first/incoming/report.pdf"'
        second = '- Downloaded file report.pdf to "second/incoming/report.pdf"'
        self.assertEqual(
            extract_completed_download_paths(f"{first}\n{second}"),
            ("report.pdf",),
        )

    def test_non_completion_output_is_ignored(self) -> None:
        for text in (
            "- Downloading file report.pdf ...",
            "arbitrary page Downloaded file text",
            '- Snapshot: [page](page-now.yml)',
            '- Screenshot: [image](screen.png)',
            "https://example.com/report.pdf",
        ):
            with self.subTest(text=text):
                self.assertEqual(extract_completed_download_paths(text), ())

    def test_invalid_claimed_completion_paths_raise_safe_error(self) -> None:
        invalid_values = (
            "",
            "path/",
            ".",
            "..",
            "path/.",
            "path/..",
            "path/nu\x00l",
            r"path\file.txt",
            "C:/path/file.txt",
            r"\\server\share\file.txt",
        )
        for value in invalid_values:
            text = f'- Downloaded file x to "{value}"'
            with self.subTest(value=value), self.assertRaisesRegex(
                DownloadTrackingError,
                "^completed download message is invalid$",
            ):
                extract_completed_download_paths(text)


class FakeExecutor:
    def __init__(self, observation: object) -> None:
        self.observation = observation
        self.calls: list[tuple[object, ...]] = []

    async def invoke(self, tool_name, arguments, *, confirmation_granted=False):
        self.calls.append(("invoke", tool_name, arguments, confirmation_granted))
        return self.observation

    async def invoke_internal(self, tool_name, arguments):
        self.calls.append(("internal", tool_name, arguments))
        return self.observation


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunDownloadStore(Path(self.temp.name))

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    async def test_success_tracks_once_and_returns_canonical_evidence(self) -> None:
        (self.store.output_directory / "report.pdf").write_bytes(b"abc")
        observation = ToolObservation("browser_click", "success", '- Downloaded file report.pdf to "report.pdf"', None, None)
        executor = FakeExecutor(observation)
        wrapper = DownloadTrackingExecutor(executor, self.store)
        arguments = {"target": "ref"}
        returned = await wrapper.invoke("browser_click", arguments, confirmation_granted=True)
        self.assertIsNot(returned, observation)
        self.assertEqual(returned.status, observation.status)
        self.assertEqual(returned.error, observation.error)
        self.assertEqual(
            returned.text,
            '- Downloaded file report.pdf to "report.pdf"\n'
            'Application recorded download: report.pdf (3 bytes)',
        )
        self.assertEqual(executor.calls, [("invoke", "browser_click", arguments, True)])
        metadata = self.store.list_metadata()[0]
        self.assertEqual(metadata.filename, "report.pdf")
        self.assertNotIn(str(self.store.output_directory), returned.text)
        self.assertNotIn(metadata.file_id, returned.text)

    async def test_reported_prefix_is_not_used_for_filesystem_access(self) -> None:
        external = Path(self.temp.name) / "claimed" / "incoming" / "report.pdf"
        external.parent.mkdir(parents=True)
        external.write_bytes(b"external")
        observation = ToolObservation(
            "browser_click",
            "success",
            f'- Downloaded file report.pdf to "{external.as_posix()}"',
            None,
            None,
        )
        wrapper = DownloadTrackingExecutor(FakeExecutor(observation), self.store)

        with self.assertRaisesRegex(
            DownloadTrackingError,
            "^completed download tracking failed safely$",
        ):
            await wrapper.invoke("browser_click", {})

        self.assertEqual(external.read_bytes(), b"external")
        self.assertEqual(self.store.list_metadata(), ())

    async def test_real_mcp_path_tracks_run_scoped_incoming_basename(self) -> None:
        incoming = self.store.output_directory / "report.pdf"
        incoming.write_bytes(b"run-scoped")
        observation = ToolObservation(
            "browser_click",
            "success",
            '- Downloaded file report.pdf to "untrusted/prefix/incoming/report.pdf"',
            None,
            None,
        )
        wrapper = DownloadTrackingExecutor(FakeExecutor(observation), self.store)

        await wrapper.invoke("browser_click", {})

        metadata = self.store.list_metadata()[0]
        self.assertEqual((metadata.filename, metadata.size), ("report.pdf", 10))
        self.assertEqual(
            self.store.get_file(metadata.file_id).path.read_bytes(),
            b"run-scoped",
        )

    async def test_success_without_completion_does_not_track(self) -> None:
        observation = ToolObservation("browser_snapshot", "success", "plain", None, None)
        wrapper = DownloadTrackingExecutor(FakeExecutor(observation), self.store)
        self.assertIs(await wrapper.invoke("browser_snapshot", {}), observation)
        self.assertEqual(self.store.list_metadata(), ())

    async def test_multiple_downloads_append_ordered_canonical_markers(self) -> None:
        (self.store.output_directory / "first.txt").write_bytes(b"one")
        (self.store.output_directory / "second.bin").write_bytes(b"12345")
        raw = (
            '- Downloaded file first.txt to "first.txt"\n'
            '- Downloaded file second.bin to "second.bin"'
        )
        observation = ToolObservation("browser_click", "success", raw, None, None)
        returned = await DownloadTrackingExecutor(
            FakeExecutor(observation), self.store
        ).invoke("browser_click", {})

        self.assertEqual(
            returned.text.splitlines()[-2:],
            [
                "Application recorded download: first.txt (3 bytes)",
                "Application recorded download: second.bin (5 bytes)",
            ],
        )
        self.assertEqual(
            [item.filename for item in self.store.list_metadata()],
            ["first.txt", "second.bin"],
        )

    async def test_error_observations_do_not_track(self) -> None:
        for status in ("mcp_error", "transport_error"):
            observation = ToolObservation("browser_click", status, '- Downloaded file x to "x"', None, "error")
            wrapper = DownloadTrackingExecutor(FakeExecutor(observation), self.store)
            self.assertIs(await wrapper.invoke("browser_click", {}), observation)

    async def test_parse_or_registration_failure_is_safe(self) -> None:
        for text in ('- Downloaded file x to "../x"', '- Downloaded file x to "missing"'):
            observation = ToolObservation("browser_click", "success", text, None, None)
            wrapper = DownloadTrackingExecutor(FakeExecutor(observation), self.store)
            with self.assertRaisesRegex(DownloadTrackingError, "download"):
                await wrapper.invoke("browser_click", {})

    async def test_invalid_observation_is_rejected(self) -> None:
        wrapper = DownloadTrackingExecutor(FakeExecutor(object()), self.store)
        with self.assertRaises(DownloadTrackingError):
            await wrapper.invoke("browser_click", {})

    async def test_internal_delegates_once_without_tracking(self) -> None:
        observation = ToolObservation("browser_close", "success", '- Downloaded file x to "x"', None, None)
        executor = FakeExecutor(observation)
        wrapper = DownloadTrackingExecutor(executor, self.store)
        self.assertIs(await wrapper.invoke_internal("browser_close", {}), observation)
        self.assertEqual(executor.calls, [("internal", "browser_close", {})])
        self.assertEqual(self.store.list_metadata(), ())


if __name__ == "__main__":
    unittest.main()
