import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from coding_agent.remote_server import request


def test_remote_tools_keep_process_state_and_clean_up(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    socket = Path("/tmp") / ("ca-test-" + uuid.uuid4().hex[:12] + ".sock")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "coding_agent.remote_server",
            "--socket",
            str(socket),
            "--workspace",
            str(workspace),
            "--artifacts",
            str(tmp_path / "outputs"),
            "--timeout",
            "2",
            "--lifetime",
            "10",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def rpc(operation, **kwargs):
        return json.loads(request(str(socket), json.dumps({"operation": operation, **kwargs}), 5))[
            "result"
        ]

    try:
        for _ in range(100):
            if socket.exists() or process.poll() is not None:
                break
            time.sleep(0.01)
        assert socket.exists(), process.communicate(timeout=1)
        result = rpc(
            "execute",
            call={
                "id": "1",
                "name": "start_process",
                "arguments": {"command": 'read value; echo "$value"'},
            },
        )
        identity = result["metadata"]["process_id"]
        rpc(
            "execute",
            call={
                "id": "2",
                "name": "write_process",
                "arguments": {"process_id": identity, "data": "works\n", "eof": True},
            },
        )
        rpc("close")
        assert process.wait(timeout=3) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        socket.unlink(missing_ok=True)
