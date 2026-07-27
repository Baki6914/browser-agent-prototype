# Local Playwright MCP Browser-Agent Architecture

## Architectural direction

The MVP is a local browser agent composed of separate processes on the same
computer. Microsoft's open-source Playwright MCP supplies browser capabilities.
Our Python application is the orchestrator and MCP client. An LLM provider
chooses among tools that the orchestrator has discovered and approved.

This is a reference design. If implementation work proposes a different
architecture, it must first explain the documented approach, the alternative,
its advantages, its risks and costs, and its compatibility with expected
deliverables.

## Text architecture diagram

```text
  +---------------- local computer and local trust boundary ---------------+
  |                                                                        |
  |  +---------------------- Python application ------------------------+  |
  |  | task + URL -> agent loop <-> replaceable LLM provider adapter    |  |
  |  |                    |                     |                        |  |
  |  |                    |                     +--> future local vLLM   |  |
  |  |                    +--> finish / ask_user                         |  |
  |  |                    +--> gateway -> policy -> invoke               |  |
  |  +------------------------------------------------|-----------------+  |
  |                                                   | MCP over stdio     |
  |                                                   v                    |
  |  +---------------- local Playwright MCP process --------------------+  |
  |  | tool catalog | browser actions | snapshots | observations        |  |
  |  +--------------------------------|---------------------------------+  |
  |                                   v                                    |
  |                          local browser process                          |
  +-----------------------------------|------------------------------------+
                                      |
                                      v
                              target web pages

  external trust boundary:

  Python LLM provider adapter --------> external OpenAI-compatible API
```

No MCP network port is required. The orchestrator starts or attaches to the
local Playwright MCP process and exchanges protocol messages through the
process's standard input and standard output.

The external OpenAI-compatible API is outside the local-computer and local-trust
boundary. When configured, selected prompts and browser observations cross that
boundary. The future local vLLM endpoint remains inside the local boundary.

## MCP protocol versus Playwright MCP

The Model Context Protocol (MCP) is a general protocol for discovering and
invoking tools and exchanging structured context. It defines how an MCP client
and an MCP server communicate; it does not itself implement browser automation.

Playwright MCP is Microsoft's particular MCP server implementation that exposes
browser capabilities backed by Playwright. It provides tool definitions and
executes browser operations.

The word "server" describes the MCP role, not necessarily a remote machine or a
network service. A local child process communicating through stdio is still an
MCP server. In this MVP, Playwright MCP is local rather than a cloud service and
does not listen on an exposed MCP port.

## Process roles

### Local browser process

The browser loads target pages, runs page-provided code, maintains tabs and
dialogs, and produces page, console, network, and screenshot information.
Although local, it handles data from potentially untrusted websites. Browser
isolation and Playwright MCP's launch settings therefore matter.

### Local Playwright MCP process

Playwright MCP translates MCP tool calls into Playwright browser operations and
returns structured results. Its intended capability may cover navigation,
accessibility-based inspection, clicking, typing, form filling, option
selection, keyboard input, tabs, dialogs, waits, screenshots, console and
network inspection, scraping, and page analysis. `browser_evaluate` may supply
page-context JavaScript evaluation only under the explicit policy described
below.

Upstream file upload and download tools may also be discovered. Discovery does
not grant permission: those tools are excluded from the initial MVP allowlist,
and file upload, download lifecycle management, and credential handling remain
deferred.

The available upstream tool catalog can change between versions. The
orchestrator must not assume that every desired tool exists or that every
discovered tool is safe.

### Python orchestrator

The Python application owns the agent workflow. It:

- starts and manages the local MCP connection;
- discovers and normalizes MCP tool definitions;
- decides which tools are approved for LLM use;
- validates requested arguments and applies policy before execution;
- invokes approved Playwright MCP tools;
- adds application-owned agent-control tools;
- returns observations and errors to the agent loop;
- enforces step, timeout, confirmation, and termination rules; and
- records or persists state only in later approved milestones.

The orchestrator, rather than the LLM, is the enforcement boundary. Prompt
instructions alone are not a security policy.

### LLM provider

The LLM provider receives the task, relevant conversation or observation state,
and approved tool schemas. It returns a proposed tool call or agent-control
decision. It does not execute tools directly.

A provider interface will isolate provider-specific request, response, and
tool-call formats. The initial real integration will be OpenAI-compatible. A
future local vLLM implementation should use the same interface so the agent
loop does not need redesign.

## Dynamic MCP tool discovery

At connection time, the gateway asks Playwright MCP for its current tool list.
For each tool it reads the name, description, and input schema, then converts
that metadata into a stable internal representation.

Discovery avoids rebuilding and permanently hard-coding the full upstream
catalog. It does not mean automatic trust. Every discovered tool must pass the
classification and policy layer before it can be shown to the LLM or invoked.
Unknown or changed tools default to unavailable until explicitly assessed.

## Tool execution and observation loop

The intended one-action-at-a-time loop is:

1. Build the model input from the user task and current observations.
2. Supply only approved Playwright MCP tools and agent-control tools.
3. Ask the configured LLM provider for the next decision.
4. Validate the selected tool name and arguments.
5. Apply classification, confirmation, and policy rules.
6. Invoke an allowed browser tool through the local MCP gateway, or execute an
   agent-control tool in the orchestrator.
7. Normalize the result, relevant browser observation, denial, or error.
8. Feed that observation into the next model decision.
9. Stop on `finish`, pause on `ask_user`, or terminate at a safety limit.

The loop must treat page text and tool output as untrusted observations, not as
instructions that override system or user policy.

## Playwright MCP tools and agent-control tools

Playwright MCP tools act on or inspect the browser. They are dynamically
discovered and invoked through MCP.

Agent-control tools are owned by the Python application and never sent to
Playwright MCP. At minimum:

- `finish` ends the loop with a final result; and
- `ask_user` pauses the loop to request missing information or approval.

Keeping these groups distinct makes execution routing, validation, policy, and
testing explicit.

## Tool categories and policy

Classification is based on capability and effect, not merely tool name.

### Read-only

These tools observe state without intentionally changing the page or browser,
such as accessibility snapshots, page text extraction, screenshots, and
console or network inspection. They can still reveal sensitive data and require
output handling rules.

### Mutating

These tools change browser or page state, including navigation, clicking,
typing, form filling, option selection, keyboard interaction, tab changes, and
dialog responses. Some mutations are low risk; others can submit data, trigger
transactions, or leave the current site.

### Sensitive

These tools can access or transmit protected information or cause consequential
actions. Examples include reading authenticated content, entering personal
data, submitting forms, uploading files, downloading files, or interacting with
financial or administrative controls. Sensitive actions require explicit
policy and may require user confirmation. Credential handling is out of scope
for the MVP.

### Unsafe

These tools permit arbitrary code execution, bypass the intended browser-action
boundary, or cannot be constrained sufficiently. They are denied and omitted
from LLM-visible tools.

`browser_run_code_unsafe`, or any equivalent host or server-side arbitrary code
execution capability, is always excluded because it could run code with the MCP
process's host permissions, access local resources, evade action-level
validation, and turn malicious page instructions or a mistaken model decision
into host compromise. It must never be exposed to the LLM.

`browser_evaluate` is not exposed by default. It may be enabled only for a
narrowly justified page-context task through an explicitly approved policy.
Page-context evaluation remains distinct from host or server-side execution,
but still requires narrow inputs and normal validation and policy checks.

## Why use Playwright MCP

Playwright MCP already supplies a broad, evolving browser-tool surface and
accessibility-oriented observations. Using it lets this project focus on the
parts that distinguish the application: orchestration, tool policy, provider
replacement, agent control, evaluation, and later session management.

Rebuilding every browser action would duplicate upstream work, increase the
maintenance and testing burden, and delay evaluation of the agent design.
Dynamic discovery also makes upstream additions visible without forcing the
application to duplicate each action implementation.

This choice does not make upstream code immutable. A later fork or vendored
copy remains possible if pinning, offline packaging, security review, missing
capabilities, incompatible changes, or targeted modifications justify the
ownership cost. Such a change is an architectural and dependency decision and
requires an approved plan.

## Trust boundaries and data leakage paths

The main trust boundaries are:

- **User to orchestrator:** tasks may request unsafe or unauthorized actions.
- **Target page to browser and observations:** pages are untrusted and may
  contain prompt injection, deceptive controls, or hostile scripts.
- **Browser/Playwright MCP to orchestrator:** tool results may be large,
  malformed, sensitive, or version-dependent.
- **Orchestrator to LLM provider:** prompts and observations may leave the local
  machine when an external provider is configured.
- **Orchestrator to local host:** screenshots, logs, temporary profiles, and
  later persisted sessions may contain confidential data.
- **Browser to network:** navigation, forms, subresources, analytics, and
  downloads can transmit data to target sites or third parties.

Possible leakage paths include sending page contents or screenshots to an
external LLM, entering private data into a form, loading secret-bearing URLs,
retaining browser profiles or logs, exposing console or network payloads, and
allowing arbitrary host code execution.

Until local vLLM is integrated, evaluation must use only synthetic or public
data. Secrets, internal URLs, institution data, and confidential browser
contents must never be sent to external services. Policy enforcement,
redaction, confirmation, retention, browser isolation, and provider selection
must be revisited before sensitive use.

Local-only MCP narrows the transport exposure but does not make browser content
safe or prevent an external LLM request from carrying observations off-device.

## Future local-vLLM flow

In the future local configuration, the Python orchestrator will send the same
provider-level request to an adapter targeting a local vLLM
OpenAI-compatible endpoint. Tool discovery, classification, validation,
execution, and the observation loop remain unchanged.

```text
Python agent loop
    -> provider interface
        -> local vLLM adapter and endpoint
    <- proposed tool call
    -> policy and local Playwright MCP over stdio
    <- local browser observation
    -> next local model decision
```

This preserves a replaceable provider boundary and allows browser snapshots and
model traffic to remain on the local machine, subject to the target web pages'
own network behavior.
