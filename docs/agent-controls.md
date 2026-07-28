# Agent Control Tools

## Boundary and ownership

Agent controls are application-owned orchestration tools. They are separate
from the browser tools discovered from the local Playwright MCP process:

- `finish` ends the current agent task and supplies its final result.
- `ask_user` pauses progress because information or clarification is missing.

The Python orchestrator owns their definitions, validation, and execution.
They are not discovered through MCP, registered in the MCP tool policy,
classified as browser tools, or passed to an MCP gateway, policy-enforced MCP
executor, MCP session, Playwright, subprocess, or network service.

The future routing model is:

```text
tool decision
    -> if agent-control name:
           AgentControlExecutor.execute()
       else:
           PolicyEnforcedToolExecutor.invoke()
```

This milestone defines neither that router nor the surrounding agent loop.

## Definitions

`AgentControlExecutor.definitions()` returns an alphabetically ordered tuple:
`ask_user`, then `finish`.

The `finish` input schema is:

```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "description": "Final result to return to the user."
    }
  },
  "required": ["result"],
  "additionalProperties": false
}
```

The `ask_user` input schema is:

```json
{
  "type": "object",
  "properties": {
    "question": {
      "type": "string",
      "description": "Question that must be answered before continuing."
    }
  },
  "required": ["question"],
  "additionalProperties": false
}
```

Definitions are immutable dataclass values, but their JSON-compatible schemas
contain mutable dictionaries and lists. Each public call therefore constructs
fresh nested schema data. Mutating a returned schema cannot affect the private
control specifications or a later call.

## Status and result model

`AgentControlStatus` has two string values:

- `FINISHED` (`"finished"`) means `finish` ended the task.
- `AWAITING_USER` (`"awaiting_user"`) means `ask_user` paused progress for an
  answer.

Every execution returns an immutable `AgentControlResult` with:

- `tool_name`: the executed control name;
- `status`: its `AgentControlStatus`;
- `text`: the exact supplied result or question;
- `final_result`: the exact finish result, otherwise `None`; and
- `question`: the exact ask-user question, otherwise `None`.

For `finish({"result": value})`, execution returns `FINISHED`, copies `value`
unchanged into `text` and `final_result`, and sets `question` to `None`.
`finish` does not verify that the task was completed correctly.

For `ask_user({"question": value})`, execution returns `AWAITING_USER`, copies
`value` unchanged into `text` and `question`, and sets `final_result` to
`None`. This result represents an orchestration pause only; no real user
interface is displayed.

## Validation and failure behavior

Execution first requires an explicitly registered control name. Unknown,
empty, browser, MCP, and future unregistered names raise
`UnknownAgentControlError`.

Arguments must be a mapping containing exactly the required field. Missing
fields, additional fields, non-string values (including booleans), empty
strings, and whitespace-only strings raise
`InvalidAgentControlArgumentsError`. Validation uses only the Python standard
library. Accepted text is checked with whitespace awareness but is never
trimmed or rewritten. These rules fail closed and errors identify the control
and relevant field.

After validation, execution creates the typed result directly in the
orchestrator process. No browser capability is needed, which is why these
controls must never reach MCP.

## Current limitations and deferred work

This milestone does not implement an agent loop, tool router, confirmation UI,
display of questions, waiting for or persisting user answers, automatic resume,
session or approval persistence, step or timeout limits, an LLM provider,
OpenAI-compatible provider adapter, local vLLM, an HTTP API, database support,
Docker, offline packaging, file lifecycle management, or credential handling.
