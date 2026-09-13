"""Cleanup failure through the real CLI must never strand a waiting caller."""

import json
import subprocess
import sys


def test_cli_cleanup_error_does_not_deadlock_command(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    task = tmp_path / "task"
    task.write_text("Run a command")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "1",
                            "type": "function",
                            "function": {
                                "name": "run_command",
                                "arguments": json.dumps({"command": "true", "verify": True}),
                            },
                        }
                    ],
                }
            ]
        )
    )
    config = tmp_path / "config.toml"
    config.write_text("[run]\nmax_turns=1\n")
    code = (
        "from coding_agent import execution\n"
        "def denied(child):\n"
        "    raise PermissionError(1, 'injected group cleanup failure')\n"
        "execution._kill_group = denied\n"
        "from coding_agent.cli import main\n"
        "raise SystemExit(main())\n"
    )
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "run",
            "--workspace",
            str(work),
            "--task-file",
            str(task),
            "--fixture",
            str(fixture),
            "--config",
            str(config),
            "--backend",
            "local",
            "--trace-dir",
            str(tmp_path / "trace"),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert process.returncode == 2, process.stderr
    events = [
        json.loads(line) for line in (tmp_path / "trace/events.jsonl").read_text().splitlines()
    ]
    result = next(event["result"] for event in events if event["kind"] == "tool_result")
    assert result["status"] == "error"
    assert "PermissionError" in result["metadata"]["cleanup_error"]
