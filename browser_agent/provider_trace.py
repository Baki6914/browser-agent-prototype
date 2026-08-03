"""Safe, bounded, dependency-free provider-attempt traces."""

from __future__ import annotations

import unicodedata
from collections import deque
from dataclasses import dataclass
from typing import Protocol


_TEXT_LIMIT = 200


def _safe_text(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value.strip() or len(value) > _TEXT_LIMIT:
        raise ValueError(f"{name} must be safe non-empty text")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError(f"{name} contains control or format characters")
    return value


def _count(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise ValueError(f"{name} is invalid")
    return value


@dataclass(frozen=True)
class ProviderTraceRecord:
    """Immutable public metadata for one provider HTTP attempt."""

    sequence: int
    timestamp: str
    decision_sequence: int
    attempt_number: int
    model: str
    tool_choice: str
    offered_tool_names: tuple[str, ...]
    previous_step_count: int
    user_interaction_count: int
    application_event_count: int
    http_status: int | None
    duration_ms: int
    finish_reason: str | None
    tool_call_count: int | None
    assistant_content_present: bool
    selected_tool_name: str | None
    result_status: str
    safe_error_type: str | None

    def __post_init__(self) -> None:
        _count(self.sequence, "sequence", positive=True)
        _safe_text(self.timestamp, "timestamp")
        _count(self.decision_sequence, "decision_sequence", positive=True)
        _count(self.attempt_number, "attempt_number", positive=True)
        _safe_text(self.model, "model")
        _safe_text(self.tool_choice, "tool_choice")
        if type(self.offered_tool_names) is not tuple:
            raise TypeError("offered_tool_names must be a tuple")
        for item in self.offered_tool_names:
            _safe_text(item, "offered_tool_name")
        _count(self.previous_step_count, "previous_step_count")
        _count(self.user_interaction_count, "user_interaction_count")
        _count(self.application_event_count, "application_event_count")
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("http_status is invalid")
        _count(self.duration_ms, "duration_ms")
        _safe_text(self.finish_reason, "finish_reason", optional=True)
        if self.tool_call_count is not None:
            _count(self.tool_call_count, "tool_call_count")
        if type(self.assistant_content_present) is not bool:
            raise TypeError("assistant_content_present must be a bool")
        _safe_text(self.selected_tool_name, "selected_tool_name", optional=True)
        _safe_text(self.result_status, "result_status")
        _safe_text(self.safe_error_type, "safe_error_type", optional=True)


class ProviderTraceRecorderProtocol(Protocol):
    def record(self, **metadata: object) -> None: ...


class ProviderTraceStore:
    """Run-local bounded store; it never accepts provider content or arguments."""

    def __init__(self, *, enabled: bool = False, limit: int = 200) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be a bool")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._enabled = enabled
        self._limit = limit
        self._next_sequence = 1
        self._records: deque[ProviderTraceRecord] = deque(maxlen=limit)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, **metadata: object) -> None:
        if not self._enabled:
            return
        record = ProviderTraceRecord(sequence=self._next_sequence, **metadata)  # type: ignore[arg-type]
        self._records.append(record)
        self._next_sequence += 1

    def snapshot(self) -> tuple[ProviderTraceRecord, ...]:
        return tuple(self._records)

    def clear(self) -> None:
        self._records.clear()


@dataclass(frozen=True)
class ProviderTraceSnapshot:
    enabled: bool
    traces: tuple[ProviderTraceRecord, ...]

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a bool")
        if type(self.traces) is not tuple or any(
            not isinstance(item, ProviderTraceRecord) for item in self.traces
        ):
            raise TypeError("traces must contain ProviderTraceRecord values")
