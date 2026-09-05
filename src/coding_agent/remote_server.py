"""Persistent sandbox tools over a local Unix socket for external eval runners."""

import argparse
import json
import socket
import time
from pathlib import Path
from typing import Any

from coding_agent.tool_server import dispatch
from coding_agent.toolset import WorkspaceTools


def request(path: str, payload: str, timeout: float) -> str:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(timeout)
        client.connect(path)
        client.sendall(payload.encode() + b"\n")
        with client.makefile("rb") as stream:
            data = stream.readline(2 * 1024 * 1024)
    if not data.endswith(b"\n"):
        raise ValueError("incomplete remote tool response")
    return data.decode()


def handle(tools: WorkspaceTools, payload: bytes) -> tuple[dict[str, Any], bool]:
    try:
        value = json.loads(payload)
        if value["operation"] == "close":
            tools.close()
            return {"result": None}, True
        return {"result": dispatch(tools, value)}, False
    except (ValueError, OSError, TypeError, KeyError) as error:
        return {"error": f"{type(error).__name__}: {error}"}, False


def serve(path: Path, workspace: Path, artifacts: Path, timeout: float, lifetime: float) -> None:
    tools = WorkspaceTools(workspace, artifacts, command_timeout=timeout)
    deadline = time.monotonic() + lifetime
    try:
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            path.chmod(0o600)
            server.listen(1)
            server.settimeout(1)
            while time.monotonic() < deadline:
                if _connection(server, tools, timeout):
                    return
    finally:
        tools.close()
        path.unlink(missing_ok=True)


def _connection(server: socket.socket, tools: WorkspaceTools, timeout: float) -> bool:
    try:
        client, _ = server.accept()
    except TimeoutError:
        return False
    with client:
        client.settimeout(timeout + 10)
        with client.makefile("rb") as stream:
            payload = stream.readline(2 * 1024 * 1024)
        response, stop = handle(tools, payload)
        try:
            client.sendall(json.dumps(response).encode() + b"\n")
        except (BrokenPipeError, TimeoutError):
            return False
        return stop


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--lifetime", type=float, default=1800)
    parser.add_argument("--request")
    args = parser.parse_args()
    if args.request is not None:
        print(request(str(args.socket), args.request, args.timeout), end="")
    else:
        if args.workspace is None or args.artifacts is None:
            parser.error("server requires --workspace and --artifacts")
        serve(args.socket, args.workspace, args.artifacts, args.timeout, args.lifetime)


if __name__ == "__main__":
    main()
