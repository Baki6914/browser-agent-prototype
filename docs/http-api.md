# Milestone 8C HTTP API

## Purpose and boundary

Milestone 8C adds a FastAPI HTTP boundary over the existing in-memory
`RunManager` public API. `create_http_app(run_manager)` receives the exact
manager instance from a future composition layer. Importing this module or
constructing the application does not create a run, session, provider, MCP
process, browser, subprocess, or network connection.

The HTTP layer validates and serializes requests, delegates one operation to
`RunManager`, converts the returned immutable `RunSnapshot`, and translates
exceptions. Run lifecycle, concurrency, idempotency, interaction consumption,
confirmation trust, session ownership, and cleanup remain `RunManager`
responsibilities.

## Endpoints

The application defines exactly four product endpoints:

| Method | Path | Success | Manager call |
| --- | --- | --- | --- |
| `POST` | `/runs` | `202 Accepted` | `create_run(start_url, task)` |
| `GET` | `/runs/{run_id}` | `200 OK` | `get_run(run_id)` |
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
  "error": null
}
```

`RunStatus` is serialized as its lowercase enum value. Failed snapshots expose
only the sanitized `error_type` and `error_message` already provided by
`RunManager`; they are represented as `error.type` and `error.message`.
Internal records, sessions, tasks, locks, request maps, and factories are never
serialized.

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

This milestone is an in-process, in-memory boundary. It has no persistence or
resume after service restart, no multi-worker coordination, and no real
provider, NVIDIA, MCP, Playwright, or browser composition. Future composition
work must inject a fully constructed manager.

There is no secret-safe credential path. Passwords, OTPs, credentials, API
keys, confidential data, and institution data must not be sent through these
endpoints. This milestone also provides no authentication, authorization,
CORS, rate limiting, downloads, uploads, files, login, UI, WebSocket, SSE,
Docker, or multi-worker support.
