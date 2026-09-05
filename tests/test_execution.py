import os
import shlex
import sys
import time

from coding_agent.execution import ProcessRunner, sanitized_environment


def test_real_timeout_and_full_output(tmp_path):
    runner = ProcessRunner(tmp_path, tmp_path / "outputs", timeout=0.15, output_limit=80)
    try:
        result = runner.run("printf 'started'; sleep 5")
        assert result.status == "timeout"
        assert result.metadata["timed_out"]
        assert "started" in result.output
        result = runner.run("printf '%10000s' x", timeout=2)
        assert result.status == "ok"
        assert len(result.output) <= 80
        assert os.path.getsize(result.artifact) == 10000
    finally:
        runner.close()


def test_pipe_interaction_and_incremental_poll(tmp_path):
    runner = ProcessRunner(tmp_path, tmp_path / "outputs", timeout=3)
    try:
        result = runner.start("read answer; printf 'received:%s' \"$answer\"")
        identity = result.metadata["process_id"]
        result = runner.write(identity, "hello\n", eof=True)
        output = result.output
        deadline = time.monotonic() + 2
        while result.status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
            result = runner.poll(identity)
            output += result.output
        assert result.status == "ok"
        assert output == "received:hello"
        assert runner.poll(identity).output == ""
    finally:
        runner.close()


def test_child_cannot_continue_after_parent_exits(tmp_path):
    runner = ProcessRunner(tmp_path, tmp_path / "outputs", timeout=2)
    try:
        command = "(sleep 0.3; touch escaped) & exit 0"
        assert runner.run(command).status == "ok"
        time.sleep(0.4)
        assert not (tmp_path / "escaped").exists()
    finally:
        runner.close()


def test_secret_environment_is_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "private-value")
    runner = ProcessRunner(tmp_path, tmp_path / "outputs")
    try:
        command = shlex.join(
            [sys.executable, "-c", "import os; print(os.environ.get('AGENT_API_KEY', 'absent'))"]
        )
        assert runner.run(command).output.strip() == "absent"
    finally:
        runner.close()
    assert sanitized_environment(
        {"PATH": "/bin", "MODAL_TOKEN_ID": "x", "CUSTOM_PASSWORD": "y"}
    ) == {"PATH": "/bin"}
