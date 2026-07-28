# OpenAI-Compatible Decision Provider

The Milestone 6 provider is a provider-neutral OpenAI-compatible HTTP boundary.
NVIDIA API is the temporary real integration target, but neither its endpoint
nor a model name is hard-coded. A future internal endpoint or local vLLM server
uses the same implementation by changing only the endpoint, model, and API key.

## Boundary and request flow

`OpenAICompatibleDecisionSource` receives the current `AgentLoopContext`, sends
one `POST` to `{base_url}/chat/completions`, and returns one `AgentToolCall`.
The base URL is the API root, normally ending in `/v1`. Each request contains
the configured model, one system message, deterministic JSON context in one
user message, the context's tools, `tool_choice: "auto"`, and
`parallel_tool_calls: false`.

Tool definitions are converted in their existing order to OpenAI function-tool
objects. Nested schemas are copied. The provider neither discovers nor
authorizes tools, and it does not execute them. The existing `AgentToolRouter`,
`McpToolPolicy`, policy executor, and local agent controls remain the execution
and enforcement boundaries. Provider visibility is not execution authority.

Responses fail closed unless the first choice contains exactly one function
tool call whose non-empty name and JSON-object arguments are valid. Text
responses, missing or multiple calls, malformed structures, and non-object
arguments raise `OpenAIProviderResponseError`. A syntactically valid unknown
tool name is returned unchanged so the existing router can reject it.

Configuration, timeout, transport, HTTP status, and invalid response failures
have separate typed errors. HTTP errors include only a bounded body excerpt,
with the configured key redacted if echoed. The API key is excluded from config
representation and is never intentionally logged.

There is no retry, streaming, conversation persistence, confirmation UI, or
user-response resume. `ScriptedDecisionSource` is not expanded.

## Temporary NVIDIA API smoke

The live script reads these provider-neutral variables:

- Required: `BROWSER_AGENT_LLM_BASE_URL`
- Required: `BROWSER_AGENT_LLM_MODEL`
- Optional: `BROWSER_AGENT_LLM_API_KEY`
- Optional: `BROWSER_AGENT_LLM_TIMEOUT` (default `60`)

Point the generic variables temporarily at an NVIDIA API root ending in `/v1`,
its selected model, and its key. Do not put endpoints, models, or keys in source
control. The same variables can later point at an internal provider, for
example an approved compatible root such as `http://localhost:8000/v1`.

Run `python scripts/openai_agent_smoke.py` only when explicitly approved. It
uses the local Node executable, local `@playwright/mcp`, the committed spike
config, MCP stdio, dynamic discovery, policy filtering, the real router, and
the deterministic loop. With only public/synthetic data, the model must:

1. navigate to `https://example.com`;
2. snapshot the page;
3. call local `finish` with a result containing `Example Domain`;
4. allow orchestrator cleanup to call internal-only `browser_close`.

Passing this smoke proves external NVIDIA integration only. It does not prove
offline operation, an internal or on-prem deployment, local-vLLM compatibility,
safe handling of confidential data, or production readiness. Never send
secrets, internal URLs, institution data, or confidential page content to the
external smoke provider.
