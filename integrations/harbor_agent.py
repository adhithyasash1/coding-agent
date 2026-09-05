"""External Harbor agent: model and trace writer remain outside the task sandbox.

The optional Harbor dependency belongs in a separate evaluation environment.
"""

import asyncio
import json
import math
import os
import shlex
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent

import coding_agent
from coding_agent.config import load_config
from coding_agent.model import OpenAIModel
from coding_agent.provenance import provenance
from coding_agent.runtime import Agent
from coding_agent.trace import EventLog
from coding_agent.types import ToolCall, ToolResult


class HarborTools:
    def __init__(self, environment, loop, root, workspace, outputs, timeout):
        self.environment, self.loop, self.root = environment, loop, root
        self.workspace, self.outputs, self.timeout = workspace, outputs, timeout
        self._revision = ""
        self.closed = False
        self.deadline = float("inf")
        self._schemas = self.rpc("schemas")

    def set_deadline(self, deadline):
        self.deadline = deadline

    def rpc(self, operation, **data):
        remaining = max(1, min(self.timeout + 15, self.deadline - time.monotonic()))
        command = shlex.join(
            [
                "python3",
                "-m",
                "coding_agent.remote_server",
                "--socket",
                f"{self.root}/tools.sock",
                "--timeout",
                str(remaining),
                "--request",
                json.dumps({"operation": operation, **data}),
            ]
        )
        future = asyncio.run_coroutine_threadsafe(
            self.environment.exec(
                command=command, env={"PYTHONPATH": self.root}, timeout_sec=math.ceil(remaining + 5)
            ),
            self.loop,
        )
        try:
            result = future.result(timeout=remaining + 10)
        except TimeoutError:
            future.cancel()
            raise
        if result.return_code != 0:
            raise RuntimeError("remote tool transport failed; inspect sandbox logs")
        response = json.loads(result.stdout)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    def schemas(self):
        return self._schemas

    def execute(self, call: ToolCall) -> ToolResult:
        result = ToolResult(**self.rpc("execute", call=asdict(call)))
        self._revision = result.revision or self._revision
        if result.artifact:
            name = Path(result.artifact).name
            result = replace(result, artifact=str(self.outputs / name))
        return result

    def revision(self):
        if not self.closed:
            self._revision = self.rpc("revision")
        return self._revision

    def close(self):
        if not self.closed:
            self.rpc("close")
            self.closed = True


class AdaptiveAgent(BaseAgent):
    def __init__(
        self, *args, config: str, workspace: str | None = None, commit_patch: bool = False, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.config = load_config(Path(config))
        self.workspace = workspace
        self.commit_patch = commit_patch
        self.root = "/tmp/ca-" + uuid.uuid4().hex[:12]
        self.result: dict[str, Any] | None = None
        if self.model_name and self.model_name != self.config.model.name:
            raise ValueError("Harbor model_name must exactly match config.model.name")

    @staticmethod
    def name() -> str:
        return "adaptive-coding-agent"

    def version(self) -> str:
        return coding_agent.__version__

    async def setup(self, environment) -> None:
        check = await environment.exec(
            command="python3 -c 'import sys; assert sys.version_info >= (3,11)'", timeout_sec=10
        )
        if check.return_code:
            raise RuntimeError("Task image needs Python 3.11+ for tool execution")
        if self.workspace is None:
            result = await environment.exec(command="pwd", timeout_sec=10)
            self.workspace = result.stdout.strip()
        if self.workspace == "/":
            raise ValueError("Set --ak workspace to the task checkout, not filesystem root")
        await environment.exec(command=shlex.join(["mkdir", "-p", self.root]), timeout_sec=10)
        await environment.upload_dir(
            Path(coding_agent.__file__).parent, f"{self.root}/coding_agent"
        )
        command = shlex.join(
            [
                "python3",
                "-m",
                "coding_agent.remote_server",
                "--socket",
                f"{self.root}/tools.sock",
                "--workspace",
                self.workspace,
                "--artifacts",
                f"{self.root}/outputs",
                "--timeout",
                str(self.config.run.command_timeout),
                "--lifetime",
                str(self.config.run.max_seconds + 120),
            ]
        )
        launch = (
            f"PYTHONPATH={shlex.quote(self.root)} nohup {command} "
            f">{shlex.quote(self.root + '/server.log')} 2>&1 </dev/null &"
        )
        await environment.exec(command=launch, timeout_sec=10)
        for _ in range(100):
            ready = await environment.exec(
                command=shlex.join(["test", "-S", f"{self.root}/tools.sock"]), timeout_sec=5
            )
            if ready.return_code == 0:
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("Tool server did not become ready")

    async def run(self, instruction, environment, context) -> None:
        loop = asyncio.get_running_loop()
        outputs = self.logs_dir / "harness" / "outputs"
        profiles = [self.config.model, self.config.supervisor_model or self.config.model]
        secrets = tuple(
            os.environ[p.api_key_env] for p in profiles if os.environ.get(p.api_key_env)
        )
        log = EventLog(self.logs_dir / "harness", secrets)
        log.emit(
            "environment",
            backend="harbor",
            workspace=self.workspace,
            provenance=provenance(),
            commit_patch=self.commit_patch,
        )

        def work():
            tools = HarborTools(
                environment,
                loop,
                self.root,
                self.workspace,
                outputs,
                self.config.run.command_timeout,
            )
            supervisor = (
                OpenAIModel(self.config.supervisor_model) if self.config.supervisor_model else None
            )
            return Agent(self.config, OpenAIModel(self.config.model), tools, log, supervisor).run(
                instruction
            )

        try:
            self.result = await asyncio.to_thread(work)
            self.populate_context_post_run(context)
            if self.commit_patch and self.result["status"] in {"submitted", "budget_exhausted"}:
                await self._commit(environment)
        finally:
            log.close()
            await environment.download_dir(f"{self.root}/outputs", outputs)
            await environment.download_file(
                f"{self.root}/server.log", self.logs_dir / "tool-server.log"
            )
        if self.result["status"] not in {"submitted", "budget_exhausted"}:
            raise RuntimeError(f"Harness stopped with {self.result['status']}")

    async def _commit(self, environment):
        command = (
            "git add -A && if ! git diff --cached --quiet; then "
            "git -c user.name='Evaluation Runner' -c user.email='eval@localhost' "
            "-c core.hooksPath=/dev/null -c commit.gpgsign=false "
            "commit -m 'Submit task patch'; fi"
        )
        result = await environment.exec(command=command, cwd=self.workspace, timeout_sec=30)
        with (self.logs_dir / "submission.json").open("x") as receipt:
            json.dump(
                {
                    "workspace": self.workspace,
                    "return_code": result.return_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                receipt,
                indent=2,
            )
        if result.return_code:
            raise RuntimeError("DeepSWE patch commit failed")

    def populate_context_post_run(self, context):
        if self.result is None:
            return
        usage = self.result["usage"]
        context.n_input_tokens = usage["input_tokens"]
        context.n_output_tokens = usage["output_tokens"]
        context.n_cache_tokens = usage["cached_tokens"]
