"""Tests for application-owned secret filling and sanitization."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from browser_agent import (
    SecretApplicationConstructionError,
    SecretApplicationExecutionError,
    SecretApplicationResult,
    SecretApplicationValidationError,
    SecretField,
    SecretFieldTarget,
    SecretFormApplier,
    SecretTargetSummary,
    ToolDefinition,
    ToolObservation,
)


_DEFAULT_SCHEMA = object()


def definition(schema=_DEFAULT_SCHEMA):
    return ToolDefinition("browser_fill_form", None, ({
        "type": "object",
        "properties": {"fields": {"type": "array"}},
        "required": ["fields"],
        "additionalProperties": False,
    } if schema is _DEFAULT_SCHEMA else schema))


class FakeExecutor:
    def __init__(self, result=None):
        self.result = result or ToolObservation("browser_fill_form", "success", "SENSITIVE", {"raw": True}, None)
        self.calls = []
        self.local_arguments = None

    async def invoke(self, name, arguments, *, confirmation_granted):
        self.local_arguments = arguments
        self.calls.append((name, deepcopy(arguments), confirmation_granted))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class ControlledExecutor(FakeExecutor):
    def __init__(self, result=None):
        super().__init__(result)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, name, arguments, *, confirmation_granted):
        self.local_arguments = arguments
        self.calls.append((name, deepcopy(arguments), confirmation_granted))
        self.entered.set()
        await self.release.wait()
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class TargetTests(unittest.TestCase):
    def test_target_and_summary_are_safe_and_immutable(self):
        first = SecretFieldTarget(SecretField.PASSWORD, "Password", "synthetic-ref")
        second = SecretFieldTarget(SecretField.PASSWORD, "Password", "other-ref")
        self.assertNotEqual(first, second)
        self.assertNotIn("synthetic-ref", repr(first))
        with self.assertRaises(FrozenInstanceError):
            first.name = "changed"  # type: ignore[misc]
        summary = SecretTargetSummary.from_target(first)
        self.assertFalse(hasattr(summary, "ref"))
        with self.assertRaises(FrozenInstanceError):
            summary.name = "changed"  # type: ignore[misc]

    def test_exact_types_and_non_empty_strings(self):
        for args in (("password", "P", "r"), (SecretField.PASSWORD, "", "r"), (SecretField.PASSWORD, "P", " ")):
            with self.assertRaises(SecretApplicationValidationError):
                SecretFieldTarget(*args)  # type: ignore[arg-type]


class ConstructionTests(unittest.TestCase):
    def test_valid_and_catalog_failures(self):
        SecretFormApplier(FakeExecutor(), [definition()])
        invalid = (
            [], [definition(), definition()],
            [definition({"type": "array"})],
            [definition({"type": "object", "properties": {"fields": {"type": "string"}}, "required": ["fields"], "additionalProperties": False})],
            [definition({"type": "object", "properties": {"fields": {"type": "array"}}, "required": [], "additionalProperties": False})],
            [definition({"type": "object", "properties": {"fields": {"type": "array"}}, "required": ["fields"], "additionalProperties": True})],
        )
        for definitions in invalid:
            with self.assertRaises(SecretApplicationConstructionError):
                SecretFormApplier(FakeExecutor(), definitions)

    def test_executor_sequence_items_and_all_schema_boundaries(self):
        invalid = (
            (object(), [definition()]),
            (FakeExecutor(), "definitions"),
            (FakeExecutor(), [object()]),
            (FakeExecutor(), [ToolDefinition("other", None, {})]),
            (FakeExecutor(), [definition({})]),
            (FakeExecutor(), [definition({"type": "object", "properties": {}, "required": ["fields"], "additionalProperties": False})]),
            (FakeExecutor(), [definition({"type": "object", "properties": {"fields": "array"}, "required": ["fields"], "additionalProperties": False})]),
        )
        for executor, definitions in invalid:
            with self.subTest(definitions=definitions):
                with self.assertRaises(SecretApplicationConstructionError):
                    SecretFormApplier(executor, definitions)  # type: ignore[arg-type]

    def test_schema_mutation_after_construction_has_no_effect(self):
        schema = deepcopy(definition().input_schema)
        applier = SecretFormApplier(FakeExecutor(), [definition(schema)])
        schema.clear()
        self.assertIsInstance(applier, SecretFormApplier)


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.targets = (
            SecretFieldTarget(SecretField.PASSWORD, "Password", "p-ref"),
            SecretFieldTarget(SecretField.USERNAME, "Email", "u-ref"),
        )
        self.values = {SecretField.USERNAME: "synthetic-user", SecretField.PASSWORD: "synthetic-pass"}

    async def test_exact_single_invocation_and_cleanup(self):
        executor = FakeExecutor()
        result = await SecretFormApplier(executor, [definition()]).apply(self.targets, self.values)
        self.assertEqual(result, SecretApplicationResult((SecretField.PASSWORD, SecretField.USERNAME)))
        self.assertEqual(result.message, "Requested secret fields were applied by the application.")
        self.assertEqual(executor.calls, [("browser_fill_form", {"fields": [
            {"name": "Password", "type": "textbox", "ref": "p-ref", "value": "synthetic-pass"},
            {"name": "Email", "type": "textbox", "ref": "u-ref", "value": "synthetic-user"},
        ]}, True)])
        self.assertEqual(executor.local_arguments, {})
        self.assertEqual(self.values[SecretField.PASSWORD], "synthetic-pass")
        self.assertNotIn("synthetic-pass", repr(result))
        self.assertNotIn("p-ref", repr(result))

    async def test_strict_validation(self):
        applier = SecretFormApplier(FakeExecutor(), [definition()])
        cases = (
            (list(self.targets), self.values), ((), self.values),
            (tuple(reversed(self.targets)), self.values),
            ((self.targets[0], self.targets[0]), {SecretField.PASSWORD: "x"}),
            ((self.targets[0], SecretFieldTarget(SecretField.USERNAME, "U", "p-ref")), self.values),
            (self.targets, {SecretField.PASSWORD: "x"}),
            (self.targets, {**self.values, SecretField.OTP: "x"}),
            (self.targets, {"password": "x"}),
            (self.targets, {SecretField.PASSWORD: " " , SecretField.USERNAME: "x"}),
        )
        for targets, values in cases:
            with self.assertRaises(SecretApplicationValidationError):
                await applier.apply(targets, values)  # type: ignore[arg-type]

    async def test_dependency_failures_are_fixed_and_unchained(self):
        for failure in (
            ToolObservation("browser_fill_form", "mcp_error", "raw", None, "secret"),
            ToolObservation("browser_fill_form", "transport_error", "raw", None, "secret"),
            object(), RuntimeError("synthetic-pass p-ref"),
        ):
            with self.assertRaisesRegex(SecretApplicationExecutionError, "^Secret application failed safely\\.$") as raised:
                await SecretFormApplier(FakeExecutor(failure), [definition()]).apply(self.targets, self.values)
            self.assertIsNone(raised.exception.__cause__)
            self.assertNotIn("synthetic-pass", str(raised.exception))

    async def test_executor_safe_error_is_replaced_and_sanitized(self):
        failure = SecretApplicationExecutionError("synthetic-pass p-ref")
        with self.assertRaisesRegex(
            SecretApplicationExecutionError,
            "^Secret application failed safely\\.$",
        ) as raised:
            await SecretFormApplier(FakeExecutor(failure), [definition()]).apply(
                self.targets, self.values
            )
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertNotIn("synthetic-pass", str(raised.exception))
        self.assertNotIn("p-ref", str(raised.exception))

    async def test_cancelled_error_propagates(self):
        with self.assertRaises(asyncio.CancelledError):
            await SecretFormApplier(FakeExecutor(asyncio.CancelledError()), [definition()]).apply(self.targets, self.values)

    async def test_owned_arguments_are_cleared_after_ordinary_executor_failure(self):
        executor = FakeExecutor(RuntimeError("synthetic-pass p-ref"))
        original = dict(self.values)
        with self.assertRaises(SecretApplicationExecutionError):
            await SecretFormApplier(executor, [definition()]).apply(
                self.targets, self.values
            )
        self.assertEqual(self.values, original)
        self.assertEqual(executor.local_arguments, {})
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][0], "browser_fill_form")
        self.assertNotIn("click", repr(executor.calls).lower())
        self.assertNotIn("submit", repr(executor.calls).lower())
        self.assertNotIn("enter", repr(executor.calls).lower())
        # The executor's defensive call copy is deliberately retained by this
        # fake; the application claims cleanup only for its owned containers.
        self.assertIn("synthetic-pass", repr(executor.calls))

    async def test_owned_arguments_are_cleared_after_cancellation(self):
        executor = ControlledExecutor()
        original = dict(self.values)
        operation = asyncio.create_task(
            SecretFormApplier(executor, [definition()]).apply(
                self.targets, self.values
            )
        )
        await executor.entered.wait()
        operation.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await operation
        self.assertEqual(self.values, original)
        self.assertEqual(executor.local_arguments, {})
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(executor.calls[0][0], "browser_fill_form")
        self.assertNotIn("click", repr(executor.calls).lower())
        self.assertNotIn("submit", repr(executor.calls).lower())
        self.assertNotIn("enter", repr(executor.calls).lower())


if __name__ == "__main__":
    unittest.main()
