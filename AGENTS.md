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

## Current development stage

The current code is a synchronous learning prototype.

For now, work only on the browser foundation:

- asynchronous Playwright,
- BrowserService,
- simplified PageState representation,
- temporary element IDs,
- navigate,
- inspect_page,
- click,
- automated tests.

The following are currently out of scope:

- FastAPI endpoints,
- PostgreSQL,
- SQLAlchemy and Alembic,
- Docker and Docker Compose,
- NVIDIA or vLLM integration,
- the complete agent loop,
- file downloading,
- credential handling.

## Working rules

- Always plan before editing.
- Do not change files until explicitly asked to implement.
- Do not add or remove dependencies without approval.
- Do not make architectural decisions silently.
- Stop and ask before changing a public function or data contract.
- Never delete tests to make a change pass.
- Never commit or push changes.
- Never add API keys, passwords, internal URLs, confidential documents, or institution-specific data.
- Keep each task coherent and reviewable. Do not split a logically complete
change solely to reduce the number of changed files.
- Preserve existing behavior unless the task explicitly changes it.
- Prefer clear, typed Python over clever abstractions.
- Use Python 3.11+.
- Use async Playwright for production-facing browser code.
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

Do not perform a large rewrite that would be difficult to follow

## Scope Control — Latest Rule
This section supersedes any earlier fixed file-count limit in this document.

Change only files explicitly listed in the approved implementation plan.
If another file becomes necessary during implementation, stop and explain why before editing it.
Stop for approval before dependency changes, public API changes, database schema changes, or security-sensitive changes.
Do not commit or push.
