"""Deterministic tests for transient secret storage."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from enum import Enum
import math
import unittest
from unittest.mock import patch

import browser_agent
import browser_agent.secret_store as secret_store
from browser_agent import (
    SecretBindingError,
    SecretExpiredError,
    SecretField,
    SecretNotFoundError,
    SecretReference,
    SecretStoreClosedError,
    SecretStoreError,
    SecretValidationError,
    TransientSecretStore,
)


class FakeClock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class OtherField(Enum):
    PASSWORD = "password"


class SecretStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = FakeClock()
        self.store = TransientSecretStore(ttl_seconds=5, clock=self.clock)

    async def asyncTearDown(self) -> None:
        await self.store.close()

    async def test_round_trips_and_preserves_values(self) -> None:
        values = {
            SecretField.USERNAME: " user ",
            SecretField.PASSWORD: "päss ",
        }
        reference = await self.store.put(" run ", " interaction ", values)
        self.assertEqual(
            reference.fields, (SecretField.PASSWORD, SecretField.USERNAME)
        )
        self.assertIsInstance(reference.fields, tuple)
        self.assertEqual(reference.run_id, " run ")
        self.assertEqual(await self.store.consume(reference), values)

    async def test_otp_only_round_trip(self) -> None:
        reference = await self.store.put(
            "run", "interaction", {SecretField.OTP: "012345"}
        )
        self.assertEqual(
            await self.store.consume(reference), {SecretField.OTP: "012345"}
        )

    async def test_reference_is_immutable_and_contains_metadata_only(self) -> None:
        raw = "highly-secret"
        reference = await self.store.put(
            "run", "interaction", {SecretField.PASSWORD: raw}
        )
        with self.assertRaises(FrozenInstanceError):
            reference.run_id = "other"  # type: ignore[misc]
        self.assertNotIn(raw, repr(reference))
        self.assertNotIn(raw, repr(self.store))
        self.assertNotIn(raw, repr(self.store._records[reference.secret_id]))

    async def test_invalid_values_and_identifiers(self) -> None:
        invalid_values = (
            {},
            [],
            {"password": "x"},
            {OtherField.PASSWORD: "x"},
            {1: "x"},
            {SecretField.PASSWORD: 1},
            {SecretField.PASSWORD: ""},
            {SecretField.PASSWORD: "   "},
        )
        for values in invalid_values:
            with self.subTest(values=type(values).__name__):
                with self.assertRaises(SecretValidationError):
                    await self.store.put("run", "interaction", values)  # type: ignore[arg-type]
        for run_id, interaction_id in (
            ("", "interaction"),
            (" ", "interaction"),
            (1, "interaction"),
            ("run", ""),
            ("run", " "),
            ("run", None),
        ):
            with self.assertRaises(SecretValidationError):
                await self.store.put(run_id, interaction_id, {SecretField.OTP: "x"})  # type: ignore[arg-type]
        self.assertEqual(self.store._records, {})

    async def test_validation_precedes_id_and_buffer_creation(self) -> None:
        with (
            patch.object(secret_store, "_new_secret_id") as generate,
            patch.object(secret_store, "_make_buffer") as make,
        ):
            with self.assertRaises(SecretValidationError):
                await self.store.put("run", "interaction", {"password": "x"})  # type: ignore[dict-item]
        generate.assert_not_called()
        make.assert_not_called()

    async def test_caller_mapping_is_not_retained(self) -> None:
        values = {SecretField.PASSWORD: "first"}
        reference = await self.store.put("run", "interaction", values)
        values[SecretField.PASSWORD] = "changed"
        self.assertEqual(
            await self.store.consume(reference),
            {SecretField.PASSWORD: "first"},
        )

    async def test_secrets_generation_and_collision_retry(self) -> None:
        with patch.object(
            secret_store.secrets,
            "token_urlsafe",
            side_effect=["same", "same", "different"],
        ) as token:
            first = await self.store.put("r1", "i", {SecretField.OTP: "1"})
            second = await self.store.put("r2", "i", {SecretField.OTP: "2"})
        self.assertEqual(first.secret_id, "same")
        self.assertEqual(second.secret_id, "different")
        self.assertEqual(token.call_args_list[0].args, (32,))
        self.assertEqual((await self.store.consume(first))[SecretField.OTP], "1")
        self.assertEqual((await self.store.consume(second))[SecretField.OTP], "2")

    async def test_consume_is_atomic_single_use(self) -> None:
        reference = await self.store.put(
            "run", "interaction", {SecretField.PASSWORD: "value"}
        )
        results = await asyncio.gather(
            self.store.consume(reference),
            self.store.consume(reference),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertEqual(
            sum(isinstance(item, SecretNotFoundError) for item in results), 1
        )

    async def test_full_binding_mismatch_preserves_record(self) -> None:
        reference = await self.store.put(
            "run", "interaction", {SecretField.PASSWORD: "value"}
        )
        forged = (
            replace(reference, run_id="other"),
            replace(reference, interaction_id="other"),
            replace(reference, fields=(SecretField.OTP,)),
            replace(reference, expires_at=reference.expires_at + 1),
        )
        for item in forged:
            with self.assertRaises(SecretBindingError):
                await self.store.consume(item)
        self.assertEqual(
            (await self.store.consume(reference))[SecretField.PASSWORD], "value"
        )

    async def test_unknown_and_invalid_references(self) -> None:
        reference = SecretReference(
            "missing", "run", "interaction", (SecretField.OTP,), 1.0
        )
        with self.assertRaises(SecretNotFoundError):
            await self.store.consume(reference)
        with self.assertRaises(SecretValidationError):
            await self.store.consume(object())  # type: ignore[arg-type]

    async def test_expiry_removes_and_zeroizes(self) -> None:
        reference = await self.store.put(
            "run", "interaction", {SecretField.OTP: "123"}
        )
        buffer = self.store._records[reference.secret_id].buffers[SecretField.OTP]
        self.clock.now = reference.expires_at
        with self.assertRaises(SecretExpiredError):
            await self.store.consume(reference)
        self.assertNotIn(reference.secret_id, self.store._records)
        self.assertEqual(buffer, bytearray(b"\0\0\0"))
        with self.assertRaises(SecretNotFoundError):
            await self.store.consume(reference)

    async def test_consume_discard_and_close_zeroize_all_buffers(self) -> None:
        refs = [
            await self.store.put(
                f"run-{number}",
                "interaction",
                {SecretField.USERNAME: "u", SecretField.PASSWORD: "p"},
            )
            for number in range(3)
        ]
        records = [self.store._records[item.secret_id] for item in refs]
        await self.store.consume(refs[0])
        await self.store.discard(refs[1])
        await self.store.close()
        self.assertEqual(self.store._records, {})
        for record in records:
            self.assertTrue(
                all(set(buffer) <= {0} for buffer in record.buffers.values())
            )

    async def test_cleanup_zeroizes_after_lock_exit_cancellation(self) -> None:
        for operation_name in ("discard", "discard_run", "close"):
            with self.subTest(operation=operation_name):
                store = TransientSecretStore()
                reference = await store.put(
                    "run", "interaction", {SecretField.PASSWORD: "value"}
                )
                buffer = store._records[reference.secret_id].buffers[
                    SecretField.PASSWORD
                ]
                original_lock = store._lock

                class CancellingLock:
                    async def __aenter__(self) -> None:
                        await original_lock.acquire()

                    async def __aexit__(self, *args: object) -> None:
                        original_lock.release()
                        raise asyncio.CancelledError

                store._lock = CancellingLock()  # type: ignore[assignment]
                operation = {
                    "discard": lambda: store.discard(reference),
                    "discard_run": lambda: store.discard_run("run"),
                    "close": store.close,
                }[operation_name]
                with self.assertRaises(asyncio.CancelledError):
                    await operation()
                self.assertTrue(all(byte == 0 for byte in buffer))

    async def test_discard_contract(self) -> None:
        reference = await self.store.put(
            "run", "interaction", {SecretField.OTP: "123"}
        )
        with self.assertRaises(SecretBindingError):
            await self.store.discard(replace(reference, run_id="wrong"))
        self.assertIn(reference.secret_id, self.store._records)
        await self.store.discard(reference)
        await self.store.discard(reference)
        await self.store.close()
        await self.store.discard(reference)

    async def test_discard_run_exact_and_validation(self) -> None:
        first = await self.store.put("run", "i", {SecretField.OTP: "1"})
        second = await self.store.put("run-extra", "i", {SecretField.OTP: "2"})
        await self.store.discard_run("run")
        with self.assertRaises(SecretNotFoundError):
            await self.store.consume(first)
        self.assertEqual((await self.store.consume(second))[SecretField.OTP], "2")
        await self.store.discard_run("absent")
        with self.assertRaises(SecretValidationError):
            await self.store.discard_run(" ")

    async def test_close_and_post_close_contract(self) -> None:
        reference = await self.store.put(
            "run", "interaction", {SecretField.OTP: "123"}
        )
        await self.store.close()
        await self.store.close()
        with self.assertRaises(SecretStoreClosedError):
            await self.store.put("run", "interaction", {SecretField.OTP: "x"})
        with self.assertRaises(SecretStoreClosedError):
            await self.store.consume(reference)
        await self.store.discard(reference)
        await self.store.discard_run(1)  # type: ignore[arg-type]

    async def test_failed_insertion_zeroizes_buffers(self) -> None:
        captured: list[bytearray] = []
        real_zeroize = secret_store._zeroize

        def observe(buffer: bytearray) -> None:
            captured.append(buffer)
            real_zeroize(buffer)

        with (
            patch.object(secret_store, "_new_secret_id", side_effect=RuntimeError),
            patch.object(secret_store, "_zeroize", side_effect=observe),
        ):
            with self.assertRaises(RuntimeError):
                await self.store.put(
                    "run", "interaction", {SecretField.PASSWORD: "value"}
                )
        self.assertEqual(len(captured), 1)
        self.assertTrue(all(byte == 0 for byte in captured[0]))
        self.assertEqual(self.store._records, {})

    async def test_partial_buffer_allocation_failure_zeroizes_owned_buffers(
        self,
    ) -> None:
        first_buffer = bytearray(b"value")
        with patch.object(
            secret_store,
            "_make_buffer",
            side_effect=[first_buffer, UnicodeEncodeError("utf-8", "", 0, 1, "fail")],
        ):
            with self.assertRaises(UnicodeEncodeError):
                await self.store.put(
                    "run",
                    "interaction",
                    {
                        SecretField.PASSWORD: "value",
                        SecretField.USERNAME: "other",
                    },
                )
        self.assertTrue(all(byte == 0 for byte in first_buffer))
        self.assertEqual(self.store._records, {})

    async def test_error_messages_do_not_contain_raw_secret(self) -> None:
        raw = "never-in-message"
        errors: list[BaseException] = []
        for operation in (
            self.store.put("run", "interaction", {SecretField.PASSWORD: ""}),
            self.store.consume(
                SecretReference("missing", "run", "i", (SecretField.OTP,), 1)
            ),
        ):
            try:
                await operation
            except BaseException as error:
                errors.append(error)
        self.assertTrue(all(raw not in str(error) for error in errors))

    def test_reference_constructor_validation(self) -> None:
        valid = ("id", "run", "interaction", (SecretField.OTP,), 1.0)
        invalid = (
            ("", *valid[1:]),
            ("id", "", *valid[2:]),
            ("id", "run", "", *valid[3:]),
            ("id", "run", "interaction", (), 1.0),
            ("id", "run", "interaction", [SecretField.OTP], 1.0),
            ("id", "run", "interaction", (SecretField.OTP,) * 2, 1.0),
            ("id", "run", "interaction", (SecretField.USERNAME, SecretField.PASSWORD), 1.0),
            ("id", "run", "interaction", (OtherField.PASSWORD,), 1.0),
            ("id", "run", "interaction", (SecretField.OTP,), math.inf),
        )
        for args in invalid:
            with self.assertRaises(SecretValidationError):
                SecretReference(*args)  # type: ignore[arg-type]

    def test_constructor_validation_and_clock_domain(self) -> None:
        for ttl in (0, -1, True, math.nan, math.inf):
            with self.assertRaises(SecretValidationError):
                TransientSecretStore(ttl_seconds=ttl)  # type: ignore[arg-type]
        with self.assertRaises(SecretValidationError):
            TransientSecretStore(clock=1)  # type: ignore[arg-type]

    async def test_clock_domain_and_no_background_task(self) -> None:
        before = asyncio.all_tasks()
        store = TransientSecretStore(ttl_seconds=7, clock=FakeClock(100))
        reference = await store.put("run", "i", {SecretField.OTP: "1"})
        self.assertEqual(reference.expires_at, 107)
        self.assertEqual(asyncio.all_tasks(), before)
        await store.close()

    def test_public_exports(self) -> None:
        names = {
            "SecretField",
            "SecretReference",
            "TransientSecretStore",
            "SecretStoreError",
            "SecretStoreClosedError",
            "SecretValidationError",
            "SecretNotFoundError",
            "SecretExpiredError",
            "SecretBindingError",
        }
        self.assertTrue(names <= set(browser_agent.__all__))
        self.assertTrue(all(hasattr(browser_agent, name) for name in names))
        self.assertFalse(hasattr(browser_agent, "_zeroize"))
        self.assertFalse(hasattr(browser_agent, "_SecretRecord"))
        self.assertTrue(issubclass(SecretValidationError, SecretStoreError))
