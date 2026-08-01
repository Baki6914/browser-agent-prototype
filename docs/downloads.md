# Minimal controlled downloads

## Proven mechanism

Milestone 10A was required because tool discovery alone did not establish how
Playwright MCP saves a browser download. The local live spike proved that
`@playwright/mcp` 0.0.78 accepts `--output-dir <path>` and saves the completed
synthetic download there. No dedicated download MCP tool was discovered.

The installed implementation reports completion in this exact event-line
shape:

```text
- Downloaded file <suggested filename> to "<relative output file>"
```

It separately reports starts as `- Downloading file <suggested filename> ...`.
`extract_completed_download_paths` accepts only the completed form and only a
safe relative plain basename in the quoted output position. It does not parse
start events, URLs, screenshots, snapshot YAML links, arbitrary page text, or
absolute and nested paths.

## Application boundary and flow

Future composition wraps the existing policy-enforced executor:

```text
PolicyEnforcedToolExecutor -> DownloadTrackingExecutor -> RunDownloadStore
```

`DownloadTrackingExecutor` delegates exactly once and preserves the tool name,
arguments, trusted confirmation boolean, and returned `ToolObservation`. It
tracks completion text only after a successful observation. It never invokes
MCP or the gateway, grants confirmation, changes visibility or classification,
or exposes a download tool. Its internal invocation path delegates unchanged
and does not track, preserving internal-only browser cleanup.

Each `RunDownloadStore` creates one random run directory below a configured,
non-symlink allowed base. A private `incoming` child is the future MCP output
directory; a separate private `managed` child contains registered completed
files. Registration accepts only a direct, regular, non-symlink incoming file,
rejects common partial suffixes, verifies resolved containment, and moves it to
a new opaque file-ID physical name. Two downloads both named `report.pdf`
therefore retain independent bytes under distinct IDs.

Public `DownloadMetadata` contains only `file_id`, the safe original
`filename`, and exact non-negative byte `size`. `DownloadFile` pairs that
metadata with a repr-hidden private `Path` for application and HTTP use; it is
never placed in snapshots or JSON.

## Lifecycle and HTTP access

On successful completion, the store is finalized: every remaining incoming
artifact, including partial files and `page-*.yml`, is removed while managed
downloads remain available for the process lifetime. Failed, cancelled,
step-limited, and decision-source-exhausted runs close their store and remove
the entire isolated run directory. `RunManager.close()` removes all remaining
stores, including files retained for finished runs.

`RunSnapshot.files` exposes ordered metadata. `GET
/runs/{run_id}/files/{file_id}` resolves the recorded `DownloadFile` through
`RunManager` and returns a FastAPI `FileResponse` with
`application/octet-stream` and the safe original filename. Unknown files use
`run_file_not_found`; internal paths are absent from JSON and normal errors.

## Security boundaries and limitations

The store rejects traversal, nested or absolute names, separators, symlinks,
directories, missing files, partial suffixes, and managed-file escape. It does
not follow symlinks during incoming cleanup and never overwrites a prior
download. This milestone adds no persistence, restart recovery, checksum,
antivirus inspection, quota, file-type inspection, authentication,
authorization, upload support, or broad external-site compatibility claim.

The real factory wiring that passes each store's `output_directory` to
Playwright MCP `--output-dir` remains Milestone 11 composition work. Milestone
10B performs no live browser or provider end-to-end execution.
