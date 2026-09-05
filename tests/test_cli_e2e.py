"""Real CLI fixtures reproduce integration failures without paid model calls."""

import json
import subprocess
import sys
from pathlib import Path


def call(name: str, arguments: dict[str, object], identity: str = "1") -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": identity,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def run_fixture(tmp_path: Path, replies: list[dict[str, object]], config: str = ""):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = tmp_path / "task.txt"
    task.write_text("Create answer.txt containing 42 and verify it.")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(replies))
    settings = tmp_path / "config.toml"
    settings.write_text(config)
    return subprocess.run(
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
            str(settings),
            "--backend",
            "local",
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )


def test_cli_edits_verifies_and_persists_final_result(tmp_path):
    process = run_fixture(
        tmp_path,
        [
            call("edit_file", {"path": "answer.txt", "old": "", "new": "42", "create": True}),
            call("run_command", {"command": 'test "$(cat answer.txt)" = 42', "verify": True}),
            call("submit", {"summary": "Created and verified answer.txt"}),
        ],
    )
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "trace/result.json").read_text())
    assert result["status"] == "submitted"
    assert result["grader_result"] is None
    assert (tmp_path / "workspace/answer.txt").read_text() == "42"


def test_cli_budget_still_has_a_result(tmp_path):
    process = run_fixture(tmp_path, [call("list_files", {})], "[run]\nmax_turns=1\n")
    assert process.returncode == 2, process.stdout + process.stderr
    assert json.loads((tmp_path / "trace/result.json").read_text())["status"] == "budget_exhausted"


def test_documented_demo_runs_end_to_end(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/demo.py"
    process = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=20
    )
    assert process.returncode == 0, process.stdout + process.stderr
    trace = next((tmp_path / ".agent-runs").glob("*/trace/events.jsonl"))
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    outcomes = [e["result"]["status"] for e in events if e["kind"] == "tool_result"]
    assert outcomes == ["error", "ok", "ok", "ok"]
