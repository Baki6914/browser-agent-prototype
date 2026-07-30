# Agent control tools

## Boundary and ownership

`finish` and `ask_user` are application-owned orchestration controls.
`AgentControlExecutor` defines, validates, and executes them locally. They are
separate from browser tools and never reach the MCP gateway, policy executor,
Playwright, a subprocess, or a network service. Browser decisions continue
through the policy-enforced executor.

## Schemas

`finish` is unchanged: it requires exactly one non-empty string field,
`result`, and rejects additional properties.

`ask_user` remains backward compatible:

```json
{
  "type": "object",
  "properties": {
    "question": {
      "type": "string",
      "description": "Question that must be answered before continuing."
    },
    "confirmation_for_step": {
      "type": "integer",
      "minimum": 1,
      "description": "Claim that this question asks approval for the immediately preceding rejected step."
    }
  },
  "required": ["question"],
  "additionalProperties": false
}
```

`question` must be a non-empty, non-whitespace string.
`confirmation_for_step` is optional; when present it must be an exact positive
integer, and a boolean is rejected. Unexpected fields remain invalid.
Definitions contain mutable JSON-compatible dictionaries, so every call
returns fresh nested schema data.

`AgentControlResult` carries the validated optional reference. This is only a
model-supplied claimed association. The control executor does not grant
confirmation and cannot authorize a browser call.

## Association and fallback

A confirmation pause exists only when all of these facts agree:

1. The immediately preceding decision was an MCP/browser tool call.
2. It was rejected through the typed `ToolConfirmationRequiredError` path.
3. No tool or control occurred between that rejection and `ask_user`.
4. `confirmation_for_step` exactly names that immediately preceding step.
5. The rejected call's arguments can be safely canonicalized.

The pending record takes its tool name and arguments only from the rejected
browser call, never from `ask_user`. A syntactically valid positive integer
reference that is wrong, stale, unbound, or ambiguous produces an ordinary
`USER_INPUT` pause with no pending confirmation. A later `ask_user` cannot
reuse a candidate invalidated by an intervening rejected control call.

Invalid schema values for `confirmation_for_step`—including null, booleans,
zero, negative integers, floats, and strings—are rejected by
`AgentControlExecutor` validation. They are not converted into `USER_INPUT`
pauses. A denial, invalid tool arguments, intervening decision, missing
reference, or unsafe arguments also cannot establish a confirmation.
Ambiguity therefore fails closed.

`ask_user({"question": "Which reporting period should I use?"})` retains its
original normal-input behavior. `finish` retains its schema, result, and
terminal behavior unchanged.

## Validation and limits

Unknown control names raise `UnknownAgentControlError`. Invalid mappings,
missing fields, wrong types, empty text, invalid confirmation references, and
additional fields raise `InvalidAgentControlArgumentsError`. Accepted text is
preserved rather than trimmed.

The resumable loop is process-memory only. These controls do not implement
FastAPI, an HTTP RunManager, persistence, login, downloads, or secret-safe
input.
