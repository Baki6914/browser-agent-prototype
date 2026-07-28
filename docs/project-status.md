# Project Status

## Status summary

- **Active branch:** `mvp/playwright-mcp-agent`
- **Remote tracking:** configured for `origin/mvp/playwright-mcp-agent`
- **Current completed milestone:** Milestone 4, Agent Control Tools
- **Current implementation:** synchronous learning prototype, verified local
  Playwright MCP connectivity spike, dynamic MCP tool gateway, typed tool
  classification and policy layer, and typed application-owned `finish` and
  `ask_user` controls
- **Current phase:** Preparing and reviewing the Milestone 5, Deterministic
  MCP-backed Mock Agent Loop, Implementation Brief

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
- Milestone 3 introduced `ToolCategory`, `PolicyAction`, immutable
  `ToolPolicyDecision`, a policy-owned error hierarchy, `McpToolPolicy`, and
  `PolicyEnforcedToolExecutor`.
- The explicit typed policy registry covers all 24 tools in the committed
  inventory and is exposed as an immutable `MappingProxyType`.
- The verified base policy distribution is:
  - `ALLOW` (11): `browser_console_messages`, `browser_find`,
    `browser_hover`, `browser_navigate`, `browser_navigate_back`,
    `browser_network_request`, `browser_network_requests`, `browser_resize`,
    `browser_snapshot`, `browser_take_screenshot`, and `browser_wait_for`.
  - `REQUIRE_CONFIRMATION` (8): `browser_click`, `browser_drag`,
    `browser_fill_form`, `browser_handle_dialog`, `browser_press_key`,
    `browser_select_option`, `browser_tabs`, and `browser_type`.
  - `INTERNAL_ONLY` (1): `browser_close`.
  - `DENY` (4): `browser_drop`, `browser_evaluate`,
    `browser_file_upload`, and `browser_run_code_unsafe`.
- Unknown or unclassified tools fail closed as `DENY`.
- Denied and internal-only tools are excluded from the LLM-visible catalog.
- Normal invocation and internal-only invocation are separate;
  `browser_close` is available only through the internal cleanup path, and
  that path cannot bypass `DENY`.
- A non-empty filename escalates an otherwise `ALLOW` observation tool to
  `REQUIRE_CONFIRMATION`.
- `confirmation_granted` is separate trusted orchestrator state. Strict
  runtime validation accepts only an actual `bool`; truthy non-booleans such
  as strings, integers, lists, or dictionaries are rejected before gateway
  invocation.
- The real discovery-only policy smoke started MCP, initialized the session,
  discovered and classified 24 tools, and shut down successfully. It reported
  `allow=11`, `deny=4`, `internal_only=1`, and
  `require_confirmation=8`; it invoked no browser tool.
- The verified Milestone 3 code checkpoint is
  `8b14ef7a3b65ad9d56a70e8ef3f9fee6d5fafb31` (`feat: add MCP tool policy
  layer`).
- The Milestone 3 code checkpoint was committed and pushed by the human, the
  local and remote branch hashes were verified equal, and the working tree was
  clean after the push.
- Milestone 3, Tool Classification and Policy Layer, is completed.
- Milestone 4 introduced `AgentControlStatus` with `FINISHED` and
  `AWAITING_USER`, immutable `AgentControlDefinition` and
  `AgentControlResult`, a control-owned error hierarchy, private immutable
  control specifications, and `AgentControlExecutor`.
- Exactly two application-owned controls are defined: `finish` and `ask_user`.
  Their definitions use deterministic alphabetical ordering and fresh
  defensive copies of nested schemas.
- Strict dependency-free validation fails closed for unknown or empty control
  names, browser or MCP names, non-mapping arguments, missing or unexpected
  fields, non-string values including booleans, and empty or whitespace-only
  strings.
- Accepted text is preserved exactly. `finish` returns `FINISHED`, with the
  supplied result in both `text` and `final_result` and `question=None`.
  `ask_user` returns `AWAITING_USER`, with the supplied question in both
  `text` and `question` and `final_result=None`.
- Explicit execution branches route `finish` and `ask_user`; a future control
  registered without an execution branch fails closed with
  `AgentControlError`.
- Unexpected argument fields use deterministic `repr`-sorted rendering.
- Agent controls remain separate from MCP and browser tools: they are not
  discovered through MCP, added to MCP policy, or sent to the policy executor,
  gateway, MCP session, Playwright, subprocesses, or network services.
- The verified Milestone 4 feature checkpoint is
  `1146ad838d44edb65091fb964ff605db1e502556` (`feat: add agent control
  tools`).
- The full unit-test suite ran 103 tests and all passed with final result
  `OK`; `py_compile` and `git diff --check` passed.
- No live MCP or browser smoke was needed for the dependency-free local
  controls.
- The Milestone 4 feature checkpoint was committed and pushed by the human,
  the local and remote branch hashes were verified equal, and the working tree
  was clean after the push.
- Milestone 4, Agent Control Tools, is completed.

The gateway remains the browser-capability boundary and normalization layer.
Dynamic discovery is capability information, not authorization. The policy
layer enforces authorization decisions. The agent-control layer remains local
to the orchestrator. Neither layer is an agent loop.

## In progress

- Preparation and review of the Milestone 5, Deterministic MCP-backed Mock
  Agent Loop, Implementation Brief.

## Not started

- A tool router.
- A deterministic MCP-backed mock agent loop.
- A real confirmation UI or question presentation.
- User-response waiting and resume.
- Trusted approval-state management or persistence.
- Session and step persistence.
- Step and timeout limits.
- An OpenAI-compatible LLM provider.
- General browser-agent MVP evaluation.
- Local vLLM integration.
- HTTP API work.
- Database work.
- Offline packaging and optional Docker.

Milestone 5, Deterministic MCP-backed Mock Agent Loop, is the next engineering
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
- The Milestone 2 gateway smoke invoked only `browser_navigate`,
  `browser_snapshot`, and `browser_close`. It did not invoke
  `browser_evaluate`, `browser_run_code_unsafe`, file upload, file download, or
  any other discovered tool.
- The distinct Milestone 3 policy smoke performed MCP startup, session
  initialization, `list_tools` discovery, local classification, and shutdown
  only. It did not call `McpToolGateway.invoke()`,
  `PolicyEnforcedToolExecutor.invoke()`,
  `PolicyEnforcedToolExecutor.invoke_internal()`, `session.call_tool()`,
  `browser_navigate`, `browser_close`, or any other browser tool.
- Dynamic discovery is capability information, not authorization. The policy
  registry is tied to the current committed 24-tool inventory. Any newly
  discovered unregistered tool fails closed as `DENY` and requires human
  review.
- `confirmation_granted` is only an enforcement input. No confirmation UI,
  trusted approval source, identity, or persisted approval exists.
- `AgentControlExecutor` performs only local Python validation and result
  construction. `finish` and `ask_user` never invoke MCP or browser tools.
- `finish` does not verify factual task completion.
- `ask_user` produces `AWAITING_USER` only; it does not display a UI, wait for
  an answer, persist it, or resume execution automatically.
- No tool router or deterministic loop exists yet, and no real LLM provider
  exists.
- The policy layer and agent-control layer are implemented but are not yet
  joined by a loop. The intended future route sends agent controls to
  `AgentControlExecutor` and browser tools to
  `PolicyEnforcedToolExecutor`.
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

Prepare and review the Milestone 5, Deterministic MCP-backed Mock Agent Loop,
Implementation Brief. Milestone 5 implementation has not started.

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

Also verified on `2026-07-28`:

- The verified Milestone 3 code checkpoint is
  `8b14ef7a3b65ad9d56a70e8ef3f9fee6d5fafb31`, with commit subject
  `feat: add MCP tool policy layer`.
- The unit-test suite ran 78 tests and all passed with final result `OK`.
- `py_compile` passed.
- `git diff --check` passed.
- The real command `python scripts/mcp_policy_smoke.py` discovered and
  classified 24 tools with action counts `allow=11`, `deny=4`,
  `internal_only=1`, and `require_confirmation=8`.
- Its stdout ended with
  `MCP policy smoke succeeded: discovery and classification only`, stderr was
  empty, and the exit code was 0.
- The smoke performed MCP startup, session initialization, `list_tools`
  discovery, local classification, and shutdown only. It did not invoke the
  gateway or either policy-executor path, call `session.call_tool()`, or invoke
  `browser_navigate`, `browser_close`, or any other browser tool.
- Explicit coverage of all 24 committed inventory tools, unknown-tool
  fail-closed denial, LLM-visible filtering, separate normal and internal-only
  invocation paths, filename escalation, strict boolean confirmation
  validation, and immutable registry behavior were verified.
- The Milestone 3 code checkpoint was committed and pushed by the human, the
  local and remote branch hashes were verified equal, and the working tree was
  clean after the push.

Milestone 3, Tool Classification and Policy Layer, is completed.

Also verified on `2026-07-28`:

- The verified Milestone 4 feature checkpoint is
  `1146ad838d44edb65091fb964ff605db1e502556`, with commit subject
  `feat: add agent control tools`.
- The full unit-test suite ran 103 tests and all passed with final result
  `OK`.
- `py_compile` passed.
- `git diff --check` passed.
- Agent controls were tested without Node, MCP, Chromium, Playwright, network,
  or external services. No live MCP or browser smoke was required because
  their execution is local and dependency-free.
- `finish` preserves its exact non-empty supplied result in `text` and
  `final_result`, returns `FINISHED`, and sets `question=None`.
- `ask_user` preserves its exact non-empty supplied question in `text` and
  `question`, returns `AWAITING_USER`, and sets `final_result=None`.
- Fail-closed validation rejects invalid names and arguments, and explicit
  routing rejects registered but unimplemented future controls.
- The checkpoint was committed and pushed by the human, the local and remote
  branch hashes were verified equal, and the working tree was clean after the
  push.

Milestone 4, Agent Control Tools, is completed.
