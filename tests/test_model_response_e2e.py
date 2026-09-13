"""End-to-end model response validation through the real CLI."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def test_cli_rejects_truncated_tool_response_before_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task = tmp_path / "task.txt"
    task.write_text("Do not create marker.txt.")
    config = tmp_path / "config.toml"
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            requests.append(self.path)
            body = {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "edit_file",
                                        "arguments": json.dumps(
                                            {
                                                "path": "marker.txt",
                                                "old": "",
                                                "new": "executed",
                                                "create": True,
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
            encoded = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config.write_text(
        f'[model]\nbase_url = "http://127.0.0.1:{server.server_port}/v1"\n[run]\nmax_turns = 1\n'
    )
    try:
        environment = os.environ.copy()
        environment["AGENT_API_KEY"] = "local-test-key"
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
                "--config",
                str(config),
                "--backend",
                "local",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            env=environment,
            timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert process.returncode == 3, process.stdout + process.stderr
    result = json.loads((tmp_path / "trace/result.json").read_text())
    assert result["status"] == "model_error"
    assert requests == ["/v1/chat/completions"]
    assert not (workspace / "marker.txt").exists()
