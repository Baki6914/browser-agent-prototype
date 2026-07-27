"""Unit tests for the connectivity spike's dependency-free pure helpers."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "mcp_connectivity_spike.py"
)
SPEC = importlib.util.spec_from_file_location("mcp_connectivity_spike", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
spike = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spike)


class SchemaSummaryTests(unittest.TestCase):
    def test_summarizes_and_sorts_schema_fields(self) -> None:
        schema = {
            "required": ["url"],
            "properties": {
                "wait": {"type": "boolean", "default": False},
                "url": {
                    "type": "string",
                    "description": "Destination",
                    "enum": ["https://example.com"],
                },
            },
            "type": "object",
            "additionalProperties": False,
        }

        self.assertEqual(
            spike.summarize_schema(schema),
            {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Destination",
                        "enum": ["https://example.com"],
                    },
                    "wait": {"type": "boolean", "default": False},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        )


class InventoryTests(unittest.TestCase):
    def test_builds_sorted_json_compatible_inventory(self) -> None:
        tools = [
            SimpleNamespace(
                name="browser_snapshot",
                description=None,
                inputSchema={"type": "object"},
            ),
            {
                "name": "browser_navigate",
                "description": "Navigate",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
        ]

        inventory = spike.build_inventory(tools)

        self.assertEqual(
            [tool["name"] for tool in inventory["tools"]],
            ["browser_navigate", "browser_snapshot"],
        )
        self.assertEqual(inventory["tool_count"], 2)
        self.assertEqual(
            json.loads(json.dumps(inventory, sort_keys=True)),
            inventory,
        )


class RequiredToolTests(unittest.TestCase):
    def test_required_tools_pass_when_present(self) -> None:
        spike.validate_required_tools(
            ["browser_snapshot", "browser_navigate", "browser_close"]
        )

    def test_required_tools_report_all_missing_names(self) -> None:
        with self.assertRaisesRegex(
            spike.SpikeError, "browser_navigate, browser_snapshot"
        ):
            spike.validate_required_tools(["browser_close"])


class ResultTextTests(unittest.TestCase):
    def test_extracts_text_and_deterministic_structured_content(self) -> None:
        result = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="  Example Domain  "),
                SimpleNamespace(type="image", data="ignored"),
            ],
            structuredContent={"z": 1, "a": "value"},
        )

        self.assertEqual(
            spike.extract_result_text(result),
            'Example Domain\n{"a": "value", "z": 1}',
        )

    def test_usable_result_rejects_empty_content(self) -> None:
        with self.assertRaisesRegex(spike.SpikeError, "no usable content"):
            spike.ensure_usable_result("browser_snapshot", {"content": []})


class FailurePreservationTests(unittest.TestCase):
    def test_primary_operation_error_remains_reported_when_shutdown_succeeds(
        self,
    ) -> None:
        primary_error = spike.SpikeError("snapshot failed")

        with self.assertRaises(spike.SpikeError) as raised:
            spike.raise_preserving_failures(primary_error, None)

        self.assertIs(raised.exception, primary_error)

    def test_shutdown_error_is_reported_when_primary_operation_succeeds(self) -> None:
        shutdown_error = spike.SpikeError("browser_close failed")

        with self.assertRaises(spike.SpikeError) as raised:
            spike.raise_preserving_failures(None, shutdown_error)

        self.assertIs(raised.exception, shutdown_error)

    def test_both_errors_are_identified_and_reported(self) -> None:
        primary_error = spike.SpikeError("snapshot failed")
        shutdown_error = spike.SpikeError("browser_close failed")

        with self.assertRaises(spike.SpikeError) as raised:
            spike.raise_preserving_failures(primary_error, shutdown_error)

        message = str(raised.exception)
        self.assertIn("primary operation failure: snapshot failed", message)
        self.assertIn(
            "additional shutdown failure: browser_close failed",
            message,
        )

    def test_direct_spike_error_remains_unchanged(self) -> None:
        direct_error = spike.SpikeError("invocation error: snapshot failed")

        with self.assertRaises(spike.SpikeError) as raised:
            spike.raise_preserving_failures(direct_error, None)

        self.assertIs(raised.exception, direct_error)


class ExceptionGroupVisibilityTests(unittest.TestCase):
    def test_nested_single_leaf_exposes_spike_error_message(self) -> None:
        nested = ExceptionGroup(
            "outer task group",
            [
                ExceptionGroup(
                    "inner task group",
                    [spike.SpikeError("invocation error: browser_navigate failed")],
                )
            ],
        )

        self.assertEqual(
            spike.extract_exception_messages(nested),
            ["invocation error: browser_navigate failed"],
        )

    def test_multiple_nested_leaf_errors_are_included_in_order(self) -> None:
        nested = ExceptionGroup(
            "outer task group",
            [
                spike.SpikeError("primary operation failure: navigate failed"),
                ExceptionGroup(
                    "shutdown task group",
                    [
                        spike.SpikeError(
                            "additional shutdown failure: browser_close failed"
                        )
                    ],
                ),
            ],
        )

        self.assertEqual(
            spike.extract_exception_messages(nested),
            [
                "primary operation failure: navigate failed",
                "additional shutdown failure: browser_close failed",
            ],
        )

    def test_duplicate_leaf_messages_are_not_repeated(self) -> None:
        nested = ExceptionGroup(
            "outer task group",
            [
                spike.SpikeError("browser unavailable"),
                ExceptionGroup(
                    "inner task group",
                    [spike.SpikeError("browser unavailable")],
                ),
            ],
        )

        self.assertEqual(
            spike.extract_exception_messages(nested),
            ["browser unavailable"],
        )


if __name__ == "__main__":
    unittest.main()
