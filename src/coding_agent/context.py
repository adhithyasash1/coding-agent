"""Bounded model context backed by a complete external event log."""

import json
from dataclasses import dataclass, field

from coding_agent.types import Message, Schema


def estimate_tokens(value: object) -> int:
    """Conservative UTF-8 byte bound for common byte/subword tokenizers.

    This deliberately overestimates typical source code. Provider usage remains
    authoritative for accounting; unusual tokenizers need a calibrated adapter.
    """
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 32


class ContextOverflow(ValueError):
    pass


@dataclass
class Context:
    system: str
    task: str
    groups: list[list[Message]] = field(default_factory=list)
    memory: str = ""
    dropped_groups: int = 0

    def add(self, messages: list[Message]) -> None:
        """A complete assistant/tool exchange is indivisible."""
        self.groups.append(messages)

    def build(self, schemas: list[Schema], limit: int, reserve: int) -> list[Message]:
        available = limit - reserve - estimate_tokens(schemas) - 256
        base = self._base()
        if estimate_tokens(base) > available:
            raise ContextOverflow("task, instructions, and tool schemas exceed context allowance")
        while self.groups and estimate_tokens(base + self._flatten()) > available:
            self.groups.pop(0)
            self.dropped_groups += 1
            base = self._base()
        if estimate_tokens(base) > available:
            raise ContextOverflow("durable task state exceeds context allowance")
        return base + self._flatten()

    def _base(self) -> list[Message]:
        base: list[Message] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.task},
        ]
        if self.memory or self.dropped_groups:
            base.append(
                {
                    "role": "user",
                    "content": (
                        f"Current durable state:\n{self.memory}\n"
                        f"Earlier exchanges omitted: {self.dropped_groups}. "
                        "Consult workspace and trace artifacts for details. "
                        "Do not assume omitted tests passed."
                    ),
                }
            )
        return base

    def _flatten(self) -> list[Message]:
        return [message for group in self.groups for message in group]
