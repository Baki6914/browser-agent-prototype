# Resumable MCP-backed agent loop

Milestone 8A adds a dependency-free, in-memory `ResumableAgentSession`.
`DeterministicAgentLoop.run(task)` remains the compatible one-shot API: it
creates a fresh session, calls `start(task)`, and returns on the first terminal
state or pause. `ScriptedDecisionSource` remains a finite deterministic source
for tests and smoke checks.

## Routing and context

`AgentToolRouter` combines policy-visible MCP definitions with `finish` and
`ask_user`. Controls execute locally and never reach MCP. Browser tools still
go only through `PolicyEnforcedToolExecutor`. Its explicit
`confirmation_granted` keyword defaults to `False`; every model-selected call
uses `False`. A model-controlled argument with that name has no authorization
effect. Only the session's trusted replay path passes `True`.

Before every provider decision, `AgentLoopContext` contains the exact task,
fresh tool definitions, the complete immutable tuple of step records, and the
complete immutable tuple of `AgentUserInput` records. Each interaction records
the pause-producing step number, pause kind, exact question, and response.
Normal responses remain strings and confirmation responses remain booleans.
The OpenAI-compatible provider serializes these interactions as a distinct,
deterministically ordered `user_interactions` value. It remains decision-only:
the serialized data cannot apply policy or grant authority.

## Session state and pauses

The session stores the task, complete step and interaction histories, a
temporary confirmation candidate, an immutable pending confirmation, current
pause kind and question, terminal state, total step budget, start state, and an
`asyncio.Lock`. Read-only properties return immutable tuples or immutable
values. Nothing is written to disk or a database.

`start()` is single-use. `respond(text)` is available only during a
`USER_INPUT` pause and strictly requires a non-empty string. It records the
interaction after the `ask_user` step and continues without resetting step
numbers or `max_steps`. `confirm(approved)` is available only during a
`CONFIRMATION` pause and accepts an actual boolean. Terminal and failed
sessions cannot resume. Typed session construction, transition, and response
errors fail closed.

The session lock covers each complete `start`, `respond`, or `confirm`
transition, including provider and tool awaits. It prevents concurrent
operations from mutating or resuming one session. It is not the future
RunManager concurrency or HTTP-idempotency layer.

## Explicit confirmation binding

The router marks a rejection as confirmation-required only by catching the
concrete `ToolConfirmationRequiredError`. Generic rejection status, exception
text, observations, questions, and `confirmation_for_step` never imply this
condition.

A temporary candidate is created only for that typed rejection of an MCP
decision whose arguments can be normalized safely. It survives for exactly
the immediately following decision. That decision must be a valid `ask_user`
whose `confirmation_for_step` exactly equals the rejected step. Any missing or
wrong reference, policy denial, invalid control or tool arguments, intervening
tool/control, older reference, or unsafe normalization discards the candidate
and produces a normal `USER_INPUT` pause.

Once bound, `PendingConfirmation` holds only the rejected decision's exact tool
name, original step number, and canonical JSON arguments. Canonicalization
sorts keys, uses compact separators, preserves UTF-8, rejects non-finite
numbers, and accepts only strict JSON-native structures: exact dictionaries
with string keys, exact lists, strings, booleans, integers, finite floats, and
null. The root must be an exact dictionary. Tuples, sets, bytes, arbitrary
mapping or sequence subclasses, custom objects, circular containers, and other
unsupported Python structures are rejected rather than silently converted.
Stored text is decoded with duplicate keys and non-finite constants forbidden,
then re-canonicalized and required to match exactly. Fresh independent mappings
are reconstructed for every access, so caller mutation cannot alter the
snapshot.

## Single-use replay and limits

Both approval and rejection atomically consume the pending record.
`confirm(False)` records the boolean rejection and resumes without executing
the tool. `confirm(True)` clears pending and candidate state before any await,
reconstructs the exact saved call, and routes it once with trusted confirmation.
The model is not asked to recreate the call first.

Replay is a new numbered `AgentStepRecord` with
`replay_of_step_number` pointing to the rejected step. It consumes the same
total `max_steps` budget as model decisions. If the budget is already full,
the pending record is still consumed, no fake replay step is added, and the
result is `STEP_LIMIT_REACHED`. If replay uses the final slot, no further
provider decision is requested.

Normalized replay failures are recorded normally and cannot reuse the
approval. If replay raises an unexpected exception, timeout, or cancellation,
the pending state remains consumed and the session fails closed; it is never
restored or automatically retried. The lock plus consume-before-await ordering
guarantees one pending confirmation can cause at most one execution attempt.

## Boundaries

All state is process memory only. There is no persistence, service-restart
resume, FastAPI interface, HTTP RunManager, request-id idempotency, login, or
download handling yet.

`respond()` is not a secret-input path. Passwords, OTPs, credentials, API
keys, confidential data, and institution data must not be supplied through
it. Secret-safe interaction remains future Milestone 9 work.
