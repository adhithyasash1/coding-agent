"""Workspace file tools and explicitly local shell execution for the harness."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from coding_agent.execution import ProcessRunner, bounded_text
from coding_agent.types import Schema, ToolCall, ToolResult

_IGNORED = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "node_modules",
        ".tox",
        ".nox",
        ".next",
        ".coverage",
        ".DS_Store",
    }
)
_STRING: Schema = {"type": "string"}
_BOOLEAN: Schema = {"type": "boolean"}
_TIMEOUT: Schema = {"type": "number", "exclusiveMinimum": 0}


def _schema(name: str, description: str, properties: Schema, required: list[str]) -> Schema:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _text(arguments: dict[str, Any], key: str, default: str | None = None) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _boolean(arguments: dict[str, Any], key: str) -> bool:
    value = arguments.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _timeout(arguments: dict[str, Any]) -> float | None:
    value = arguments.get("timeout")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a number")
    return float(value)


def _line(arguments: dict[str, Any], key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return int(value)


def _atomic_write(path: Path, text: str, mode: int | None) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            if mode is not None:
                os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class WorkspaceTools:
    def __init__(
        self,
        workspace: Path,
        artifacts: Path,
        *,
        command_timeout: float = 60,
        output_limit: int = 12000,
        blocked_env: tuple[str, ...] = (),
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        self.artifacts = artifacts.resolve()
        if self.workspace.is_relative_to(self.artifacts):
            raise ValueError("artifacts must not contain the workspace")
        self.output_limit = output_limit
        self._runner = ProcessRunner(
            self.workspace,
            self.artifacts,
            timeout=command_timeout,
            output_limit=output_limit,
            blocked_env=blocked_env,
        )
        self._closed = False
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
            "read_file": self._read_file,
            "list_files": self._list_files,
            "search": self._search,
            "edit_file": self._edit_file,
            "run_command": self._run_command,
            "start_process": self._start_process,
            "poll_process": self._poll_process,
            "write_process": self._write_process,
            "stop_process": self._stop_process,
        }

    def schemas(self) -> list[Schema]:
        command = {"command": dict(_STRING), "timeout": dict(_TIMEOUT)}
        process = {"process_id": dict(_STRING)}
        return [
            _schema(
                "read_file",
                "Read UTF-8 text inside the workspace; lines are 1-based.",
                {
                    "path": dict(_STRING),
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                ["path"],
            ),
            _schema(
                "list_files",
                "Recursively list workspace files, excluding runtime caches.",
                {
                    "path": dict(_STRING),
                    "glob": dict(_STRING),
                },
                [],
            ),
            _schema(
                "search",
                "Find text with path and line number; regex is opt-in.",
                {
                    "pattern": dict(_STRING),
                    "path": dict(_STRING),
                    "glob": dict(_STRING),
                    "regex": dict(_BOOLEAN),
                },
                ["pattern"],
            ),
            _schema(
                "edit_file",
                "Atomically replace one exact match of old with new. "
                "To create a missing file, set create=true; old may be omitted or empty.",
                {
                    "path": dict(_STRING),
                    "old": dict(_STRING),
                    "new": dict(_STRING),
                    "create": dict(_BOOLEAN),
                },
                ["path", "new"],
            ),
            _schema(
                "run_command",
                "Run a local /bin/sh command in the workspace sandbox. "
                "verify=true marks a successful check only if workspace content is unchanged.",
                {**command, "verify": dict(_BOOLEAN)},
                ["command"],
            ),
            _schema(
                "start_process",
                "Start a local shell process with pipe stdin and a deadline.",
                command,
                ["command"],
            ),
            _schema(
                "poll_process",
                "Read new output and status since the previous process call.",
                process,
                ["process_id"],
            ),
            _schema(
                "write_process",
                "Write exact text to pipe stdin; eof=true closes stdin.",
                {**process, "data": dict(_STRING), "eof": dict(_BOOLEAN)},
                ["process_id", "data"],
            ),
            _schema(
                "stop_process",
                "Stop a process and its process group, preserving its log.",
                process,
                ["process_id"],
            ),
        ]

    def execute(self, call: ToolCall) -> ToolResult:
        try:
            if self._closed:
                raise ValueError("toolset is closed")
            if call.name not in self._handlers:
                raise ValueError(f"unknown tool: {call.name}")
            self._validate_arguments(call)
            result = self._handlers[call.name](call.arguments)
            if result.revision is None:
                result = replace(result, revision=self.revision())
            return result
        except (OSError, ValueError, RuntimeError, re.error) as error:
            return self._output(f"{type(error).__name__}: {error}", error=True)

    def _validate_arguments(self, call: ToolCall) -> None:
        schema = next(
            item["function"] for item in self.schemas() if item["function"]["name"] == call.name
        )["parameters"]
        unknown = set(call.arguments) - schema["properties"].keys()
        missing = set(schema["required"]) - call.arguments.keys()
        if unknown or missing:
            raise ValueError(
                f"invalid arguments: unknown={sorted(unknown)}, missing={sorted(missing)}"
            )

    def _path(self, value: str) -> Path:
        path = (self.workspace / value).resolve()
        if not path.is_relative_to(self.workspace):
            raise ValueError("path must stay inside the workspace")
        return path

    def _excluded(self, path: Path) -> bool:
        return (
            path.name in _IGNORED
            or path.suffix in {".pyc", ".pyo"}
            or path.name.startswith(".coverage.")
            or path.is_relative_to(self.artifacts)
        )

    def _walk(self, root: Path) -> Iterator[Path]:
        if not root.is_dir():
            if not root.exists():
                raise FileNotFoundError(root)
            yield root
            return
        for directory, names, files in os.walk(root, followlinks=False, onerror=_raise_walk_error):
            base = Path(directory)
            names[:] = sorted(name for name in names if not self._excluded(base / name))
            for name in sorted(files + [name for name in names if (base / name).is_symlink()]):
                path = base / name
                if not self._excluded(path):
                    yield path

    def revision(self) -> str:
        digest = hashlib.sha256()
        for path in self._walk(self.workspace):
            relative = path.relative_to(self.workspace).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big") + relative)
            digest.update(stat.S_IMODE(path.lstat().st_mode).to_bytes(4, "big"))
            if path.is_symlink():
                content = os.readlink(path).encode("utf-8")
                digest.update(b"link" + hashlib.sha256(content).digest())
            else:
                with path.open("rb") as stream:
                    digest.update(b"file" + hashlib.file_digest(stream, "sha256").digest())
        return digest.hexdigest()

    def _output(self, text: str, *, error: bool = False) -> ToolResult:
        artifact = self.artifacts / f"tool-{uuid.uuid4().hex}.txt"
        artifact.write_text(text, encoding="utf-8")
        return ToolResult(
            status="error" if error else "ok",
            output=bounded_text(text, self.output_limit),
            artifact=str(artifact),
        )

    def _read_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._path(_text(arguments, "path"))
        text = path.read_text(encoding="utf-8")
        if "start_line" in arguments or "end_line" in arguments:
            lines = text.splitlines(keepends=True)
            start = _line(arguments, "start_line", 1)
            end = _line(arguments, "end_line", max(1, len(lines)))
            if end < start:
                raise ValueError("end_line must not precede start_line")
            text = "".join(lines[start - 1 : end])
        return self._output(text)

    def _files(self, arguments: dict[str, Any]) -> Iterator[Path]:
        root = self._path(_text(arguments, "path", "."))
        pattern = _text(arguments, "glob", "*")
        for path in self._walk(root):
            if path.resolve().is_relative_to(self.workspace) and fnmatch.fnmatch(
                path.relative_to(self.workspace).as_posix(), pattern
            ):
                yield path

    def _list_files(self, arguments: dict[str, Any]) -> ToolResult:
        paths = sorted(
            path.relative_to(self.workspace).as_posix() for path in self._files(arguments)
        )
        return self._output("\n".join(paths))

    def _search(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = _text(arguments, "pattern")
        expression = re.compile(pattern if _boolean(arguments, "regex") else re.escape(pattern))
        matches: list[str] = []
        for path in self._files(arguments):
            matches.extend(self._matches(path, expression))
        return self._output("\n".join(matches))

    def _matches(self, path: Path, expression: re.Pattern[str]) -> Iterator[str]:
        if not path.is_file():
            return
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return
        relative = path.relative_to(self.workspace).as_posix()
        for number, line in enumerate(text.splitlines(), 1):
            if expression.search(line):
                yield f"{relative}:{number}:{line}"

    def _edit_file(self, arguments: dict[str, Any]) -> ToolResult:
        path = self._path(_text(arguments, "path"))
        old, new = _text(arguments, "old", ""), _text(arguments, "new")
        create = _boolean(arguments, "create")
        if path.exists():
            with path.open(encoding="utf-8", newline="") as stream:
                original = stream.read()
            if not old or original.count(old) != 1:
                raise ValueError("old must match exactly once and must not be empty")
            replacement = original.replace(old, new, 1)
            mode = stat.S_IMODE(path.stat().st_mode)
        else:
            if not create or old:
                raise ValueError("missing file requires create=true and old='' ")
            replacement, mode = new, None
        if path.exists() and replacement == original:
            return self._output(f"Unchanged: {path.relative_to(self.workspace)}")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, replacement, mode)
        return self._output(f"Updated: {path.relative_to(self.workspace)}")

    def _run_command(self, arguments: dict[str, Any]) -> ToolResult:
        verify = _boolean(arguments, "verify")
        before = self.revision()
        result = self._runner.run(_text(arguments, "command"), _timeout(arguments))
        after = self.revision()
        metadata = {
            **result.metadata,
            "revision_before": before,
            "revision_after": after,
            "verification": verify
            and result.status == "ok"
            and result.exit_code == 0
            and before == after,
        }
        return replace(result, revision=after, metadata=metadata)

    def _start_process(self, arguments: dict[str, Any]) -> ToolResult:
        return self._runner.start(_text(arguments, "command"), _timeout(arguments))

    def _poll_process(self, arguments: dict[str, Any]) -> ToolResult:
        return self._runner.poll(_text(arguments, "process_id"))

    def _write_process(self, arguments: dict[str, Any]) -> ToolResult:
        return self._runner.write(
            _text(arguments, "process_id"),
            _text(arguments, "data"),
            eof=_boolean(arguments, "eof"),
        )

    def _stop_process(self, arguments: dict[str, Any]) -> ToolResult:
        return self._runner.stop(_text(arguments, "process_id"))

    def close(self) -> None:
        self._runner.close()
        self._closed = True


def _raise_walk_error(error: OSError) -> None:
    raise error
