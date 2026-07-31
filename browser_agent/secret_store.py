"""Process-local transient storage for secret values."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
import secrets
import time


class SecretStoreError(Exception):
    """Base error for transient secret storage."""


class SecretStoreClosedError(SecretStoreError):
    """Raised when an operation requires an open store."""


class SecretValidationError(SecretStoreError):
    """Raised when public input is invalid."""


class SecretNotFoundError(SecretStoreError):
    """Raised when a referenced secret does not exist."""


class SecretExpiredError(SecretStoreError):
    """Raised when a referenced secret has expired."""


class SecretBindingError(SecretStoreError):
    """Raised when reference metadata does not match its stored record."""


class SecretField(Enum):
    """Approved secret fields."""

    USERNAME = "username"
    PASSWORD = "password"
    OTP = "otp"


def _valid_text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


@dataclass(frozen=True, slots=True)
class SecretReference:
    """Immutable, process-local metadata used to retrieve a secret once."""

    secret_id: str
    run_id: str
    interaction_id: str
    fields: tuple[SecretField, ...]
    expires_at: float

    def __post_init__(self) -> None:
        if not _valid_text(self.secret_id):
            raise SecretValidationError("Secret reference metadata is invalid.")
        if not _valid_text(self.run_id):
            raise SecretValidationError("Secret reference metadata is invalid.")
        if not _valid_text(self.interaction_id):
            raise SecretValidationError("Secret reference metadata is invalid.")
        if type(self.fields) is not tuple or not self.fields:
            raise SecretValidationError("Secret reference metadata is invalid.")
        if any(type(field) is not SecretField for field in self.fields):
            raise SecretValidationError("Secret reference metadata is invalid.")
        expected = tuple(sorted(set(self.fields), key=lambda field: field.value))
        if self.fields != expected:
            raise SecretValidationError("Secret reference metadata is invalid.")
        if (
            type(self.expires_at) not in (int, float)
            or not math.isfinite(self.expires_at)
        ):
            raise SecretValidationError("Secret reference metadata is invalid.")


class _SecretRecord:
    __slots__ = (
        "secret_id",
        "run_id",
        "interaction_id",
        "fields",
        "expires_at",
        "buffers",
    )

    def __init__(
        self,
        secret_id: str,
        run_id: str,
        interaction_id: str,
        fields: tuple[SecretField, ...],
        expires_at: float,
        buffers: dict[SecretField, bytearray],
    ) -> None:
        self.secret_id = secret_id
        self.run_id = run_id
        self.interaction_id = interaction_id
        self.fields = fields
        self.expires_at = expires_at
        self.buffers = buffers

    def __repr__(self) -> str:
        return (
            f"_SecretRecord(secret_id={self.secret_id!r}, "
            f"run_id={self.run_id!r}, interaction_id={self.interaction_id!r}, "
            f"fields={self.fields!r}, expires_at={self.expires_at!r})"
        )


def _zeroize(buffer: bytearray) -> None:
    buffer[:] = b"\x00" * len(buffer)


def _zeroize_record(record: _SecretRecord) -> None:
    for buffer in record.buffers.values():
        _zeroize(buffer)


def _make_buffer(value: str) -> bytearray:
    return bytearray(value.encode("utf-8"))


def _new_secret_id() -> str:
    return secrets.token_urlsafe(32)


def _validate_identifier(value: object, label: str) -> str:
    if not _valid_text(value):
        raise SecretValidationError(f"{label} must be a non-empty string.")
    return value


def _validate_values(values: object) -> tuple[tuple[SecretField, str], ...]:
    if not isinstance(values, Mapping) or not values:
        raise SecretValidationError("Secret values must be a non-empty mapping.")
    validated: list[tuple[SecretField, str]] = []
    for field, value in values.items():
        if type(field) is not SecretField:
            raise SecretValidationError("Secret field key is invalid.")
        if not _valid_text(value):
            raise SecretValidationError(
                "Secret field value must be a non-empty string."
            )
        validated.append((field, value))
    return tuple(sorted(validated, key=lambda item: item[0].value))


class TransientSecretStore:
    """One-time, in-memory store for store-owned mutable secret buffers."""

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            type(ttl_seconds) not in (int, float)
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise SecretValidationError("ttl_seconds must be finite and positive.")
        if not callable(clock):
            raise SecretValidationError("clock must be callable.")
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._records: dict[str, _SecretRecord] = {}
        self._closed = False

    def __repr__(self) -> str:
        return "TransientSecretStore()"

    async def put(
        self,
        run_id: str,
        interaction_id: str,
        values: Mapping[SecretField, str],
    ) -> SecretReference:
        checked_run_id = _validate_identifier(run_id, "run_id")
        checked_interaction_id = _validate_identifier(
            interaction_id, "interaction_id"
        )
        checked_values = _validate_values(values)

        buffers: dict[SecretField, bytearray] = {}
        transferred = False
        try:
            for field, value in checked_values:
                buffers[field] = _make_buffer(value)
            expires_at = self._clock() + self._ttl_seconds
            fields = tuple(field for field, _ in checked_values)
            async with self._lock:
                if self._closed:
                    raise SecretStoreClosedError("Secret store is closed.")
                secret_id = _new_secret_id()
                while secret_id in self._records:
                    secret_id = _new_secret_id()
                reference = SecretReference(
                    secret_id,
                    checked_run_id,
                    checked_interaction_id,
                    fields,
                    expires_at,
                )
                self._records[secret_id] = _SecretRecord(
                    secret_id,
                    checked_run_id,
                    checked_interaction_id,
                    fields,
                    expires_at,
                    buffers,
                )
                transferred = True
            return reference
        finally:
            if not transferred:
                for buffer in buffers.values():
                    _zeroize(buffer)

    async def consume(
        self, reference: SecretReference
    ) -> dict[SecretField, str]:
        self._validate_reference(reference)
        record: _SecretRecord | None = None
        expired = False
        async with self._lock:
            if self._closed:
                raise SecretStoreClosedError("Secret store is closed.")
            record = self._records.get(reference.secret_id)
            if record is None:
                raise SecretNotFoundError("Secret was not found.")
            self._check_binding(record, reference)
            del self._records[reference.secret_id]
            expired = self._clock() >= record.expires_at
        try:
            if expired:
                raise SecretExpiredError("Secret has expired.")
            return {
                field: record.buffers[field].decode("utf-8")
                for field in record.fields
            }
        finally:
            _zeroize_record(record)

    async def discard(self, reference: SecretReference) -> None:
        record: _SecretRecord | None = None
        try:
            async with self._lock:
                if self._closed:
                    return
                self._validate_reference(reference)
                candidate = self._records.get(reference.secret_id)
                if candidate is None:
                    return
                self._check_binding(candidate, reference)
                record = self._records.pop(reference.secret_id)
        finally:
            if record is not None:
                _zeroize_record(record)

    async def discard_run(self, run_id: str) -> None:
        detached: list[_SecretRecord] = []
        try:
            async with self._lock:
                if self._closed:
                    return
                checked_run_id = _validate_identifier(run_id, "run_id")
                for secret_id, record in tuple(self._records.items()):
                    if record.run_id == checked_run_id:
                        detached.append(self._records.pop(secret_id))
        finally:
            for record in detached:
                _zeroize_record(record)

    async def close(self) -> None:
        detached: list[_SecretRecord] = []
        try:
            async with self._lock:
                if self._closed:
                    return
                self._closed = True
                detached = list(self._records.values())
                self._records.clear()
        finally:
            for record in detached:
                _zeroize_record(record)

    @staticmethod
    def _validate_reference(reference: object) -> None:
        if type(reference) is not SecretReference:
            raise SecretValidationError("Secret reference is invalid.")

    @staticmethod
    def _check_binding(
        record: _SecretRecord, reference: SecretReference
    ) -> None:
        if (
            record.secret_id != reference.secret_id
            or record.run_id != reference.run_id
            or record.interaction_id != reference.interaction_id
            or record.fields != reference.fields
            or record.expires_at != reference.expires_at
        ):
            raise SecretBindingError("Secret reference binding does not match.")
