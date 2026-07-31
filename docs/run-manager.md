# In-memory RunManager

## Milestone 9B secret ownership

`RunManager` requires and owns one keyword-only `TransientSecretStore`. A
secret pause becomes `AWAITING_SECRET`; successful `submit_secret()` stores the
defensive value copy, retains only a private repr-hidden `SecretReference`,
clears question and interaction, and increments once into
`AWAITING_SECRET_APPLICATION`. Snapshots expose only sorted `secret_fields`.
The session stays paused and the secret is neither consumed nor applied.

Secret idempotency uses a private process-local 32-byte HMAC-SHA256 key and
length-prefixed operation, interaction, sorted field names, and exact UTF-8
values. Raw values, an unkeyed hash, digest, and key are absent from public
state and repr. Final close zeroizes the mutable key.

Lock ordering is manager lock, then per-run lock, then the dependency-free
store operation. Shared shielded cleanup runs outside the record lock.
Cancellation, failure, terminal completion, and shutdown call idempotent
`discard_run`; shutdown attempts cleanup for every run even after a failure,
then always attempts store closure and zeroizes the HMAC key. The first fixed
safe close failure is sticky for concurrent and later callers. Arbitrary
secret-store exceptions are neither retained nor exception-chained.

## Purpose and boundary

`RunManager` is the application-level owner of multiple resumable agent
sessions. The FastAPI layer delegates lifecycle operations to this API without
moving HTTP concerns into the manager.

The manager knows only `AgentRunResult` and three dependency-free protocols:

- `RunSessionProtocol` starts a task and resumes it with either text or a
  trusted boolean confirmation.
- `RunSessionHandleProtocol` exposes the session and closes all resources that
  belong to it.
- `RunSessionFactoryProtocol` asynchronously constructs one handle for a new
  run.

Future composition code owns provider, router, MCP, browser, and session
construction. `RunManager` does not import or construct NVIDIA,
OpenAI-compatible providers, MCP gateways, Playwright, subprocesses, or
network transports.

## Lifecycle and background ownership

`create_run` validates and stores a run as `queued` at version 1, schedules one
`asyncio.Task`, and returns immediately. The task transitions the run to
`running`, asks the factory for a handle, and calls that session's `start`.
Text responses call `respond` on the same session; trusted approvals call
`confirm` on the same session. Resume methods consume the current interaction,
schedule one task, and return the new `running` snapshot without waiting for
session execution.

`RunStatus` is the manager-facing lifecycle enum. It includes queued, running,
the two distinct pause states, finished, failed, cancelled, step-limit, and
decision-source-exhausted states. It does not replace `AgentRunStatus`.
`RunSnapshot` is a frozen copy of the externally visible state. It never
contains a session, handle, task, lock, mutable record, or idempotency map.

Every pause receives a fresh opaque `interaction_id`. The identifier is
single-use and binds a response to the current pause and its expected
operation type. Consuming it clears both the question and identifier before
background execution begins.

## Concurrency, idempotency, and versions

Each run has its own `asyncio.Lock`. Status, interaction consumption,
request-id checks, task ownership, resource references, and snapshot fields
are changed under that lock. Thus a run has at most one session operation in
flight, while unrelated runs do not share their per-run lock.

`respond`, `confirm`, and `cancel` require a caller-provided `request_id`.
The manager stores an immutable per-run fingerprint before scheduling work.
An exact retry returns the latest snapshot even after a later pause or
terminal result. Reusing the identifier for a different operation,
interaction, text, or boolean raises `RunIdempotencyConflictError`.

Version 1 is the initial queued state. Each visible lifecycle transition
increments the positive version once. Inspection and exact idempotent retries
do not increment it. A cleanup failure also increments the version exactly
once when it changes visible status or safe error fields, including when a
cancelled run retains its status and gains cleanup-error details.

## Cancellation, shutdown, and failures

Cancellation first publishes `cancelled`, detaches the active task, then
cancels and awaits it outside the run lock. A paused handle is also closed.
Construction cancellation safely closes a handle that was created but not
attached. Cleanup ownership for an attached handle is represented by one
shared per-run cleanup task. The first cleanup path atomically detaches the
handle and session, creates that task, and stores it under the run lock.
Concurrent terminal completion, cancellation, and manager shutdown paths all
await the same task. Their waits are shielded, so cancelling one waiter does
not cancel the underlying `handle.close` operation. The cleanup task consumes
close failures and records only safe snapshot fields; it is not retried.

`RunManager.close` is idempotent. It marks the manager closed, prevents new
mutations, publishes cancellation for active non-terminal runs, cancels and
awaits their execution tasks, and then awaits shared cleanup while preserving
records for inspection. For an already-terminal run, shutdown does not cancel
an active task that is still finalizing result publication or cleanup; it
awaits that finalization and the shared cleanup task. The manager therefore
does not return while cleanup it knows about is still running. Cleanup
continues when one handle or secret cleanup fails. Store closure is still
attempted, and close succeeds only if every required cleanup succeeds.

Factory, invalid-handle, start, resume, result-consistency, and cleanup errors
become failed snapshots. Cleanup failure preserves an already-cancelled
status. Error snapshots retain only the exception type name and a line-break
normalized message truncated to 500 characters; exception and traceback
objects are never retained. Background task exceptions are consumed by the
task implementation.

Result translation also validates pause ownership. A normal user-input pause
must not contain pending-confirmation state. A confirmation pause must contain
an actual `PendingConfirmation`, so a question alone cannot create an
approval interaction. A finished result must contain non-empty,
non-whitespace final text, and all terminal results must keep pause fields and
pending-confirmation state absent.

## Limitations and next work

This manager is single-process and in-memory. It provides no persistence,
cross-process coordination, resume after service restart, authentication, or
production composition. Its secret submission path stores values transiently,
consumes them once, delegates one handle-bound fill, and resumes with categories
only.

## Milestone 9C application worker

Each run handle owns an applier bound to the same browser session, discovered catalog, and policy-enforced executor. Submission returns `awaiting_secret_application`; a single worker consumes once, fills once, and resumes the session. Cancellation and manager close cancel that active worker and retain the shared-close lifecycle. Filling is never automatically retried, and cancellation may leave a browser field partially filled; rollback is not promised.
