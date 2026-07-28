# MCP Tool Classification and Policy

Dynamic discovery reports what an MCP server can do; it does not authorize the
application or an LLM to use every reported capability. `McpToolPolicy` is the
typed authorization boundary between the discovered `ToolDefinition` catalog
and future orchestrator code. A tool absent from the explicit registry fails
closed.

## Policy concepts

`ToolCategory` describes the kind of capability:

- `read_only`: page, console, network, or rendered-output observation.
- `navigation`: movement between URLs or browser contexts.
- `page_interaction`: an operation performed against page or viewport state.
- `file_io`: upload, download, drop, or other file-boundary behavior.
- `code_execution`: arbitrary code evaluation.
- `internal_control`: lifecycle behavior reserved for the orchestrator.
- `unknown`: no explicit registry entry exists.

`PolicyAction` describes authorization:

- `allow`: normal invocation may proceed.
- `require_confirmation`: normal invocation needs a trusted confirmation flag.
- `internal_only`: excluded from normal and LLM-visible use; available only
  through the dedicated cleanup path.
- `deny`: must never reach gateway invocation.

## Committed inventory policy

The registry explicitly covers all 24 tools in
`docs/mcp-tool-inventory.json`.

| Tool | Category | Base action |
| --- | --- | --- |
| `browser_click` | `page_interaction` | `require_confirmation` |
| `browser_close` | `internal_control` | `internal_only` |
| `browser_console_messages` | `read_only` | `allow` |
| `browser_drag` | `page_interaction` | `require_confirmation` |
| `browser_drop` | `file_io` | `deny` |
| `browser_evaluate` | `code_execution` | `deny` |
| `browser_file_upload` | `file_io` | `deny` |
| `browser_fill_form` | `page_interaction` | `require_confirmation` |
| `browser_find` | `read_only` | `allow` |
| `browser_handle_dialog` | `page_interaction` | `require_confirmation` |
| `browser_hover` | `page_interaction` | `allow` |
| `browser_navigate` | `navigation` | `allow` |
| `browser_navigate_back` | `navigation` | `allow` |
| `browser_network_request` | `read_only` | `allow` |
| `browser_network_requests` | `read_only` | `allow` |
| `browser_press_key` | `page_interaction` | `require_confirmation` |
| `browser_resize` | `page_interaction` | `allow` |
| `browser_run_code_unsafe` | `code_execution` | `deny` |
| `browser_select_option` | `page_interaction` | `require_confirmation` |
| `browser_snapshot` | `read_only` | `allow` |
| `browser_tabs` | `navigation` | `require_confirmation` |
| `browser_take_screenshot` | `read_only` | `allow` |
| `browser_type` | `page_interaction` | `require_confirmation` |
| `browser_wait_for` | `read_only` | `allow` |

The explicitly denied capabilities are arbitrary host or page-context code
execution, file upload, and external file/data drop. Download and unrestricted
network-mutation tools would also require explicit denied registry rules if a
future MCP version discovers them. Unknown tools are categorized as `unknown`
and denied until reviewed and added to the typed registry.

## Argument-aware decisions

Policy decisions consider both the tool name and an argument mapping. A
non-empty string `filename` escalates an otherwise `allow` tool to
`require_confirmation`, because observation then writes output. An absent,
`None`, empty, or whitespace-only filename does not escalate. Denied,
internal-only, and unknown tools retain their base action regardless of
arguments. Non-mapping arguments are rejected by the policy before the gateway
can be called.

## Catalog and invocation boundaries

`llm_visible_tools()` returns defensive copies, sorted alphabetically, for
discovered tools whose base action is `allow` or `require_confirmation`. It
excludes denied, internal-only, and unknown tools. Registry entries that were
not discovered do not appear.

`PolicyEnforcedToolExecutor.invoke()` is the normal future orchestrator path.
Allowed tools reach `McpToolGateway.invoke()`. Confirmation-gated tools reach it
only when the separate keyword-only `confirmation_granted` parameter is true.
Internal-only and denied tools raise policy-owned errors first.

`invoke_internal()` is a distinct lifecycle-cleanup path. It accepts only
internal-only tools, currently `browser_close`. It cannot bypass denial and
rejects normal allowed or confirmation-gated tools.

`confirmation_granted` belongs to trusted future orchestrator state. It is not
an MCP tool argument, must not be model-generated, and is never forwarded to
the gateway. This milestone supplies only the boolean enforcement point; it
does not implement a confirmation UI, agent loop, or LLM integration.

## Current limitations and deferred work

The registry matches one committed Playwright MCP inventory and must be
reviewed when runtime discovery changes. The policy does not yet implement
user identity, persisted approvals, confirmation presentation, credential
handling, file lifecycle management, download support, or per-site policy.

`scripts/mcp_policy_smoke.py` starts the local MCP process over stdio,
initializes a session, performs `list_tools` discovery, classifies the resulting
catalog locally, prints an alphabetical summary, and shuts down. It exposes no
MCP network port, invokes no browser tool, performs no navigation, and
intentionally persists no runtime output.
