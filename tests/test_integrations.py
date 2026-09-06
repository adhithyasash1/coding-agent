"""SDK conformance checks. Run with the isolated evaluation environment."""

import asyncio
import contextlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from coding_agent.execution import sanitized_environment
from coding_agent.model import ScriptedModel
from coding_agent.types import ModelReply, ToolCall, Usage


class LocalEnvironment:
    def __init__(self, workspace):
        self.workspace = workspace
        self.daemons = []

    async def exec(self, command, cwd=None, env=None, timeout_sec=10):
        if command.startswith("PYTHONPATH=") and " nohup " in command:
            words = shlex.split(command)
            end = next(i for i, word in enumerate(words) if word.startswith(">"))
            with open(words[end][1:], "wb") as log:
                child = subprocess.Popen(
                    [sys.executable, *words[3:end]],
                    cwd=self.workspace,
                    env=sanitized_environment() | {"PYTHONPATH": words[0].split("=", 1)[1]},
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                )
            self.daemons.append((child, words[0].split("=", 1)[1]))
            return SimpleNamespace(return_code=0, stdout="", stderr="")
        child = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd or self.workspace,
            env=sanitized_environment() | (env or {}),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout_sec)
        return SimpleNamespace(
            return_code=child.returncode, stdout=stdout.decode(), stderr=stderr.decode()
        )

    async def upload_dir(self, source_dir, target_dir):
        shutil.copytree(source_dir, target_dir)

    async def download_dir(self, source_dir, target_dir):
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

    async def download_file(self, source_path, target_path):
        shutil.copyfile(source_path, target_path)

    async def teardown(self):
        # Local stand-in for destroying a Harbor sandbox: close its process owner
        # and wait for it to reap all managed children before deleting files.
        for child, root in self.daemons:
            try:
                if child.poll() is None:
                    result = await self.exec(
                        shlex.join(
                            [
                                sys.executable,
                                "-m",
                                "coding_agent.remote_server",
                                "--socket",
                                f"{root}/tools.sock",
                                "--timeout",
                                "5",
                                "--request",
                                json.dumps({"operation": "close"}),
                            ]
                        ),
                        env={"PYTHONPATH": root},
                    )
                    if result.return_code:
                        raise RuntimeError(result.stderr)
                await asyncio.to_thread(child.wait, 10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)


@unittest.skipUnless(importlib.util.find_spec("harbor"), "optional Harbor SDK environment required")
class HarborIntegration(unittest.TestCase):
    def test_committed_submission_has_a_receipt(self):
        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            logs = root / "logs"
            logs.mkdir()
            settings = root / "config.toml"
            settings.write_text("")
            subprocess.run(
                ["git", "init", "-b", "main", str(work)], check=True, capture_output=True
            )
            (work / "answer").write_text("submitted")
            agent = AdaptiveAgent(
                logs_dir=logs, config=str(settings), workspace=str(work), commit_patch=True
            )
            asyncio.run(agent._commit(LocalEnvironment(work)))
            result = subprocess.run(
                ["git", "-C", str(work), "show", "HEAD:answer"],
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.stdout, "submitted")
            self.assertTrue((logs / "submission.json").exists())

    def test_real_sdk_external_agent_end_to_end(self):
        from harbor.models.agent.context import AgentContext

        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            settings = root / "agent.toml"
            settings.write_text("[run]\nmax_turns=3\n")
            replies = [
                ModelReply(
                    {"role": "assistant", "content": None},
                    (ToolCall("1", "run_command", {"command": "true", "verify": True}),),
                    Usage(10, 10),
                ),
                ModelReply(
                    {"role": "assistant", "content": None},
                    (ToolCall("2", "submit", {"summary": "verified"}),),
                    Usage(10, 10),
                ),
            ]
            agent = AdaptiveAgent(logs_dir=root / "logs", config=str(settings))
            environment = LocalEnvironment(work)
            context = AgentContext()

            async def run():
                await agent.setup(environment)
                await agent.run("Verify the environment", environment, context)

            try:
                with patch(
                    "integrations.harbor_agent.OpenAIModel", return_value=ScriptedModel(replies)
                ):
                    asyncio.run(run())
                self.assertEqual(agent.result["status"], "submitted")
                self.assertEqual(context.n_input_tokens, 20)
                self.assertTrue((root / "logs/harness/result.json").exists())
                self.assertTrue(list((root / "logs/harness/outputs").glob("*.log")))
            finally:
                asyncio.run(environment.teardown())
                shutil.rmtree(agent.root, ignore_errors=True)

    def test_service_survives_return_until_teardown_or_bounded_expiry(self):
        from integrations.harbor_agent import AdaptiveAgent

        for expire in (False, True):
            with self.subTest(expire=expire), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                work = root / "work"
                work.mkdir()
                (work / "server.py").write_text(
                    "import http.server, json, os\n"
                    "from pathlib import Path\n"
                    "server = http.server.HTTPServer(('127.0.0.1', 0), "
                    "http.server.SimpleHTTPRequestHandler)\n"
                    "Path('endpoint.json').write_text(json.dumps({"
                    "'port': server.server_port, 'pid': os.getpid()}))\n"
                    "server.serve_forever()\n"
                )
                (work / "answer").write_text("lifecycle-ok")
                (work / "verify.py").write_text(
                    "import json, time, urllib.request\n"
                    "from pathlib import Path\n"
                    "for attempt in range(100):\n"
                    "    try:\n"
                    "        port = json.loads(Path('endpoint.json').read_text())['port']\n"
                    "        url = f'http://127.0.0.1:{port}/answer'\n"
                    "        assert urllib.request.urlopen(url).read() == b'lifecycle-ok'\n"
                    "        break\n"
                    "    except (OSError, ValueError):\n"
                    "        time.sleep(.05)\n"
                    "else:\n"
                    "    raise RuntimeError('Service did not become ready')\n"
                )
                settings = root / "agent.toml"
                # The service must outlive the ordinary command timeout.
                settings.write_text(
                    '[run]\nmax_turns=3\nmax_seconds=30\ncommand_timeout=1\nsupervision="off"\n'
                )
                agent = AdaptiveAgent(
                    logs_dir=root / "logs",
                    config=str(settings),
                    workspace=str(work),
                    service_lifetime_sec=5 if expire else 30,
                )
                environment = LocalEnvironment(work)
                replies = [
                    ModelReply({"role": "assistant", "content": ""}, (call,))
                    for call in (
                        ToolCall("start", "start_process", {"command": "exec python3 server.py"}),
                        ToolCall(
                            "verify",
                            "run_command",
                            {
                                "command": "python3 verify.py",
                                "verify": True,
                                "timeout": 10,
                            },
                        ),
                        ToolCall("submit", "submit", {"summary": "Service ready for grading"}),
                    )
                ]
                endpoint = None
                try:

                    async def run(agent=agent, environment=environment):
                        await agent.setup(environment)
                        await agent.run(
                            "Start and verify the service", environment, SimpleNamespace()
                        )

                    with patch(
                        "integrations.harbor_agent.OpenAIModel", return_value=ScriptedModel(replies)
                    ):
                        asyncio.run(run())
                    self.assertEqual(agent.result["status"], "submitted")
                    endpoint = json.loads((work / "endpoint.json").read_text())
                    time.sleep(1.1)
                    url = f"http://127.0.0.1:{endpoint['port']}/answer"
                    with urllib.request.urlopen(url, timeout=2) as response:
                        self.assertEqual(response.read(), b"lifecycle-ok")
                    self.assertIsNone(environment.daemons[0][0].poll())
                    if expire:
                        environment.daemons[0][0].wait(timeout=10)
                    else:
                        asyncio.run(environment.teardown())
                    with self.assertRaises(OSError):
                        urllib.request.urlopen(url, timeout=1)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(endpoint["pid"], 0)
                    self.assertEqual(environment.daemons[0][0].returncode, 0)
                finally:
                    asyncio.run(environment.teardown())
                    shutil.rmtree(agent.root, ignore_errors=True)

    def test_download_failure_preserves_model_result_and_attempts_both_downloads(self):
        from unittest.mock import AsyncMock

        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            settings = root / "agent.toml"
            settings.write_text("")
            agent = AdaptiveAgent(logs_dir=root / "logs", config=str(settings), workspace=str(work))
            environment = LocalEnvironment(work)
            environment.download_dir = AsyncMock(side_effect=RuntimeError("missing sandbox"))
            environment.download_file = AsyncMock(side_effect=RuntimeError("missing server log"))
            try:

                async def run():
                    await agent.setup(environment)
                    await agent.run("Model fails", environment, SimpleNamespace())

                with (
                    patch("integrations.harbor_agent.OpenAIModel", return_value=ScriptedModel([])),
                    self.assertRaisesRegex(RuntimeError, "Harness stopped with model_error"),
                ):
                    asyncio.run(run())
                result = json.loads((root / "logs/harness/result.json").read_text())
                self.assertEqual(result["status"], "model_error")
                events = (root / "logs/harness/events.jsonl").read_text().splitlines()
                self.assertEqual(json.loads(events[-1])["kind"], "run_finished")
                evidence = json.loads((root / "logs/artifact-download-errors.json").read_text())
                self.assertEqual(len(evidence), 2)
                environment.download_file.assert_awaited_once()
            finally:
                asyncio.run(environment.teardown())
                shutil.rmtree(agent.root, ignore_errors=True)

    def test_download_failure_does_not_mask_original_exception(self):
        from unittest.mock import AsyncMock

        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "agent.toml"
            settings.write_text("")
            agent = AdaptiveAgent(logs_dir=root / "logs", config=str(settings))
            environment = SimpleNamespace(
                download_dir=AsyncMock(side_effect=RuntimeError("secret-value transfer failed")),
                download_file=AsyncMock(side_effect=RuntimeError("missing server log")),
            )
            with patch("integrations.harbor_agent.HarborTools", side_effect=ValueError("original")):
                agent.service_deadline = time.monotonic() + 30
                with (
                    patch.dict(os.environ, {agent.config.model.api_key_env: "secret-value"}),
                    self.assertRaisesRegex(ValueError, "original"),
                ):
                    asyncio.run(agent.run("Fail initialization", environment, SimpleNamespace()))
            evidence = (root / "logs/artifact-download-errors.json").read_text()
            self.assertNotIn("secret-value", evidence)
            self.assertIn("[REDACTED]", evidence)
            environment.download_file.assert_awaited_once()

    def test_harbor_process_timeout_and_detach_semantics(self):
        from integrations.harbor_agent import HarborTools

        with patch.object(HarborTools, "rpc", return_value=[]):
            tools = HarborTools(
                None, None, "/tmp/unused", "/work", Path("/outputs"), 120, time.monotonic() + 900
            )
        with patch.object(tools, "rpc", return_value={"status": "running", "output": ""}) as rpc:
            tools.execute(ToolCall("start", "start_process", {"command": "service"}))
            self.assertGreater(rpc.call_args.kwargs["call"]["arguments"]["timeout"], 890)
            tools.execute(
                ToolCall("start", "start_process", {"command": "service", "timeout": 120})
            )
            self.assertEqual(rpc.call_args.kwargs["call"]["arguments"]["timeout"], 120)
            tools.execute(ToolCall("run", "run_command", {"command": "check"}))
            self.assertNotIn("timeout", rpc.call_args.kwargs["call"]["arguments"])
            rpc.reset_mock()
            tools.close()
            tools.close()
            tools.revision()
            rpc.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "transport is closed"):
            tools.rpc("revision")

    def test_tool_python_uses_image_when_recent(self):
        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "agent.toml"
            settings.write_text("")
            agent = AdaptiveAgent(
                logs_dir=Path(directory) / "logs", config=str(settings), workspace="/work"
            )

            class Env:
                async def exec(self, command, timeout_sec=10):
                    return SimpleNamespace(return_code=0, stdout="3.13\n", stderr="")

            self.assertEqual(asyncio.run(agent._tool_python(Env())), "python3")

    def test_tool_python_installs_private_interpreter(self):
        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "agent.toml"
            settings.write_text("")
            agent = AdaptiveAgent(
                logs_dir=Path(directory) / "logs", config=str(settings), workspace="/work"
            )
            sidecar = f"{agent.root}/python/cpython/bin/python3"

            class Env:
                async def exec(self, command, timeout_sec=10):
                    if "sys.version_info" in command:
                        return SimpleNamespace(return_code=0, stdout="3.9\n", stderr="")
                    if "uv python install" in command:
                        return SimpleNamespace(return_code=0, stdout=sidecar + "\n", stderr="")
                    return SimpleNamespace(return_code=0, stdout="", stderr="")

            self.assertEqual(asyncio.run(agent._tool_python(Env())), sidecar)

    def test_tool_python_fails_when_sidecar_unavailable(self):
        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "agent.toml"
            settings.write_text("")
            agent = AdaptiveAgent(
                logs_dir=Path(directory) / "logs", config=str(settings), workspace="/work"
            )

            class Env:
                async def exec(self, command, timeout_sec=10):
                    if "sys.version_info" in command:
                        return SimpleNamespace(return_code=0, stdout="3.9\n", stderr="")
                    return SimpleNamespace(return_code=1, stdout="", stderr="uv missing")

            with self.assertRaisesRegex(RuntimeError, "Python 3.11"):
                asyncio.run(agent._tool_python(Env()))

    def test_service_lifetime_must_be_bounded(self):
        from integrations.harbor_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "agent.toml"
            settings.write_text("")
            for lifetime in (0, -1, float("inf"), float("nan")):
                with self.subTest(lifetime=lifetime), self.assertRaises(ValueError):
                    AdaptiveAgent(
                        logs_dir=Path(directory),
                        config=str(settings),
                        service_lifetime_sec=lifetime,
                    )


@unittest.skipUnless(importlib.util.find_spec("modal"), "optional Modal SDK environment required")
class ModalIntegration(unittest.TestCase):
    def test_modal_definition_requires_no_credentials_and_never_logs_key(self):
        import importlib
        import io

        environment = {
            "AGENT_MODEL_NAME": "fixture/model",
            "AGENT_MODEL_REVISION": "abc123",
            "AGENT_TOOL_PARSER": "hermes",
            "AGENT_GPU": "L4",
            "VLLM_API_KEY": "dummy-secret-value",
        }
        with patch.dict(os.environ, environment):
            module = importlib.import_module("integrations.modal_inference")
            output = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                patch.object(subprocess, "Popen") as spawn,
                patch.object(module, "wait_for_server"),
            ):
                module.serve.local()
            command = spawn.call_args.args[0]
            self.assertNotIn("dummy-secret-value", str(command))
            self.assertNotIn("dummy-secret-value", output.getvalue())

    def test_server_command_avoids_runtime_kernel_compilation(self):
        import importlib

        with patch.dict(
            os.environ,
            AGENT_MODEL_NAME="fixture/model",
            AGENT_MODEL_REVISION="abc123",
            AGENT_TOOL_PARSER="hermes",
            AGENT_GPU="L4",
        ):
            module = importlib.import_module("integrations.modal_inference")
            command = module.server_command(module.settings())
        self.assertEqual(command[command.index("--gdn-prefill-backend") + 1], "triton")
        self.assertNotIn("--disable-log-requests", command)

    def test_failed_inference_child_fails_startup_immediately(self):
        import importlib
        import sys

        with patch.dict(
            os.environ,
            AGENT_MODEL_NAME="fixture/model",
            AGENT_MODEL_REVISION="abc123",
            AGENT_TOOL_PARSER="hermes",
            AGENT_GPU="L4",
        ):
            module = importlib.import_module("integrations.modal_inference")
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(2)"])
        process.wait(timeout=10)
        with self.assertRaisesRegex(RuntimeError, "exited during startup: 2"):
            module.wait_for_server(process)


if __name__ == "__main__":
    unittest.main()
