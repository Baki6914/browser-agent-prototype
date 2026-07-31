# Milestone 9A: Transient Secret Handling

## Milestone 9B integration

The 9A store is now owned by `RunManager`. Raw values briefly enter the strict
local HTTP model, then one defensive copy feeds exact HMAC fingerprinting and
`TransientSecretStore.put`. Only its private repr-hidden `SecretReference`
remains on the record; public state carries categories only. Keyed HMAC avoids
an offline-testable unkeyed digest for low-entropy passwords and OTPs.

Accepted submissions publish `AWAITING_SECRET_APPLICATION` before a worker
consumes, fills, and resumes. Terminal,
failure, cancellation, and shutdown discard run-owned store records; shutdown
attempts every run cleanup and store closure even after failures, reports a
sticky fixed safe result, and zeroizes the mutable key. Arbitrary store
exceptions are not exposed or chained. Python immutable strings and
temporary UTF-8 copies cannot be reliably zeroized, and no such claim is made.

There is no persistence, authentication, automatic OTP detection,
login-success detection, or restart resume, and Milestone 9 is not complete.

Milestone 9A provides a dependency-free, process-local `TransientSecretStore`
foundation for future secure-login work. It is strictly isolated from HTTP
models, `RunManager`, run snapshots and history, the agent loop, model context,
providers, MCP, Playwright, browsers, logs, reports, persistence, and
checkpoints. It does not fill login forms or detect OTP prompts. M9C remains
future work for consumption, browser application, and session resume.

`SecretField` permits exactly username, password, and OTP fields.
`SecretReference` is immutable metadata: an opaque cryptographically strong
secret ID, exact run and interaction bindings, a deterministic immutable tuple
of fields, and an injected-clock monotonic expiry deadline. The deadline is not
a wall-clock timestamp and neither references nor deadlines are persistable,
portable across processes, or usable for restart recovery. A reference is
bound in full across all five metadata fields; altered references cannot
consume or discard the real record.

The store uses one `asyncio.Lock` to make record ownership and closed-state
transitions atomic. `consume` detaches a record exactly once under that lock,
then returns a new `dict[SecretField, str]`; concurrent consumers cannot both
succeed. `discard` is idempotent when a reference is absent, but rejects a
mismatched binding without deleting the real record. `discard_run` removes
only exact run-ID matches. `close` atomically closes the store and detaches all
records. After close, `put` and `consume` raise `SecretStoreClosedError`, while
`discard`, `discard_run`, and repeated `close` are safe no-ops.

Values are copied into store-owned UTF-8 `bytearray` buffers. Every normal
consume, discard, run cleanup, expiry cleanup, close, and failed insertion
overwrites those mutable buffers with zero bytes. This guarantee is deliberately
narrow: Python cannot promise erasure of caller strings, returned strings,
temporary immutable UTF-8 bytes, allocator copies, interpreter copies, or
operating-system copies.

Expiry uses the injected monotonic clock and occurs at or beyond the deadline.
There is no background cleanup task. Consequently, an expired unused record
may remain in process memory until a later access, discard, run cleanup, or
close detaches and zeroizes it.

The store itself adds no dependencies or logging. M9C integrates it with the
HTTP submission, handle-bound MCP form fill, and safe session-resume path, but
production composition and live MCP compatibility remain unproven. Secure
login is not complete.

## Milestone 9C application boundary

Accepted secrets are consumed once from the transient store and passed briefly to the handle-bound form applier. Application-owned dictionaries and lists are cleared in `finally` blocks, but Python strings, executor or transport copies, interpreter/OS memory, MCP server copies, and browser/page state cannot be claimed as zeroized. MCP observations and dependency failures are sanitized, while safe application events record categories only. A later OTP pause is independent. There is no persistence, authentication, automatic form submission, or login-success guarantee. A live MCP compatibility smoke remains required before Milestone 9 closure.
