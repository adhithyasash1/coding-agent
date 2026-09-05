import json
import subprocess
import sys
from pathlib import Path

from coding_agent.sandbox import docker_command


def test_docker_binds_only_task_artifacts_and_readonly_harness(tmp_path):
    command = docker_command(
        "test", tmp_path / "work", tmp_path / "outputs", "python:3.12-slim", "none", 60
    )
    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    mounts = [command[i + 1] for i, item in enumerate(command) if item == "--mount"]
    assert len(mounts) == 3
    assert mounts[-1].endswith("/harness,readonly")
    assert not any("API_KEY" in part or "TOKEN" in part for part in command)


def test_docker_shared_stdio_protocol_runs_real_tools(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    requests = [
        {"operation": "schemas"},
        {
            "operation": "execute",
            "call": {
                "id": "1",
                "name": "run_command",
                "arguments": {"command": "printf protocol-ok"},
            },
        },
    ]
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "coding_agent.tool_server",
            "--workspace",
            str(workspace),
            "--artifacts",
            str(tmp_path / "outputs"),
        ],
        input="".join(json.dumps(request) + "\n" for request in requests),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert isinstance(replies[0]["result"], list)
    assert replies[1]["result"]["output"] == "protocol-ok"
    assert Path(replies[1]["result"]["artifact"]).read_text() == "protocol-ok"
