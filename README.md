# Browser Agent Prototype

This repository is a learning-stage prototype for an LLM-assisted browser
automation service. The intended product accepts a URL and natural-language
task, lets a model propose browser actions, enforces local policy, executes
approved actions through Playwright MCP, and pauses for user information or
approval when needed.

## Intended architecture

Client or user interface → mandatory FastAPI HTTP service → in-memory
`RunManager` → resumable custom agent loop → NVIDIA OpenAI-compatible LLM API
→ `AgentToolRouter` and policy enforcement → Playwright MCP → browser

The custom agent loop remains the selected MVP orchestration approach. Runs
will be resumable across HTTP requests while the service process remains
running, but persistent sessions and service-restart resume are not planned
for the MVP.

## Current maturity

Implemented:

- local Playwright MCP integration over stdio with dynamic tool discovery;
- tool classification, policy enforcement, and application-owned `finish` and
  `ask_user` controls;
- a synchronous, in-memory custom agent loop;
- a replaceable OpenAI-compatible decision-provider boundary, with NVIDIA as
  the active reference provider; and
- a provider-neutral MVP evaluation framework with verified public-data runs.

Planned, not yet implemented:

- the mandatory FastAPI service and in-memory `RunManager`;
- resumable runs across HTTP requests, trusted confirmation, and user-response
  handling;
- secure login and secret handling; and
- controlled automatic file download, beginning with a Playwright MCP
  capability spike.

File upload is optional future work and remains disabled and denied by default.
The prototype is not production ready.

## Provider configuration

The generic provider settings are:

- `BROWSER_AGENT_LLM_BASE_URL`
- `BROWSER_AGENT_LLM_MODEL`
- `BROWSER_AGENT_LLM_API_KEY`
- `BROWSER_AGENT_LLM_TIMEOUT`

Do not commit secret values or send credentials, confidential browser content,
internal URLs, or institution data to NVIDIA. The same provider boundary can
later target another approved OpenAI-compatible endpoint through configuration;
institution-internal LLM integration is outside this project's responsibility.

## Documentation

- [Roadmap](ROADMAP.md)
- [Project status](docs/project-status.md)
- [OpenAI-compatible provider](docs/openai-provider.md)
- [MVP evaluation](docs/mvp-evaluation.md)
