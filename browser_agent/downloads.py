"""Typed, run-scoped storage and tracking for completed browser downloads."""

from __future__ import annotations

import inspect
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4

from .mcp_gateway import ToolObservation

_PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")
_COMPLETED_LINE = re.compile(r'^- Downloaded file .+ to "([^"]*)"$')
_COMPLETED_CLAIM = "- Downloaded file "
_APPLICATION_DOWNLOAD_MARKER = "Application recorded download:"


class DownloadError(Exception):
    """Base class for safe download-boundary errors."""


class DownloadConstructionError(DownloadError):
    """The download store could not be constructed safely."""


class DownloadValidationError(DownloadError):
    """A download identifier or filename is invalid."""


class DownloadNotFoundError(DownloadError):
    """A recorded download is unavailable."""


class DownloadTrackingError(DownloadError):
    """A successful tool observation could not be tracked safely."""


def _opaque_id(value: object) -> str:
    if type(value) is not str or not value.strip():
        raise DownloadValidationError("file_id must be a non-empty string")
    return value


def _safe_basename(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or value in (".", "..")
        or value != Path(value).name
        or PureWindowsPath(value).name != value
        or bool(PureWindowsPath(value).drive)
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise DownloadValidationError("filename must be a safe plain basename")
    return value


def _completed_download_basename(value: object) -> str:
    """Return the safe final filename from an untrusted MCP path claim."""
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or "\\" in value
        or value.endswith("/")
        or bool(PureWindowsPath(value).drive)
    ):
        raise DownloadValidationError("filename must be a safe plain basename")
    return _safe_basename(value.rsplit("/", 1)[-1])


@dataclass(frozen=True)
class DownloadMetadata:
    file_id: str
    filename: str
    size: int

    def __post_init__(self) -> None:
        _opaque_id(self.file_id)
        _safe_basename(self.filename)
        if type(self.size) is not int or self.size < 0:
            raise DownloadValidationError("size must be a non-negative int")


@dataclass(frozen=True)
class DownloadFile:
    metadata: DownloadMetadata
    path: Path = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metadata, DownloadMetadata):
            raise DownloadValidationError("metadata must be DownloadMetadata")
        if not isinstance(self.path, Path):
            raise DownloadValidationError("path must be a Path")


class RunDownloadStore:
    """Own one isolated incoming and managed download directory for a run."""

    def __init__(self, base_directory: Path | str) -> None:
        if not isinstance(base_directory, (Path, str)):
            raise DownloadConstructionError("download store construction failed safely")
        base = Path(base_directory)
        run_root: Path | None = None
        try:
            if base.is_symlink():
                raise DownloadConstructionError(
                    "configured download base must not be a symlink"
                )
            base.mkdir(parents=True, exist_ok=True)
            if base.is_symlink() or not base.is_dir():
                raise DownloadConstructionError(
                    "configured download base must be a directory"
                )
            self._base = base.resolve(strict=True)
            run_root = Path(tempfile.mkdtemp(prefix="run-", dir=self._base))
            self._run_root = run_root.resolve(strict=True)
            self._incoming = self._run_root / "incoming"
            self._managed = self._run_root / "managed"
            self._incoming.mkdir()
            self._managed.mkdir()
        except DownloadConstructionError:
            if run_root is not None:
                shutil.rmtree(run_root, ignore_errors=True)
            raise
        except (OSError, RuntimeError):
            if run_root is not None:
                shutil.rmtree(run_root, ignore_errors=True)
            raise DownloadConstructionError(
                "download store construction failed safely"
            ) from None
        self._lock = threading.RLock()
        self._files: dict[str, DownloadFile] = {}
        self._closed = False

    @property
    def output_directory(self) -> Path:
        return self._incoming

    def record_completed(self, relative_path: str) -> DownloadMetadata:
        with self._lock:
            self._ensure_open()
            filename = _safe_basename(relative_path)
            if filename.lower().endswith(_PARTIAL_SUFFIXES):
                raise DownloadValidationError("partial download files are not accepted")
            source = self._incoming / filename
            try:
                if source.is_symlink() or not source.is_file():
                    raise DownloadValidationError("completed download is unavailable")
                resolved = source.resolve(strict=True)
                incoming = self._incoming.resolve(strict=True)
                if resolved.parent != incoming or not resolved.is_relative_to(incoming):
                    raise DownloadValidationError("completed download is invalid")
                managed = self._managed.resolve(strict=True)
                if self._managed.is_symlink() or managed.parent != self._run_root:
                    raise DownloadValidationError("completed download is invalid")
                file_id = uuid4().hex
                while True:
                    destination = managed / file_id
                    try:
                        destination.touch(exist_ok=False)
                        break
                    except FileExistsError:
                        file_id = uuid4().hex
                try:
                    os.replace(source, destination)
                except BaseException:
                    destination.unlink(missing_ok=True)
                    raise
                if destination.is_symlink() or not destination.is_file():
                    raise DownloadValidationError("completed download is invalid")
                metadata = DownloadMetadata(file_id, filename, destination.stat().st_size)
            except DownloadError:
                raise
            except OSError:
                raise DownloadError("completed download could not be recorded safely") from None
            download = DownloadFile(metadata, destination)
            self._files[file_id] = download
            return metadata

    def list_metadata(self) -> tuple[DownloadMetadata, ...]:
        with self._lock:
            if self._closed:
                return ()
            return tuple(item.metadata for item in self._files.values())

    def get_file(self, file_id: str) -> DownloadFile:
        with self._lock:
            _opaque_id(file_id)
            if self._closed:
                raise DownloadNotFoundError("download file not found")
            item = self._files.get(file_id)
            if item is None:
                raise DownloadNotFoundError("download file not found")
            try:
                managed = self._managed.resolve(strict=True)
                if (
                    item.path.is_symlink()
                    or not item.path.is_file()
                    or item.path.resolve(strict=True).parent != managed
                ):
                    raise DownloadNotFoundError("download file not found")
            except OSError:
                raise DownloadNotFoundError("download file not found") from None
            return item

    def finalize(self) -> None:
        with self._lock:
            self._ensure_open()
            try:
                for child in tuple(self._incoming.iterdir()):
                    if child.is_symlink() or not child.is_dir():
                        child.unlink(missing_ok=True)
                    else:
                        shutil.rmtree(child)
            except OSError:
                raise DownloadError("download finalization failed safely") from None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                shutil.rmtree(self._run_root)
            except FileNotFoundError:
                pass
            except OSError:
                raise DownloadError("download cleanup failed safely") from None
            self._files.clear()
            self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise DownloadError("download store is closed")


def extract_completed_download_paths(observation_text: str) -> tuple[str, ...]:
    """Extract only exact MCP 0.0.78 completed-download event paths."""
    if type(observation_text) is not str:
        raise DownloadTrackingError("download observation is invalid")
    paths: list[str] = []
    seen: set[str] = set()
    for line in observation_text.splitlines():
        if not line.startswith(_COMPLETED_CLAIM):
            continue
        match = _COMPLETED_LINE.fullmatch(line)
        if match is None:
            raise DownloadTrackingError("completed download message is invalid")
        try:
            path = _completed_download_basename(match.group(1))
        except DownloadValidationError:
            raise DownloadTrackingError("completed download message is invalid") from None
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return tuple(paths)


class DownloadTrackingExecutor:
    """Track completed files after policy-enforced tool execution succeeds."""

    def __init__(self, executor: object, store: object) -> None:
        if executor is None or any(
            not inspect.iscoroutinefunction(getattr(executor, name, None))
            for name in ("invoke", "invoke_internal")
        ):
            raise DownloadConstructionError("executor must provide the async invoke API")
        required = (
            "record_completed", "list_metadata", "get_file", "finalize", "close"
        )
        if (
            store is None
            or not isinstance(getattr(store, "output_directory", None), Path)
            or any(not callable(getattr(store, name, None)) for name in required)
        ):
            raise DownloadConstructionError("store must provide the download store API")
        self._executor = executor
        self._store = store

    async def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        confirmation_granted: bool = False,
    ) -> ToolObservation:
        observation = await self._executor.invoke(
            tool_name, arguments, confirmation_granted=confirmation_granted
        )
        if type(observation) is not ToolObservation:
            raise DownloadTrackingError("tool observation is invalid")
        if observation.status != "success":
            return observation
        recorded: list[DownloadMetadata] = []
        try:
            for path in extract_completed_download_paths(observation.text):
                recorded.append(self._store.record_completed(path))
        except DownloadTrackingError:
            raise
        except DownloadError:
            raise DownloadTrackingError("completed download tracking failed safely") from None
        except Exception:
            raise DownloadTrackingError("completed download tracking failed safely") from None
        if not recorded:
            return observation
        markers = "\n".join(
            f"{_APPLICATION_DOWNLOAD_MARKER} {item.filename} ({item.size} bytes)"
            for item in recorded
        )
        text = f"{observation.text}\n{markers}" if observation.text else markers
        return replace(observation, text=text)

    async def invoke_internal(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> ToolObservation:
        return await self._executor.invoke_internal(tool_name, arguments)
