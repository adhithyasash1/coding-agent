"""JSONL tool transport running inside a task sandbox, without model credentials."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from coding_agent.config import EnvironmentConfig
from coding_agent.toolset import WorkspaceTools
from coding_agent.types import ToolCall


def dispatch(tools: WorkspaceTools, request: dict[str, Any]) -> Any:
    operation = request["operation"]
    if operation == "schemas":
        return tools.schemas()
    if operation == "revision":
        return tools.revision()
    if operation == "environment":
        settings = request.get("settings", {})
        settings["test_entry_points"] = tuple(settings.get("test_entry_points", ()))
        return tools.describe_environment(EnvironmentConfig(**settings))
    if operation == "execute":
        return asdict(tools.execute(ToolCall(**request["call"])))
    raise ValueError(f"Unknown operation: {operation}")


def serve(workspace: Path, artifacts: Path, timeout: float) -> None:
    tools = WorkspaceTools(workspace, artifacts, command_timeout=timeout)
    try:
        for line in sys.stdin:
            try:
                result = {"result": dispatch(tools, json.loads(line))}
            except (ValueError, KeyError, TypeError, OSError) as error:
                result = {"error": f"{type(error).__name__}: {error}"}
            print(json.dumps(result), flush=True)
    finally:
        tools.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("/workspace"))
    parser.add_argument("--artifacts", type=Path, default=Path("/artifacts"))
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    serve(args.workspace, args.artifacts, args.timeout)
