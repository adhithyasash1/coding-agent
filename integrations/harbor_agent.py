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


class HarborRuntime(Agent):
    def _execute(self, call: ToolCall) -> ToolResult:
        if call.name == "start_process" and "timeout" not in call.arguments:
            # The shared runtime fills omitted timeouts with command_timeout.
            # Preserve omission here so HarborTools can use the sandbox lifetime.
            call = replace(call, arguments={**call.arguments, "timeout": None})
        return super()._execute(call)


class HarborTools:
    def __init__(self, environment, loop, root, workspace, outputs, timeout, service_deadline):
        self.environment, self.loop, self.root = environment, loop, root
        self.workspace, self.outputs, self.timeout = workspace, outputs, timeout
        self._revision = ""
        self.closed = False
        self.deadline = float("inf")
        self.service_deadline = service_deadline
        self._schemas = self.rpc("schemas")
        for schema in self._schemas:
            if schema["function"]["name"] == "start_process":
                schema["function"]["description"] += (
                    " In Harbor, omitted timeout keeps the service alive for grading until "
                    "sandbox teardown or the bounded tool-server lifetime. "
                    "An explicit timeout is still honored."
                )

    def set_deadline(self, deadline):
        self.deadline = deadline

    def rpc(self, operation, **data):
        if self.closed:
            raise RuntimeError("Harbor tool transport is closed")
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
        if call.name == "start_process" and call.arguments.get("timeout") is None:
            call = replace(
                call,
                arguments={
                    **call.arguments,
                    "timeout": max(0.001, self.service_deadline - time.monotonic()),
                },
            )
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
        # Harbor grades after run() returns. The sandbox owns remote processes;
        # detach here and let teardown (or the server's finite lifetime) stop them.
        self.closed = True


class AdaptiveAgent(BaseAgent):
    def __init__(
        self,
        *args,
        config: str,
        workspace: str | None = None,
        commit_patch: bool = False,
        service_lifetime_sec: float | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.config = load_config(Path(config))
        # Measured from setup, including the run and a verifier window. Harbor
        # does not expose the trial's verifier timeout through BaseAgent.
        self.service_lifetime_sec = (
            self.config.run.max_seconds + 720
            if service_lifetime_sec is None
            else float(service_lifetime_sec)
        )
        if not math.isfinite(self.service_lifetime_sec) or self.service_lifetime_sec <= 0:
            raise ValueError("service_lifetime_sec must be positive and finite")
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
                str(self.service_lifetime_sec),
            ]
        )
        launch = (
            f"PYTHONPATH={shlex.quote(self.root)} nohup {command} "
            f">{shlex.quote(self.root + '/server.log')} 2>&1 </dev/null &"
        )
        self.service_deadline = time.monotonic() + self.service_lifetime_sec
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
            cleanup_strategy="sandbox_teardown_or_service_lifetime",
            service_lifetime_sec=self.service_lifetime_sec,
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
                self.service_deadline,
            )
            supervisor = (
                OpenAIModel(self.config.supervisor_model) if self.config.supervisor_model else None
            )
            return HarborRuntime(
                self.config, OpenAIModel(self.config.model), tools, log, supervisor
            ).run(instruction)

        try:
            self.result = await asyncio.to_thread(work)
            self.populate_context_post_run(context)
            if self.commit_patch and self.result["status"] in {"submitted", "budget_exhausted"}:
                await self._commit(environment)
        finally:
            log.close()
            await self._download_artifacts(environment, outputs, secrets)
        if self.result["status"] not in {"submitted", "budget_exhausted"}:
            raise RuntimeError(f"Harness stopped with {self.result['status']}")

    async def _download_artifacts(self, environment, outputs, secrets):
        errors = []
        for download, source, destination in (
            (environment.download_dir, f"{self.root}/outputs", outputs),
            (
                environment.download_file,
                f"{self.root}/server.log",
                self.logs_dir / "tool-server.log",
            ),
        ):
            try:
                await download(source, destination)
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                for secret in sorted(secrets, key=len, reverse=True):
                    message = message.replace(secret, "[REDACTED]")
                errors.append({"source": source, "error": message})
        if errors:
            # The run trace is already finalized. Keep optional transfer failures
            # separately, without replacing an original exception or run result.
            try:
                (self.logs_dir / "artifact-download-errors.json").write_text(
                    json.dumps(errors, indent=2) + "\n"
                )
            except OSError:
                self.logger.warning("Could not write artifact-download error evidence")
            self.logger.warning("Harbor artifact downloads failed; inspect download error evidence")

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
