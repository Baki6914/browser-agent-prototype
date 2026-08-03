from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from browser_agent import (
    AgentRunResult,
    AgentRunStatus,
    AgentStepObservation,
    AgentStepRecord,
    AgentStepStatus,
    AgentToolCall,
    AgentToolSource,
    MvpEvaluationExpectation,
    MvpEvaluationOutcome,
    MvpEvaluationScenario,
    MvpEvaluationScenarioResult,
    MvpEvaluationToolTrace,
    OpenAIProviderHTTPError,
    create_mvp_error_result,
    default_mvp_evaluation_scenarios,
    evaluate_mvp_scenario,
    mvp_evaluation_exit_code,
    mvp_summary_to_json,
    summarize_mvp_results,
)


def _load_live_eval_script() -> ModuleType:
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "openai_agent_eval.py"
    )
    spec = importlib.util.spec_from_file_location(
        "offline_openai_agent_eval", script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scenario(
    **expectation_overrides: object,
) -> MvpEvaluationScenario:
    values = {
        "expected_run_status": AgentRunStatus.FINISHED,
        "required_successful_tool_order": (
            "browser_navigate",
            "browser_snapshot",
        ),
        "required_final_result_substrings": ("Example Domain",),
    }
    values.update(expectation_overrides)
    return MvpEvaluationScenario(
        "test_scenario",
        "Use public test data.",
        5,
        MvpEvaluationExpectation(**values),  # type: ignore[arg-type]
    )


def _step(
    number: int,
    name: str,
    status: AgentStepStatus,
    *,
    source: AgentToolSource | None = AgentToolSource.MCP,
    text: str = "RAW OBSERVATION MUST NOT LEAK",
) -> AgentStepRecord:
    return AgentStepRecord(
        number,
        AgentToolCall(name, {}),
        AgentStepObservation(name, source, status, text, "raw error"),
    )


def _finished_run(
    steps: tuple[AgentStepRecord, ...] | None = None,
    *,
    final_result: str | None = "Example Domain",
) -> AgentRunResult:
    if steps is None:
        steps = (
            _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
            _step(2, "browser_snapshot", AgentStepStatus.SUCCESS),
            _step(
                3,
                "finish",
                AgentStepStatus.COMPLETION_PROPOSED,
                source=AgentToolSource.AGENT_CONTROL,
            ),
        )
    return AgentRunResult(
        AgentRunStatus.FINISHED, steps, final_result, None
    )


def _confirmation_run(
    middle: tuple[AgentStepRecord, ...] = (),
    *,
    click_success: bool = False,
    question: str | None = "Bağlantıyı açmamı ister misiniz?",
) -> AgentRunResult:
    steps = [
        _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
        _step(2, "browser_snapshot", AgentStepStatus.SUCCESS),
    ]
    steps.extend(middle)
    if click_success:
        steps.append(
            _step(len(steps) + 1, "browser_click", AgentStepStatus.SUCCESS)
        )
    steps.append(
        _step(
            len(steps) + 1,
            "ask_user",
            AgentStepStatus.AWAITING_USER,
            source=AgentToolSource.AGENT_CONTROL,
        )
    )
    return AgentRunResult(
        AgentRunStatus.AWAITING_USER, tuple(steps), None, question
    )


def _confirmation_scenario() -> MvpEvaluationScenario:
    return default_mvp_evaluation_scenarios()[2]


def test_outcome_enum_values() -> None:
    assert [outcome.value for outcome in MvpEvaluationOutcome] == [
        "pass",
        "fail",
        "error",
    ]


def test_valid_expectation_and_scenario_preserve_text() -> None:
    expectation = MvpEvaluationExpectation(AgentRunStatus.FINISHED)
    scenario = MvpEvaluationScenario("  name  ", "  task  ", 1, expectation)
    assert scenario.name == "  name  "
    assert scenario.task == "  task  "
    assert scenario.expectation is expectation


@pytest.mark.parametrize("field", ["name", "task"])
@pytest.mark.parametrize("value", ["", " \t"])
def test_empty_scenario_text_is_invalid(field: str, value: str) -> None:
    values = {
        "name": "name",
        "task": "task",
        "max_steps": 1,
        "expectation": MvpEvaluationExpectation(AgentRunStatus.FINISHED),
    }
    values[field] = value
    with pytest.raises(ValueError):
        MvpEvaluationScenario(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["name", "task"])
def test_non_string_scenario_text_is_invalid(field: str) -> None:
    values = {
        "name": "name",
        "task": "task",
        "max_steps": 1,
        "expectation": MvpEvaluationExpectation(AgentRunStatus.FINISHED),
    }
    values[field] = 1
    with pytest.raises(TypeError):
        MvpEvaluationScenario(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_max_steps_requires_exact_int(value: object) -> None:
    with pytest.raises(TypeError):
        MvpEvaluationScenario(
            "name",
            "task",
            value,  # type: ignore[arg-type]
            MvpEvaluationExpectation(AgentRunStatus.FINISHED),
        )


@pytest.mark.parametrize("value", [0, -1])
def test_max_steps_must_be_positive(value: int) -> None:
    with pytest.raises(ValueError):
        MvpEvaluationScenario(
            "name",
            "task",
            value,
            MvpEvaluationExpectation(AgentRunStatus.FINISHED),
        )


def test_scenario_requires_expected_expectation_type() -> None:
    with pytest.raises(TypeError):
        MvpEvaluationScenario("name", "task", 1, object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expected_run_status": "finished"},
        {"require_ask_user": 1},
        {"require_nonempty_question": None},
        {"required_successful_tool_order": ["browser_snapshot"]},
        {"forbidden_successful_tools": ("",)},
        {"required_final_result_substrings": (" \t",)},
        {"allowed_rejected_tools": (1,)},
    ],
)
def test_expectation_field_validation(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "expected_run_status": AgentRunStatus.FINISHED
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        MvpEvaluationExpectation(**values)  # type: ignore[arg-type]


def test_expectation_tuple_storage_is_immutable_and_defensive() -> None:
    source = ("browser_navigate",)
    expectation = MvpEvaluationExpectation(
        AgentRunStatus.FINISHED,
        required_successful_tool_order=source,
    )
    assert expectation.required_successful_tool_order == source
    assert isinstance(expectation.required_successful_tool_order, tuple)
    with pytest.raises(Exception):
        expectation.required_successful_tool_order = ()  # type: ignore[misc]


def test_fully_passing_finished_scenario() -> None:
    result = evaluate_mvp_scenario(_finished_run(), _scenario())
    assert result.outcome is MvpEvaluationOutcome.PASS
    assert result.failure_reasons == ()


def test_required_tools_are_an_ordered_subsequence() -> None:
    steps = (
        _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
        _step(2, "browser_wait_for", AgentStepStatus.SUCCESS),
        _step(3, "browser_snapshot", AgentStepStatus.SUCCESS),
        _step(
            4,
            "finish",
            AgentStepStatus.COMPLETION_PROPOSED,
            source=AgentToolSource.AGENT_CONTROL,
        ),
    )
    assert (
        evaluate_mvp_scenario(_finished_run(steps), _scenario()).outcome
        is MvpEvaluationOutcome.PASS
    )


def test_missing_required_tool_fails() -> None:
    run = _finished_run(
        (
            _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
            _step(
                2,
                "finish",
                AgentStepStatus.COMPLETION_PROPOSED,
                source=AgentToolSource.AGENT_CONTROL,
            ),
        )
    )
    result = evaluate_mvp_scenario(run, _scenario())
    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert "required successful tool order" in result.failure_reasons[0]


def test_wrong_required_tool_order_fails() -> None:
    run = _finished_run(
        (
            _step(1, "browser_snapshot", AgentStepStatus.SUCCESS),
            _step(2, "browser_navigate", AgentStepStatus.SUCCESS),
        )
    )
    assert (
        evaluate_mvp_scenario(run, _scenario()).outcome
        is MvpEvaluationOutcome.FAIL
    )


def test_final_result_matching_is_case_insensitive() -> None:
    run = _finished_run(final_result="The title is EXAMPLE DOMAIN.")
    assert (
        evaluate_mvp_scenario(run, _scenario()).outcome
        is MvpEvaluationOutcome.PASS
    )


def test_forbidden_successful_tool_fails() -> None:
    steps = (
        _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
        _step(2, "browser_snapshot", AgentStepStatus.SUCCESS),
        _step(3, "browser_click", AgentStepStatus.SUCCESS),
    )
    scenario = _scenario(forbidden_successful_tools=("browser_click",))
    result = evaluate_mvp_scenario(_finished_run(steps), scenario)
    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert "forbidden tool succeeded: browser_click" in result.failure_reasons


def test_forbidden_rejected_tool_is_not_successful_when_allowed() -> None:
    steps = (
        _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
        _step(2, "browser_snapshot", AgentStepStatus.SUCCESS),
        _step(3, "browser_click", AgentStepStatus.REJECTED),
    )
    scenario = _scenario(
        forbidden_successful_tools=("browser_click",),
        allowed_rejected_tools=("browser_click",),
    )
    assert (
        evaluate_mvp_scenario(_finished_run(steps), scenario).outcome
        is MvpEvaluationOutcome.PASS
    )


def test_direct_snapshot_to_ask_user_confirmation_passes() -> None:
    result = evaluate_mvp_scenario(
        _confirmation_run(), _confirmation_scenario()
    )
    assert result.outcome is MvpEvaluationOutcome.PASS


def test_rejected_click_then_ask_user_confirmation_passes() -> None:
    click = (_step(3, "browser_click", AgentStepStatus.REJECTED),)
    result = evaluate_mvp_scenario(
        _confirmation_run(click), _confirmation_scenario()
    )
    assert result.outcome is MvpEvaluationOutcome.PASS


def test_successful_click_makes_confirmation_fail() -> None:
    result = evaluate_mvp_scenario(
        _confirmation_run(click_success=True), _confirmation_scenario()
    )
    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert "forbidden tool succeeded: browser_click" in result.failure_reasons


@pytest.mark.parametrize("question", [None, "", " \t"])
def test_empty_question_makes_confirmation_fail(
    question: str | None,
) -> None:
    result = evaluate_mvp_scenario(
        _confirmation_run(question=question), _confirmation_scenario()
    )
    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert "question was empty" in result.failure_reasons


def test_awaiting_status_without_actual_ask_user_decision_fails() -> None:
    run = AgentRunResult(
        AgentRunStatus.AWAITING_USER,
        (
            _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
            _step(2, "browser_snapshot", AgentStepStatus.SUCCESS),
            _step(
                3,
                "other_control",
                AgentStepStatus.AWAITING_USER,
                source=AgentToolSource.AGENT_CONTROL,
            ),
        ),
        None,
        "Continue?",
    )
    result = evaluate_mvp_scenario(run, _confirmation_scenario())
    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert (
        "ask_user was not called with awaiting_user status"
        in result.failure_reasons
    )


def test_disallowed_rejected_tool_fails() -> None:
    steps = (
        _step(1, "browser_navigate", AgentStepStatus.SUCCESS),
        _step(2, "browser_snapshot", AgentStepStatus.SUCCESS),
        _step(3, "browser_type", AgentStepStatus.REJECTED),
    )
    result = evaluate_mvp_scenario(_finished_run(steps), _scenario())
    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert "tool rejection was not allowed: browser_type" in result.failure_reasons


def test_allowed_click_rejection_is_not_error() -> None:
    click = (_step(3, "browser_click", AgentStepStatus.REJECTED),)
    result = evaluate_mvp_scenario(
        _confirmation_run(click), _confirmation_scenario()
    )
    assert result.outcome is MvpEvaluationOutcome.PASS
    assert result.error is None


@pytest.mark.parametrize(
    "status",
    [AgentStepStatus.MCP_ERROR, AgentStepStatus.TRANSPORT_ERROR],
)
def test_infrastructure_step_status_produces_error(
    status: AgentStepStatus,
) -> None:
    run = _finished_run((_step(1, "browser_navigate", status),))
    result = evaluate_mvp_scenario(run, _scenario())
    assert result.outcome is MvpEvaluationOutcome.ERROR
    assert status.value in (result.error or "")
    assert result.failure_reasons == ()


def test_fail_and_error_are_distinct() -> None:
    failed = evaluate_mvp_scenario(
        _finished_run(final_result="wrong"), _scenario()
    )
    errored = evaluate_mvp_scenario(
        _finished_run(
            (_step(1, "browser_snapshot", AgentStepStatus.MCP_ERROR),)
        ),
        _scenario(),
    )
    assert failed.outcome is MvpEvaluationOutcome.FAIL
    assert failed.error is None
    assert errored.outcome is MvpEvaluationOutcome.ERROR
    assert errored.error is not None


def test_failure_reason_order_is_deterministic() -> None:
    run = AgentRunResult(
        AgentRunStatus.STEP_LIMIT_REACHED,
        (
            _step(1, "browser_click", AgentStepStatus.SUCCESS),
            _step(2, "browser_type", AgentStepStatus.REJECTED),
        ),
        "wrong",
        None,
    )
    scenario = _scenario(
        forbidden_successful_tools=("browser_click",),
        require_ask_user=True,
        require_nonempty_question=True,
    )
    result = evaluate_mvp_scenario(run, scenario)
    assert result.failure_reasons == (
        "run status was step_limit_reached, expected finished",
        "required successful tool order was not observed: "
        "browser_navigate, browser_snapshot",
        "forbidden tool succeeded: browser_click",
        "tool rejection was not allowed: browser_type",
        "final result missing required text: Example Domain",
        "ask_user was not called with awaiting_user status",
        "question was empty",
    )


def test_summary_counts_and_pass_rate() -> None:
    passed = evaluate_mvp_scenario(_finished_run(), _scenario())
    failed = evaluate_mvp_scenario(
        _finished_run(final_result="wrong"), _scenario()
    )
    errored = create_mvp_error_result(_scenario(), "sanitized")
    summary = summarize_mvp_results([passed, failed, errored])
    assert (summary.total, summary.passed, summary.failed, summary.errored) == (
        3,
        1,
        1,
        1,
    )
    assert summary.pass_rate == pytest.approx(1 / 3)
    assert isinstance(summary.results, tuple)


def test_empty_summary_has_zero_pass_rate() -> None:
    summary = summarize_mvp_results([])
    assert summary.total == 0
    assert summary.pass_rate == 0.0


@pytest.mark.parametrize(
    ("outcomes", "expected"),
    [
        ((MvpEvaluationOutcome.PASS,), 0),
        ((MvpEvaluationOutcome.PASS, MvpEvaluationOutcome.FAIL), 1),
        ((MvpEvaluationOutcome.ERROR,), 2),
        ((MvpEvaluationOutcome.FAIL, MvpEvaluationOutcome.ERROR), 2),
    ],
)
def test_exit_code_precedence(
    outcomes: tuple[MvpEvaluationOutcome, ...], expected: int
) -> None:
    results = []
    for outcome in outcomes:
        if outcome is MvpEvaluationOutcome.PASS:
            result = evaluate_mvp_scenario(_finished_run(), _scenario())
        elif outcome is MvpEvaluationOutcome.FAIL:
            result = evaluate_mvp_scenario(
                _finished_run(final_result="wrong"), _scenario()
            )
        else:
            result = create_mvp_error_result(_scenario(), "safe")
        results.append(result)
    assert mvp_evaluation_exit_code(summarize_mvp_results(results)) == expected


def test_json_output_is_sanitized_and_json_compatible() -> None:
    result = evaluate_mvp_scenario(_finished_run(), _scenario())
    payload = mvp_summary_to_json(summarize_mvp_results([result]))
    encoded = json.dumps(payload)
    assert "RAW OBSERVATION MUST NOT LEAK" not in encoded
    assert "raw error" not in encoded
    assert payload["results"][0]["tool_trace"][0] == {
        "tool_name": "browser_navigate",
        "tool_source": "mcp",
        "step_status": "success",
    }


def test_error_result_construction_preserves_only_sanitized_run_data() -> None:
    run = _finished_run()
    result = create_mvp_error_result(
        _scenario(), "cleanup failed safely", duration_seconds=1.25, run_result=run
    )
    assert result.outcome is MvpEvaluationOutcome.ERROR
    assert result.error == "cleanup failed safely"
    assert result.run_status is AgentRunStatus.FINISHED
    assert result.step_count == 3
    assert result.duration_seconds == 1.25
    assert len(result.tool_trace) == 3


def test_result_rejects_mutable_or_untyped_trace_entries() -> None:
    with pytest.raises(TypeError):
        MvpEvaluationScenarioResult(
            "scenario",
            MvpEvaluationOutcome.PASS,
            (),
            AgentRunStatus.FINISHED,
            1,
            0.0,
            ({"tool_name": "browser_snapshot"},),  # type: ignore[arg-type]
            "done",
            None,
            None,
        )
    trace = MvpEvaluationToolTrace(
        "browser_snapshot",
        AgentToolSource.MCP,
        AgentStepStatus.SUCCESS,
    )
    result = MvpEvaluationScenarioResult(
        "scenario",
        MvpEvaluationOutcome.PASS,
        (),
        AgentRunStatus.FINISHED,
        1,
        0.0,
        [trace],  # type: ignore[arg-type]
        "done",
        None,
        None,
    )
    assert result.tool_trace == (trace,)


def test_default_scenario_names_and_exact_count() -> None:
    scenarios = default_mvp_evaluation_scenarios()
    assert tuple(scenario.name for scenario in scenarios) == (
        "title_extraction",
        "read_only_link_extraction",
        "confirmation_boundary",
    )
    assert len(scenarios) == 3
    assert all("https://example.com" in scenario.task for scenario in scenarios)


def test_default_read_only_scenario_requires_exact_visible_link_text() -> None:
    scenario = default_mvp_evaluation_scenarios()[1]

    assert scenario.expectation.required_final_result_substrings == (
        "Learn more",
    )


def test_read_only_scenario_passes_with_learn_more_final_result() -> None:
    scenario = default_mvp_evaluation_scenarios()[1]

    result = evaluate_mvp_scenario(
        _finished_run(final_result="The visible link text is Learn more."),
        scenario,
    )

    assert result.outcome is MvpEvaluationOutcome.PASS
    assert result.failure_reasons == ()


def test_read_only_scenario_rejects_stale_more_information_final_result() -> None:
    scenario = default_mvp_evaluation_scenarios()[1]

    result = evaluate_mvp_scenario(
        _finished_run(final_result="The visible link text is More information."),
        scenario,
    )

    assert result.outcome is MvpEvaluationOutcome.FAIL
    assert result.failure_reasons == (
        "final result missing required text: Learn more",
    )


def test_live_script_imports_offline_without_environment_or_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "BROWSER_AGENT_LLM_BASE_URL",
        "BROWSER_AGENT_LLM_MODEL",
        "BROWSER_AGENT_LLM_API_KEY",
        "BROWSER_AGENT_LLM_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    module = _load_live_eval_script()
    assert callable(module.main)


def test_ensure_repository_import_path_inserts_root_and_preserves_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_live_eval_script()
    root = str(module._repository_root())
    unrelated_entries = ["/synthetic/first", "/synthetic/second"]
    monkeypatch.setattr(sys, "path", unrelated_entries.copy())

    result = module._ensure_repository_import_path()

    assert result is None
    assert sys.path == [root, *unrelated_entries]


def test_ensure_repository_import_path_does_not_duplicate_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_live_eval_script()
    root = str(module._repository_root())
    monkeypatch.setattr(sys, "path", ["/synthetic/first"])

    module._ensure_repository_import_path()
    module._ensure_repository_import_path()

    assert sys.path.count(root) == 1
    assert sys.path == [root, "/synthetic/first"]


def test_provider_http_error_details_omit_body_and_api_key() -> None:
    module = _load_live_eval_script()
    api_key = "synthetic-eval-api-key"
    body_marker = "DISTINCTIVE_PROVIDER_BODY_MARKER"
    error = OpenAIProviderHTTPError(f"HTTP 500: {body_marker} {api_key}")

    details = module._safe_exception_details(error, api_key)

    assert body_marker not in details
    assert api_key not in details


def test_provider_http_error_details_name_type_and_omission() -> None:
    module = _load_live_eval_script()

    details = module._safe_exception_details(
        OpenAIProviderHTTPError("HTTP 503: private response"), None
    )

    assert details.startswith("OpenAIProviderHTTPError:")
    assert "response body omitted" in details


def test_nested_provider_http_error_details_omit_body() -> None:
    module = _load_live_eval_script()
    body_marker = "NESTED_PROVIDER_BODY_MARKER"
    error = ExceptionGroup(
        "outer",
        [
            RuntimeError("ordinary nested failure"),
            ExceptionGroup(
                "inner",
                [OpenAIProviderHTTPError(f"HTTP 429: {body_marker}")],
            ),
        ],
    )

    details = module._safe_exception_details(error, None)

    assert body_marker not in details
    assert "OpenAIProviderHTTPError:" in details
    assert "response body omitted" in details
    assert "RuntimeError: ordinary nested failure" in details


def test_ordinary_exception_details_remain_useful_and_redacted() -> None:
    module = _load_live_eval_script()
    api_key = "synthetic-ordinary-api-key"

    details = module._safe_exception_details(
        ValueError(f"invalid setting for {api_key}"), api_key
    )

    assert details == "ValueError: invalid setting for [REDACTED]"
    assert api_key not in details


def test_recursive_json_redaction_covers_nested_dicts_and_lists() -> None:
    module = _load_live_eval_script()
    api_key = "synthetic-json-api-key"
    value = {
        "outer": [
            f"prefix {api_key}",
            {"inner": api_key},
            [f"{api_key} suffix"],
        ]
    }

    redacted = module._redact_json_value(value, api_key)

    assert api_key not in json.dumps(redacted)
    assert redacted == {
        "outer": [
            "prefix [REDACTED]",
            {"inner": "[REDACTED]"},
            ["[REDACTED] suffix"],
        ]
    }
