"""Public CLI reproductions of completion, environment and output-access defects."""

import json
import shlex
import sys

import pytest
from test_cli_e2e import call, run_fixture


@pytest.mark.parametrize("command", ["false | tail -1", "false; true"])
def test_cli_never_submits_masked_verification_failure(tmp_path, command):
    process = run_fixture(
        tmp_path,
        [
            call("run_command", {"command": command, "verify": True}),
            call("submit", {"summary": "This must not be accepted"}, "2"),
        ],
        "[run]\nmax_turns=2\n",
    )
    result = json.loads((tmp_path / "trace/result.json").read_text())
    assert process.returncode == 2
    assert result["verified_revision"] is None


def test_cli_worker_gets_artifact_ids_instead_of_trace_storage_paths(tmp_path):
    process = run_fixture(
        tmp_path,
        [call("list_files", {}), call("remember", {"notes": "Output inspected"}, "2")],
        "[run]\nmax_turns=2\n",
    )
    assert process.returncode == 2
    events = [
        json.loads(line) for line in (tmp_path / "trace/events.jsonl").read_text().splitlines()
    ]
    requests = [e for e in events if e["kind"] == "model_request"]
    content = next(m["content"] for m in requests[-1]["messages"] if m["role"] == "tool")
    result = json.loads(content)
    assert result.get("artifact_id")
    assert str(tmp_path / "trace") not in content


def test_cli_explicit_project_interpreter_and_argv_verification(tmp_path):
    package = tmp_path / "project_packages"
    package.mkdir()
    (package / "only_project.py").write_text("VALUE = 42\n")
    interpreter = tmp_path / "project-python"
    interpreter.write_text(
        "#!/bin/sh\n"
        f"export PYTHONPATH={shlex.quote(str(package))}\n"
        f'exec {shlex.quote(sys.executable)} -B "$@"\n'
    )
    interpreter.chmod(0o755)
    process = run_fixture(
        tmp_path,
        [
            call(
                "verify_command",
                {"argv": [sys.executable, "-B", "-c", "import only_project"]},
                "default",
            ),
            call(
                "verify_command",
                {
                    "argv": [
                        str(interpreter),
                        "-c",
                        "import only_project; assert only_project.VALUE == 42",
                    ]
                },
            ),
            call("submit", {"summary": "Verified using the declared project interpreter"}, "2"),
        ],
        "[environment]\ntask_interpreter=" + json.dumps(str(interpreter)) + "\n",
    )
    assert process.returncode == 0, process.stderr
    events = [
        json.loads(line) for line in (tmp_path / "trace/events.jsonl").read_text().splitlines()
    ]
    environment = next(e for e in events if e["kind"] == "task_environment")["environment"]
    assert environment["task_interpreter"] == str(interpreter)
    assert environment["task_interpreter_available"] is True
    default = next(e for e in events if e["kind"] == "tool_result" and e["call"]["id"] == "default")
    assert default["result"]["exit_code"] != 0
    receipt = next(
        e
        for e in events
        if e["kind"] == "tool_result" and e["result"]["metadata"].get("verification")
    )["result"]["metadata"]
    assert receipt["verification_receipt"]["executable"] == str(interpreter)


@pytest.mark.parametrize("interrupted", [False, True])
def test_cli_setup_failure_has_terminal_evidence_and_preserves_cleanup_error(
    tmp_path, monkeypatch, interrupted
):
    from coding_agent import cli
    from coding_agent.model import ScriptedModel

    class BrokenTools:
        closed = False

        def describe_environment(self, settings):
            if interrupted:
                raise KeyboardInterrupt()
            raise RuntimeError("setup failed")

        def close(self):
            self.closed = True
            raise RuntimeError("cleanup also failed")

    tools = BrokenTools()
    monkeypatch.setattr(cli, "_tools", lambda *args: tools)
    monkeypatch.setattr(cli, "_worker_model", lambda *args: ScriptedModel([]))
    work = tmp_path / "work"
    work.mkdir()
    task = tmp_path / "task.txt"
    task.write_text("Test setup")
    trace = tmp_path / "trace"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "coding-agent",
            "run",
            "--task-file",
            str(task),
            "--workspace",
            str(work),
            "--trace-dir",
            str(trace),
        ],
    )
    assert cli.main() != 0
    result = json.loads((trace / "result.json").read_text())
    assert result["worker_status"] == "not_started"
    assert result["status"] == ("interrupted" if interrupted else "runtime_error")
    assert result["reward"] is None
    assert "cleanup" not in result["detail"]
    assert tools.closed
    assert "cleanup_failed" in (trace / "events.jsonl").read_text()
