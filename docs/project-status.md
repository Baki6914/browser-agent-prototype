# Project Status

## Status summary

- **Active branch:** `mvp/playwright-mcp-agent`
- **Remote tracking:** configured for `origin/mvp/playwright-mcp-agent`
- **Current completed milestone:** Milestone 7, MVP Evaluation
  - This is the latest milestone whose implementation, review, tests, Learning
    Handoff, human commit, human push, and remote verification are complete.
- **Current milestone:** Milestone 8, local vLLM planning and integration
- **Current implementation:** synchronous learning prototype, verified local
  Playwright MCP connectivity, dynamic MCP tool gateway, typed classification
  and policy enforcement, typed `finish` and `ask_user` controls,
  `AgentToolRouter`, a deterministic observation-driven agent loop, and a
  provider-neutral real decision source connected to that existing loop, plus
  a provider-neutral MVP evaluation framework
- **Current phase:** PLAN — Milestone 8

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
- Milestone 5 introduced typed `AgentToolSource`, `AgentStepStatus`, and
  `AgentRunStatus`; immutable `AgentToolDefinition`, `AgentStepObservation`,
  `AgentStepRecord`, `AgentLoopContext`, and `AgentRunResult`; defensively
  copied `AgentToolCall`; `AgentDecisionSourceProtocol`; temporary
  `ScriptedDecisionSource`; `McpToolExecutorProtocol`; `AgentToolCatalogError`;
  `AgentToolRouter`; and `DeterministicAgentLoop`.
- `AgentToolCall` accepts only a mapping, deep-copies nested arguments during
  construction, retains no caller-owned mutable dictionary, and returns a
  fresh nested copy through public argument access.
- `AgentToolRouter` combines policy-visible MCP definitions with `finish` and
  `ask_user`, sorts the catalog alphabetically, and returns fresh nested schema
  copies. Duplicate MCP names and MCP/control name collisions are rejected.
- `finish` and `ask_user` remain local and never reach MCP. Browser tools use
  only the supplied policy-enforced executor; the router never calls
  `McpToolGateway` directly. Unknown tools call neither executor.
- The router always passes `confirmation_granted=False`; tool arguments,
  including similarly named arguments, cannot grant trusted approval.
- `DeterministicAgentLoop` validates its task and positive exact-integer
  `max_steps`, rejecting booleans; builds a fresh context for each decision;
  records decisions and observations; and feeds observations into the next
  context.
- `finish` terminates with `FINISHED`; `ask_user` pauses with
  `AWAITING_USER`; source exhaustion and the step limit are typed termination
  states. All run state and step records remain in memory.
- Expected repository-owned control, policy, and gateway errors become
  `REJECTED` observations containing their exception type and message.
  Unexpected programming errors propagate with their original traceback.
- A rejected confirmation-required action is not retried or executed after a
  later `ask_user` decision.
- The live smoke's false-positive protection requires successful browser
  steps and rejects `MCP_ERROR` or `TRANSPORT_ERROR` in either browser step.
- The verified Milestone 5 feature checkpoint is
  `09af169f5370f8b5cfa8087e500555afd02c22fe`, with commit subject
  `feat: add deterministic MCP agent loop`.
- The full unit-test suite ran 126 tests and all passed with final result
  `OK`; `py_compile` and `git diff --check` passed.
- The real MCP smoke passed. Stdout was exactly
  `MCP agent loop smoke succeeded: navigate, snapshot, finish, and close`;
  stderr was empty and the exit code was 0.
- The Milestone 5 feature checkpoint was committed and pushed by the human,
  the local and remote branch hashes were verified equal, and the working tree
  was clean after the push.
- Milestone 5, Deterministic MCP-backed Mock Agent Loop, is completed.
- Milestone 6 introduced the public `OpenAICompatibleProviderConfig`,
  `OpenAICompatibleDecisionSource`, `OpenAIProviderError`,
  `OpenAIProviderConfigurationError`, `OpenAIProviderTimeoutError`,
  `OpenAIProviderTransportError`, `OpenAIProviderHTTPError`, and
  `OpenAIProviderResponseError` types.
- `OpenAICompatibleDecisionSource` implements the existing
  `AgentDecisionSourceProtocol` as a provider-neutral OpenAI-compatible Chat
  Completions boundary. Endpoint, model, API key, and timeout are supplied
  through configuration; no provider-specific NVIDIA runtime class or
  hard-coded runtime endpoint or model was added.
- The provider consumes `AgentLoopContext`. It deterministically serializes the
  task and previous step observations, converts only the context tool
  definitions into OpenAI-compatible function tools, and defensively copies
  nested schemas. Requests use `tool_choice="auto"` and
  `parallel_tool_calls=false`.
- Response parsing accepts exactly one function tool call. Missing, malformed,
  text-only, multiple, or non-object tool-call responses fail closed with
  typed response errors. Syntactically valid unknown tool names remain the
  router's responsibility.
- The provider proposes decisions only. It does not execute tools, call MCP,
  apply policy, or grant confirmation. `AgentToolRouter`, `McpToolPolicy`,
  `PolicyEnforcedToolExecutor`, `McpToolGateway`, and local agent controls
  remain the enforcement and execution boundaries.
- Configuration, timeout, transport, HTTP-status, and response failures use
  typed provider errors. The API key is excluded from configuration `repr`,
  and an echoed API key is redacted before HTTP error-body truncation.
  Base URLs containing literal whitespace are rejected, and invalid response
  UTF-8 is normalized to `OpenAIProviderResponseError`.
- No retries, streaming, persistence, confirmation UI, waiting/resume, or
  local vLLM integration was added.
- The full unit-test suite ran 153 tests with 153 passing and 0 failures.
  `py_compile` and `git diff --check` passed.
- The verified Milestone 6 feature checkpoint is
  `3e195e0fa46d1224a78b3208e20c39e5f0aae489`, with commit subject
  `feat: add OpenAI-compatible decision provider`.
- That checkpoint changed exactly six files: `browser_agent/__init__.py`,
  `browser_agent/openai_provider.py`, `docs/openai-provider.md`,
  `requirements.txt`, `scripts/openai_agent_smoke.py`, and
  `tests/test_openai_provider.py`.
- The feature checkpoint was committed and pushed by the human, the local and
  remote branch hashes were verified equal, and the working tree was clean
  after the feature push.
- A minimal external probe temporarily used NVIDIA API with model
  `z-ai/glm-5.2`, supplied its API root through the generic environment
  contract, returned HTTP 200 in 8.87 seconds, and produced model response
  `OK`.
- The real external-provider plus local-MCP smoke ran
  `python scripts/openai_agent_smoke.py` against only public
  `https://example.com` data. Its decision flow was
  `browser_navigate` -> `browser_snapshot` -> `finish`, followed by internal
  close. Both browser actions succeeded through MCP; `finish` remained a local
  application-owned control and did not reach MCP; `browser_close` ran only
  through `PolicyEnforcedToolExecutor.invoke_internal()`.
- Smoke stdout was exactly
  `OpenAI-compatible agent smoke succeeded: model navigate, snapshot, finish, and close`;
  stderr was empty and the exit code was 0.
- The external checks used only public or synthetic data and committed no API
  key. They prove that a real external OpenAI-compatible model can drive the
  existing loop, router, policy, gateway, MCP, Playwright, and local controls,
  and that provider replacement works through configuration without changing
  the loop.
- NVIDIA API was only the temporary external verification provider. These
  checks do not prove offline operation, on-prem or institution-internal
  deployment, local-vLLM compatibility, approval to use confidential or
  institution data with external models, or production readiness.
- Milestone 6 implementation, review, unit tests, Learning Handoff, human
  feature commit and push, remote feature-checkpoint verification, and live
  external-provider smoke are complete.
- Milestone 6, OpenAI-Compatible LLM Provider, is completed.
- Milestone 7 introduced provider-neutral evaluation result types and
  deterministic `PASS`, `FAIL`, and `ERROR` scoring. `ERROR` has higher
  exit-code precedence than `FAIL`.
- The evaluator defines exactly three public-data scenarios against
  `https://example.com`: title extraction, read-only link extraction, and the
  policy-enforced confirmation boundary.
- Evaluation runs use one fresh MCP/browser session per scenario and perform
  `browser_close` only through the internal cleanup path.
- Sanitized JSON reports omit provider response bodies, redact API keys, and
  avoid raw browser snapshots. The executable runner is import-safe when run
  as `python scripts/openai_agent_eval.py`.
- Offline validation completed with 217 tests passed, `py_compile` passed, and
  `git diff --check` passed.
- The first valid live baseline on `2026-07-29` temporarily used the external
  OpenAI-compatible model `z-ai/glm-5.2`. It recorded 2 PASS / 1 FAIL /
  0 ERROR, a 66.67% pass rate, and exit code 1:
  `title_extraction` passed, `read_only_link_extraction` failed, and
  `confirmation_boundary` passed.
- The read-only scenario safely completed `browser_navigate SUCCESS`,
  `browser_snapshot SUCCESS`, and `finish FINISHED`; no
  confirmation-required interaction succeeded. The model reported the
  fixture's visible link text as `Learn more`. Its only failure was the stale
  oracle expecting `More information`.
- That first baseline remains unchanged as a historical result; it was not
  replaced or rewritten.
- The declared expectation and relevant scenario task text were corrected to
  the public fixture's exact visible text, `Learn more`, for future runs. This
  was an oracle correction, not prompt tuning, scoring relaxation, or
  acceptance-criteria weakening.
- A separate post-correction live verification on `2026-07-29`, using the same
  temporary external model, recorded 3 PASS / 0 FAIL / 0 ERROR, a 100.00% pass
  rate, exit code 0, and empty stderr. All three scenarios passed.
- The confirmation-boundary verification observed
  `browser_navigate SUCCESS` -> `browser_snapshot SUCCESS` ->
  `browser_click REJECTED` -> `ask_user AWAITING_USER`. The model proposes
  decisions, while `AgentToolRouter`, `McpToolPolicy`,
  `PolicyEnforcedToolExecutor`, and `McpToolGateway` remain the authorization
  and execution boundaries. No automatic confirmation was granted; policy
  prevented the rejected click from reaching execution, and the model then
  asked a non-empty confirmation question.
- The live results prove only temporary external real-model integration,
  public `example.com` browser use, integration of the existing
  MCP/policy/agent-loop/evaluator boundaries, and safe confirmation-boundary
  behavior in these scenarios. They do not prove offline operation,
  on-premises deployment, confidential or institution-data safety, local vLLM
  integration, broad statistical reliability, or production readiness.
- Milestone 7 implementation, review, 217 passing tests, Learning Handoff,
  human feature commit, human push, and remote feature-checkpoint verification
  are complete.
- The verified Milestone 7 feature checkpoint is
  `d2e2716f646d3aaaff6eab1624a91609a3688b00`, with commit subject
  `feat: add MVP evaluation framework`.
- The feature checkpoint changed exactly seven files: `AGENTS.md`,
  `browser_agent/__init__.py`, `browser_agent/mvp_evaluation.py`,
  `docs/mvp-evaluation.md`, `docs/project-status.md`,
  `scripts/openai_agent_eval.py`, and `tests/test_mvp_evaluation.py`.
- Local and remote SHA both matched the verified feature checkpoint. The
  branch was up to date with `origin/mvp/playwright-mcp-agent`, the remote
  comparison from checkpoint
  `9721001a8f20bbb75ee90c16a7b8a679d83246bb` was ahead by 1 commit and
  behind by 0 commits, and the working tree was clean after the push.
- Milestone 7, MVP Evaluation, is completed.

The gateway remains the browser-capability boundary and normalization layer.
Dynamic discovery is capability information, not authorization. The policy
layer enforces authorization decisions. The agent-control layer remains local
to the orchestrator, and the router joins that local path with the
policy-enforced MCP path without collapsing their boundaries.

## In progress

- Milestone 8 planning.
- Milestone 8 planning has started; Milestone 8 implementation has not started.

## Not started

- A real confirmation UI or question presentation.
- User-response waiting and resume.
- Trusted approval-state management.
- Session and step persistence.
- A timeout system.
- Local vLLM integration.
- HTTP API work.
- Database work.
- Offline packaging and optional Docker.

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
- `ScriptedDecisionSource` is finite, deterministic, and memory-only. It tests
  only the loop execution contract; it is not a real LLM, a provider
  simulation, or a model of provider request and response internals.
- A real model now chooses tools through the provider boundary, but external
  model output remains untrusted proposed decisions. The router, policy,
  gateway, policy-enforced executor, and local controls remain the enforcement
  and execution boundaries.
- `AgentToolRouter` joins the local control path and the policy-enforced MCP
  path. Agent controls never reach MCP, and browser tools never bypass policy
  enforcement.
- `confirmation_granted` remains false in the loop. No trusted confirmation
  workflow exists, so model-selected confirmation-required tools are rejected
  and are not automatically retried.
- `ask_user` does not display a UI, wait for an answer, persist state, or
  resume execution.
- Provider configuration includes an HTTP timeout, and provider failures
  propagate as typed provider exceptions from the decision source. No retries
  or streaming are implemented.
- Run state and step history are memory-only. No general run timeout system
  exists.
- Unexpected programming errors propagate rather than becoming generic run
  results.
- Mock infrastructure will not be expanded further.
- The external NVIDIA verification does not establish offline operation,
  institution-internal or on-prem deployment, local-vLLM compatibility,
  confidential-data suitability, or production readiness.
- Allowed origins are a defensive configuration, not a complete security
  boundary. Local MCP transport does not make target web content trusted.
- File upload and download lifecycle management and credential handling remain
  deferred.
- Docker is not required for the first browser-agent MVP.
- FastAPI, sessions, persistence, offline packaging, and production deployment
  remain out of scope.
- Until local vLLM integration, only synthetic or public data may be sent to
  external providers.
- Secrets, internal URLs, institution data, and confidential browser contents
  remain prohibited from external services.

## Immediate next step

Prepare and review the Milestone 8 Implementation Brief. Milestone 8 planning
has started; Milestone 8 implementation has not started.

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

Also verified on `2026-07-28`:

- The verified Milestone 5 feature checkpoint is
  `09af169f5370f8b5cfa8087e500555afd02c22fe`, with commit subject
  `feat: add deterministic MCP agent loop`.
- The checkpoint changed exactly five files: `browser_agent/__init__.py`,
  `browser_agent/agent_loop.py`, `docs/agent-loop.md`,
  `scripts/mcp_agent_loop_smoke.py`, and `tests/test_agent_loop.py`.
- The full unit-test suite ran 126 tests and all passed with final result
  `OK`.
- `py_compile` passed.
- `git diff --check` passed.
- The real MCP-backed live smoke passed. Stdout was exactly
  `MCP agent loop smoke succeeded: navigate, snapshot, finish, and close`;
  stderr was empty and the exit code was 0.
- `browser_navigate` and `browser_snapshot` both returned source `MCP` and
  status `SUCCESS`; `finish` remained local with source `AGENT_CONTROL` and
  status `FINISHED`.
- The loop recorded exactly three steps. `finish` did not reach MCP, and
  `browser_close` succeeded only through
  `PolicyEnforcedToolExecutor.invoke_internal()`.
- False-positive protection rejects `MCP_ERROR` and `TRANSPORT_ERROR` in
  either browser step rather than allowing a later `finish` to hide the
  failure.
- The feature checkpoint was committed and pushed by the human, the local and
  remote branch hashes were verified equal, and the working tree was clean
  after the push.

Milestone 5, Deterministic MCP-backed Mock Agent Loop, is completed.

Also verified on `2026-07-28`:

- The verified Milestone 6 feature checkpoint is
  `3e195e0fa46d1224a78b3208e20c39e5f0aae489`, with commit subject
  `feat: add OpenAI-compatible decision provider`.
- The checkpoint changed exactly six files: `browser_agent/__init__.py`,
  `browser_agent/openai_provider.py`, `docs/openai-provider.md`,
  `requirements.txt`, `scripts/openai_agent_smoke.py`, and
  `tests/test_openai_provider.py`.
- The full unit-test suite ran 153 tests with 153 passing and 0 failures.
- `py_compile` passed.
- `git diff --check` passed.
- The minimal external-provider probe temporarily used NVIDIA API with model
  `z-ai/glm-5.2`. Its generic API root was supplied through the environment;
  it returned HTTP 200 in 8.87 seconds with model response `OK`.
- The real command `python scripts/openai_agent_smoke.py` used NVIDIA API
  temporarily with `z-ai/glm-5.2` and only public `https://example.com` data.
  Its decision flow was `browser_navigate` -> `browser_snapshot` -> `finish`,
  followed by internal `browser_close`.
- `browser_navigate` and `browser_snapshot` used the MCP path and succeeded.
  `finish` remained an application-owned local control and did not reach MCP.
  `browser_close` ran only through
  `PolicyEnforcedToolExecutor.invoke_internal()`.
- Stdout was exactly
  `OpenAI-compatible agent smoke succeeded: model navigate, snapshot, finish, and close`;
  stderr was empty and the exit code was 0.
- Only synthetic or public data was used externally, and no API key was
  committed.
- The human committed and pushed the feature checkpoint, the local and remote
  branch hashes were verified equal, and the working tree was clean at the
  feature checkpoint.
- This proves external integration and configuration-based provider
  replacement through the existing loop. It does not prove offline operation,
  on-prem or institution-internal deployment, local-vLLM compatibility,
  approval for confidential or institution data, or production readiness.
Milestone 6, OpenAI-Compatible LLM Provider, is completed.

Verified on `2026-07-29`:

- Milestone 7 implemented provider-neutral evaluation types, deterministic
  `PASS` / `FAIL` / `ERROR` scoring with `ERROR` exit-code precedence,
  exactly three public `https://example.com` scenarios, sanitized JSON
  reporting, an import-safe executable runner, one fresh MCP/browser session
  per scenario, and internal-only `browser_close` cleanup.
- The full unit-test suite ran 217 tests and all passed. `py_compile` and
  `git diff --check` passed.
- The first valid live baseline recorded 2 PASS / 1 FAIL / 0 ERROR, 66.67%,
  and exit code 1. The sole failure was a stale read-only-link oracle expecting
  `More information` when the public fixture visibly said `Learn more`.
- The historical first baseline was not replaced or rewritten. After the
  declared expectation and relevant task text were corrected to `Learn more`,
  a separate live verification recorded 3 PASS / 0 FAIL / 0 ERROR, 100.00%,
  exit code 0, and empty stderr.
- The corrected verification's confirmation flow was
  `browser_click REJECTED` -> `ask_user AWAITING_USER`. No automatic
  confirmation was granted, and the rejected click did not execute.
- Both live runs temporarily used external model `z-ai/glm-5.2` and only
  public `example.com` data. This evidence is limited to temporary external
  real-model integration and the existing MCP, policy, agent-loop, evaluator,
  and confirmation boundaries in these scenarios. It does not establish
  offline or on-premises operation, confidential or institution-data safety,
  local vLLM integration, broad statistical reliability, or production
  readiness.

Milestone 7 implementation, review, 217 passing tests, Learning Handoff,
human feature commit, human push, and remote feature-checkpoint verification
are complete.

The verified Milestone 7 feature checkpoint is
`d2e2716f646d3aaaff6eab1624a91609a3688b00`, with commit subject
`feat: add MVP evaluation framework`. It changed exactly seven files:
`AGENTS.md`, `browser_agent/__init__.py`,
`browser_agent/mvp_evaluation.py`, `docs/mvp-evaluation.md`,
`docs/project-status.md`, `scripts/openai_agent_eval.py`, and
`tests/test_mvp_evaluation.py`.

Local and remote SHA both matched
`d2e2716f646d3aaaff6eab1624a91609a3688b00`. The branch was up to date with
`origin/mvp/playwright-mcp-agent`. Compared with checkpoint
`9721001a8f20bbb75ee90c16a7b8a679d83246bb`, the remote was ahead by 1
commit and behind by 0 commits. The working tree was clean after the push.

Milestone 7, MVP Evaluation, is completed.

Milestone 8, local vLLM planning and integration, is the current milestone.
Its current phase is PLAN — Milestone 8. Milestone 8 planning has started;
Milestone 8 implementation has not started. The immediate next step is to
prepare and review the Milestone 8 Implementation Brief.
