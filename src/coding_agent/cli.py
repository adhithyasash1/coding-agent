"""Command-line entry point and local trace inspection."""

import argparse
import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from typing import Any

from coding_agent.config import ModelConfig, load_config
from coding_agent.model import OpenAIModel, ScriptedModel
from coding_agent.provenance import provenance
from coding_agent.runtime import Agent
from coding_agent.toolset import WorkspaceTools
from coding_agent.trace import EventLog, read_events
from coding_agent.types import Model, ModelReply, ToolCall, Usage

EXIT_CODES = {
    "submitted": 0,
    "budget_exhausted": 2,
    "model_error": 3,
    "runtime_error": 4,
    "interrupted": 130,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observable adaptive coding-agent harness")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Run a task using the configured model")
    run.add_argument("--task-file", type=Path, required=True)
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--config", type=Path)
    run.add_argument("--trace-dir", type=Path)
    run.add_argument("--backend", choices=["local", "docker"], default="docker")
    run.add_argument("--image", default="python:3.12-slim")
    run.add_argument("--network", choices=["none", "bridge"], default="none")
    run.add_argument("--fixture", type=Path, help="Offline scripted responses for harness testing")
    run.add_argument("--quiet", action="store_true", help="Disable progress lines on stderr")
    inspect = commands.add_parser("inspect", help="Validate and summarize a local trace")
    inspect.add_argument("trace_dir", type=Path)
    inspect.add_argument("--events", action="store_true", help="Print every recorded event")
    return parser


def fixture_model(path: Path) -> ScriptedModel:
    values = json.loads(path.read_text())
    if not isinstance(values, list):
        raise ValueError("fixture must contain a list of assistant messages")
    replies = []
    for value in values:
        calls = tuple(
            ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"]))
            for c in value.get("tool_calls", [])
        )
        replies.append(ModelReply(value, calls, Usage(10, 10), "tool_calls" if calls else "stop"))
    return ScriptedModel(replies)


def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    task = args.task_file.read_text(encoding="utf-8")
    workspace = args.workspace.resolve(strict=True)
    trace_dir = (args.trace_dir or Path(".agent-runs") / uuid.uuid4().hex).resolve()
    if trace_dir.is_relative_to(workspace):
        raise ValueError("trace directory must be outside the task workspace")
    profiles = [config.model, config.supervisor_model or config.model]
    secrets = _secrets(profiles)
    log = EventLog(trace_dir, secrets=secrets, observer=None if args.quiet else _progress)
    print(f"Trace: {trace_dir}", file=sys.stderr, flush=True)
    tools = None
    worker_started = False
    try:
        model = _worker_model(args, config.model)
        supervisor = OpenAIModel(config.supervisor_model) if config.supervisor_model else None
        tools = _tools(
            args,
            workspace,
            trace_dir,
            config.run.command_timeout,
            tuple(p.api_key_env for p in profiles),
        )
        log.emit(
            "environment",
            backend=args.backend,
            image=args.image,
            network=args.network,
            workspace=str(workspace),
            fixture=args.fixture is not None,
            provenance=provenance(),
        )
        agent = Agent(config, model, tools, log, supervisor)
        worker_started = True
        result = agent.run(task)
    except (Exception, KeyboardInterrupt) as error:
        status = "interrupted" if isinstance(error, KeyboardInterrupt) else "runtime_error"
        result = {
            "status": status,
            "detail": f"{type(error).__name__}: {error}",
            "worker_status": "not_started" if not worker_started else "unknown",
            "submission_status": "not_submitted",
            "grader_status": None,
            "reward": None,
        }
        artifact = log.artifact("initialization-error.txt", traceback.format_exc())
        log.emit("initialization_failed", artifact=artifact, **result)
        _cleanup_initialization(tools, worker_started, log)
        tools = None
        log.finish(result)
    finally:
        _cleanup_initialization(tools, worker_started, log)
        log.close()
    print(json.dumps(result, indent=2))
    return EXIT_CODES[result["status"]]


def _cleanup_initialization(tools: Any, worker_started: bool, log: EventLog) -> None:
    if tools is None or worker_started:
        return
    try:
        tools.close()
    except Exception as error:
        log.emit("cleanup_failed", error=f"{type(error).__name__}: {error}")


def _worker_model(args: argparse.Namespace, profile: ModelConfig) -> Model:
    return fixture_model(args.fixture) if args.fixture else OpenAIModel(profile)


def _secrets(profiles: list[ModelConfig]) -> tuple[str, ...]:
    return tuple(os.environ[p.api_key_env] for p in profiles if os.environ.get(p.api_key_env))


def _progress(event: dict[str, Any]) -> None:
    kind = event["kind"]
    detail = event.get("role", "")
    if kind == "tool_started":
        detail = event["call"]["name"]
    elif kind == "tool_result":
        detail = f"{event['call']['name']}: {event['result']['status']}"
    elif kind == "model_response":
        detail = f"{detail}: {event['usage']}"
    elif kind == "run_finished":
        detail = event["result"]["status"]
    print(
        f"[{event['id']:04d} +{event['elapsed_seconds']:.1f}s] {kind} {detail}",
        file=sys.stderr,
        flush=True,
    )


def _tools(
    args: argparse.Namespace,
    workspace: Path,
    trace_dir: Path,
    timeout: float,
    blocked_env: tuple[str, ...],
) -> Any:
    if args.backend == "local":
        return WorkspaceTools(
            workspace, trace_dir / "outputs", command_timeout=timeout, blocked_env=blocked_env
        )
    from coding_agent.sandbox import DockerTools

    return DockerTools(
        workspace, trace_dir / "outputs", image=args.image, network=args.network, timeout=timeout
    )


def _inspect(args: argparse.Namespace) -> int:
    events = read_events(args.trace_dir / "events.jsonl")
    if args.events:
        for event in events:
            print(json.dumps(event, ensure_ascii=False))
    result_path = args.trace_dir / "result.json"
    result = (
        json.loads(result_path.read_text()) if result_path.exists() else {"status": "unfinished"}
    )
    print(json.dumps({"events": len(events), "result": result}, indent=2))
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        return _run(args) if args.command == "run" else _inspect(args)
    except (ValueError, OSError, TypeError) as error:
        print(f"coding-agent: {error}", file=sys.stderr)
        return 4
