# Local operator UI

## Purpose and architecture

The operator UI is a local, same-origin control panel for the existing FastAPI,
`RunManager`, NVIDIA-compatible model provider, and Playwright MCP composition.
It does not implement a second agent loop. The browser loads static HTML, CSS,
and JavaScript from FastAPI, and that JavaScript uses only the existing run,
response, cancellation, status, and file-download HTTP endpoints.

## Prerequisites and configuration

Use Python 3.11 or newer, install the repository's existing Python and Node.js
dependencies, and ensure the fixed Playwright MCP base configuration is valid.
Set these required environment variables:

- `BROWSER_AGENT_LLM_BASE_URL`
- `BROWSER_AGENT_LLM_MODEL`
- `BROWSER_AGENT_DOWNLOAD_BASE_DIR`

`BROWSER_AGENT_LLM_API_KEY`, `BROWSER_AGENT_LLM_TIMEOUT`,
`BROWSER_AGENT_MAX_STEPS`, `BROWSER_AGENT_NODE_COMMAND`,
`BROWSER_AGENT_MCP_CLI_PATH`, and `BROWSER_AGENT_MCP_CONFIG_PATH` are optional
according to the existing application configuration contract. Never put a key
in source control or shell history.

Start the local service with:

```bash
python -m uvicorn browser_agent.application:create_application \
  --factory \
  --host 127.0.0.1 \
  --port 8000 \
  --loop asyncio
```

Here, `-m` runs the installed `uvicorn` module; `--factory` says the import
target creates the ASGI application; `--host` selects the loopback interface;
`--port` selects TCP port 8000; and `--loop asyncio` uses the asyncio event
loop required by the owned MCP lifecycle.

Open <http://127.0.0.1:8000/>. Enter an absolute HTTP or HTTPS start URL and a
task. The panel follows `queued` and `running` automatically. At
`awaiting_user`, type a normal answer. At `awaiting_confirmation`, approve or
reject the displayed action. At `awaiting_secret`, use only the generated
username, password, or OTP fields. `awaiting_secret_application` means the
application is applying those transient values. You can cancel a live run,
read its final result or safe failure, and retrieve listed files using the
download links. The Action timeline shows in-memory, fixed safe summaries of
agent actions and operator decisions. It intentionally omits tool arguments,
raw page contents, normal response values, secret values, paths, and internal
errors; it is lost when the service restarts.

## Responsibility and confirmation boundaries

The institution or deployment owns proxy, firewall, DNS, egress,
destination-site access, and confidential-data controls. The browser-agent
does not decide whether a site is institutionally allowed. It performs only
basic start-URL syntax validation, operation execution, policy classification,
trusted confirmation, audit reporting, secret isolation, download delivery,
and lifecycle cleanup. Its deployment-owned MCP configuration is passed
unchanged to Playwright MCP and is not interpreted or rewritten per run.

Removing application origin filtering does not bypass institutional network
controls. Colab receives no separate application allowlist either: its network
environment remains responsible for destination access. Real institution data
must never be sent to Colab or an unapproved external provider, and the
configured external provider's confidentiality restrictions still apply.

Confirmation is deliberately conservative and based on tool capability, not
button wording. Navigation, back navigation, snapshots, find, hover, wait, and
other read-only observations run automatically. Click, fill/type, key press,
selection, drag, dialog handling, and tab operations require operator
confirmation.

## Operation and limitations

The server must remain running. Runs are memory-only and files use temporary
run storage; restart recovery does not exist. Shut down with Ctrl+C and allow
the application lifespan to close active runs and MCP processes.

Secret values are transient. Never enter a username, password, or OTP in the
normal response textarea. CAPTCHA, passkey, and device-approval challenges are
not bypassed.

External-model data restrictions still apply. Do not send confidential page
content to an external model that has not been approved for that data.

The documented command binds only to loopback, and this MVP has no production
authentication. Do not bind it to a public interface or otherwise expose it
to a public network.
