"""Environment discovery cleanup through the public command-line entry point."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_cli_probe_kills_forked_descendant(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wrapper = workspace / "probe-wrapper"
    wrapper.write_text("#!/bin/sh\n(sleep 30) &\nprintf '%s' \"$!\" > child.pid\nsleep 30\n")
    wrapper.chmod(0o755)
    task = tmp_path / "task.txt"
    task.write_text("Probe the environment and finish.")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "submit-1",
                            "type": "function",
                            "function": {
                                "name": "submit",
                                "arguments": json.dumps({"summary": "done"}),
                            },
                        }
                    ],
                }
            ]
        )
    )
    config = tmp_path / "config.toml"
    config.write_text(
        '[environment]\ntask_interpreter = "./probe-wrapper"\n'
        "[run]\nmax_turns = 1\nrequire_verification = false\n"
    )
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "coding_agent",
            "run",
            "--task-file",
            str(task),
            "--workspace",
            str(workspace),
            "--trace-dir",
            str(tmp_path / "trace"),
            "--fixture",
            str(fixture),
            "--config",
            str(config),
            "--backend",
            "local",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    pid = int((workspace / "child.pid").read_text())
    try:
        deadline = time.monotonic() + 3
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid), f"probe descendant {pid} is still running"
    finally:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
