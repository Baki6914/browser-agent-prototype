# Deterministic MCP-backed agent loop

Milestone 5 connects application-owned agent controls to policy-enforced MCP
browser execution. Its `ScriptedDecisionSource` is a temporary, finite,
in-memory execution source for deterministic tests and smoke checks. It is not
an LLM provider and does not model provider request or response internals. The
mock infrastructure will not be expanded further.

`AgentToolRouter` combines the caller-supplied, policy-visible MCP definitions
with `finish` and `ask_user`. It rejects ambiguous names, executes agent
controls locally, and sends browser tools only through
`PolicyEnforcedToolExecutor`. Agent controls never reach MCP. The router always
passes `confirmation_granted=False`; a similarly named tool argument is not
trusted approval.

Tool sources, step outcomes, and run termination states are enums. Each
`AgentToolCall` deep-copies nested arguments at construction and on every
public access. Before each decision, the loop supplies the exact task, fresh
tool definitions, and immutable in-memory records of prior decisions and
observations. This makes a browser observation available to the next scripted
decision.

`finish` returns the final result and stops the loop. `ask_user` returns a
question and stops the loop, but no user interface, waiting, or resume behavior
exists. Exhausting the script and reaching the positive integer `max_steps`
limit are separate typed termination states.

Expected control, policy, and gateway rejections become typed rejected
observations with the exception type and message. A confirmation-required
operation can therefore be followed by `ask_user`, but this milestone provides
no real confirmation, automatic retry, or execution of the rejected action.
Unexpected programming exceptions are not caught; they propagate with their
original traceback.

The live smoke starts the local Playwright MCP process over stdio, discovers
tools, applies `McpToolPolicy`, navigates to Example Domain, takes a snapshot,
and finishes locally. It verifies that only navigation and snapshot used the
normal MCP path, then closes the browser through the policy executor's internal
cleanup path.

All loop context and steps exist only in memory. There is no persistence,
timeout, user-response handling, or resume. The next engineering milestone is
a real OpenAI-compatible LLM provider.
