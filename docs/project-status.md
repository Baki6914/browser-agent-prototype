# Project Status

## Status summary

- **Active branch:** `mvp/playwright-mcp-agent`
- **Remote tracking:** configured for `origin/mvp/playwright-mcp-agent`
- **Current milestone:** Milestone 1, local Playwright MCP connectivity spike
- **Current implementation:** synchronous Playwright learning prototype and a
  verified local Playwright MCP connectivity spike
- **Current phase:** Results documentation and milestone workflow completion

## Completed

- A synchronous Playwright learning prototype exists in the repository.
- `agent_demo.py` contains a fake, deterministic LLM decision example.
- The active branch and its remote-tracking configuration have been verified.
- The project has selected a local Playwright MCP architecture direction for
  the MVP.
- Milestone 0 was reviewed, tested as applicable, handed off, committed and
  pushed by the human, and remotely verified.
- The verified Milestone 0 commit is
  `7abe516fc114c5c46e57661eb93b7fe510fc0018`.
- Milestone 1 technical connectivity acceptance criteria were demonstrated on
  `2026-07-27`.

The demonstrated integration is limited to the connectivity spike. It does not
constitute a general MCP gateway, policy layer, or agent loop.

## In progress

- Milestone 1: a narrow local Playwright MCP connectivity spike.
- The implementation and generated artifacts still require complete diff
  review, applicable checks, Learning Handoff, human commit, human push, and
  remote verification before Milestone 1 is complete.

## Not started

- A dynamic MCP tool gateway.
- Tool classification and policy enforcement.
- Agent-control tools such as `finish` and `ask_user`.
- A deterministic MCP-backed mock agent loop.
- An OpenAI-compatible LLM provider.
- General browser-agent MVP evaluation.
- Local vLLM integration.
- Session and persistence work.
- Offline packaging and optional Docker.

Milestone 2, Dynamic MCP Tool Gateway, is the next engineering milestone. It
has not started.

## Known constraints

- The repository still contains the synchronous learning prototype.
- A previously generated asynchronous `BrowserService` draft was lost because
  it was never committed and pushed. It was not recreated during Milestone 0
  and is not part of Milestone 1.
- The verified run started Playwright MCP locally, initialized MCP over stdio,
  discovered 24 tools, navigated to `https://example.com`, took a snapshot,
  closed the browser, and exited successfully without exposing an MCP network
  port.
- The real tool inventory includes unsafe and deferred capabilities such as
  `browser_evaluate`, `browser_file_upload`, and
  `browser_run_code_unsafe`. Discovery records advertised capability; it does
  not authorize execution.
- Only `browser_navigate`, `browser_snapshot`, and `browser_close` were invoked.
  No evaluation, arbitrary code, upload, or download tool was called.
- Tool classification, authorization, filtering, and policy enforcement remain
  unimplemented.
- Allowed origins are a defensive configuration, not a complete security
  boundary. Local MCP transport does not make target web content trusted.
- File upload and download lifecycle management and credential handling remain
  deferred.
- Docker is not required for the first browser-agent MVP.
- FastAPI, sessions, persistence, offline packaging, and production deployment
  remain out of scope.
- Until local vLLM integration, only synthetic or public test data may be used.
- Secrets, internal URLs, institution data, and confidential browser contents
  must not be sent to external services.

## Immediate next step

Complete the Milestone 1 diff review, applicable checks, and Learning Handoff,
then hand control to the human for commit and push. After the human push,
verify the intended commit and files on the remote branch. The next engineering
milestone after that checkpoint is Milestone 2, Dynamic MCP Tool Gateway.

## Last verified checkpoint

Verified on `2026-07-27`:

- Node.js `v20.19.0`, npm `10.8.2`, Python `3.12.13`, Python MCP SDK
  `1.28.1`, Playwright MCP `0.0.78`, and its Playwright dependency
  `1.62.0-alpha-1783623505000` were used.
- The dependency-free unit-test command ran 6 tests and all passed.
- The live connectivity command printed
  `Playwright MCP connectivity spike succeeded`, wrote nothing to stderr, and
  exited with code 0.
- The real `list_tools` response contained 24 tools and generated
  `docs/mcp-tool-inventory.json`.
- Real npm resolution generated `package-lock.json`.
- Temporary `.playwright-mcp/` runtime output was removed and is ignored;
  installed `node_modules/` content remains ignored.

Technical connectivity acceptance criteria are demonstrated, but Milestone 1
is not complete until complete diff review, applicable checks, Learning
Handoff, human commit, human push, and remote verification are complete.
