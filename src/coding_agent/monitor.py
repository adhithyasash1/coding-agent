"""Deterministic progress signals. A failing test alone is not a stuck signal."""

import hashlib
import json
import re
import time
from collections import deque
from dataclasses import dataclass

from coding_agent.types import ToolCall, ToolResult


@dataclass(frozen=True)
class Signal:
    reason: str
    event_ids: tuple[int, ...]
    revision: str


@dataclass(frozen=True)
class Observation:
    fingerprint: str
    revision: str
    event_id: int


class ProgressMonitor:
    def __init__(self, threshold: int = 3) -> None:
        self.threshold = threshold
        self.history: deque[Observation] = deque(maxlen=12)

    def observe(self, call: ToolCall, result: ToolResult, event_id: int) -> Signal | None:
        payload = [call.name, call.arguments, result.status, result.exit_code, result.output]
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        current = Observation(fingerprint, result.revision or "unknown", event_id)
        self.history.append(current)
        matches = [
            o
            for o in self.history
            if o.fingerprint == fingerprint and o.revision == current.revision
        ]
        if len(matches) < self.threshold:
            return None
        return Signal(
            "repeated action and outcome without workspace progress",
            tuple(o.event_id for o in matches),
            current.revision,
        )


def diagnostic_fingerprint(call: ToolCall, result: ToolResult) -> str:
    """Normalize only common pytest duration and ISO timestamp metadata."""
    output = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b", "<time>", result.output)
    output = re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <duration>s", output)
    payload = [call.name, call.arguments, result.status, result.exit_code, output]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class AdaptiveProgressMonitor:
    """Candidate signals for review, never an assertion that diagnosis is useless."""

    def __init__(self) -> None:
        self.revision: str | None = None
        self.reads: deque[tuple[float, int]] = deque(maxlen=256)
        self.failures: deque[tuple[str, int]] = deque(maxlen=12)

    def observe(
        self, call: ToolCall, result: ToolResult, event_id: int, *, now: float | None = None
    ) -> Signal | None:
        revision = result.revision or "unknown"
        if revision != self.revision or result.metadata.get("verification_receipt"):
            self.reads.clear()
            self.failures.clear()
            self.revision = revision
        if result.status in {"error", "timeout"}:
            key = diagnostic_fingerprint(call, result)
            self.failures.append((key, event_id))
            matches = self._matches(key)
            if len(matches) >= 3:
                return Signal("candidate: repeated diagnostic failure", matches, revision)
        if call.name in {"read_file", "list_files", "search", "read_artifact", "poll_process"}:
            self._read(event_id, now)
            if len(self.reads) >= 6 and self.reads[-1][0] - self.reads[0][0] >= 120:
                return Signal(
                    "candidate: extended read-only diagnosis",
                    tuple(i for _, i in self.reads),
                    revision,
                )
        return None

    def _matches(self, key: str) -> tuple[int, ...]:
        return tuple(identity for fingerprint, identity in self.failures if fingerprint == key)

    def _read(self, event_id: int, now: float | None) -> None:
        self.reads.append((time.monotonic() if now is None else now, event_id))
