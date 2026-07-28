# Project Status

## Status summary

- **Active branch:** `mvp/playwright-mcp-agent`
- **Remote tracking:** configured for `origin/mvp/playwright-mcp-agent`
- **Current completed milestone:** Milestone 2, Dynamic MCP Tool Gateway
- **Current implementation:** synchronous Playwright learning prototype and a
  verified local Playwright MCP connectivity spike and dynamic MCP tool gateway
- **Current phase:** Preparing and reviewing the Milestone 3, Tool
  Classification and Policy Layer, Implementation Brief

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
- The verified Milestone 1 feature checkpoint is
  `9e063e8c004083d05a5a8111fdb58b197570f296`.
- The verified Milestone 1 error-visibility fix checkpoint is
  `d4a49c72256c8a706b4c51ecd2b2453eecc0f23c`.
- Both Milestone 1 code checkpoints were committed and pushed by the human and
  remotely verified.
- The normal successful CLI path and a controlled blocked-origin failure path
  were verified after the error-visibility fixes.
- Milestone 1, Local Playwright MCP Connectivity Spike, is completed.
- Milestone 2 introduced the `browser_agent` package, SDK-neutral
  `ToolDefinition` and `ToolObservation` models, `McpSessionProtocol`, and
  `McpToolGateway`.
- The gateway dynamically discovers tools at runtime and maintains
  deterministic, defensively copied internal tool definitions.
- Gateway-owned errors cover catalog discovery, unknown tools, invalid
  arguments, and unsupported schemas.
- Dependency-free argument validation runs locally before MCP invocation.
- Tool results are normalized into success, MCP error, and transport error
  observations.
- Cleanup attempts `browser_close` when it was discovered, preserves primary
  and cleanup failure details, and exposes useful nested `ExceptionGroup` leaf
  details.
- The gateway is compatible with the real Playwright MCP `$schema` metadata
  keyword.
- The real local stdio gateway smoke invoked only `browser_navigate`,
  `browser_snapshot`, and `browser_close` and succeeded.
- The verified Milestone 2 code checkpoint is
  `62dfee653eaa11290fc910166f8e7ae6366ed5ee` (`feat: add dynamic MCP tool
  gateway`).
- The Milestone 2 code checkpoint was committed and pushed by the human and
  remotely verified.
- Milestone 2, Dynamic MCP Tool Gateway, is completed.

The gateway is a browser-capability boundary and normalization layer. Dynamic
discovery is not authorization, and the gateway is not an agent loop.

## In progress

- Preparation and review of the Milestone 3, Tool Classification and Policy
  Layer, Implementation Brief.

## Not started

- Tool classification and policy enforcement.
- LLM-visible tool allowlisting and filtering.
- Confirmation rules.
- Agent-control tools such as `finish` and `ask_user`.
- A deterministic MCP-backed mock agent loop.
- An OpenAI-compatible LLM provider.
- General browser-agent MVP evaluation.
- Local vLLM integration.
- Session and persistence work.
- HTTP API work.
- Offline packaging and optional Docker.

Milestone 3, Tool Classification and Policy Layer, is the next engineering
milestone. Its implementation has not started.

## Known constraints

- The repository still contains the synchronous learning prototype.
- A previously generated asynchronous `BrowserService` draft was lost because
  it was never committed and pushed. It was not recreated during Milestone 0
  or the completed Milestones 1 and 2.
- The verified run started Playwright MCP locally, initialized MCP over stdio,
  discovered 24 tools, navigated to `https://example.com`, took a snapshot,
  closed the browser, and exited successfully without exposing an MCP network
  port.
- The real tool inventory includes unsafe and deferred capabilities such as
  `browser_evaluate`, `browser_file_upload`, and
  `browser_run_code_unsafe`. Discovery records advertised capability; it does
  not authorize execution.
- Only `browser_navigate`, `browser_snapshot`, and `browser_close` were invoked.
  No `browser_evaluate`, `browser_run_code_unsafe`, file upload, file download,
  or any other discovered tool was invoked.
- Discovery records advertised capability; it does not authorize execution.
  Tool classification, authorization, LLM-visible filtering, policy
  enforcement, and confirmation rules remain unimplemented.
- The gateway is not a deterministic agent loop and does not provide
  `finish`, `ask_user`, or an LLM provider.
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

Prepare and review the Milestone 3, Tool Classification and Policy Layer,
Implementation Brief. Milestone 3 implementation has not started.

## Last verified checkpoint

Verified on `2026-07-27`:

- Node.js `v20.19.0`, npm `10.8.2`, Python `3.12.13`, Python MCP SDK
  `1.28.1`, Playwright MCP `0.0.78`, and its Playwright dependency
  `1.62.0-alpha-1783623505000` were used.
- The dependency-free unit-test command ran 13 tests and all passed with final
  result `OK` in approximately 0.002 seconds. The tests did not start Node,
  MCP, Chromium, subprocesses, or network access.
- The live connectivity command printed
  `Playwright MCP connectivity spike succeeded`, wrote nothing to stderr, and
  exited with code 0.
- A controlled temporary-script probe of
  `https://not-allowed.invalid` was blocked by the configured allowed-origin
  restriction, exposed the nested `net::ERR_BLOCKED_BY_CLIENT` detail in
  stderr, and exited with code 1. It did not modify the committed script or
  real MCP configuration, and the temporary copy was deleted without being
  committed.
- Error handling always attempts advertised `browser_close`, preserves primary
  and shutdown failures separately or together as applicable, and recursively
  exposes distinct nested `ExceptionGroup` leaf messages in deterministic
  first-seen order.
- The real `list_tools` response contained 24 tools and generated
  `docs/mcp-tool-inventory.json`.
- Real npm resolution generated `package-lock.json`.
- Temporary `.playwright-mcp/` runtime output was removed and is ignored;
  installed `node_modules/` content remains ignored.
- Milestone 1 code checkpoints
  `9e063e8c004083d05a5a8111fdb58b197570f296` and
  `d4a49c72256c8a706b4c51ecd2b2453eecc0f23c` were committed, pushed, and
  remotely verified.

Milestone 1, Local Playwright MCP Connectivity Spike, is completed.

Verified on `2026-07-28`:

- The verified Milestone 2 code checkpoint is
  `62dfee653eaa11290fc910166f8e7ae6366ed5ee`, with commit subject
  `feat: add dynamic MCP tool gateway`.
- The unit-test suite ran 47 tests and all passed with final result `OK`.
- `py_compile` passed.
- `git diff --check` passed.
- The real command `python scripts/mcp_gateway_smoke.py` printed
  `MCP gateway smoke succeeded: navigate, snapshot, and close` to stdout,
  wrote nothing to stderr, and exited with code 0.
- The smoke invoked only `browser_navigate`, `browser_snapshot`, and
  `browser_close`; it did not invoke `browser_evaluate`,
  `browser_run_code_unsafe`, file upload, file download, or any other
  discovered tool.
- Dynamic runtime discovery, local dependency-free argument validation before
  MCP invocation, and normalized success, MCP error, and transport error
  observations were verified.
- The Milestone 2 code checkpoint was committed and pushed by the human, and
  the local and remote branch hashes were verified equal.
- The working tree was clean after the push.

Milestone 2, Dynamic MCP Tool Gateway, is completed.
