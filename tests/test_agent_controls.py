"""Unit tests for application-owned agent controls."""

from pathlib import Path
import sys
import unittest
from types import MappingProxyType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import browser_agent.agent_controls as agent_controls
from browser_agent import (
    AgentControlError,
    AgentControlExecutor,
    AgentControlStatus,
    InvalidAgentControlArgumentsError,
    UnknownAgentControlError,
    SecretField,
)


class AgentControlDefinitionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = AgentControlExecutor()

    def test_definitions_are_a_tuple_with_exact_controls(self) -> None:
        definitions = self.executor.definitions()

        self.assertIsInstance(definitions, tuple)
        self.assertEqual([definition.name for definition in definitions], [
            "ask_user",
            "finish",
            "request_secret",
        ])

    def test_definitions_are_alphabetically_ordered(self) -> None:
        names = [definition.name for definition in self.executor.definitions()]

        self.assertEqual(names, sorted(names))

    def test_definitions_are_defensive_copies(self) -> None:
        first = self.executor.definitions()
        second = self.executor.definitions()

        self.assertIsNot(first[0], second[0])
        self.assertIsNot(first[0].input_schema, second[0].input_schema)
        self.assertIsNot(
            first[0].input_schema["properties"],
            second[0].input_schema["properties"],
        )

    def test_mutating_returned_schema_does_not_affect_later_call(self) -> None:
        definitions = self.executor.definitions()
        definitions[0].input_schema["properties"]["question"]["type"] = "number"
        definitions[0].input_schema["required"].clear()

        later_ask_user = self.executor.definitions()[0]
        self.assertEqual(
            later_ask_user.input_schema["properties"]["question"]["type"],
            "string",
        )
        self.assertEqual(later_ask_user.input_schema["required"], ["question"])

    def test_finish_definition_has_required_result_schema(self) -> None:
        finish = self.executor.definitions()[1]

        self.assertEqual(
            finish.input_schema,
            {
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "Non-empty proposed final result for user review.",
                    }
                },
                "required": ["result"],
                "additionalProperties": False,
            },
        )

    def test_ask_user_definition_has_required_question_schema(self) -> None:
        ask_user = self.executor.definitions()[0]

        self.assertEqual(
            ask_user.input_schema,
            {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": (
                            "Question that must be answered before continuing."
                        ),
                    },
                    "confirmation_for_step": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Claim that this question asks approval for the "
                            "immediately preceding rejected step."
                        ),
                    },
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        )


class AgentControlExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = AgentControlExecutor()

    def test_finish_returns_completion_proposed_and_exact_text(self) -> None:
        supplied = "  Work completed.\n"

        result = self.executor.execute("finish", {"result": supplied})

        self.assertEqual(result.tool_name, "finish")
        self.assertIs(result.status, AgentControlStatus.COMPLETION_PROPOSED)
        self.assertEqual(result.text, supplied)
        self.assertIsNone(result.final_result)
        self.assertEqual(result.text, supplied)
        self.assertIsNone(result.question)

    def test_ask_user_returns_awaiting_user_and_exact_question(self) -> None:
        supplied = "  Which file should I use?\n"

        result = self.executor.execute("ask_user", {"question": supplied})

        self.assertEqual(result.tool_name, "ask_user")
        self.assertIs(result.status, AgentControlStatus.AWAITING_USER)
        self.assertEqual(result.text, supplied)
        self.assertEqual(result.question, supplied)
        self.assertIsNone(result.final_result)

    def test_execution_requires_no_gateway_or_mcp_object(self) -> None:
        executor = AgentControlExecutor()

        self.assertEqual(
            executor.execute("finish", {"result": "done"}).text,
            "done",
        )

    def test_registered_control_without_execution_branch_fails_closed(self) -> None:
        future_spec = agent_controls._ControlSpec(
            description="Future control used to verify fail-closed routing.",
            field_name="value",
            field_description="Future control value.",
            status=AgentControlStatus.AWAITING_USER,
        )
        registered_controls = MappingProxyType(
            {**agent_controls._CONTROL_SPECS, "future_control": future_spec}
        )

        with patch.object(
            agent_controls,
            "_CONTROL_SPECS",
            registered_controls,
        ):
            with self.assertRaisesRegex(
                AgentControlError,
                "future_control.*no implemented execution branch.*refusing",
            ):
                self.executor.execute("future_control", {"value": "payload"})


class AgentControlValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = AgentControlExecutor()

    def test_unknown_control_is_rejected(self) -> None:
        for name in ("complete", "finish_task", "future_control"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(UnknownAgentControlError, repr(name)):
                    self.executor.execute(name, {})

    def test_empty_tool_name_is_rejected(self) -> None:
        with self.assertRaises(UnknownAgentControlError):
            self.executor.execute("", {})

    def test_browser_and_mcp_tool_names_are_rejected(self) -> None:
        for name in ("browser_close", "browser_navigate"):
            with self.subTest(name=name):
                with self.assertRaises(UnknownAgentControlError):
                    self.executor.execute(name, {})

    def test_non_mapping_arguments_are_rejected(self) -> None:
        for arguments in ([], None, "result", 1):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(
                    InvalidAgentControlArgumentsError,
                    "finish.*mapping",
                ):
                    self.executor.execute("finish", arguments)  # type: ignore[arg-type]

    def test_missing_finish_result_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            InvalidAgentControlArgumentsError,
            "finish.*result",
        ):
            self.executor.execute("finish", {})

    def test_missing_ask_user_question_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            InvalidAgentControlArgumentsError,
            "ask_user.*question",
        ):
            self.executor.execute("ask_user", {})

    def test_finish_wrong_value_type_is_rejected(self) -> None:
        for value in (None, 1, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    InvalidAgentControlArgumentsError,
                    "finish.*result.*string",
                ):
                    self.executor.execute("finish", {"result": value})

    def test_ask_user_wrong_value_type_is_rejected(self) -> None:
        for value in (None, 1, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    InvalidAgentControlArgumentsError,
                    "ask_user.*question.*string",
                ):
                    self.executor.execute("ask_user", {"question": value})

    def test_finish_empty_string_is_rejected(self) -> None:
        with self.assertRaises(InvalidAgentControlArgumentsError):
            self.executor.execute("finish", {"result": ""})

    def test_finish_whitespace_only_string_is_rejected(self) -> None:
        with self.assertRaises(InvalidAgentControlArgumentsError):
            self.executor.execute("finish", {"result": "   "})

    def test_ask_user_empty_string_is_rejected(self) -> None:
        with self.assertRaises(InvalidAgentControlArgumentsError):
            self.executor.execute("ask_user", {"question": ""})

    def test_ask_user_whitespace_only_string_is_rejected(self) -> None:
        with self.assertRaises(InvalidAgentControlArgumentsError):
            self.executor.execute("ask_user", {"question": "\n\t"})

    def test_finish_unexpected_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            InvalidAgentControlArgumentsError,
            "finish.*extra",
        ):
            self.executor.execute("finish", {"result": "done", "extra": True})

    def test_ask_user_unexpected_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(
            InvalidAgentControlArgumentsError,
            "ask_user.*extra",
        ):
            self.executor.execute(
                "ask_user",
                {"question": "Which file?", "extra": "x"},
            )

    def test_ask_user_accepts_positive_confirmation_reference(self) -> None:
        result = self.executor.execute(
            "ask_user",
            {"question": "Approve?", "confirmation_for_step": 4},
        )
        self.assertEqual(result.confirmation_for_step, 4)

    def test_ask_user_rejects_invalid_confirmation_reference(self) -> None:
        for value in (None, True, False, 0, -1, 1.5, "1"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidAgentControlArgumentsError):
                    self.executor.execute(
                        "ask_user",
                        {
                            "question": "Approve?",
                            "confirmation_for_step": value,
                        },
                    )

    def test_multiple_unexpected_fields_are_rendered_in_repr_order(self) -> None:
        with self.assertRaises(InvalidAgentControlArgumentsError) as raised:
            self.executor.execute(
                "finish",
                {
                    "result": "done",
                    "zeta": True,
                    2: "integer key",
                    ("tuple",): "tuple key",
                },
            )

        self.assertEqual(
            str(raised.exception),
            "Agent control 'finish' received unexpected field(s): "
            "'zeta', ('tuple',), 2.",
        )


class RequestSecretControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = AgentControlExecutor()

    def test_exact_schema_and_safe_success(self) -> None:
        definition = next(
            item for item in self.executor.definitions()
            if item.name == "request_secret"
        )
        self.assertEqual(definition.input_schema["required"], ["question", "fields", "targets"])
        self.assertFalse(definition.input_schema["additionalProperties"])
        self.assertEqual(definition.input_schema["properties"]["targets"]["items"]["required"], ["field", "name", "ref"])
        result = self.executor.execute(
            "request_secret",
            {"question": "Enter credentials", "fields": ["username", "password"], "targets": [
                {"field": "username", "name": "Email", "ref": "u"},
                {"field": "password", "name": "Password", "ref": "p"},
            ]},
        )
        self.assertEqual(result.status, AgentControlStatus.AWAITING_SECRET)
        self.assertEqual(result.secret_fields, (SecretField.PASSWORD, SecretField.USERNAME))
        self.assertIsNone(result.final_result)

    def test_otp_and_strict_rejections(self) -> None:
        result = self.executor.execute(
            "request_secret", {"question": "Enter OTP", "fields": ["otp"], "targets": [{"field": "otp", "name": "Code", "ref": "o"}]}
        )
        self.assertEqual(result.secret_fields, (SecretField.OTP,))
        invalid = (
            {}, {"question": "Q"}, {"fields": ["otp"]},
            {"question": "Q", "fields": [], "targets": []},
            {"question": "Q", "fields": ("otp",)},
            {"question": "Q", "fields": "otp"},
            {"question": "Q", "fields": ["otp", "otp"]},
            {"question": "Q", "fields": ["token"]},
            {"question": "Q", "fields": [1]},
            {"question": "Q", "fields": ["otp"], "targets": [], "values": {"otp": "x"}},
            {"question": "Q", "fields": ["otp"], "targets": [], "password": "x"},
        )
        for arguments in invalid:
            with self.subTest(arguments=tuple(arguments)):
                with self.assertRaises(InvalidAgentControlArgumentsError):
                    self.executor.execute("request_secret", arguments)

    def test_non_secret_results_have_no_fields(self) -> None:
        self.assertIsNone(self.executor.execute("finish", {"result": "done"}).secret_fields)
        self.assertIsNone(self.executor.execute("ask_user", {"question": "Q"}).secret_fields)


if __name__ == "__main__":
    unittest.main()
