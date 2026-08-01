"""Minimal live spike for controlled downloads through the local Playwright MCP."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterable, Literal

SUGGESTED_FILENAME = "m10-demo-download.txt"
EXPECTED_PAYLOAD = b"M10 synthetic download payload."
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")
DOWNLOAD_MECHANISM = "documented --output-dir CLI argument"
RESULT_EXIT_CODES = {"SUPPORTED": 0, "UNSUPPORTED": 2, "BLOCKED": 3, "ERROR": 1}


class SpikeError(RuntimeError):
    """A genuine spike implementation or validation failure."""


@dataclass(frozen=True)
class DirectoryScan:
    completed: tuple[Path, ...]
    partial: tuple[Path, ...]


@dataclass(frozen=True)
class PollResult:
    file: Path | None
    partial_seen: bool
    partial_remaining: bool


@dataclass
class SpikeReport:
    result: Literal["SUPPORTED", "UNSUPPORTED", "BLOCKED", "ERROR"] = "ERROR"
    mcp_startup: str = "not reached"
    download_tools: str = "none"
    mechanism: str = DOWNLOAD_MECHANISM
    navigation: str = "not reached"
    snapshot_ref: str = "not reached"
    click: str = "not reached"
    filename: str | None = None
    byte_size: int | None = None
    content_verified: str = "not reached"
    partial_cleanup: str = "not reached"
    cleanup: str = "pending"
    stage: str | None = None
    detail: str | None = None
    private_paths: list[str] = field(default_factory=list, repr=False)


def validate_safe_basename(filename: str) -> str:
    """Return a safe plain basename or reject separators and traversal."""
    if not filename or filename in {".", ".."}:
        raise ValueError("filename must be a non-empty plain basename")
    if "/" in filename or "\\" in filename or Path(filename).name != filename:
        raise ValueError("filename must not contain path separators or traversal")
    return filename


def scan_download_directory(directory: Path) -> DirectoryScan:
    """Take one deterministic snapshot of completed and partial regular files."""
    completed: list[Path] = []
    partial: list[Path] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not path.is_file():
            continue
        if path.name.lower().endswith(PARTIAL_SUFFIXES):
            partial.append(path)
        else:
            completed.append(path)
    return DirectoryScan(tuple(completed), tuple(partial))


def select_single_completed(scan: DirectoryScan) -> Path | None:
    """Select exactly one completed file and reject ambiguous output."""
    if len(scan.completed) > 1:
        names = ", ".join(path.name for path in scan.completed)
        raise SpikeError(f"multiple completed files observed: {names}")
    return scan.completed[0] if scan.completed else None


def poll_for_stable_download(
    directory: Path,
    *,
    expected_filename: str = SUGGESTED_FILENAME,
    timeout: float = 8.0,
    interval: float = 0.1,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    scan: Callable[[Path], DirectoryScan] = scan_download_directory,
) -> PollResult:
    """Wait boundedly for the expected file to be stable and have no partials."""
    expected_filename = validate_safe_basename(expected_filename)
    relevant_partial_names = {
        f"{expected_filename}{suffix}" for suffix in PARTIAL_SUFFIXES
    }
    deadline = clock() + timeout
    previous: tuple[Path, int] | None = None
    partial_seen = False
    last_scan = DirectoryScan((), ())
    while clock() <= deadline:
        last_scan = scan(directory)
        relevant_partials = tuple(
            path for path in last_scan.partial if path.name in relevant_partial_names
        )
        partial_seen = partial_seen or bool(relevant_partials)
        candidate = next(
            (
                path
                for path in last_scan.completed
                if path.name == expected_filename
            ),
            None,
        )
        if candidate is not None:
            current = (candidate, candidate.stat().st_size)
            if current == previous and not relevant_partials:
                return PollResult(candidate, partial_seen, False)
            previous = current
        else:
            previous = None
        sleep(interval)
    relevant_partial_remaining = any(
        path.name in relevant_partial_names for path in last_scan.partial
    )
    return PollResult(None, partial_seen, relevant_partial_remaining)


def classify_wait_result(result: PollResult) -> Literal["SUPPORTED", "UNSUPPORTED"]:
    """A stable completed file is supported; a bounded timeout is unsupported."""
    return "SUPPORTED" if result.file is not None else "UNSUPPORTED"


def verify_download(path: Path, controlled_directory: Path) -> tuple[str, int]:
    """Validate containment, filename, exact size, and exact bytes."""
    resolved = path.resolve()
    root = controlled_directory.resolve()
    if not resolved.is_relative_to(root) or resolved.parent != root:
        raise SpikeError("completed file was outside the controlled directory")
    filename = validate_safe_basename(path.name)
    payload = path.read_bytes()
    if len(payload) != len(EXPECTED_PAYLOAD):
        raise SpikeError(
            f"download size mismatch: {len(payload)} != {len(EXPECTED_PAYLOAD)}"
        )
    if payload != EXPECTED_PAYLOAD:
        raise SpikeError("download content did not match the synthetic payload")
    return filename, len(payload)


def is_browser_permission_blocker(detail: str) -> bool:
    """Recognize Chromium launch failures caused by environment permissions."""
    lowered = detail.lower()
    known = "sandbox_host_linux.cc" in lowered and "operation not permitted" in lowered
    equivalent = (
        any(word in lowered for word in ("chromium", "browser", "sandbox"))
        and any(
            phrase in lowered
            for phrase in (
                "operation not permitted",
                "permission denied",
                "failed to move to new namespace",
                "no usable sandbox",
            )
        )
    )
    return known or equivalent


def find_download_ref(snapshot_text: str) -> str:
    """Extract the synthetic link ref without printing the full snapshot."""
    for line in snapshot_text.splitlines():
        if "download demo file" not in line.lower():
            continue
        match = re.search(r"\[ref=([^\]]+)\]", line)
        if match:
            return match.group(1)
    raise SpikeError("snapshot did not expose the labelled download link ref")


def browser_click_arguments(target: str) -> dict[str, str]:
    """Build the exact installed browser_click 0.0.78 argument shape."""
    return {"element": "Download demo file", "target": target}


def _leaf_exception(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        parts = [_leaf_exception(item) for item in exc.exceptions]
        return "; ".join(part for part in parts if part)
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def classify_failure(report: SpikeReport, detail: str) -> None:
    """Classify only genuine missing/permission prerequisites as blocked."""
    lowered = detail.lower()
    startup_permission = report.stage in {
        "MCP subprocess startup",
        "MCP initialization",
    } and any(
        phrase in lowered
        for phrase in (
            "permissionerror",
            "operation not permitted",
            "permission denied",
        )
    )
    missing_runtime = report.stage == "MCP subprocess startup" and any(
        phrase in lowered
        for phrase in (
            "node.js executable was not found",
            "local @playwright/mcp cli was not found",
        )
    )
    if startup_permission or missing_runtime or is_browser_permission_blocker(detail):
        report.result = "BLOCKED"
        report.detail = detail
    else:
        report.result = "ERROR"
        report.detail = f"spike implementation/validation error: {detail}"


def render_report(report: SpikeReport) -> str:
    """Render the output contract while redacting temporary absolute paths."""
    values: list[tuple[str, object | None]] = [
        ("RESULT", report.result),
        ("MCP_STARTUP", report.mcp_startup),
        ("DOWNLOAD_RELATED_TOOLS", report.download_tools),
        ("DOWNLOAD_MECHANISM", report.mechanism),
        ("BROWSER_NAVIGATION", report.navigation),
        ("SNAPSHOT_REF", report.snapshot_ref),
        ("CONFIRMED_CLICK", report.click),
        ("FINAL_FILENAME", report.filename),
        ("FINAL_BYTE_SIZE", report.byte_size),
        ("CONTENT_VERIFICATION", report.content_verified),
        ("PARTIAL_FILE_CLEANUP", report.partial_cleanup),
        ("CLEANUP", report.cleanup),
        ("OUTCOME_STAGE", report.stage),
        ("OUTCOME_DETAIL", report.detail),
    ]
    lines = []
    for key, value in values:
        if value is None:
            continue
        safe = str(value).replace("\n", " ").replace("\r", " ")
        for private_path in report.private_paths:
            safe = safe.replace(private_path, "<temporary-path>")
        lines.append(f"{key}={safe}")
    return "\n".join(lines)


class _SyntheticHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        if self.path == "/":
            body = (
                b"<!doctype html><title>M10 download spike</title>"
                b'<a href="/download" download>Download demo file</a>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path == "/download":
            body = EXPECTED_PAYLOAD
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header(
                "Content-Disposition", f'attachment; filename="{SUGGESTED_FILENAME}"'
            )
        else:
            self.send_error(404)
            return
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        return


def _playwright_mcp_cli(root: Path) -> Path:
    package = root / "node_modules" / "@playwright" / "mcp"
    metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
    package_bin = metadata.get("bin", {}).get("playwright-mcp")
    candidate = (package / package_bin).resolve() if isinstance(package_bin, str) else None
    if (
        candidate is None
        or not candidate.is_file()
        or not candidate.is_relative_to(package.resolve())
    ):
        raise SpikeError("local @playwright/mcp CLI was not found")
    return candidate


def download_related_tools(definitions: Iterable[object]) -> str:
    matches = []
    for definition in definitions:
        name = str(getattr(definition, "name", ""))
        description = str(getattr(definition, "description", "") or "")
        if "download" in f"{name} {description}".lower():
            matches.append(name)
    return ", ".join(sorted(matches)) or "none"


async def _invoke(
    executor: object, name: str, arguments: dict, *, confirmed: bool = False
):
    observation = await executor.invoke(  # type: ignore[attr-defined]
        name, arguments, confirmation_granted=confirmed
    )
    if observation.status != "success":
        raise RuntimeError(f"{name} {observation.status}: {observation.error}")
    return observation


async def _run_mcp(
    root: Path, url: str, config_path: Path, output_dir: Path, report: SpikeReport
) -> None:
    sys.path.insert(0, str(root))
    from browser_agent import McpToolGateway, McpToolPolicy, PolicyEnforcedToolExecutor
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    report.stage = "MCP subprocess startup"
    node = shutil.which("node")
    if node is None:
        raise SpikeError("Node.js executable was not found")
    server = StdioServerParameters(
        command=node,
        args=[
            str(_playwright_mcp_cli(root)),
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
        ],
        cwd=str(root),
    )
    async with stdio_client(server) as streams:
        report.mcp_startup = "subprocess started"
        async with ClientSession(*streams) as session:
            report.stage = "MCP initialization"
            await session.initialize()
            report.mcp_startup = "initialized"
            gateway = McpToolGateway(session)
            report.stage = "tool discovery"
            definitions = await gateway.discover()
            report.download_tools = download_related_tools(definitions)
            names = {definition.name for definition in definitions}
            required = {"browser_navigate", "browser_snapshot", "browser_click"}
            if missing := sorted(required - names):
                report.result = "UNSUPPORTED"
                report.stage = "tool discovery"
                report.detail = f"missing required tools: {', '.join(missing)}"
                return
            executor = PolicyEnforcedToolExecutor(gateway, McpToolPolicy(definitions))
            try:
                report.stage = "browser_navigate"
                await _invoke(executor, "browser_navigate", {"url": url})
                report.navigation = "success"
                report.stage = "browser_snapshot"
                snapshot = await _invoke(executor, "browser_snapshot", {})
                report.stage = "download ref extraction"
                ref = find_download_ref(snapshot.text)
                report.snapshot_ref = f"found ({ref})"
                report.stage = "browser_click"
                await _invoke(
                    executor,
                    "browser_click",
                    browser_click_arguments(ref),
                    confirmed=True,
                )
                report.click = "success with trusted confirmation"
                report.stage = "completed-file observation"
                polled = await asyncio.to_thread(
                    poll_for_stable_download,
                    output_dir,
                    expected_filename=SUGGESTED_FILENAME,
                )
                report.partial_cleanup = (
                    "verified: no partial files remain"
                    if not polled.partial_remaining
                    else "failed: partial files remain"
                )
                report.result = classify_wait_result(polled)
                if polled.file is None:
                    report.detail = "bounded wait found no stable completed download"
                    return
                report.stage = "download verification"
                filename, byte_size = verify_download(polled.file, output_dir)
                report.filename = filename
                report.byte_size = byte_size
                report.content_verified = "exact payload matched"
            finally:
                primary_stage = report.stage
                report.stage = "cleanup"
                if "browser_close" in names:
                    close = await executor.invoke_internal("browser_close", {})
                    if close.status != "success":
                        raise SpikeError(f"browser_close failed: {close.error}")
                report.stage = primary_stage


def run_live_spike() -> SpikeReport:
    root = Path(__file__).resolve().parents[1]
    report = SpikeReport()
    server: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    with (
        tempfile.TemporaryDirectory(prefix="m10-server-") as server_temp,
        tempfile.TemporaryDirectory(prefix="m10-config-") as config_temp,
        tempfile.TemporaryDirectory(prefix="m10-download-") as download_temp,
    ):
        report.private_paths.extend((server_temp, config_temp, download_temp))
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), _SyntheticHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            origin = f"http://127.0.0.1:{port}"
            config_path = Path(config_temp) / "playwright-mcp.json"
            config_path.write_text(
                json.dumps(
                    {
                        "browser": {
                            "browserName": "chromium",
                            "isolated": True,
                            "launchOptions": {"headless": True},
                        },
                        "capabilities": ["core"],
                        "network": {"allowedOrigins": [origin]},
                        "imageResponses": "omit",
                        "codegen": "none",
                        "allowUnrestrictedFileAccess": False,
                        "saveSession": False,
                        "sharedBrowserContext": False,
                    }
                ),
                encoding="utf-8",
            )
            asyncio.run(
                _run_mcp(root, origin, config_path, Path(download_temp), report)
            )
        except Exception as exc:
            detail = _leaf_exception(exc)
            classify_failure(report, detail)
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            if thread is not None:
                thread.join(timeout=2)
            report.cleanup = "complete"
    return report


def main() -> int:
    report = run_live_spike()
    print(render_report(report))
    return RESULT_EXIT_CODES[report.result]


if __name__ == "__main__":
    raise SystemExit(main())
