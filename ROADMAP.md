# Browser Agent Roadmap

## Project goal

Build an LLM-assisted web automation service that accepts a URL and a
natural-language task, lets an LLM choose validated actions one step at a time,
executes those actions in a browser, and returns observations until the task is
finished or user input is required.

The implementation uses Microsoft's open-source Playwright MCP as a local
browser-capability process. Our Python application remains responsible for
orchestration, policy enforcement, agent control, and LLM-provider integration.

The intended product architecture is:

Client or user interface → mandatory FastAPI HTTP service → in-memory
`RunManager` → resumable custom agent loop → NVIDIA OpenAI-compatible LLM API
→ `AgentToolRouter` and policy enforcement → Playwright MCP → browser

NVIDIA's OpenAI-compatible API is the active reference provider. Base URL,
model, API key, and timeout remain configurable so another approved
OpenAI-compatible provider can be used without redesigning the agent loop.
Institution-internal LLM integration is not this project's responsibility.
The custom agent loop remains the MVP orchestration approach.

## MVP definition

The implemented learning prototype:

- starts or connects to Playwright MCP locally through stdio;
- discovers the available MCP tools at runtime;
- exposes an approved subset of those tools to the agent;
- classifies and checks tools before execution;
- supports agent-control tools such as `finish` and `ask_user`;
- runs a deterministic tool-call and observation loop;
- supports an OpenAI-compatible LLM provider behind a replaceable interface;
- evaluates representative tasks from all three MVP task families; and
- supports resumable agent sessions and trusted confirmation replay;
- provides an in-memory `RunManager` with per-run concurrency protection,
  request and interaction idempotency, cancellation, and terminal cleanup;
- exposes FastAPI HTTP start, inspect, respond, and cancel endpoints; and
- performs lifespan shutdown through `RunManager.close()`;
- does not expose arbitrary server-side code execution to the LLM.

The complete product MVP is not finished. Secure login and secret handling,
controlled downloads, and a real HTTP composition root that constructs and
wires NVIDIA, MCP, Playwright, browser, and session resources are not
implemented. Persistence, service-restart resume, authentication, production
deployment, and multi-worker coordination are also not implemented.

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

### 8. FastAPI HTTP Run Service and Human-in-the-Loop Resume

**Purpose:** Add the mandatory product HTTP boundary and allow an in-memory run
to pause for a user interaction and resume across HTTP requests while the
service remains running.

**Acceptance criteria:**

- A mandatory FastAPI service exposes start, inspect, respond, and cancel
  behavior.
- An in-memory `RunManager` owns explicit typed run states.
- The custom agent loop resumes after user questions and trusted confirmation.
- Per-run concurrency protection prevents conflicting execution.
- Interaction handling is idempotent.
- Browser and MCP resources are cleaned up on completion, cancellation, and
  failure.
- No persistent session, database, or service-restart resume is added.

**Completion note:** Milestone 8 is completed through three verified
implementation checkpoints:

- M8A: `c6fc1174788fbee1dcf7ecdb1450fbb902b6055e` (`feat: add secure
  resumable agent sessions`)
- M8B: `27859c9778ebadf3fc96d76b2d6d8b2ceef22cf5` (`feat: add in-memory run
  manager`)
- M8C: `e42269d2b4b448131c7324b89108dcd18b0d4fa6` (`feat: add FastAPI run
  service`)

Final M8C validation recorded 26 focused HTTP tests passed, 293 total tests
passed, `py_compile` passed, and `git diff --check` passed. Human commit, push,
and remote verification were completed.

### 9. Secure Login and Secret Handling

**Purpose:** Support login without allowing credentials or one-time passwords
to enter model-visible or serializable state.

**Acceptance criteria:**

- Credentials and optional OTP values are supplied through HTTP interactions.
- Temporary secrets are stored separately from run history.
- Raw secrets are never sent to NVIDIA or included in LLM context, logs,
  reports, or serializable run history.
- Login forms are filled through a secure application-controlled path.
- Only sanitized login observations are returned to the model.
- Tests cover successful, failed, cancelled, duplicate, and invalid
  interactions.

### 10. Controlled Automatic File Download

**Purpose:** Let the agent trigger and safely manage a target-site download
without requiring the user to click the site's download button manually.

**Acceptance criteria:**

- Work begins with a Playwright MCP download-capability spike because the
  mechanism is not yet implemented or proven.
- The agent automatically activates the target site's download action.
- The resulting download is captured or otherwise safely managed.
- A user-selected destination is accepted only within approved filesystem
  boundaries, with a safe default destination when none is supplied.
- Paths, filenames, collisions, partial downloads, failures, and cleanup are
  handled.
- The result exposes sanitized file metadata and the final local path.
- File upload remains out of scope for this milestone.

### 11. End-to-End NVIDIA Browser-Agent MVP

**Purpose:** Demonstrate the complete product flow with the reference provider.

**Acceptance criteria:**

- A task is submitted through HTTP and handled by a real NVIDIA model.
- The flow covers navigation, page inspection, user interaction and resume,
  secure login, form filling, selection, clicking, and automatic download.
- Cancellation and browser/MCP resource cleanup are verified.
- End-to-end evaluation uses only public or synthetic data.

### 12. Installation, HTTP API, Usage, and Adaptation Documentation

**Purpose:** Document how to install, configure, operate, and safely adapt the
completed MVP.

**Acceptance criteria:**

- Installation and NVIDIA environment configuration are documented.
- HTTP endpoints and examples are documented.
- Interaction, confirmation, login, and secret-safety flows are explained.
- Download behavior and destination rules are explained.
- Supported, confirmation-required, denied, and optional actions are listed.
- Troubleshooting is included.
- Configuration of another approved OpenAI-compatible provider is explained.

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

## Optional future milestone — Controlled file upload

File upload is optional future work. It remains disabled, and
`browser_file_upload` remains denied, until a separately approved milestone
changes that policy.

## Explicitly out of scope

- Docker and Docker Compose
- PostgreSQL, SQLAlchemy, Alembic, and database-backed persistence
- Persistent sessions and service-restart resume
- Local vLLM, GPU/VRAM planning, and offline model hosting
- Institution-internal LLM integration
- Production scaling and distributed workers
- Unrestricted JavaScript or arbitrary host or server-side code execution

## Possible future LangGraph reconsideration

LangChain and LangGraph are not part of the current MVP. LangGraph may be
reconsidered only if persistent sessions, service-restart resume, durable
checkpoints, checkpoint history or replay, complex branching, or many
long-lived waiting runs become real requirements.

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
