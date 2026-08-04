# Web Agent Project Instructions

## Project goal

Build an LLM-assisted web automation service.

The service will eventually:

- receive a URL and a natural-language task through an HTTP API,
- inspect and simplify the current web page,
- let an LLM choose one action at a time,
- validate the selected action,
- execute it through Playwright,
- pause and request user input or confirmation when necessary,
- resume the same in-memory run while the service remains running, and
- automatically manage approved file downloads.

The architecture document is a reference design, not an immutable specification.
Do not depart from it silently. When proposing an alternative, first explain:

1. the current documented approach,
2. the alternative,
3. why it may be better,
4. its risks and costs,
5. its compatibility with the expected deliverables.

## Current architecture and development stage

The current code is a synchronous learning prototype with a mandatory FastAPI
HTTP boundary and resumable in-memory runs.

The MVP architecture uses Microsoft's open-source Playwright MCP as a local
process on the same computer:

- Playwright MCP communicates with the Python application through stdio.
- No MCP network port is exposed.
- The Python application is the orchestrator and MCP client.
- MCP tools are discovered dynamically, classified, and filtered before they
  are exposed to the LLM.
- The orchestrator validates policy before executing a selected tool and feeds
  the result back to the LLM.
- Application-owned agent-control tools are `finish`, `ask_user`, and
  `request_secret`.
- The active reference provider is NVIDIA's OpenAI-compatible API. Base URL,
  model, API key, and timeout remain configurable so another approved
  OpenAI-compatible endpoint can be used without redesigning the agent loop.
- The custom agent loop remains the selected MVP orchestration approach.
- Resumable in-memory agent sessions support trusted confirmation replay.
- The mandatory FastAPI HTTP service is backed by an in-memory `RunManager`.
  It exposes `POST /runs`, `GET /runs/{run_id}`,
  `POST /runs/{run_id}/responses`, `POST /runs/{run_id}/cancel`, and
  `GET /runs/{run_id}/files/{file_id}`.
- Transient secret storage and handle-bound `SecretFormApplier` execution keep
  raw secrets outside model-visible and serializable state.
- A run-specific `RunDownloadStore`, `DownloadTrackingExecutor`, path-free
  `DownloadMetadata` in `RunSnapshot`, and HTTP file retrieval provide the
  typed controlled-download application boundary.
- `browser_run_code_unsafe`, or any equivalent arbitrary host or server-side
  code execution capability, must never be exposed to the LLM.
- `browser_evaluate` is not exposed by default. It may be enabled only for a
  narrowly justified page-context task through an explicitly approved policy.
- File upload is optional future work. `browser_file_upload` remains denied by
  default unless a separately approved milestone changes it.

Milestones 1 through 11 are completed. Milestone 10 was completed through M10A
`22153a419b4d865073bc056e227405eeb3379212` (`test: prove controlled
download capability`) and M10B
`a5781ee81c088e9e212eddeaee16c4e2d2a25ec7` (`feat: add minimal controlled
downloads`); M10B's full suite recorded 418 passed. Milestone 11's latest
completed code checkpoint is
`a15eb0dacb8538008cd814a9335d233d05d105a2`; its final automated suite recorded
531 passed. The current phase is PLAN — Milestone 12, Installation, HTTP API,
Usage, and Adaptation Documentation. The product runtime MVP is technically
complete, while the Milestone 12 documentation deliverable remains open.

Provider visibility is not execution authority. `AgentToolRouter`,
`McpToolPolicy`, `PolicyEnforcedToolExecutor`, `McpToolGateway`, and the local
agent controls remain the enforcement and execution boundaries. Dynamic
discovery remains distinct from authorization.

Do not recreate the lost asynchronous `BrowserService` draft unless a
separately approved plan explicitly requires it.

The real application composition root now constructs and wires the configured
NVIDIA/OpenAI-compatible provider, a per-run MCP stdio session and Playwright
browser, policy executor, `SecretFormApplier`, run-specific
`RunDownloadStore`, `DownloadTrackingExecutor`, resumable session,
`RunManager`, FastAPI application, and Operator UI. Each store's
`output_directory` is passed to MCP through `--output-dir`, and the per-run
`PolicyEnforcedToolExecutor` is wrapped by `DownloadTrackingExecutor`.

There is no persistence, restart recovery, database-backed session or
persistent file catalog, production authentication or authorization,
multi-worker coordination, upload, antivirus scanning, checksums, quotas, or
file-type inspection. Broad external-site download compatibility is unproven,
and path validation alone is not production security.

The following are currently out of scope:

- PostgreSQL,
- SQLAlchemy and Alembic,
- Docker and Docker Compose for the initial MVP,
- local vLLM, GPU/VRAM planning, and offline model hosting,
- persistent sessions and service-restart resume,
- institution-internal LLM integration, and
- file upload unless separately approved.

The authoritative future milestone sequence is in `ROADMAP.md`. Planned
components must not be described as implemented.

## Mandatory named stages

Every response must state the current stage. Use one of these names:

### PLAN

Inspect the current state, clarify scope, identify architectural or contract
decisions, and propose a reviewable plan. Do not edit files in this stage.

### IMPLEMENT

Make only the explicitly approved changes and remain within the approved file
list. Add or update tests for every new behavior, unless the approved task is
documentation-only.

### REVIEW

Inspect the complete diff for correctness, scope, security, compatibility,
unintended changes, and consistency with the architecture and acceptance
criteria. Review is not a substitute for tests.

### TEST

Run the approved automated and manual checks. Report the exact commands,
results, skipped checks, and reasons. Never claim a test was run when it was
not.

### LEARNING HANDOFF

After implementation and review, teach the change in a structured handoff. Do
not edit files during the Learning Handoff unless explicitly asked.

The Learning Handoff must explain:

- every changed file and why it changed;
- every important class, function, pattern, and dependency;
- each important input and output;
- which component calls which;
- what state is stored and where;
- important failure cases and how they surface;
- tests run and what they prove; and
- remaining risks, tradeoffs, and follow-up work.

Do not replace the Learning Handoff with a short summary.

### COMMIT CHECKPOINT

Confirm that review, tests, and the Learning Handoff are complete, then hand
control to the human for commit and push. Codex must never commit or push.
After the human push, verify the intended commit and files on the remote branch.

Do not declare a milestone complete before review, tests, Learning Handoff,
human commit, human push, and remote verification.

## Implementation Brief

Before implementation, provide an Implementation Brief and wait for explicit
approval. The brief must state:

- the goal;
- why the change is needed;
- affected files;
- data flow;
- new concepts, patterns, classes, functions, or dependencies;
- acceptance criteria;
- risks and important failure cases; and
- out-of-scope items.

Do not implement without explicit approval. Approval applies only to the stated
scope. If implementation requires a file or decision outside that scope, stop
and request further approval.

## Working rules

- Always plan before editing.
- Do not change files until explicitly asked to implement.
- Do not add or remove dependencies without approval.
- Do not make architectural decisions silently.
- Dependency, public API, data contract, database schema, security-sensitive,
  or architectural changes require explicit approval.
- Never delete tests to make a change pass.
- Codex must never commit or push changes.
- Never add API keys, passwords, internal URLs, confidential documents, or institution-specific data.
- Use only synthetic or public test data with the configured external NVIDIA
  service.
- Never send secrets, internal URLs, institution data, or confidential browser
  contents to external services.
- Secret values must remain outside LLM context, logs, reports, and
  serializable run history when secret handling is implemented.
- Keep each task coherent and reviewable. Do not split a logically complete
change solely to reduce the number of changed files.
- Preserve existing behavior unless the task explicitly changes it.
- Prefer clear, typed Python over clever abstractions.
- Use Python 3.11+.
- Use the local Playwright MCP process as the browser-capability boundary for
  the MVP unless an architectural change is explicitly approved.
- Every new behavior must have a test.
- After each implementation, list:
  - changed files,
  - behavior added,
  - tests run,
  - remaining risks.

## Learning requirement

The developer is learning the architecture while building it.

When introducing a new pattern, class, Python construct, or dependency, explain briefly:

- what it is,
- why it is used here,
- its input and output,
- which component calls it,
- its important failure cases.

Do not perform a large rewrite that would be difficult to follow.

## Scope Control — Latest Rule

This section supersedes any earlier fixed file-count limit in this document.

Change only files explicitly listed in the approved implementation plan.
If another file becomes necessary during implementation, stop and explain why before editing it.
Stop for approval before dependency changes, public API changes, database schema changes, or security-sensitive changes.
Do not commit or push.
