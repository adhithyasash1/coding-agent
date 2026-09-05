"""SDK conformance checks. Run with the isolated evaluation environment."""

import asyncio
import contextlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from coding_agent.execution import sanitized_environment
from coding_agent.model import ScriptedModel
from coding_agent.types import ModelReply, ToolCall, Usage


class LocalEnvironment:
    def __init__(self, workspace):
        self.workspace = workspace

    async def exec(self, command, cwd=None, env=None, timeout_sec=10):
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
                shutil.rmtree(agent.root, ignore_errors=True)


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
