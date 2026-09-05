"""Credential-free Docker tool transport. The model client stays on the host."""

import json
import os
import selectors
import subprocess
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from coding_agent.types import Schema, ToolCall, ToolResult


def docker_command(
    name: str, workspace: Path, artifacts: Path, image: str, network: str, timeout: float
) -> list[str]:
    source = Path(__file__).resolve().parent.parent
    if any("," in str(path) for path in (source, workspace, artifacts)):
        raise ValueError("Docker bind paths cannot contain commas")
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "--name",
        name,
        "--init",
        "--network",
        network,
        "--cpus",
        "2",
        "--memory",
        "4g",
        "--pids-limit",
        "256",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,size=512m",
        "--mount",
        f"type=bind,source={workspace},target=/workspace",
        "--mount",
        f"type=bind,source={artifacts},target=/artifacts",
        "--mount",
        f"type=bind,source={source},target=/harness,readonly",
        "--env",
        "PYTHONPATH=/harness",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--workdir",
        "/workspace",
        "--entrypoint",
        "python",
        image,
        "-u",
        "-m",
        "coding_agent.tool_server",
        "--timeout",
        str(timeout),
    ]


class DockerTools:
    def __init__(
        self,
        workspace: Path,
        artifacts: Path,
        *,
        image: str,
        network: str = "none",
        timeout: float = 60,
    ) -> None:
        self.artifacts = artifacts.resolve()
        self.artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.name = "coding-agent-" + uuid.uuid4().hex
        self.timeout = timeout
        self._revision = ""
        self._closed = False
        self._deadline = float("inf")
        self._stderr = (artifacts / "sandbox.stderr.log").open("xb")
        try:
            self.process = subprocess.Popen(
                docker_command(
                    self.name, workspace.resolve(), self.artifacts, image, network, timeout
                ),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
            )
            self._schemas = cast(list[Schema], self._rpc("schemas"))
            self.revision()
        except BaseException:
            self.close()
            raise

    def set_deadline(self, deadline: float) -> None:
        self._deadline = deadline

    def _rpc(self, operation: str, **data: Any) -> Any:
        if self._closed or self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("sandbox transport is closed")
        self.process.stdin.write((json.dumps({"operation": operation, **data}) + "\n").encode())
        self.process.stdin.flush()
        duration = min(self.timeout + 10, self._deadline - time.monotonic())
        raw = self._readline(max(0, duration))
        if not raw:
            raise RuntimeError("sandbox exited; inspect outputs/sandbox.stderr.log")
        response = json.loads(raw)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    def _readline(self, duration: float) -> bytes:
        assert self.process.stdout is not None
        deadline = time.monotonic() + duration
        chunks = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in chunks:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise TimeoutError("sandbox tool transport deadline exceeded")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    return bytes(chunks)
                chunks.extend(chunk)
                if len(chunks) > 2 * 1024 * 1024:
                    raise ValueError("sandbox response exceeds transport limit")
        return bytes(chunks)

    def schemas(self) -> list[Schema]:
        return self._schemas

    def execute(self, call: ToolCall) -> ToolResult:
        result = ToolResult(**self._rpc("execute", call=asdict(call)))
        self._revision = result.revision or self._revision
        if result.artifact:
            relative = Path(result.artifact).relative_to("/artifacts")
            result = replace(result, artifact=str(self.artifacts / relative))
        return result

    def revision(self) -> str:
        if not self._closed:
            self._revision = str(self._rpc("revision"))
        return self._revision

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = getattr(self, "process", None)
        if process is not None:
            if process.stdin:
                process.stdin.close()
            subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, timeout=15)
            process.wait(timeout=15)
        self._stderr.close()
