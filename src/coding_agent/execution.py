"""Local shell processes, bounded incremental output, and durable combined logs.

This is a process runner, not an OS sandbox. Callers must supply the sandbox in
which shell commands are allowed to run.
"""

from __future__ import annotations

import math
import os
import re
import signal
import subprocess
import threading
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from coding_agent.types import ToolResult

_SECRET_NAME = re.compile(
    r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE|AUTH|PRIVATE_KEY|ACCESS_KEY"
    r"|API_?KEY|(?:^|_)KEY(?:$|_)|(?:^|_)DSN$",
    re.IGNORECASE,
)
_PROVIDER_PREFIXES = (
    "OPENAI",
    "ANTHROPIC",
    "AZURE",
    "GOOGLE",
    "GEMINI",
    "GROQ",
    "MISTRAL",
    "COHERE",
    "DEEPSEEK",
    "TOGETHER",
    "FIREWORKS",
    "OPENROUTER",
    "HUGGINGFACE",
    "HF_",
    "AWS_",
    "BEDROCK",
    "MODAL",
    "LANGSMITH",
    "LANGCHAIN",
)
_TRUNCATION = "\n... output truncated; see full artifact ...\n"


def sanitized_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Strip provider configuration and credential-bearing environment entries."""
    entries = os.environ if source is None else source
    return {key: value for key, value in entries.items() if _safe_environment(key, value)}


def _safe_environment(key: str, value: str) -> bool:
    if key.upper().startswith(_PROVIDER_PREFIXES) or _SECRET_NAME.search(key):
        return False
    return re.search(r"\w+://[^/\s]*@", value) is None


def positive_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return value


def bounded_text(text: str, limit: int) -> str:
    """Bound even the truncation marker, retaining both ends when possible."""
    if len(text) <= limit:
        return text
    marker = _TRUNCATION[:limit]
    remaining = limit - len(marker)
    head = (remaining + 1) // 2
    tail = remaining // 2
    return text[:head] + marker + (text[-tail:] if tail else "")


def _read_excerpt(path: Path, offset: int, limit: int) -> tuple[str, int]:
    with path.open("rb") as stream:
        end = stream.seek(0, os.SEEK_END)
        stream.seek(offset)
        length = max(0, end - offset)
        if length <= limit * 4:
            return bounded_text(stream.read(length).decode("utf-8", "replace"), limit), end
        head = stream.read(limit).decode("utf-8", "replace")
        stream.seek(max(offset, end - limit))
        tail = stream.read(end - stream.tell()).decode("utf-8", "replace")
    return bounded_text(head + _TRUNCATION + tail, limit), end


@dataclass
class _Process:
    id: str
    child: subprocess.Popen[bytes]
    artifact: Path
    timeout: float
    finished: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    offset: int = 0
    timed_out: bool = False
    stopped: bool = False
    monitor: threading.Thread | None = None


def _kill_group(child: subprocess.Popen[bytes]) -> None:
    # Each child is its own session leader. Kill the group even if its leader
    # has exited, since grandchildren can retain inherited descriptors.
    with suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGKILL)


def _monitor(process: _Process) -> None:
    try:
        try:
            process.child.wait(timeout=process.timeout)
        except subprocess.TimeoutExpired:
            with process.lock:
                process.timed_out = not process.stopped
            _kill_group(process.child)
            process.child.wait()
    finally:
        _kill_group(process.child)
        if process.child.stdin is not None:
            process.child.stdin.close()
        process.finished.set()


class ProcessRunner:
    """Own all process groups until exit, explicit stop, timeout, or close."""

    def __init__(
        self,
        workspace: Path,
        artifacts: Path,
        *,
        timeout: float = 60,
        output_limit: int = 12000,
        blocked_env: tuple[str, ...] = (),
    ) -> None:
        self.workspace = workspace.resolve(strict=True)
        self.artifacts = artifacts.resolve()
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.timeout = positive_timeout(timeout)
        if output_limit < 1:
            raise ValueError("output_limit must be positive")
        self.output_limit = output_limit
        self._environment = {
            key: value for key, value in sanitized_environment().items() if key not in blocked_env
        }
        self._processes: dict[str, _Process] = {}
        self._closed = False

    def _spawn(self, command: str, timeout: float | None) -> _Process:
        if self._closed:
            raise RuntimeError("process runner is closed")
        if not command.strip():
            raise ValueError("command must not be empty")
        duration = positive_timeout(self.timeout if timeout is None else timeout)
        process_id = uuid.uuid4().hex
        artifact = self.artifacts / f"process-{process_id}.log"
        with artifact.open("xb") as output:
            child = subprocess.Popen(
                command,
                shell=True,
                executable="/bin/sh",
                cwd=self.workspace,
                env=self._environment,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                bufsize=0,
            )
        process = _Process(process_id, child, artifact, duration)
        self._processes[process.id] = process
        process.monitor = threading.Thread(target=_monitor, args=(process,), daemon=True)
        process.monitor.start()
        return process

    def start(self, command: str, timeout: float | None = None) -> ToolResult:
        return self._result(self._spawn(command, timeout))

    def run(self, command: str, timeout: float | None = None) -> ToolResult:
        process = self._spawn(command, timeout)
        # Synchronous commands cannot be interacted with. EOF also prevents
        # commands reading stdin from hanging until the deadline.
        if process.child.stdin is not None:
            process.child.stdin.close()
        process.finished.wait()
        return self._result(process)

    def _get(self, process_id: str) -> _Process:
        try:
            return self._processes[process_id]
        except KeyError:
            raise ValueError(f"unknown process: {process_id}") from None

    def poll(self, process_id: str) -> ToolResult:
        return self._result(self._get(process_id))

    def write(self, process_id: str, data: str, *, eof: bool = False) -> ToolResult:
        process = self._get(process_id)
        pipe = process.child.stdin
        if process.finished.is_set() or pipe is None or pipe.closed:
            raise ValueError("process stdin is closed")
        # The watchdog can always kill a blocked reader, including a process
        # that does not consume a large write to its pipe.
        payload = memoryview(data.encode("utf-8"))
        try:
            while payload:
                written = pipe.write(payload)
                if not written:
                    raise BrokenPipeError("process stdin is closed")
                payload = payload[written:]
            if eof:
                pipe.close()
        except (BrokenPipeError, ValueError) as error:
            raise ValueError("process stdin is closed") from error
        return self._result(process)

    def stop(self, process_id: str) -> ToolResult:
        process = self._get(process_id)
        with process.lock:
            if not process.finished.is_set():
                process.stopped = True
                _kill_group(process.child)
        process.finished.wait()
        return self._result(process)

    def _result(self, process: _Process) -> ToolResult:
        with process.lock:
            output, process.offset = _read_excerpt(
                process.artifact, process.offset, self.output_limit
            )
            finished = process.finished.is_set()
            exit_code = process.child.returncode if finished else None
            status = "running"
            if finished:
                status = "timeout" if process.timed_out else ("ok" if exit_code == 0 else "error")
            return ToolResult(
                status=status,  # type: ignore[arg-type]
                output=output,
                exit_code=exit_code,
                artifact=str(process.artifact),
                metadata={
                    "process_id": process.id,
                    "pid": process.child.pid,
                    "timeout": process.timeout,
                    "timed_out": process.timed_out,
                    "stopped": process.stopped,
                    "output_bytes": process.offset,
                },
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for process in self._processes.values():
            self.stop(process.id)
            if process.monitor is not None:
                process.monitor.join()
