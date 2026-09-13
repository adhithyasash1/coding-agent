"""Shared wire types. No execution or provider dependencies."""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

Message = dict[str, Any]
Schema = dict[str, Any]
Status = Literal["ok", "error", "timeout", "running"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    status: Status
    output: str
    exit_code: int | None = None
    artifact: str | None = None
    revision: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact_id: str | None = None


def worker_result(result: ToolResult) -> dict[str, Any]:
    """Storage paths belong to the host trace, never the worker tool protocol."""
    value = asdict(result)
    value.pop("artifact")
    return value


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ModelReply:
    message: Message
    calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"


class Model(Protocol):
    def complete(
        self, messages: list[Message], tools: list[Schema], max_tokens: int
    ) -> ModelReply: ...


class Toolset(Protocol):
    def schemas(self) -> list[Schema]: ...

    def execute(self, call: ToolCall) -> ToolResult: ...

    def revision(self) -> str: ...

    def close(self) -> None: ...
