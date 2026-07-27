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


if __name__ == "__main__":
    unittest.main()
