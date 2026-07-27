# Browser Agent Roadmap

## Project goal

Build an LLM-assisted web automation service that accepts a URL and a
natural-language task, lets an LLM choose validated actions one step at a time,
executes those actions in a browser, and returns observations until the task is
finished or user input is required.

The first implementation will use Microsoft's open-source Playwright MCP as a
local browser-capability process. Our Python application will remain responsible
for orchestration, policy enforcement, agent control, and LLM-provider
integration.

## MVP definition

The browser-agent MVP is a local Python application that:

- starts or connects to Playwright MCP locally through stdio;
- discovers the available MCP tools at runtime;
- exposes an approved subset of those tools to the agent;
- classifies and checks tools before execution;
- supports agent-control tools such as `finish` and `ask_user`;
- runs a deterministic tool-call and observation loop;
- supports an OpenAI-compatible LLM provider behind a replaceable interface;
- completes representative tasks from all three MVP task families; and
- does not expose arbitrary server-side code execution to the LLM.

FastAPI, durable sessions, database persistence, Docker, and offline packaging
are not required for this MVP.

## Milestones

### 0. Repository recovery and project memory

**Purpose:** Recover from the loss of the uncommitted async draft by recording
the agreed direction, current state, working rules, and ordered delivery plan.

**Acceptance criteria:**

- The roadmap, architecture, and project-status documents exist and agree.
- `AGENTS.md` describes the local Playwright MCP direction and mandatory
  delivery stages.
- The documents do not claim that MCP integration or new application code
  already exists.
- The next task is explicitly identified as the local connectivity spike.

### 1. Local Playwright MCP connectivity spike

**Purpose:** Prove that Python can start Microsoft's Playwright MCP locally,
communicate through stdio, list tools, invoke a small safe subset, and shut down
cleanly.

**Acceptance criteria:**

- A documented local launch command or configuration starts Playwright MCP.
- The Node.js version is recorded.
- The Playwright MCP version is pinned and recorded.
- The Python MCP SDK version is recorded.
- The Python spike completes MCP initialization and dynamic tool discovery.
- A reviewable inventory records discovered tool names and concise input-schema
  summaries.
- At least one navigation and one page-inspection operation work against public
  or synthetic test content.
- Startup, protocol, timeout, and shutdown failures are visible and handled.
- No network port is exposed for MCP.

### 2. Dynamic MCP tool gateway

**Purpose:** Introduce a typed boundary that discovers, normalizes, invokes, and
returns results from Playwright MCP without hard-coding the whole upstream tool
catalog.

**Acceptance criteria:**

- Tool names, descriptions, and input schemas are discovered at runtime.
- The gateway presents a stable internal representation to its callers.
- Tool arguments are validated before MCP invocation.
- Tool results and MCP errors are normalized into explicit observations.
- Tests cover discovery, invocation, invalid input, and transport failure.

### 3. Tool classification and policy layer

**Purpose:** Decide which discovered browser tools an LLM may see and under what
conditions they may execute.

**Acceptance criteria:**

- Tools are classified as read-only, mutating, sensitive, or unsafe.
- An allowlist or equivalent explicit policy controls LLM-visible tools.
- Mutating and sensitive operations have defined confirmation or denial rules.
- Unsafe arbitrary host or server-side code execution, including
  `browser_run_code_unsafe` or an equivalent, is never exposed to the LLM.
- `browser_evaluate` is not exposed by default and may be enabled only for a
  narrowly justified page-context task through an explicitly approved policy.
- Discovered file upload or download tools are excluded from the initial MVP
  allowlist.
- Policy decisions and denials are testable and observable.

### 4. Agent-control tools

**Purpose:** Add application-owned tools that control the agent conversation
rather than the browser.

**Acceptance criteria:**

- `finish` records a final result and stops the loop.
- `ask_user` pauses with a clear request for missing information or approval.
- Agent-control tools are distinguishable from Playwright MCP tools.
- Input validation, invalid state transitions, and repeated calls are tested.

### 5. Deterministic mock agent loop

**Purpose:** Prove orchestration independently of a live LLM by using scripted,
repeatable decisions.

**Acceptance criteria:**

- The loop supplies approved tool definitions and current observations to a
  deterministic mock provider.
- It validates and executes one selected tool at a time.
- Results are fed into the next decision.
- The loop terminates through `finish`, pauses through `ask_user`, and enforces
  step and failure limits.
- Automated tests cover successful, denied, malformed, paused, and exhausted
  flows.

### 6. OpenAI-compatible LLM provider

**Purpose:** Connect a real tool-calling model without coupling the agent loop to
a single hosted or local provider.

**Acceptance criteria:**

- A provider interface separates model requests and responses from the loop.
- An OpenAI-compatible implementation translates discovered tools, messages,
  tool calls, and errors.
- Provider configuration is external to orchestration logic.
- Tests use mocks or fakes and do not require secrets.
- Only synthetic or public test data is used with external services.

### 7. General browser-agent MVP evaluation

**Purpose:** Verify useful end-to-end behavior across the three required task
families and identify reliability or safety gaps.

**Acceptance criteria:**

- All three demonstration scenarios below run end to end.
- Evaluation records task success, steps, tool denials, errors, and termination.
- Browser capabilities include the approved subset needed for navigation,
  accessibility inspection, interaction, waiting, tabs, dialogs, screenshots,
  console inspection, network inspection, scraping, and page analysis.
- `browser_evaluate` is not exposed by default. It is enabled only for a
  narrowly justified page-context task through an explicitly approved policy;
  host or server-side arbitrary code execution remains excluded.
- Known failures and follow-up work are documented without overstating MVP
  capability.

### 8. Local vLLM integration

**Purpose:** Run the LLM provider locally while preserving the same agent-loop
and tool-policy design.

**Acceptance criteria:**

- A local vLLM OpenAI-compatible endpoint can replace the external provider by
  configuration.
- Tool schema and tool-call translations work with the selected local model.
- The three MVP scenarios are reevaluated for quality and compatibility.
- Provider-specific limitations are isolated in the provider adapter.
- Sensitive browser observations can remain local in the demonstrated flow.

### 9. Session and persistence layer

**Purpose:** Persist sessions, steps, tool calls, observations, user pauses, and
file metadata so work can be inspected or resumed.

**Acceptance criteria:**

- A separately approved data model defines stored state and retention.
- Session lifecycle and resume behavior are explicit.
- Sensitive observations receive appropriate access and redaction treatment.
- Persistence failures do not silently corrupt agent state.
- Migrations and integration tests exist before this milestone is complete.

### 10. Offline packaging and optional Docker

**Purpose:** Make the validated local system reproducible and, where useful,
deployable without relying on an always-online development environment.

**Acceptance criteria:**

- Installation, browser assets, model requirements, and startup are documented.
- An offline or controlled-network setup path is demonstrated.
- Docker remains optional and is added only if it improves reproducibility or
  deployment.
- Packaging preserves the local trust boundaries and does not expose an MCP
  port by default.

## End-to-end MVP demonstration scenarios

### 1. Navigation and structured scraping

Given a public or synthetic multi-page site, navigate to a specified section,
inspect it through accessibility-based snapshots, follow relevant links, and
return structured records with source-page context.

### 2. Forms and browser interaction

Given a synthetic form, fill text fields, select options, use keyboard
interaction, handle a dialog or tab when needed, wait for a visible completion
condition, and report the submitted non-sensitive values and outcome.

### 3. Page, console, and network analysis

Given a public or synthetic diagnostic page, inspect page structure, capture a
screenshot, review relevant console messages and network activity, and produce
an evidence-based explanation without executing arbitrary server-side code.

## Deferred features

The following are intentionally deferred until their milestones or a separately
approved plan:

- FastAPI and other HTTP endpoints;
- multi-user or long-running service deployment;
- PostgreSQL, SQLAlchemy, Alembic, and durable session persistence;
- file upload, file download, and download lifecycle management;
- credential handling and authenticated institution systems;
- offline model and browser packaging;
- Docker and Docker Compose;
- production observability, scaling, and distributed workers;
- a fork or vendored copy of Playwright MCP;
- unrestricted JavaScript or arbitrary server-side code execution.

## Mandatory checkpoint process

Every milestone follows this sequence:

1. **Review diff:** inspect the exact changes for scope, correctness, safety,
   architectural consistency, and accidental edits.
2. **Test:** run the approved automated and manual checks and record the actual
   results.
3. **Learning handoff:** explain the changed files, important concepts, data
   flow, callers, inputs, outputs, stored state, and failure cases.
4. **Human commit:** the developer reviews and creates the commit; Codex never
   commits.
5. **Human push:** the developer pushes the commit; Codex never pushes.
6. **Remote verification:** verify that the intended commit and files exist on
   the tracked remote branch.

A milestone is not complete until all six checkpoints have occurred in order.
