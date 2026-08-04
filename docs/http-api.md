# Milestone 8C HTTP API

## Milestone 9B secret response

The existing `POST /runs/{run_id}/responses` accepts a third discriminator:

```json
{"type":"secret","request_id":"request-1","interaction_id":"interaction-1","values":{"username":"synthetic-user","password":"synthetic-password"}}
```

The strict non-empty object permits only `username`, `password`, and `otp`
keys with actual non-whitespace strings. `SecretStr` redacts model repr/str;
values are revealed only at final local conversion before `submit_secret`.
Validation keeps the generic envelope and never echoes the body.

`awaiting_secret` responses contain the safe question, interaction ID, and
sorted `secret_fields`. `awaiting_secret_application` clears question and
interaction while retaining those field categories. Responses never contain
values, references, secret IDs, expiry, fingerprints, keys, or store details.
There is no new endpoint. Milestone 11 later composed this response path into
the real browser-login runtime without changing its HTTP contract.

## Purpose and boundary

Milestone 8C added a FastAPI HTTP boundary over the existing in-memory
`RunManager` public API. The real application factory now supplies the manager
and a run-session factory. Creating a run constructs a separate MCP runtime,
browser session, `RunDownloadStore`, `SecretFormApplier`, and
`DownloadTrackingExecutor`; `RunManager` owns those per-run resources and their
lifecycle. Each store's output directory is passed to Playwright MCP through
`--output-dir`. Importing the HTTP module alone still creates no run, provider,
MCP process, browser, subprocess, or network connection.

The HTTP layer validates and serializes requests, delegates one operation to
`RunManager`, converts the returned immutable `RunSnapshot`, and translates
exceptions. Run lifecycle, concurrency, idempotency, interaction consumption,
confirmation trust, session ownership, and cleanup remain `RunManager`
responsibilities.

## Endpoints

The application defines five product endpoints; the original four lifecycle
endpoints retain their contracts:

| Method | Path | Success | Manager call |
| --- | --- | --- | --- |
| `POST` | `/runs` | `202 Accepted` | `create_run(start_url, task)` |
| `GET` | `/runs/{run_id}` | `200 OK` | `get_run(run_id)` |
| `GET` | `/runs/{run_id}/files/{file_id}` | `200 OK` | `get_download(run_id, file_id)` |
| `POST` | `/runs/{run_id}/responses` | `202 Accepted` | `respond(...)` or `confirm(...)` |
| `POST` | `/runs/{run_id}/cancel` | `200 OK` | `cancel(run_id, request_id)` |

Creation and response acceptance use 202 because execution is asynchronous;
the returned run can still be `queued` or `running`. Endpoints do not poll for
background completion.

Create a run:

```json
{
  "start_url": "https://example.com",
  "task": "Find the report"
}
```

Submit normal user input:

```json
{
  "request_id": "request-1",
  "interaction_id": "interaction-1",
  "type": "user_input",
  "text": "Select 2025"
}
```

Submit a trusted confirmation decision:

```json
{
  "request_id": "request-2",
  "interaction_id": "interaction-2",
  "type": "confirmation",
  "approved": true
}
```

Cancel a run:

```json
{
  "request_id": "request-3"
}
```

`request_id` gives `RunManager` the idempotency identity for a mutating
request. `interaction_id` identifies the currently pending pause and prevents
a response from being applied to another interaction. The HTTP layer passes
both values unchanged and does not implement either rule itself.

The two response forms are exclusive. `user_input` requires non-empty `text`
and forbids `approved`; `confirmation` requires an actual JSON boolean
`approved` and forbids `text`. Nulls, coercible primitive values, extra fields,
and whitespace-only required strings are rejected.

## Response

```json
{
  "run_id": "run-1",
  "status": "awaiting_user",
  "version": 3,
  "start_url": "https://example.com",
  "task": "Find the report",
  "step_count": 2,
  "question": "Which year?",
  "interaction_id": "interaction-1",
  "final_result": null,
  "error": null,
  "secret_fields": null,
  "secret_targets": null,
  "files": [
    {"file_id": "8f4...", "filename": "report.pdf", "size": 1234}
  ]
}
```

`RunStatus` is serialized as its lowercase enum value. Failed snapshots expose
only the sanitized `error_type` and `error_message` already provided by
`RunManager`; they are represented as `error.type` and `error.message`.
Internal records, sessions, tasks, locks, request maps, and factories are never
serialized.

`files` is always an ordered array of safe metadata and never contains a path
or output directory. A file request returns the exact retained bytes with
`Content-Type: application/octet-stream` and a normal `Content-Disposition`
attachment filename derived from the safe metadata filename. The private
server path is supplied only to `FileResponse`.

## Errors and validation

Errors use one stable envelope:

```json
{
  "error": {
    "code": "run_not_found",
    "message": "run not found"
  }
}
```

| Exception | HTTP status | Code |
| --- | --- | --- |
| `RunNotFoundError` | 404 | `run_not_found` |
| `RunFileNotFoundError` | 404 | `run_file_not_found` |
| `RunIdempotencyConflictError` | 409 | `idempotency_conflict` |
| `RunConflictError` | 409 | `run_conflict` |
| `RunManagerClosedError` | 503 | `run_manager_closed` |
| `RunConstructionError` | 422 | `run_construction_error` |
| other `RunManagerError` | 400 | `run_manager_error` |
| request validation | 422 | `validation_error` |
| unexpected exception | 500 | `internal_error` |

Known manager messages have line breaks replaced with spaces and are limited
to 500 characters. Unexpected errors return exactly `Internal server error.`
Validation returns `Request validation failed.` and never returns the raw
request body, submitted values, or Pydantic `input` data.

## Lifespan and current limitations

FastAPI lifespan shutdown awaits `run_manager.close()`. It is not called at
application construction or after requests, and the HTTP layer invokes no
other cleanup operation. Repeated application lifespans rely on the injected
manager's idempotent close contract.
HTTP tests enter the real `app.router.lifespan_context(app)` and use HTTPX
`ASGITransport` on the same event loop for entry, requests, and exit. They do
not substitute a direct manager close for lifespan cleanup.

This remains an in-process, in-memory boundary. It has no persistence or resume
after service restart and no multi-worker coordination. The application
factory provides the real NVIDIA, MCP, Playwright, browser, and per-run resource
composition. The Operator UI is a client of these same HTTP endpoints; it does
not introduce a separate run-control API.

The secret response variant is the transient credential and OTP submission
path. Other response variants must not carry secrets. The prototype provides
no HTTP authentication, authorization, CORS, rate limiting, uploads,
WebSocket, SSE, Docker, or multi-worker support. The Operator UI and browser
login flow added later use the same HTTP and secret-safety boundaries.

Download access is process-local and unauthenticated in this prototype. It has
no persistence, restart recovery, custom range handling, authentication, or
authorization. The application factory binds each run's download output
directory to MCP and serves registered files through the existing retrieval
endpoint.

## Milestone 9C secret summaries

The existing responses endpoint is unchanged. Secret pauses and the immediate `awaiting_secret_application` response include summaries containing only `field` and `name`; element refs never cross HTTP. Clients poll the existing run GET endpoint for later completion. The application fills fields locally but does not click, press Enter, or submit the form.
