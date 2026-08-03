"""Safety and boundedness tests for provider trace values."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from browser_agent.provider_trace import ProviderTraceStore


def metadata(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "timestamp": "2026-08-03T00:00:00+00:00",
        "decision_sequence": 1,
        "attempt_number": 1,
        "model": "safe-model",
        "tool_choice": "required",
        "offered_tool_names": ("browser_snapshot", "finish"),
        "previous_step_count": 0,
        "user_interaction_count": 0,
        "application_event_count": 0,
        "http_status": 200,
        "duration_ms": 7,
        "finish_reason": "tool_calls",
        "tool_call_count": 1,
        "assistant_content_present": False,
        "selected_tool_name": "browser_snapshot",
        "result_status": "success",
        "safe_error_type": None,
    }
    values.update(changes)
    return values


def test_store_is_disabled_by_default_bounded_and_immutable() -> None:
    disabled = ProviderTraceStore()
    disabled.record(**metadata())
    assert disabled.snapshot() == ()

    store = ProviderTraceStore(enabled=True, limit=2)
    for attempt in range(1, 4):
        store.record(**metadata(attempt_number=attempt))
    snapshot = store.snapshot()
    assert [item.sequence for item in snapshot] == [2, 3]
    with pytest.raises(FrozenInstanceError):
        snapshot[0].model = "changed"  # type: ignore[misc]


def test_store_rejects_unstructured_sensitive_or_control_fields() -> None:
    store = ProviderTraceStore(enabled=True)
    forbidden = (
        {"raw_prompt": "task secret page text"},
        {"model": "secret\nvalue"},
        {"offered_tool_names": ("browser_snapshot", "bad\u2066name")},
        {"selected_tool_name": {"arguments": {"password": "secret"}}},
    )
    for changes in forbidden:
        with pytest.raises((TypeError, ValueError)):
            store.record(**metadata(**changes))
    assert store.snapshot() == ()


def test_separate_stores_are_isolated_and_clearable() -> None:
    first = ProviderTraceStore(enabled=True)
    second = ProviderTraceStore(enabled=True)
    first.record(**metadata(model="run-a"))
    second.record(**metadata(model="run-b"))
    assert [item.model for item in first.snapshot()] == ["run-a"]
    assert [item.model for item in second.snapshot()] == ["run-b"]
    first.clear()
    assert first.snapshot() == ()
    assert [item.model for item in second.snapshot()] == ["run-b"]
