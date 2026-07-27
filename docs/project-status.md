# Project Status

## Status summary

- **Active branch:** `mvp/playwright-mcp-agent`
- **Remote tracking:** configured for `origin/mvp/playwright-mcp-agent`
- **Current milestone:** Milestone 0, repository recovery and project memory
- **Current implementation:** synchronous Playwright learning prototype
- **Next technical task:** Milestone 1, local Playwright MCP connectivity spike

## Completed

- A synchronous Playwright learning prototype exists in the repository.
- `agent_demo.py` contains a fake, deterministic LLM decision example.
- The active branch and its remote-tracking configuration have been verified.
- The project has selected a local Playwright MCP architecture direction for
  the MVP.

These items do not constitute Playwright MCP integration or a general agent
loop.

## In progress

- Milestone 0: repository recovery and project memory.
- Recording the roadmap, architecture, current state, mandatory workflow, trust
  boundaries, and exact next milestone.

Milestone 0 remains in progress until its diff is reviewed, its applicable
checks are completed, the Learning Handoff is delivered, a human commits and
pushes the changes, and the remote branch is verified.

## Not started

- Local Playwright MCP connectivity through stdio.
- Dynamic MCP tool discovery and invocation.
- A dynamic MCP tool gateway.
- Tool classification and policy enforcement.
- Agent-control tools such as `finish` and `ask_user`.
- A deterministic MCP-backed mock agent loop.
- An OpenAI-compatible LLM provider.
- General browser-agent MVP evaluation.
- Local vLLM integration.
- Session and persistence work.
- Offline packaging and optional Docker.

No Playwright MCP integration exists, and no application code has been changed
for the new architecture.

## Known constraints

- The repository still contains the synchronous learning prototype.
- A previously generated asynchronous `BrowserService` draft was lost because
  it was never committed and pushed. It is not being recreated in Milestone 0.
- Playwright MCP has not yet been launched, connected, version-checked, or
  evaluated in this repository.
- The actual upstream tool catalog and schemas have not yet been discovered.
- Tool policy details must be validated against the real discovered tools.
- Upstream file upload or download tools may be discovered, but they are
  excluded from the initial MVP allowlist. File upload, download lifecycle
  management, and credential handling remain deferred.
- Docker is not required for the first browser-agent MVP.
- FastAPI, sessions, persistence, offline packaging, and production deployment
  remain out of scope.
- Until local vLLM integration, only synthetic or public test data may be used.
- Secrets, internal URLs, institution data, and confidential browser contents
  must not be sent to external services.

## Immediate next step

Prepare an approved Implementation Brief for Milestone 1: a narrow local
Playwright MCP connectivity spike. The spike should prove local stdio startup,
MCP initialization, dynamic tool listing, a minimal safe browser interaction,
error visibility, and clean shutdown using public or synthetic content.
It must record the Node.js version, pin and record the Playwright MCP version,
record the Python MCP SDK version, and save a reviewable inventory of discovered
tool names and schema summaries.

It must not yet build the complete gateway, policy layer, agent loop, API,
persistence system, or Docker packaging.

## Last verified checkpoint

At the start of this documentation implementation:

- Git reported the branch as `mvp/playwright-mcp-agent`.
- Git reported tracking of `origin/mvp/playwright-mcp-agent`.
- Git reported no pre-existing working-tree changes.
- The repository's documented implementation direction still described the
  older browser-foundation draft.

No tests were run for this documentation-only task. No commit, push, or remote
content verification has been performed. The previous uncommitted async draft
remains lost.
