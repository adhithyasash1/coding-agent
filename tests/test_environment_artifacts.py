"""Execution-environment and captured-output access through the real tool server."""

import json
import subprocess
import sys
from dataclasses import asdict

from coding_agent.config import EnvironmentConfig
from coding_agent.toolset import WorkspaceTools
from coding_agent.types import ToolCall, worker_result


def test_tool_server_environment_and_trial_local_retrieval(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "example.txt").write_text("a" * 30000)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "coding_agent.tool_server",
            "--workspace",
            str(work),
            "--artifacts",
            str(tmp_path / "outputs"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    def rpc(operation, **kwargs):
        process.stdin.write(json.dumps({"operation": operation, **kwargs}) + "\n")
        process.stdin.flush()
        return json.loads(process.stdout.readline())["result"]

    try:
        environment = rpc("environment", settings={"task_interpreter": "missing-project-python"})
        assert environment["checkout"] == str(work.resolve())
        assert environment["task_interpreter_available"] is False
        output = rpc("execute", call=asdict(ToolCall("1", "read_file", {"path": "example.txt"})))
        assert len(output["output"]) <= 12000
        data = rpc(
            "execute",
            call=asdict(
                ToolCall(
                    "2",
                    "read_artifact",
                    {"artifact_id": output["artifact_id"], "offset": 12000, "limit": 15000},
                )
            ),
        )
        assert data["output"] == "a" * 15000
        assert data["metadata"] == {"next_offset": 27000, "eof": False}
        rejected = rpc(
            "execute",
            call=asdict(ToolCall("3", "read_artifact", {"artifact_id": output["artifact"]})),
        )
        assert rejected["status"] == "error"
    finally:
        process.communicate(timeout=5)
    assert process.returncode == 0


def test_no_silent_interpreter_selection_and_artifact_isolation(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    first = WorkspaceTools(work, tmp_path / "a")
    second = WorkspaceTools(work, tmp_path / "b")
    try:
        info = first.describe_environment(EnvironmentConfig())
        assert info["task_interpreter"] is None
        assert info["warnings"]
        output = first.execute(ToolCall("1", "list_files", {}))
        assert str(tmp_path / "a") not in json.dumps(worker_result(output))
        assert (
            second.execute(
                ToolCall("2", "read_artifact", {"artifact_id": output.artifact_id})
            ).status
            == "error"
        )
    finally:
        first.close()
        second.close()


def test_missing_bash_and_permission_only_verification(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "script").write_text("true")
    tools = WorkspaceTools(work, tmp_path / "outputs")
    try:
        monkeypatch.setattr("coding_agent.toolset.shutil.which", lambda name: None)
        missing = tools.execute(ToolCall("1", "run_command", {"command": "true", "verify": True}))
        assert missing.status == "error" and "verify_command" in missing.output
        result = tools.execute(
            ToolCall("2", "verify_command", {"argv": ["/bin/chmod", "+x", "script"]})
        )
        assert result.exit_code == 0 and not result.metadata["verification"]
        assert result.metadata["revision_before"] != result.metadata["revision_after"]
        passed = tools.execute(ToolCall("3", "verify_command", {"argv": ["/bin/sh", "script"]}))
        assert passed.metadata["verification"]
        (work / "script").chmod(0o600)
        assert tools.revision() != passed.revision
        assert (
            tools.execute(ToolCall("4", "verify_command", {"argv": ["true"], "cwd": ".."})).status
            == "error"
        )
    finally:
        tools.close()
