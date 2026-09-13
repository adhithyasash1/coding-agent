"""Validated configuration. Secrets are referenced by environment variable name."""

import dataclasses
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelConfig:
    name: str = "select-model-before-live-testing"
    base_url: str = "http://127.0.0.1:8000/v1"
    api_key_env: str = "AGENT_API_KEY"
    context_tokens: int = 32768
    max_output_tokens: int = 4096
    timeout: float = 60.0
    revision: str = "unselected"
    temperature: float = 0.0
    top_p: float = 1.0
    seed: int = 0
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        _validate_types(self)
        _validate_endpoint(self.base_url)
        if self.context_tokens <= self.max_output_tokens + 1024:
            raise ValueError("context_tokens must leave at least 1024 input tokens")
        if min(self.max_output_tokens, self.timeout) <= 0:
            raise ValueError("model output and timeout limits must be positive")
        if self.temperature < 0 or not 0 < self.top_p <= 1:
            raise ValueError("temperature must be nonnegative and top_p in (0, 1]")
        if self.reasoning_effort is not None and not self.reasoning_effort.strip():
            raise ValueError("reasoning_effort must be nonempty when selected")


@dataclass(frozen=True)
class RunConfig:
    max_turns: int = 50
    max_tokens: int = 100000
    max_seconds: float = 1200
    supervision: Literal["off", "shadow", "on"] = "shadow"
    max_interventions: int = 3
    supervisor_max_tokens: int = 1024
    supervisor_cooldown: int = 4
    repeat_threshold: int = 3
    command_timeout: float = 60
    require_verification: bool = True
    generation_policy: Literal["legacy", "time_aware"] = "legacy"
    context_layout: Literal["legacy", "history_first"] = "legacy"
    progress_policy: Literal["legacy", "adaptive"] = "legacy"
    recovery_policy: Literal["critic", "reminder"] = "critic"

    def __post_init__(self) -> None:
        _validate_types(self)
        positive = (
            self.max_turns,
            self.max_tokens,
            self.max_seconds,
            self.supervisor_max_tokens,
            self.supervisor_cooldown,
            self.repeat_threshold,
            self.command_timeout,
        )
        if min(positive) <= 0 or self.max_interventions < 0:
            raise ValueError("run limits must be positive; max_interventions may be zero")
        if self.supervision not in {"off", "shadow", "on"}:
            raise ValueError("supervision must be off, shadow, or on")
        for name, choices in (
            ("generation_policy", {"legacy", "time_aware"}),
            ("context_layout", {"legacy", "history_first"}),
            ("progress_policy", {"legacy", "adaptive"}),
            ("recovery_policy", {"critic", "reminder"}),
        ):
            if getattr(self, name) not in choices:
                raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class EnvironmentConfig:
    task_interpreter: str | None = None
    test_entry_points: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_types(self)
        if self.task_interpreter is not None and not self.task_interpreter.strip():
            raise ValueError("task_interpreter must be nonempty")


@dataclass(frozen=True)
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    run: RunConfig = field(default_factory=RunConfig)
    supervisor_model: ModelConfig | None = None
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)


def _validate_endpoint(value: str) -> None:
    url = urlsplit(value)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("model.base_url must be an HTTP(S) URL")
    if url.username or url.password or url.query or url.fragment:
        raise ValueError("model.base_url cannot contain credentials, query, or fragment")


def _validate_types(instance: object) -> None:
    for attribute in dataclasses.fields(instance):  # type: ignore[arg-type]
        value = getattr(instance, attribute.name)
        if not _valid_type(value, attribute.type):
            raise ValueError(f"invalid type or non-finite value for {attribute.name}")


def _valid_type(value: Any, expected: Any) -> bool:
    if expected is float:
        return type(value) in (float, int) and math.isfinite(value)
    if expected in (str, int, bool):
        return type(value) is expected
    if expected == str | None:
        return value is None or isinstance(value, str)
    if expected == tuple[str, ...]:
        return isinstance(value, tuple) and all(_nonempty_string(v) for v in value)
    return isinstance(value, str)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _section(raw: dict[str, Any], name: str, cls: type[Any]) -> Any:
    values = raw.get(name, {})
    if not isinstance(values, dict):
        raise ValueError(f"{name} must be a TOML table")
    unknown = values.keys() - {f.name for f in dataclasses.fields(cls)}
    if unknown:
        raise ValueError(f"unknown {name} settings: {sorted(unknown)}")
    if cls is EnvironmentConfig and isinstance(values.get("test_entry_points"), list):
        values = {**values, "test_entry_points": tuple(values["test_entry_points"])}
    return cls(**values)


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if raw.keys() - {"model", "run", "supervisor_model", "environment"}:
        raise ValueError("unknown configuration section")
    return Config(
        model=_section(raw, "model", ModelConfig),
        run=_section(raw, "run", RunConfig),
        supervisor_model=_section(raw, "supervisor_model", ModelConfig)
        if "supervisor_model" in raw
        else None,
        environment=_section(raw, "environment", EnvironmentConfig),
    )
