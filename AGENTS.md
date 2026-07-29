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
Agent Control Tools, is completed. Milestone 5, Deterministic MCP-backed Mock
Agent Loop, is completed. Milestone 6, OpenAI-Compatible LLM Provider, is
completed. Milestone 7 technical implementation, review, 217 passing tests,
Learning Handoff, live verification, human feature commit, human push, and
remote feature-checkpoint verification are complete. Milestone 7, MVP
Evaluation, is completed. Its verified feature checkpoint is
`d2e2716f646d3aaaff6eab1624a91609a3688b00` (`feat: add MVP evaluation
framework`). Milestone 7 adds provider-neutral evaluation types and
deterministic `PASS`, `FAIL`, and `ERROR` scoring, with `ERROR` taking
exit-code precedence over `FAIL`; exactly three public-data scenarios using
`https://example.com`; sanitized JSON reports; an import-safe executable
runner; and one fresh MCP/browser session per scenario with internal-only
`browser_close` cleanup. The scenarios cover title extraction, read-only link
extraction, and the policy-enforced confirmation boundary.

Provider visibility is not execution authority. `AgentToolRouter`,
`McpToolPolicy`, `PolicyEnforcedToolExecutor`, `McpToolGateway`, and the local
agent controls remain the enforcement and execution boundaries. Milestone 7
offline validation recorded 217 passing tests, passing `py_compile`, and
passing `git diff --check`. Its first valid live baseline on `2026-07-29`,
using temporary external model `z-ai/glm-5.2` and only public
`https://example.com` data, remains documented as 2 PASS / 1 FAIL / 0 ERROR,
66.67%, exit code 1. The sole failure exposed a stale oracle that expected
`More information` instead of the fixture's visible `Learn more`; this
historical baseline was not replaced or rewritten. After correcting the
declared expectation and relevant task text without prompt tuning, scoring
relaxation, or acceptance-criteria weakening, a separate live verification
recorded 3 PASS / 0 FAIL / 0 ERROR, 100.00%, exit code 0, and empty stderr.
The confirmation scenario observed `browser_click REJECTED` followed by
`ask_user AWAITING_USER`: the model proposed decisions, no automatic
confirmation was granted, and the rejected click did not execute.

These live results prove only temporary external real-model integration,
public `example.com` browser use, integration of the existing
MCP/policy/agent-loop/evaluator boundaries, and safe confirmation-boundary
behavior in these scenarios. They do not prove offline operation, on-premises
deployment, confidential or institution-data safety, local vLLM integration,
broad statistical reliability, or production readiness. Milestone 8, local
vLLM planning and integration, is the current milestone. Milestone 8 planning
has started, and its implementation has not started.

Confirmation UI, trusted approval, user-response waiting and resume, and
persistence remain later work. Dynamic discovery remains distinct from
authorization. Do not recreate the lost asynchronous `BrowserService` draft
unless a separately approved plan explicitly requires it.

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
