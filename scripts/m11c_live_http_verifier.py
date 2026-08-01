"""Safe live verifier for the complete Milestone 11 HTTP composition.

Importing this module is inert.  Live resources are created only by ``main``
in parent mode or by the explicitly selected private worker mode.
"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Sequence as SequenceABC
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


DOWNLOAD_FILENAME = "m11c-report.txt"
DOWNLOAD_BYTES = b"M11C synthetic public report\n"
SUCCESS_FIELDS = (
    "M11C_RESULT",
    "HTTP_BOUNDARY",
    "NVIDIA_PROVIDER",
    "MCP_STDIO",
    "POLICY_CONTROLS",
    "USER_RESUME",
    "TRUSTED_CONFIRMATION",
    "SECRET_APPLICATION",
    "SECRET_DISCLOSURE_SCAN",
    "DOWNLOAD_METADATA",
    "DOWNLOAD_BYTES",
    "CANCELLATION",
    "CLEANUP",
)
KNOWN_STATUSES = frozenset(
    {
        "queued", "running", "awaiting_user", "awaiting_confirmation",
        "awaiting_secret", "awaiting_secret_application", "finished",
        "failed", "cancelled", "step_limit_reached",
        "decision_source_exhausted",
    }
)
TERMINAL_STATUSES = frozenset(
    {"finished", "failed", "cancelled", "step_limit_reached", "decision_source_exhausted"}
)
PROHIBITED_TOOLS = frozenset(
    {"browser_close", "browser_file_upload", "browser_evaluate", "browser_run_code_unsafe"}
)
EXPECTED_CONFIRMATION_TOOLS = frozenset(
    {"browser_fill_form", "browser_select_option", "browser_click"}
)
PROVIDER_ENVIRONMENT_KEYS = (
    "BROWSER_AGENT_LLM_BASE_URL",
    "BROWSER_AGENT_LLM_MODEL",
    "BROWSER_AGENT_LLM_API_KEY",
    "BROWSER_AGENT_LLM_TIMEOUT",
)
CHECK_FIELDS = frozenset(SUCCESS_FIELDS[1:])
MAX_EVIDENCE_COUNT = 1_000_000
WORKER_TIMEOUT_SECONDS = 900.0
PROCESS_CLEANUP_SECONDS = 5.0


class FailureCategory(str, Enum):
    INVALID_ENVIRONMENT = "INVALID_ENVIRONMENT"
    MISSING_RUNTIME = "MISSING_NODE_OR_MCP_CLI"
    STARTUP = "STARTUP_FAILURE"
    PROVIDER = "PROVIDER_FAILURE"
    MCP_BROWSER = "MCP_BROWSER_FAILURE"
    TOOL_EXPOSURE = "UNEXPECTED_TOOL_EXPOSURE"
    POLL_DEADLINE = "POLL_DEADLINE"
    RUN_STATE = "UNEXPECTED_RUN_STATE"
    INTERACTION = "INTERACTION_MISMATCH"
    DISCLOSURE = "SECRET_DISCLOSURE"
    DOWNLOAD = "DOWNLOAD_MISMATCH"
    CANCELLATION = "CANCELLATION_FAILURE"
    CLEANUP = "CLEANUP_FAILURE"
    WORKER = "WORKER_CRASH"


class VerificationFailure(RuntimeError):
    def __init__(self, category: FailureCategory):
        super().__init__(category.value)
        self.category = category


@dataclass(frozen=True)
class RuntimeEnvironment:
    base_url: str
    model: str
    api_key: str | None
    timeout: float


@dataclass(frozen=True)
class WorkerResult:
    ok: bool
    category: str | None
    cleanup_category: str | None
    checks: Mapping[str, bool]
    provider_calls: int = 0
    http_responses: int = 0


@dataclass(frozen=True)
class DecisionRecord:
    step: int
    tool_name: str
    arguments: Mapping[str, Any]


class ConfirmationActionTracker:
    """Resolve confirmation references and enforce the one approved action sequence."""

    def __init__(self) -> None:
        self._confirmed_steps: set[int] = set()
        self._next_action = 0

    def resolve(self, decisions: Sequence[DecisionRecord], snapshot_step_count: object) -> DecisionRecord:
        if not decisions or decisions[-1].tool_name != "ask_user":
            raise VerificationFailure(FailureCategory.INTERACTION)
        if type(snapshot_step_count) is not int or snapshot_step_count <= 0:
            raise VerificationFailure(FailureCategory.INTERACTION)
        steps = [decision.step for decision in decisions]
        if any(type(step) is not int or step <= 0 for step in steps) or len(steps) != len(set(steps)):
            raise VerificationFailure(FailureCategory.INTERACTION)
        pending_ask = decisions[-1]
        if pending_ask.step != snapshot_step_count:
            raise VerificationFailure(FailureCategory.INTERACTION)
        recorded_reference = decisions[-1].arguments.get("confirmation_for_step")
        if type(recorded_reference) is not int or recorded_reference != snapshot_step_count - 1:
            raise VerificationFailure(FailureCategory.INTERACTION)
        by_step = {decision.step: decision for decision in decisions}
        referenced = by_step.get(recorded_reference)
        pending_actions = [
            decision for decision in decisions[:-1]
            if decision.tool_name in EXPECTED_CONFIRMATION_TOOLS
            and decision.step not in self._confirmed_steps
        ]
        if (
            referenced is None
            or referenced.step >= decisions[-1].step
            or referenced.step in self._confirmed_steps
            or referenced.tool_name not in EXPECTED_CONFIRMATION_TOOLS
            or not pending_actions
            or pending_actions[-1].step != referenced.step
        ):
            raise VerificationFailure(FailureCategory.INTERACTION)
        self._validate_action(referenced)
        self._confirmed_steps.add(referenced.step)
        self._next_action += 1
        return referenced

    def _validate_action(self, decision: DecisionRecord) -> None:
        if self._next_action >= 4:
            raise VerificationFailure(FailureCategory.INTERACTION)
        arguments = decision.arguments
        if not isinstance(arguments, Mapping):
            raise VerificationFailure(FailureCategory.INTERACTION)
        valid = False
        if self._next_action == 0 and decision.tool_name == "browser_fill_form":
            fields = arguments.get("fields")
            valid = (
                set(arguments) == {"fields"}
                and isinstance(fields, list)
                and len(fields) == 1
                and isinstance(fields[0], Mapping)
                and fields[0].get("name") == "Public note"
                and fields[0].get("value") == "public verification note"
                and set(fields[0]) <= {"name", "type", "ref", "value"}
                and isinstance(fields[0].get("ref"), str)
                and bool(fields[0]["ref"])
            )
        elif self._next_action == 1 and decision.tool_name == "browser_select_option":
            valid = (
                set(arguments) == {"element", "ref", "values"}
                and arguments.get("element") == "Report type"
                and isinstance(arguments.get("ref"), str)
                and bool(arguments["ref"])
                and arguments.get("values") == ["Detailed"]
            )
        elif self._next_action in {2, 3} and decision.tool_name == "browser_click":
            expected = "Apply synthetic choices" if self._next_action == 2 else "Download synthetic report"
            valid = (
                set(arguments) == {"element", "ref"}
                and arguments.get("element") == expected
                and isinstance(arguments.get("ref"), str)
                and bool(arguments["ref"])
            )
        if not valid or contains_prohibited_marker(json.dumps(arguments, sort_keys=True), ["password"]):
            raise VerificationFailure(FailureCategory.INTERACTION)

    @property
    def complete(self) -> bool:
        return self._next_action == 4


def repository_root(anchor: Path | None = None) -> Path:
    """Resolve a repository containing both production package and manifest."""
    candidate = (anchor or Path(__file__)).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / "browser_agent").is_dir() and (current / "package.json").is_file():
            return current
    raise VerificationFailure(FailureCategory.MISSING_RUNTIME)


def resolve_mcp_cli(root: Path) -> Path:
    """Resolve a package-declared CLI, constrained beneath @playwright/mcp."""
    package = (root / "node_modules" / "@playwright" / "mcp").resolve()
    metadata_path = package / "package.json"
    candidates: list[Path] = []
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        package_bin = metadata.get("bin")
        if isinstance(package_bin, str):
            candidates.append(package / package_bin)
        elif isinstance(package_bin, Mapping):
            candidates.extend(package / item for item in package_bin.values() if isinstance(item, str))
    except (OSError, json.JSONDecodeError):
        pass
    candidates.append(package / "cli.js")
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and resolved.is_relative_to(package):
                return resolved
        except OSError:
            continue
    raise VerificationFailure(FailureCategory.MISSING_RUNTIME)


def build_exact_origin_config(origin: str) -> dict[str, Any]:
    """Build and validate the one-origin runtime Playwright MCP configuration."""
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.query or parsed.fragment or parsed.port is None
        or not 1 <= parsed.port <= 65535 or "*" in origin
    ):
        raise VerificationFailure(FailureCategory.INVALID_ENVIRONMENT)
    config = {
        "browser": {"browserName": "chromium", "isolated": True, "launchOptions": {"headless": True}},
        "capabilities": ["core"],
        "network": {"allowedOrigins": [origin]},
        "imageResponses": "omit",
        "codegen": "none",
        "allowUnrestrictedFileAccess": False,
        "saveSession": False,
        "sharedBrowserContext": False,
    }
    validate_exact_origin_config(config, origin)
    return config


def validate_exact_origin_config(config: Mapping[str, Any], origin: str) -> None:
    allowed = config.get("network", {}).get("allowedOrigins") if isinstance(config.get("network"), Mapping) else None
    if allowed != [origin] or any("*" in item for item in allowed if isinstance(item, str)):
        raise VerificationFailure(FailureCategory.INVALID_ENVIRONMENT)


def load_runtime_environment(environ: Mapping[str, str]) -> RuntimeEnvironment:
    """Validate provider settings without exposing their values."""
    base_url = environ.get("BROWSER_AGENT_LLM_BASE_URL")
    model = environ.get("BROWSER_AGENT_LLM_MODEL")
    api_key = environ.get("BROWSER_AGENT_LLM_API_KEY")
    raw_timeout = environ.get("BROWSER_AGENT_LLM_TIMEOUT")
    if not base_url or not base_url.strip() or not model or not model.strip():
        raise VerificationFailure(FailureCategory.INVALID_ENVIRONMENT)
    if api_key is not None and not api_key.strip():
        raise VerificationFailure(FailureCategory.INVALID_ENVIRONMENT)
    try:
        timeout = 60.0 if raw_timeout is None else float(raw_timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
        from browser_agent.openai_provider import OpenAICompatibleProviderConfig
        validated = OpenAICompatibleProviderConfig(base_url, model, api_key, timeout)
    except Exception:
        raise VerificationFailure(FailureCategory.INVALID_ENVIRONMENT) from None
    return RuntimeEnvironment(validated.base_url, validated.model, api_key, timeout)


def sanitized_worker_environment(environ: Mapping[str, str]) -> dict[str, str]:
    result = dict(environ)
    for name in PROVIDER_ENVIRONMENT_KEYS:
        result.pop(name, None)
    return result


def synthetic_headers() -> dict[str, str]:
    return {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": f'attachment; filename="{DOWNLOAD_FILENAME}"',
        "Content-Length": str(len(DOWNLOAD_BYTES)),
    }


def classify_status(value: object) -> str:
    if not isinstance(value, str) or value not in KNOWN_STATUSES:
        raise VerificationFailure(FailureCategory.RUN_STATE)
    return "terminal" if value in TERMINAL_STATUSES else "active"


def interaction_payload(status: str, interaction_id: object, value: object, request_id: str) -> dict[str, Any]:
    if not isinstance(interaction_id, str) or not interaction_id or not request_id:
        raise VerificationFailure(FailureCategory.INTERACTION)
    base = {"request_id": request_id, "interaction_id": interaction_id}
    if status == "awaiting_user" and isinstance(value, str) and value.strip():
        return {**base, "type": "user_input", "text": value}
    if status == "awaiting_confirmation" and type(value) is bool:
        return {**base, "type": "confirmation", "approved": value}
    if status == "awaiting_secret" and isinstance(value, str) and value:
        return {**base, "type": "secret", "values": {"password": value}}
    raise VerificationFailure(FailureCategory.INTERACTION)


def validate_download_metadata(files: object) -> Mapping[str, Any]:
    if not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], Mapping):
        raise VerificationFailure(FailureCategory.DOWNLOAD)
    item = files[0]
    if set(item) != {"file_id", "filename", "size"} or item.get("filename") != DOWNLOAD_FILENAME or item.get("size") != len(DOWNLOAD_BYTES):
        raise VerificationFailure(FailureCategory.DOWNLOAD)
    if not isinstance(item.get("file_id"), str) or not item["file_id"]:
        raise VerificationFailure(FailureCategory.DOWNLOAD)
    return item


def contains_prohibited_marker(data: bytes | str, markers: Sequence[str]) -> bool:
    raw = data if isinstance(data, bytes) else data.encode("utf-8", "surrogatepass")
    return any(marker and marker.encode("utf-8", "surrogatepass") in raw for marker in markers)


def safe_success_output(result: WorkerResult) -> str:
    if (
        not result.ok
        or result.category is not None
        or result.cleanup_category is not None
        or set(result.checks) != CHECK_FIELDS
        or any(type(value) is not bool or not value for value in result.checks.values())
        or type(result.provider_calls) is not int
        or type(result.http_responses) is not int
        or not 0 <= result.provider_calls <= MAX_EVIDENCE_COUNT
        or not 0 <= result.http_responses <= MAX_EVIDENCE_COUNT
    ):
        raise VerificationFailure(FailureCategory.WORKER)
    lines = [f"{field}=PASS" for field in SUCCESS_FIELDS]
    lines.extend((f"PROVIDER_CALL_COUNT={result.provider_calls}", f"HTTP_RESPONSE_COUNT={result.http_responses}"))
    return "\n".join(lines)


def safe_failure_output(category: FailureCategory, cleanup_category: FailureCategory | None = None) -> str:
    lines = ["M11C_RESULT=FAIL", f"FAILURE_CATEGORY={category.value}"]
    if cleanup_category is FailureCategory.CLEANUP:
        lines.append("CLEANUP_FAILURE_CATEGORY=CLEANUP_FAILURE")
    return "\n".join(lines)


def parse_worker_result(stdout: bytes, returncode: int) -> WorkerResult:
    try:
        payload = json.loads(stdout.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"ok", "category", "cleanup_category", "checks", "provider_calls", "http_responses"}:
            raise ValueError
        result = WorkerResult(**payload)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise VerificationFailure(FailureCategory.WORKER) from None
    if type(result.ok) is not bool or not isinstance(result.checks, dict) or set(result.checks) != CHECK_FIELDS:
        raise VerificationFailure(FailureCategory.WORKER)
    if any(type(value) is not bool for value in result.checks.values()):
        raise VerificationFailure(FailureCategory.WORKER)
    for count in (result.provider_calls, result.http_responses):
        if type(count) is not int or not 0 <= count <= MAX_EVIDENCE_COUNT:
            raise VerificationFailure(FailureCategory.WORKER)
    valid_categories = {category.value for category in FailureCategory}
    if result.category is not None and (type(result.category) is not str or result.category not in valid_categories):
        raise VerificationFailure(FailureCategory.WORKER)
    if result.cleanup_category is not None and (
        type(result.cleanup_category) is not str
        or result.cleanup_category != FailureCategory.CLEANUP.value
    ):
        raise VerificationFailure(FailureCategory.WORKER)
    safe_precondition = result.category in {
        FailureCategory.INVALID_ENVIRONMENT.value,
        FailureCategory.MISSING_RUNTIME.value,
    }
    expected_code = 0 if result.ok else (2 if safe_precondition else 1)
    if returncode != expected_code or result.ok != (result.category is None and result.cleanup_category is None):
        raise VerificationFailure(FailureCategory.WORKER)
    if result.ok and any(not value for value in result.checks.values()):
        raise VerificationFailure(FailureCategory.WORKER)
    return result


class _SyntheticHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path == "/download":
            self.send_response(200)
            for name, value in synthetic_headers().items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(DOWNLOAD_BYTES)
            return
        if self.path != "/":
            self.send_error(404)
            return
        body = _synthetic_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _synthetic_html() -> bytes:
    return b"""<!doctype html><html><head><title>M11C Synthetic Form</title></head>
<body><main aria-label="M11C synthetic application"><h1>Browser Agent Verification</h1>
<p id="instructions">Complete the public synthetic workflow.</p>
<label>Public note <input aria-label="Public note" id="note"></label>
<label>Report type <select aria-label="Report type" id="kind"><option>Summary</option><option>Detailed</option></select></label>
<label id="password-label">Synthetic password <input type="password" aria-label="Synthetic password" id="secret"></label>
<button aria-label="Apply synthetic choices" id="apply">Apply synthetic choices</button>
<a aria-label="Download synthetic report" href="/download" download="m11c-report.txt">Download synthetic report</a>
<p id="complete" hidden>WORKFLOW COMPLETE</p></main><script>
const secret=document.getElementById('secret');secret.addEventListener('input',()=>setTimeout(()=>{
 const label=document.getElementById('password-label'); const replacement=document.createElement('span');
 replacement.id='secret-applied'; replacement.textContent='Synthetic secret applied'; label.replaceWith(replacement);},0));
document.getElementById('apply').addEventListener('click',()=>{document.getElementById('complete').hidden=false;});
</script></body></html>"""


class _Server:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SyntheticHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="m11c-synthetic", daemon=True)
        self._started = False
        self._closed = False

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self) -> None:
        self.thread.start()
        self._started = True

    def close(self) -> None:
        if self._closed:
            return
        failed = False
        if self._started:
            try:
                self.httpd.shutdown()
            except BaseException:
                failed = True
        try:
            self.httpd.server_close()
        except BaseException:
            failed = True
        if self._started:
            try:
                self.thread.join(timeout=5)
                if self.thread.is_alive():
                    failed = True
            except BaseException:
                failed = True
        self._closed = True
        if failed:
            raise VerificationFailure(FailureCategory.CLEANUP)


class AuditRecord:
    def __init__(self, prohibited_markers: Sequence[str] = ()) -> None:
        self.provider_calls = 0
        self.decisions: list[DecisionRecord] = []
        self.tool_sets: list[set[str]] = []
        self.http_bodies: list[bytes] = []
        self.prohibited_markers = tuple(prohibited_markers)
        self.confirmation_sequence_complete = False
        self.user_resume_accepted = False
        self.secret_submission_accepted = False
        self.user_resume_provider_calls: int | None = None
        self.secret_submission_provider_calls: int | None = None


def make_audit_source(real_type: type, record: AuditRecord) -> type:
    class AuditedDecisionSource(real_type):
        async def next_decision(self, context: Any) -> Any:
            prior_steps = getattr(context, "steps", None)
            if (
                not isinstance(prior_steps, SequenceABC)
                or isinstance(prior_steps, (str, bytes, bytearray))
            ):
                raise VerificationFailure(FailureCategory.INTERACTION)
            actual_steps: list[int] = []
            for prior in prior_steps:
                step = getattr(prior, "step_number", None)
                if type(step) is not int or step <= 0:
                    raise VerificationFailure(FailureCategory.INTERACTION)
                actual_steps.append(step)
            if len(actual_steps) != len(set(actual_steps)):
                raise VerificationFailure(FailureCategory.INTERACTION)
            pending_step = len(prior_steps) + 1
            if actual_steps and pending_step <= max(actual_steps):
                raise VerificationFailure(FailureCategory.INTERACTION)
            if pending_step in actual_steps or any(item.step == pending_step for item in record.decisions):
                raise VerificationFailure(FailureCategory.INTERACTION)
            body = self._request_body(context)
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if contains_prohibited_marker(encoded, record.prohibited_markers):
                raise VerificationFailure(FailureCategory.DISCLOSURE)
            names = {item.name for item in context.tools}
            if names & PROHIBITED_TOOLS:
                raise VerificationFailure(FailureCategory.TOOL_EXPOSURE)
            record.tool_sets.append(names)
            decision = await super().next_decision(context)
            record.provider_calls += 1
            record.decisions.append(DecisionRecord(pending_step, decision.tool_name, copy.deepcopy(decision.arguments)))
            return decision
    return AuditedDecisionSource


async def _request(client: Any, method: str, path: str, record: AuditRecord, sentinel: str, **kwargs: Any) -> Any:
    response = await client.request(method, path, **kwargs)
    body = await response.aread()
    record.http_bodies.append(body)
    if contains_prohibited_marker(body, record.prohibited_markers or [sentinel]):
        raise VerificationFailure(FailureCategory.DISCLOSURE)
    return response


async def _poll(client: Any, run_id: str, record: AuditRecord, sentinel: str, deadline: float) -> Mapping[str, Any]:
    while True:
        if time.monotonic() >= deadline:
            raise VerificationFailure(FailureCategory.POLL_DEADLINE)
        response = await _request(client, "GET", f"/runs/{run_id}", record, sentinel)
        if response.status_code != 200:
            raise VerificationFailure(FailureCategory.RUN_STATE)
        body = response.json()
        status = body.get("status")
        classify_status(status)
        if status not in {"queued", "running", "awaiting_secret_application"}:
            return body
        await asyncio.sleep(0.05)


def _request_id() -> str:
    return secrets.token_hex(16)


async def _drive_success(client: Any, origin: str, sentinel: str, record: AuditRecord, timeout: float) -> tuple[str, Mapping[str, Any]]:
    task = (
        "Use only the synthetic page. First ask_user which public profile to use. After the response, inspect with browser_snapshot. "
        "Fill Public note with 'public verification note'; select Detailed in Report type; request_secret for the password field using its current target ref. "
        "Then click Apply synthetic choices, click Download synthetic report, inspect again to verify WORKFLOW COMPLETE, and finish with result 'synthetic workflow complete'."
    )
    created = await _request(client, "POST", "/runs", record, sentinel, json={"start_url": origin, "task": task})
    if created.status_code != 202:
        raise VerificationFailure(FailureCategory.STARTUP)
    run_id = created.json().get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise VerificationFailure(FailureCategory.RUN_STATE)
    deadline = time.monotonic() + max(90.0, timeout * 16)
    seen = set()
    confirmations = ConfirmationActionTracker()
    while True:
        snapshot = await _poll(client, run_id, record, sentinel, deadline)
        status = snapshot["status"]
        seen.add(status)
        if status == "finished":
            required = {"awaiting_user", "awaiting_confirmation", "awaiting_secret"}
            if not required <= seen:
                raise VerificationFailure(FailureCategory.INTERACTION)
            return run_id, snapshot
        if status in TERMINAL_STATUSES:
            raise VerificationFailure(FailureCategory.PROVIDER if status == "failed" else FailureCategory.RUN_STATE)
        if status == "awaiting_user":
            if not record.decisions or record.decisions[-1].tool_name != "ask_user":
                raise VerificationFailure(FailureCategory.INTERACTION)
            value: object = "public-profile-blue"
        elif status == "awaiting_secret":
            if not record.decisions or record.decisions[-1].tool_name != "request_secret" or snapshot.get("secret_fields") != ["password"]:
                raise VerificationFailure(FailureCategory.INTERACTION)
            if snapshot.get("secret_targets") is None or contains_prohibited_marker(json.dumps(snapshot), [sentinel]):
                raise VerificationFailure(FailureCategory.DISCLOSURE)
            value = sentinel
        elif status == "awaiting_confirmation":
            confirmations.resolve(record.decisions, snapshot.get("step_count"))
            value = True
        else:
            raise VerificationFailure(FailureCategory.RUN_STATE)
        payload = interaction_payload(status, snapshot.get("interaction_id"), value, _request_id())
        response = await _request(client, "POST", f"/runs/{run_id}/responses", record, sentinel, json=payload)
        if response.status_code != 202:
            raise VerificationFailure(FailureCategory.INTERACTION)
        if status == "awaiting_user":
            record.user_resume_accepted = True
            record.user_resume_provider_calls = record.provider_calls
        elif status == "awaiting_secret":
            if response.json().get("status") != "awaiting_secret_application":
                raise VerificationFailure(FailureCategory.INTERACTION)
            record.secret_submission_accepted = True
            record.secret_submission_provider_calls = record.provider_calls
        if status == "awaiting_confirmation" and confirmations.complete:
            record.confirmation_sequence_complete = True


async def _drive_cancellation(client: Any, origin: str, sentinel: str, record: AuditRecord, timeout: float) -> str:
    created = await _request(client, "POST", "/runs", record, sentinel, json={
        "start_url": origin,
        "task": "Immediately call ask_user with the question 'Pause this synthetic cancellation run?' and do nothing else.",
    })
    if created.status_code != 202:
        raise VerificationFailure(FailureCategory.CANCELLATION)
    run_id = created.json().get("run_id")
    if not isinstance(run_id, str):
        raise VerificationFailure(FailureCategory.CANCELLATION)
    snapshot = await asyncio.wait_for(_poll(client, run_id, record, sentinel, time.monotonic() + max(30.0, timeout * 2)), timeout=max(35.0, timeout * 2 + 5))
    if snapshot.get("status") != "awaiting_user" or not record.decisions or record.decisions[-1].tool_name != "ask_user":
        raise VerificationFailure(FailureCategory.CANCELLATION)
    cancelled = await _request(client, "POST", f"/runs/{run_id}/cancel", record, sentinel, json={"request_id": _request_id()})
    if cancelled.status_code != 200 or cancelled.json().get("status") != "cancelled":
        raise VerificationFailure(FailureCategory.CANCELLATION)
    return run_id


async def _worker_async(private_root: Path, sentinel: str) -> WorkerResult:
    environment = load_runtime_environment(os.environ)
    sanitized = sanitized_worker_environment(os.environ)
    os.environ.clear()
    os.environ.update(sanitized)
    root = repository_root()
    node = shutil.which("node")
    if node is None:
        raise VerificationFailure(FailureCategory.MISSING_RUNTIME)
    cli = resolve_mcp_cli(root)
    private_root.mkdir(parents=True, exist_ok=True)
    if not os.access(private_root, os.W_OK):
        raise VerificationFailure(FailureCategory.INVALID_ENVIRONMENT)
    server = _Server()
    markers = [sentinel]
    if environment.api_key:
        markers.append(environment.api_key)
    record = AuditRecord(markers)
    checks = {field: False for field in SUCCESS_FIELDS[1:]}
    primary_category: FailureCategory | None = None
    cleanup_category: FailureCategory | None = None
    first_run: str | None = None
    second_run: str | None = None
    download_cleanup_verified = False
    lifespan_exited = False
    cancellation_completed = False
    http_scenario_completed = False
    try:
        server.start()
        origin = server.origin
        config_data = build_exact_origin_config(origin)
        config_path = private_root / "playwright-mcp.json"
        config_path.write_text(json.dumps(config_data), encoding="utf-8")
        downloads = private_root / "downloads"
        from unittest.mock import patch
        import httpx
        import browser_agent.application as application_module
        from browser_agent.application import BrowserAgentApplicationConfig, create_application
        from browser_agent.openai_provider import OpenAICompatibleDecisionSource, OpenAICompatibleProviderConfig
        provider = OpenAICompatibleProviderConfig(environment.base_url, environment.model, environment.api_key, environment.timeout)
        app_config = BrowserAgentApplicationConfig(provider, downloads, node, cli, config_path, 24)
        audit_type = make_audit_source(OpenAICompatibleDecisionSource, record)
        with patch.object(application_module, "OpenAICompatibleDecisionSource", audit_type):
            app = create_application(app_config)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://asgi.invalid") as client:
                    first_run, finished = await _drive_success(client, origin, sentinel, record, environment.timeout)
                    metadata = validate_download_metadata(finished.get("files"))
                    checks["DOWNLOAD_METADATA"] = True
                    file_id = metadata["file_id"]
                    downloaded = await _request(client, "GET", f"/runs/{first_run}/files/{file_id}", record, sentinel)
                    disposition = downloaded.headers.get("content-disposition", "")
                    if downloaded.status_code != 200 or downloaded.content != DOWNLOAD_BYTES or DOWNLOAD_FILENAME not in disposition or "attachment" not in disposition.lower():
                        raise VerificationFailure(FailureCategory.DOWNLOAD)
                    checks["DOWNLOAD_BYTES"] = True
                    second_run = await _drive_cancellation(client, origin, sentinel, record, environment.timeout)
                    cancellation_completed = True
                    isolated = await _request(client, "GET", f"/runs/{second_run}/files/{file_id}", record, sentinel)
                    if isolated.status_code != 404:
                        raise VerificationFailure(FailureCategory.DOWNLOAD)
                    checks["CANCELLATION"] = True
                    http_scenario_completed = True
            lifespan_exited = True
        checks["HTTP_BOUNDARY"] = http_scenario_completed
        checks["NVIDIA_PROVIDER"] = record.provider_calls == len(record.decisions) and record.provider_calls > 0
        catalogs_safe = bool(record.tool_sets) and all(not names & PROHIBITED_TOOLS for names in record.tool_sets)
        checks["POLICY_CONTROLS"] = catalogs_safe and record.confirmation_sequence_complete
        checks["TRUSTED_CONFIRMATION"] = record.confirmation_sequence_complete
        checks["USER_RESUME"] = (
            record.user_resume_accepted
            and record.user_resume_provider_calls is not None
            and record.provider_calls > record.user_resume_provider_calls
        )
        checks["SECRET_APPLICATION"] = (
            record.secret_submission_accepted
            and record.secret_submission_provider_calls is not None
            and record.provider_calls > record.secret_submission_provider_calls
            and finished.get("status") == "finished"
        )
        checks["MCP_STDIO"] = bool(first_run and second_run and cancellation_completed and lifespan_exited)
        validate_download_base_cleanup(downloads)
        download_cleanup_verified = True
        required_decisions = {"ask_user", "browser_snapshot", "browser_fill_form", "browser_select_option", "request_secret", "browser_click", "finish"}
        if not required_decisions <= {decision.tool_name for decision in record.decisions}:
            raise VerificationFailure(FailureCategory.RUN_STATE)
        retained_evidence = json.dumps(
            {
                "decisions": [decision.__dict__ for decision in record.decisions],
                "tool_sets": [sorted(names) for names in record.tool_sets],
                "provider_calls": record.provider_calls,
                "http_responses": len(record.http_bodies),
            },
            sort_keys=True,
        )
        if contains_prohibited_marker(retained_evidence, record.prohibited_markers):
            raise VerificationFailure(FailureCategory.DISCLOSURE)
        checks["SECRET_DISCLOSURE_SCAN"] = True
    except VerificationFailure as exc:
        primary_category = exc.category
    except Exception:
        primary_category = FailureCategory.MCP_BROWSER
    finally:
        try:
            server.close()
            if server.thread.is_alive():
                raise VerificationFailure(FailureCategory.CLEANUP)
            checks["CLEANUP"] = lifespan_exited and download_cleanup_verified and server._closed
        except Exception:
            cleanup_category = FailureCategory.CLEANUP
    ok = primary_category is None and cleanup_category is None and all(checks.values())
    if not ok and primary_category is None and cleanup_category is None:
        primary_category = FailureCategory.WORKER
    return WorkerResult(
        ok,
        primary_category.value if primary_category else None,
        cleanup_category.value if cleanup_category else None,
        checks,
        record.provider_calls,
        len(record.http_bodies),
    )


def validate_download_base_cleanup(download_base: Path) -> None:
    """Require application lifespan cleanup to leave no run storage artifacts."""
    if not download_base.exists():
        return
    try:
        remaining = list(download_base.iterdir())
    except OSError:
        raise VerificationFailure(FailureCategory.CLEANUP) from None
    if remaining:
        raise VerificationFailure(FailureCategory.CLEANUP)


def worker_main() -> int:
    private_text = os.environ.pop("M11C_PRIVATE_ROOT", None)
    sentinel = os.environ.pop("M11C_SECRET_SENTINEL", None)
    if not private_text or not sentinel:
        result = WorkerResult(False, FailureCategory.INVALID_ENVIRONMENT.value, None, {field: False for field in CHECK_FIELDS})
        sys.stdout.write(json.dumps(result.__dict__, separators=(",", ":")))
        return 2
    try:
        result = asyncio.run(_worker_async(Path(private_text), sentinel))
        code = 0 if result.ok else 1
    except KeyboardInterrupt:
        return 130
    except VerificationFailure as exc:
        result = WorkerResult(False, exc.category.value, None, {field: False for field in CHECK_FIELDS})
        code = 2 if exc.category in {FailureCategory.INVALID_ENVIRONMENT, FailureCategory.MISSING_RUNTIME} else 1
    except BaseException:
        result = WorkerResult(False, FailureCategory.WORKER.value, None, {field: False for field in CHECK_FIELDS})
        code = 1
    sys.stdout.write(json.dumps(result.__dict__, separators=(",", ":")))
    return code


def _close_process_pipes(process: Any) -> None:
    for name in ("stdout", "stderr"):
        pipe = getattr(process, name, None)
        if pipe is not None:
            try:
                pipe.close()
            except BaseException:
                pass


def _stop_process(process: Any) -> tuple[bytes, bytes]:
    try:
        already_exited = process.poll() is not None
    except BaseException:
        already_exited = False
    if not already_exited:
        try:
            process.terminate()
        except BaseException:
            try:
                process.kill()
            except BaseException:
                _close_process_pipes(process)
                raise VerificationFailure(FailureCategory.WORKER) from None
    try:
        return process.communicate(timeout=PROCESS_CLEANUP_SECONDS)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            process.kill()
        except BaseException:
            _close_process_pipes(process)
            raise VerificationFailure(FailureCategory.WORKER) from None
        try:
            return process.communicate(timeout=PROCESS_CLEANUP_SECONDS)
        except BaseException:
            _close_process_pipes(process)
            raise VerificationFailure(FailureCategory.WORKER) from None
    except BaseException:
        _close_process_pipes(process)
        raise VerificationFailure(FailureCategory.WORKER) from None


def run_worker(command: Sequence[str], environment: Mapping[str, str], timeout: float = WORKER_TIMEOUT_SECONDS) -> tuple[bytes, bytes, int]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(environment),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process(process)
        raise VerificationFailure(FailureCategory.WORKER) from None
    except KeyboardInterrupt:
        try:
            _stop_process(process)
        except VerificationFailure:
            raise
        raise
    return stdout, stderr, process.returncode


def parent_main() -> int:
    try:
        environment = load_runtime_environment(os.environ)
        sentinel = "m11c-secret-" + secrets.token_hex(24)
        with tempfile.TemporaryDirectory(prefix="m11c-live-") as temporary:
            child_environment = dict(os.environ)
            child_environment["M11C_PRIVATE_ROOT"] = temporary
            child_environment["M11C_SECRET_SENTINEL"] = sentinel
            stdout, stderr, returncode = run_worker(
                [sys.executable, str(Path(__file__).resolve()), "--worker"],
                child_environment,
            )
            markers = [sentinel, temporary]
            if environment.api_key:
                markers.append(environment.api_key)
            if contains_prohibited_marker(stdout, markers) or contains_prohibited_marker(stderr, markers):
                raise VerificationFailure(FailureCategory.DISCLOSURE)
            result = parse_worker_result(stdout, returncode)
            if result.ok:
                if not result.checks.get("SECRET_DISCLOSURE_SCAN"):
                    raise VerificationFailure(FailureCategory.WORKER)
                finalized_checks = dict(result.checks)
                finalized_checks["SECRET_DISCLOSURE_SCAN"] = True
                result = WorkerResult(
                    result.ok, result.category, result.cleanup_category,
                    finalized_checks, result.provider_calls, result.http_responses,
                )
        if result.ok:
            sys.stdout.write(safe_success_output(result) + "\n")
            return 0
        category = FailureCategory(result.category) if result.category else FailureCategory.CLEANUP
        cleanup = FailureCategory.CLEANUP if result.cleanup_category else None
        sys.stdout.write(safe_failure_output(category, cleanup) + "\n")
        return 2 if category in {FailureCategory.INVALID_ENVIRONMENT, FailureCategory.MISSING_RUNTIME} else 1
    except KeyboardInterrupt:
        return 130
    except VerificationFailure as exc:
        sys.stdout.write(safe_failure_output(exc.category) + "\n")
        return 2 if exc.category in {FailureCategory.INVALID_ENVIRONMENT, FailureCategory.MISSING_RUNTIME} else 1
    except BaseException:
        sys.stdout.write(safe_failure_output(FailureCategory.WORKER) + "\n")
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    return worker_main() if arguments == ["--worker"] else parent_main()


if __name__ == "__main__":
    raise SystemExit(main())
