"""Authoritative local traces. All persisted content is redacted before writing."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "authorization",
    "proxyauthorization",
    "apikey",
    "xapikey",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "token",
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "credential",
    "credentials",
    "cookie",
    "setcookie",
    "privatekey",
}
_RESERVED_KEYS = {"id", "timestamp", "elapsed_seconds", "kind"}
MAX_ARTIFACT_BYTES = 1024 * 1024


def _is_secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in _SECRET_KEYS or normalized.endswith(("apikey", "password", "secret"))


def _encode(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, content: bytes) -> None:
    """Publish a complete file without overwriting any existing evidence."""
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _sync_directory(path.parent)
    finally:
        os.unlink(temporary)


class EventLog:
    """One fresh run per directory; concurrent emits share a strictly ordered writer.

    Events have top-level id, timestamp (UTC), elapsed_seconds, kind, and caller fields.
    IDs start at one. Existing logs/artifacts/results are never overwritten. Artifacts
    are UTF-8 text limited to MAX_ARTIFACT_BYTES, before and after redaction.
    """

    def __init__(
        self,
        directory: Path,
        secrets: tuple[str, ...] = (),
        observer: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if any(not isinstance(secret, str) or not secret for secret in secrets):
            raise ValueError("Trace secrets must be nonempty strings")
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._secrets = tuple(sorted(set(secrets), key=len, reverse=True))
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._next_id = 1
        self._finished = False
        self._observer = observer
        self._failed = False
        self._stream = (self.directory / "events.jsonl").open("xb", buffering=0)
        os.chmod(self.directory / "events.jsonl", 0o600)
        _sync_directory(self.directory)

    def _text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        return text

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, dict):
            return self._redact_object(value)
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        if value is None or type(value) in (bool, int, float):
            return value
        raise ValueError("Trace values must be JSON serializable")

    def _redact_object(self, value: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Trace object keys must be strings")
            redacted_key = self._text(key)
            if redacted_key in result:
                raise ValueError("Trace key collision after redaction")
            result[redacted_key] = _REDACTED if _is_secret_key(key) else self._redact(item)
        return result

    def _check_writable(self) -> None:
        if self._stream.closed or self._finished or self._failed:
            raise ValueError("Trace is closed, finished, or has a failed write")

    def emit(self, kind: str, **data: Any) -> int:
        if not isinstance(kind, str) or not kind:
            raise ValueError("Trace kind must be a nonempty string")
        if _RESERVED_KEYS.intersection(data):
            raise ValueError("Trace data cannot override event metadata")
        with self._lock:
            self._check_writable()
            redacted = self._redact(data)
            if _RESERVED_KEYS.intersection(redacted):
                raise ValueError("Redacted trace data cannot override event metadata")
            event = {
                **redacted,
                "id": self._next_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "elapsed_seconds": time.monotonic() - self._start,
                "kind": self._text(kind),
            }
            content = _encode(event)
            try:
                if self._stream.write(content) != len(content):
                    raise OSError("Incomplete trace write")
                os.fsync(self._stream.fileno())
            except OSError:
                self._failed = True
                raise
            self._next_id += 1
            if self._observer is not None:
                self._observer(event)
            return self._next_id - 1

    def artifact(self, name: str, text: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("Artifact name must be a safe filename of at most 128 characters")
        if len(text.encode("utf-8")) > MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact exceeds maximum byte size")
        # Structured text artifacts receive the same known-key redaction as events.
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            content = self._text(text).encode("utf-8")
        else:
            content = _encode(self._redact(value))
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError("Redacted artifact exceeds maximum byte size")
        with self._lock:
            self._check_writable()
            directory = self.directory / "artifacts"
            directory.mkdir(mode=0o700, exist_ok=True)
            if directory.is_symlink():
                raise ValueError("Artifact directory must not be a symlink")
            path = directory / name
            _atomic_write(path, content)
            _sync_directory(self.directory)
            return str(path)

    def finish(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._check_writable()
            content = _encode(self._redact(result))
            _atomic_write(self.directory / "result.json", content)
            self._finished = True

    def close(self) -> None:
        with self._lock:
            self._stream.close()


def _read_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("non-finite JSON value")


def _validate_event(event: Any, expected_id: int, previous_elapsed: float) -> float:
    if not isinstance(event, dict) or type(event.get("id")) is not int:
        raise ValueError("expected event object with integer id")
    if event["id"] != expected_id:
        raise ValueError("event IDs must be sequential starting at one")
    if not isinstance(event.get("kind"), str) or not event["kind"]:
        raise ValueError("missing or invalid event kind")
    timestamp = datetime.fromisoformat(event.get("timestamp", ""))
    offset = timestamp.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("event timestamp must be UTC")
    return _validate_elapsed(event.get("elapsed_seconds"), previous_elapsed)


def _validate_elapsed(elapsed: Any, previous_elapsed: float) -> float:
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool):
        raise ValueError("invalid monotonic offset")
    if not math.isfinite(elapsed):
        raise ValueError("invalid monotonic offset")
    if elapsed < previous_elapsed:
        raise ValueError("monotonic offsets must not decrease")
    return float(elapsed)


def read_events(path: Path) -> list[dict[str, Any]]:
    """Validate an entire JSONL file; even a truncated final record is rejected visibly."""
    events: list[dict[str, Any]] = []
    previous_elapsed = 0.0
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.endswith(b"\n"):
                raise ValueError(f"Invalid trace record at line {line_number}: truncated record")
            try:
                event = json.loads(
                    raw,
                    object_pairs_hook=_read_object,
                    parse_constant=_reject_constant,
                )
                previous_elapsed = _validate_event(event, line_number, previous_elapsed)
            except (ValueError, TypeError, OverflowError, RecursionError):
                raise ValueError(
                    f"Invalid trace record at line {line_number}: corrupt JSON or event metadata"
                ) from None
            events.append(event)
    return events
