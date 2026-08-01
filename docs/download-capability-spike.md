# Milestone 10A: Minimal Download Capability Spike

## Question and outcome

This spike asks one question: can the currently installed local Playwright MCP
trigger a download from a synthetic localhost page, place the completed file in
a controlled temporary directory, and give the Python application enough local
evidence to determine its filename, byte size, exact contents, and whether
partial files were cleaned up?

The first Codex-sandbox run on 2026-08-01 was **BLOCKED** before MCP subprocess
startup because creation of the local stdio subprocess raised:

```text
PermissionError: [Errno 1] Operation not permitted
```

The first direct Colab run was not a valid BLOCKED capability result. It passed
the subprocess prerequisite and reached a spike tool-validation failure:

```text
InvalidToolArgumentsError: arguments.target: required property is missing
```

The incompatible invocation was `browser_click` with `ref`; the installed
0.0.78 schema requires `target`. This was a script implementation error, not an
environment blocker.

The second direct Colab run initialized MCP, navigated successfully, obtained
snapshot ref `e2`, and completed the confirmed `browser_click`. The controlled
output directory then contained `m10-demo-download.txt` plus two Playwright MCP
page artifacts named `page-2026-08-01T11-49-04-857Z.yml` and
`page-2026-08-01T11-49-05-486Z.yml`. The script incorrectly treated all three
regular files as completed downloads and raised `SpikeError: multiple completed
files observed` before size and exact-content verification. This proved that
the download was created and exposed an overly generic file selector, rather
than a download-capability failure.

The final corrected direct Colab run completed successfully with
**RESULT=SUPPORTED**, empty stderr, and exit code 0. Local stdio Playwright MCP
initialized; dynamic tool discovery completed and found no dedicated
download-related tool; the documented `--output-dir` CLI argument provided the
controlled-output mechanism; localhost navigation succeeded; snapshot ref
`e2` was obtained; and policy-enforced `browser_click` executed with explicit
trusted confirmation. The expected file `m10-demo-download.txt` appeared
beneath the controlled temporary output directory. Expected-file selection
ignored the Playwright MCP page YAML artifacts, the filename and exact 31-byte
size were verified, the exact payload matched, no relevant partial file
remained, and cleanup completed.

## Installed local evidence

The locally installed `@playwright/mcp` package version is **0.0.78**.

The selected mechanism is the documented **`--output-dir <path>` CLI
argument**. The local CLI help and installed package README both describe it as
the path for output files; the README's typed configuration also documents the
equivalent `outputDir?: string` property. The spike uses the CLI argument and
does not invent a configuration key.

The installed implementation in `playwright-core/lib/coreBundle.js` provides
more specific static evidence: its download-start handler derives a sanitized
name from Playwright's suggested filename, allocates that file through the MCP
output-directory boundary, calls `download.saveAs(...)`, marks the entry
finished, and exposes download start/finish text including the relative output
file. This static `--output-dir` package evidence remains valid, but it does not
replace a successful live download.

No dedicated download tool is assumed. The live script records any dynamically
discovered tool whose name or description mentions downloads. Discovery was
not reached in the sandbox-blocked run; the final direct Colab run completed
discovery and reported `DOWNLOAD_RELATED_TOOLS=none`.

## Synthetic scenario and boundaries

The import-safe script starts a standard-library HTTP server bound explicitly
to `127.0.0.1` on an automatically assigned port. Its single page contains one
visible `Download demo file` link. The attachment response supplies the safe
suggested filename `m10-demo-download.txt` and the exact bytes:

```text
M10 synthetic download payload.
```

Separate `tempfile.TemporaryDirectory` instances outside the repository hold
the server boundary, generated MCP configuration, and controlled download
output. The generated configuration permits only the exact synthetic HTTP
origin. If startup succeeds, the script dynamically discovers tools through
`McpToolGateway`, creates `McpToolPolicy` and
`PolicyEnforcedToolExecutor`, and uses that executor for `browser_navigate`,
`browser_snapshot`, and `browser_click`. The click is passed explicit trusted
confirmation. `browser_close` uses the existing internal-only cleanup path.

The spike does not use `browser_evaluate`, `browser_run_code_unsafe`, or
`browser_file_upload`. It makes no provider call and no external network
request.

After a confirmed click, a bounded polling helper selects the known safe
filename `m10-demo-download.txt`, ignoring unrelated MCP page YAML artifacts.
It requires that final file and size in at least two observations while
detecting relevant `.crdownload`, `.part`, and `.tmp` variants of the expected
filename. Verification requires a direct child of the controlled directory, a
plain basename without traversal or separators, the expected byte count, exact
payload bytes, and no remaining relevant partial file.

All server, MCP, browser, and temporary resources are cleaned up on success or
failure. The final run reached and passed filename, byte-size, exact-content,
partial-file-cleanup, and overall cleanup verification.

## Result definitions

- **SUPPORTED**: the real local MCP/browser flow completes and the controlled
  downloaded file passes every verification.
- **UNSUPPORTED**: Chromium and MCP operate, but the installed MCP interface
  provides no usable controlled-download mechanism or observable completed
  file.
- **BLOCKED**: the environment prevents a prerequisite, such as MCP process
  startup or Chromium launch, so the capability cannot be exercised.

Exit codes are 0 for SUPPORTED, 2 for UNSUPPORTED, 3 for BLOCKED, and 1 with a
fixed `ERROR` marker for a genuine spike implementation or validation failure.

## Commands

Run the focused automated tests without Node, MCP, or a browser:

```bash
python -m pytest -q tests/test_mcp_download_capability_spike.py
python tests/test_mcp_download_capability_spike.py
```

Run the live spike exactly as follows:

```bash
python scripts/mcp_download_capability_spike.py
```

## What this proves and what remains unproven

The final live run proves that local stdio Playwright MCP 0.0.78 can initialize,
dynamically discover its tools, navigate to the synthetic localhost page, and
execute a policy-enforced click with explicit trusted confirmation. With no
dedicated download-related tool discovered, the documented `--output-dir`
mechanism saved the completed download beneath the controlled directory, where
the Python application observed and verified its filename, exact 31-byte size,
exact payload, and absence of relevant partial files. Playwright MCP page YAML
artifacts did not interfere with expected-file selection. Cleanup completed,
stderr was empty, and the process exited with code 0.

This is narrow capability evidence, not production-readiness evidence. Only a
synthetic localhost download was tested; no external website compatibility was
tested. The spike provides no `RunManager` integration, file ID or catalog,
HTTP file endpoint, persistence or restart recovery, duplicate-name production
strategy, concurrent download isolation, antivirus or checksum infrastructure,
or upload capability.

The minimal implication for Milestone 10 is that Playwright MCP 0.0.78 can save
a completed download beneath a controlled `--output-dir`, and the Python
application can observe and verify the resulting file locally. Milestone 10
production work may therefore use this mechanism without adding a dedicated
MCP download tool. This spike intentionally does not design the complete
production implementation.
