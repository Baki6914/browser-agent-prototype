# Web Agent Project Instructions

## Project goal

Build an LLM-assisted web automation service.

The service will eventually:

- receive a URL and a natural-language task through an HTTP API,
- inspect and simplify the current web page,
- let an LLM choose one action at a time,
- validate the selected action,
- execute it through Playwright,
- persist session, step, and file metadata,
- pause and request user input or confirmation when necessary.

The architecture document is a reference design, not an immutable specification.
Do not depart from it silently. When proposing an alternative, first explain:

1. the current documented approach,
2. the alternative,
3. why it may be better,
4. its risks and costs,
5. its compatibility with the expected deliverables.

## Current architecture and development stage

The current code is a synchronous learning prototype.

The MVP architecture uses Microsoft's open-source Playwright MCP as a local
process on the same computer:

- Playwright MCP communicates with the Python application through stdio.
- No MCP network port is exposed.
- The Python application is the orchestrator and MCP client.
- MCP tools are discovered dynamically, classified, and filtered before they
  are exposed to the LLM.
- The orchestrator validates policy before executing a selected tool and feeds
  the result back to the LLM.
- Application-owned agent-control tools include `finish` and `ask_user`.
- The LLM provider must be replaceable so a future local vLLM provider does not
  require redesigning the agent loop.
- `browser_run_code_unsafe`, or any equivalent arbitrary host or server-side
  code execution capability, must never be exposed to the LLM.
- `browser_evaluate` is not exposed by default. It may be enabled only for a
  narrowly justified page-context task through an explicitly approved policy.
- Upstream file upload or download tools may be discovered, but they are not
  included in the initial MVP allowlist.

Milestone 1 is completed. Milestone 2, Dynamic MCP Tool Gateway, is completed.
Milestone 3, Tool Classification and Policy Layer, is completed. Milestone 4,
Agent Control Tools, is completed. Typed application-owned `finish` and
`ask_user` controls now exist. These controls remain separate from MCP browser
tools and never reach MCP. The current work is preparation and review of
Milestone 5, Deterministic MCP-backed Mock Agent Loop. Milestone 5 will connect
agent-control routing with policy-enforced MCP tool execution in a
deterministic mock loop. A real LLM provider, confirmation or question UI,
user-response waiting and resume, and persistence remain later work. Dynamic
discovery remains distinct from authorization. Do not recreate the lost
asynchronous `BrowserService` draft unless a separately approved plan
explicitly requires it.

The following are currently out of scope:

- FastAPI endpoints,
- PostgreSQL,
- SQLAlchemy and Alembic,
- Docker and Docker Compose for the initial MVP,
- local vLLM integration before its roadmap milestone,
- the complete agent loop before its roadmap milestones,
- file upload and download lifecycle management,
- credential handling.

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
- Until local vLLM is integrated, use only synthetic or public test data.
- Never send secrets, internal URLs, institution data, or confidential browser
  contents to external services.
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
