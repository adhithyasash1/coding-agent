"""Bounded, read-only environment discovery inside the execution environment."""

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from coding_agent.config import EnvironmentConfig
from coding_agent.execution import ProcessRunner


@dataclass(frozen=True)
class Interpreter:
    path: str
    version: str | None
    available: bool


@dataclass(frozen=True)
class TaskEnvironment:
    checkout: str
    shell: str | None
    interpreters: tuple[Interpreter, ...]
    task_interpreter: str | None
    task_interpreter_available: bool
    test_entry_points: tuple[str, ...]
    warnings: tuple[str, ...]


def _resolve(name: str, workspace: Path) -> str | None:
    if "/" in name:
        path = workspace / name
        return str(path.absolute()) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(name)


def _probe_result(path: str, workspace: Path, runner: ProcessRunner) -> Interpreter:
    try:
        result = runner.run([path, "--version"], timeout=1, cwd=workspace)
        return Interpreter(
            path,
            result.output[:512].strip() or None,
            result.status == "ok" and result.exit_code == 0,
        )
    except (OSError, ValueError, RuntimeError):
        return Interpreter(path, None, False)


def _probe(
    path: str,
    workspace: Path,
    blocked_env: tuple[str, ...] = (),
    *,
    runner: ProcessRunner | None = None,
) -> Interpreter:
    if runner is not None:
        return _probe_result(path, workspace, runner)
    with tempfile.TemporaryDirectory(prefix="coding-agent-probe-") as directory:
        process_runner = ProcessRunner(
            workspace, Path(directory), timeout=1, output_limit=512, blocked_env=blocked_env
        )
        try:
            return _probe_result(path, workspace, process_runner)
        finally:
            process_runner.close()


def _candidates(
    workspace: Path, settings: EnvironmentConfig, blocked_env: tuple[str, ...] = ()
) -> tuple[Interpreter, ...]:
    candidates = [
        settings.task_interpreter,
        ".venv/bin/python",
        "venv/bin/python",
        "python3",
        "python",
        "node",
    ]
    paths = dict.fromkeys(
        path for name in candidates if name and (path := _resolve(name, workspace))
    )
    with tempfile.TemporaryDirectory(prefix="coding-agent-probe-") as directory:
        runner = ProcessRunner(
            workspace, Path(directory), timeout=1, output_limit=512, blocked_env=blocked_env
        )
        try:
            return tuple(_probe(path, workspace, blocked_env, runner=runner) for path in paths)
        finally:
            runner.close()


def discover(
    workspace: Path, settings: EnvironmentConfig, blocked_env: tuple[str, ...] = ()
) -> TaskEnvironment:
    """Never infer a project interpreter from the tool server's sys.executable."""
    interpreters = _candidates(workspace, settings, blocked_env)
    selected = _resolve(settings.task_interpreter, workspace) if settings.task_interpreter else None
    available = any(item.path == selected and item.available for item in interpreters)
    warning = ""
    if settings.task_interpreter and not available:
        warning = "Declared task interpreter is unavailable; do not silently substitute another."
    elif not settings.task_interpreter:
        warning = (
            "No task interpreter selected. Inspect the available choices and task instructions."
        )
    return TaskEnvironment(
        str(workspace),
        shutil.which("sh"),
        interpreters,
        selected or settings.task_interpreter,
        available,
        settings.test_entry_points,
        (warning,) if warning else (),
    )
