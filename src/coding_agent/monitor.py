"""Deterministic progress signals. A failing test alone is not a stuck signal."""

import hashlib
import json
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
