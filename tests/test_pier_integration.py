"""Real Pier trials with a local-only transport and deterministic model replies.

The transport is a test double, not a sandbox: only this file's generated toy
tasks may use it. The real Git fixture tests exercise commit and patch transfer
without Docker, cloud, task downloads, or real model calls.
"""

import asyncio
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from coding_agent.model import ScriptedModel
from coding_agent.types import ModelReply, ToolCall, Usage


def run_git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


class LocalTransport:
    """Temporary directories emulate unmounted agent/verifier environments."""

    def __init__(self, root, options, events):
        from pier.models.trial.paths import EnvironmentPaths

        self.root = root
        self.workspace = root / "work"
        self.workspace.mkdir(parents=True)
        self.options = options
        self.events = events
        self.role = "verifier" if options.get("mounts_json") else "agent"
        self.task_os = options["task_env_config"].os
        self.default_user = options["default_user"]
        self.capabilities = SimpleNamespace(mounted=False)
        self.env_paths = EnvironmentPaths(
            logs_dir=root / "logs",
            agent_dir=root / "logs/agent",
            verifier_dir=root / "logs/verifier",
            artifacts_dir=root / "logs/artifacts",
            tests_dir=root / "tests",
        )
        self.daemons = []
        self.stopped = False
        self.preserve_workspace = False

    async def start(self, force_build=False):
        self.events.append((self.role, "start"))
        for path in (
            self.env_paths.agent_dir,
            self.env_paths.verifier_dir,
            self.env_paths.artifacts_dir,
        ):
            Path(path).mkdir(parents=True)
        if self.role == "verifier":
            # Emulate tests baked into the separate verifier image.
            await self.upload_dir(self.options["environment_dir"], self.env_paths.tests_dir)

    async def run_healthcheck(self):
        pass

    def check_live(self):
        if self.stopped:
            raise AssertionError(f"Access to stopped {self.role} environment")

    async def exec(self, command, cwd=None, env=None, timeout_sec=10, user=None):
        self.check_live()
        # Give children only fixture data, PATH and the task's explicit variables.
        child_env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"} | (env or {})
        if command.startswith("PYTHONPATH=") and " nohup " in command:
            words = shlex.split(command)
            end = next(i for i, word in enumerate(words) if word.startswith(">"))
            server_root = words[0].split("=", 1)[1]
            with open(words[end][1:], "wb") as output:
                child = subprocess.Popen(
                    [sys.executable, *words[3:end]],
                    cwd=self.workspace,
                    env=child_env | {"PYTHONPATH": server_root},
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                )
            self.daemons.append((child, server_root))
            return SimpleNamespace(return_code=0, stdout="", stderr="")
        child = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd or self.workspace,
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout_sec)
        except BaseException:
            if child.returncode is None:
                child.kill()
            await child.wait()
            raise
        return SimpleNamespace(
            return_code=child.returncode, stdout=stdout.decode(), stderr=stderr.decode()
        )

    async def upload_dir(self, source_dir, target_dir):
        self.check_live()
        self.events.append((self.role, "upload_dir", str(target_dir)))
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

    async def download_dir(self, source_dir, target_dir):
        self.check_live()
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)

    async def download_file(self, source_path, target_path):
        self.check_live()
        shutil.copyfile(source_path, target_path)

    async def upload_file(self, source_path, target_path):
        self.check_live()
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)

    async def is_dir(self, path, user=None):
        self.check_live()
        return Path(path).is_dir()

    async def empty_dirs(self, paths, chmod=False):
        self.check_live()
        for path in paths:
            shutil.rmtree(path, ignore_errors=True)
            Path(path).mkdir(parents=True)

    async def stop(self, delete=True):
        if self.stopped:
            return
        for child, server_root in self.daemons:
            try:
                if child.poll() is None:
                    await self.exec(
                        shlex.join(
                            [
                                sys.executable,
                                "-m",
                                "coding_agent.remote_server",
                                "--socket",
                                f"{server_root}/tools.sock",
                                "--timeout",
                                "5",
                                "--request",
                                json.dumps({"operation": "close"}),
                            ]
                        ),
                        env={"PYTHONPATH": server_root},
                    )
                await asyncio.to_thread(child.wait, 10)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
                shutil.rmtree(server_root, ignore_errors=True)
        self.stopped = True
        self.events.append((self.role, "stop", delete))
        if not self.preserve_workspace:
            shutil.rmtree(self.workspace)


@unittest.skipUnless(
    importlib.util.find_spec("pier") and importlib.util.find_spec("harbor"),
    "optional Pier and Harbor SDK environment required",
)
class PierIntegration(unittest.TestCase):
    def setUp(self):
        # Pier's factory imports LiteLLM. Prevent its import-time price download.
        self.environment_patch = patch.dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True")
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = self.root / "agent.toml"
        self.settings.write_text(
            '[model]\nname="fixture/toy"\napi_key_env="PIER_TOY_UNUSED_KEY"\n'
            '[run]\nmax_turns=3\nmax_seconds=30\nsupervision="off"\n'
        )
        self.events = []
        self.backends = []

    def make_config(self, mode="separate", import_path="integrations.pier_agent:AdaptiveAgent"):
        from pier.models.trial.config import TrialConfig

        task = self.root / "toy"
        task.mkdir()
        (task / "environment").mkdir()
        (task / "tests").mkdir()
        (task / "instruction.md").write_text("Write 42 to answer, verify it, and submit.")
        # Collection is Pier's responsibility, not adapter-specific patch logic.
        collect = f"cp answer {shlex.quote(str(self.root / 'agent/logs/artifacts/answer'))}"
        (task / "task.toml").write_text(
            'version="1.0"\n[agent]\ntimeout_sec=30\n[verifier]\n'
            f'environment_mode="{mode}"\ntimeout_sec=30\n'
            f"[[verifier.collect]]\ncommand={json.dumps(collect)}\n"
        )
        isolation_check = "test ! -e answer && " if mode == "separate" else ""
        (task / "tests/test.sh").write_text(
            "#!/bin/sh\n"
            f'if {isolation_check}test "$(cat ../logs/artifacts/answer)" = 42; then\n'
            "  echo 1 > ../logs/verifier/reward.txt\n"
            "else\n  echo 0 > ../logs/verifier/reward.txt\nfi\n"
        )
        return TrialConfig(
            task={"path": task},
            trials_dir=self.root / "trials",
            trial_name="toy",
            agent={"import_path": import_path, "kwargs": {"config": str(self.settings)}},
        )

    def backend_factory(self, **options):
        role = "verifier" if options.get("mounts_json") else "agent"
        backend = LocalTransport(self.root / role, options, self.events)
        self.backends.append(backend)
        return backend

    def replies(self):
        return [
            ModelReply(
                {"role": "assistant", "content": None},
                (
                    ToolCall(
                        "1",
                        "run_command",
                        {
                            "command": "printf 42 > answer",
                        },
                    ),
                ),
                Usage(6, 3),
            ),
            ModelReply(
                {"role": "assistant", "content": None},
                (
                    ToolCall(
                        "verify",
                        "run_command",
                        {"command": 'test "$(cat answer)" = 42', "verify": True},
                    ),
                ),
                Usage(6, 2),
            ),
            ModelReply(
                {"role": "assistant", "content": None},
                (ToolCall("2", "submit", {"summary": "Toy answer verified"}),),
                Usage(4, 2),
            ),
        ]

    async def run_trial(self, config, *, commit_patch=False, commit_error=False):
        from pier.trial.trial import Trial

        receipt = AsyncMock(
            side_effect=RuntimeError("toy commit failure") if commit_error else None
        )
        config.agent.kwargs["commit_patch"] = commit_patch
        with (
            patch(
                "pier.trial.execution.EnvironmentFactory.create_environment_from_config",
                side_effect=self.backend_factory,
            ),
            patch(
                "integrations.harbor_agent.OpenAIModel", return_value=ScriptedModel(self.replies())
            ),
            patch("integrations.pier_agent.AdaptiveAgent._commit", receipt),
        ):
            trial = await Trial.create(config)
            try:
                result = await trial.run()
            finally:
                for backend in self.backends:
                    await backend.stop()
                trial._close_logger_handler()
                shutil.rmtree(trial._agent.root, ignore_errors=True)
        return trial, result, receipt

    def test_unwrapped_harbor_fails_real_trial_construction(self):
        from pier.trial.trial import Trial

        config = self.make_config(import_path="integrations.harbor_agent:AdaptiveAgent")
        # Close Pier's pre-construction log handler even when __init__ fails.
        trial = Trial.__new__(Trial)
        from pier.models.task.task import Task

        try:
            with (
                patch(
                    "pier.trial.execution.EnvironmentFactory.create_environment_from_config"
                ) as f,
                self.assertRaisesRegex(AttributeError, "install_spec"),
            ):
                trial.__init__(config, _task=Task(task_dir=config.task.path))
            f.assert_not_called()
        finally:
            trial._close_logger_handler()

    def test_unwrapped_harbor_metadata_fails_real_trial_run(self):
        from pier.trial.trial import Trial
        from pydantic import ValidationError

        from integrations.harbor_agent import AdaptiveAgent

        config = self.make_config(import_path="integrations.harbor_agent:AdaptiveAgent")

        async def reproduce():
            with (
                patch.object(AdaptiveAgent, "install_spec", return_value=None, create=True),
                patch.object(AdaptiveAgent, "network_allowlist", return_value=None, create=True),
                patch("pier.trial.execution.EnvironmentFactory.create_environment_from_config"),
            ):
                trial = await Trial.create(config)
                try:
                    with self.assertRaisesRegex(ValidationError, "agent_info"):
                        await trial.run()
                finally:
                    trial._close_logger_handler()

        asyncio.run(reproduce())

    def test_real_separate_trial_transfers_artifacts_after_agent_stops(self):
        config = self.make_config()
        trial, result, commit = asyncio.run(self.run_trial(config))
        self.assertIsNone(result.exception_info)
        self.assertEqual(result.verifier_result.rewards, {"reward": 1.0})
        self.assertEqual(result.agent_result.n_input_tokens, 16)
        self.assertEqual(result.agent_result.n_output_tokens, 7)
        self.assertEqual(result.agent_info.model_info.name, "toy")
        commit.assert_not_awaited()
        self.assertEqual(len(self.backends), 2)
        agent, verifier = self.backends
        self.assertLess(
            self.events.index(("agent", "stop", False)), self.events.index(("verifier", "start"))
        )
        self.assertTrue(all(backend.stopped for backend in self.backends))
        self.assertTrue(all(child.poll() is not None for child, _ in agent.daemons))
        self.assertIsNone(agent.options["agent_install_spec"])
        self.assertEqual(agent.options["network_allowlist"].domains, [])
        self.assertIsNone(verifier.options["agent_install_spec"])
        self.assertIsNone(verifier.options["network_allowlist"])
        self.assertEqual(Path(verifier.env_paths.artifacts_dir / "answer").read_text(), "42")
        self.assertFalse(
            any(
                event[0] == "agent" and event[1] == "upload_dir" and event[2].endswith("/tests")
                for event in self.events
            )
        )
        self.assertTrue((trial.trial_dir / "agent/harness/result.json").exists())
        self.assertTrue(list((trial.trial_dir / "agent/harness/outputs").glob("*.log")))
        persisted = json.loads((trial.trial_dir / "result.json").read_text())
        self.assertEqual(persisted["verifier_result"]["rewards"], {"reward": 1.0})
        self.assertIsNone(persisted["exception_info"])

    def test_real_shared_trial_and_opt_in_commit_dispatch(self):
        _, result, commit = asyncio.run(
            self.run_trial(self.make_config("shared"), commit_patch=True)
        )
        self.assertIsNone(result.exception_info)
        self.assertEqual(result.verifier_result.rewards, {"reward": 1.0})
        self.assertEqual(len(self.backends), 1)
        commit.assert_awaited_once_with(self.backends[0])

    def test_commit_failure_is_recorded_as_trial_failure(self):
        _, result, commit = asyncio.run(
            self.run_trial(self.make_config(), commit_patch=True, commit_error=True)
        )
        commit.assert_awaited_once()
        self.assertEqual(result.exception_info.exception_type, "RuntimeError")
        self.assertIsNone(result.verifier_result)
        self.assertTrue(self.backends[0].stopped)

    def test_metadata_hooks_defaults_and_model_name_validation(self):
        from pier.agents.base import BaseAgent
        from pier.models.trial.result import AgentInfo

        from integrations.pier_agent import AdaptiveAgent

        agent = AdaptiveAgent(logs_dir=self.root / "logs", config=str(self.settings))
        self.assertIsInstance(agent, BaseAgent)
        self.assertEqual(AdaptiveAgent.import_path(), "integrations.pier_agent:AdaptiveAgent")
        self.assertFalse(agent.commit_patch)
        self.assertFalse(agent.SUPPORTS_WINDOWS)
        self.assertFalse(agent.SUPPORTS_ATIF)
        self.assertEqual(
            AgentInfo.model_validate(agent.to_agent_info()).model_info.provider, "fixture"
        )
        self.assertIsNone(agent.install_spec())
        first = agent.network_allowlist()
        first.domains.append("example.invalid")
        self.assertEqual(agent.network_allowlist().domains, [])
        with self.assertRaisesRegex(ValueError, "model_name must exactly match"):
            AdaptiveAgent(logs_dir=self.root, config=str(self.settings), model_name="wrong")
        self.settings.write_text('[model]\nname="toy"\n')
        agent = AdaptiveAgent(logs_dir=self.root, config=str(self.settings), model_name="toy")
        self.assertEqual(agent.to_agent_info().model_info.name, "toy")
        self.assertIsNone(agent.to_agent_info().model_info.provider)

    def test_commit_receipt_uses_existing_harbor_implementation_without_committing(self):
        from integrations.pier_agent import AdaptiveAgent

        for code in (0, 1):
            with self.subTest(return_code=code):
                logs = self.root / f"logs-{code}"
                logs.mkdir()
                agent = AdaptiveAgent(
                    logs_dir=logs, config=str(self.settings), workspace="/toy", commit_patch=True
                )
                environment = SimpleNamespace(
                    exec=AsyncMock(
                        return_value=SimpleNamespace(
                            return_code=code,
                            stdout="fixture",
                            stderr="",
                        )
                    )
                )
                if code:
                    with self.assertRaisesRegex(RuntimeError, "patch commit failed"):
                        asyncio.run(agent._commit(environment))
                else:
                    asyncio.run(agent._commit(environment))
                call = environment.exec.call_args.kwargs
                self.assertIn("git add -A", call["command"])
                self.assertEqual(call["cwd"], "/toy")
                self.assertEqual(
                    json.loads((logs / "submission.json").read_text())["return_code"], code
                )

    def test_committed_patch_applies_to_pristine_verifier_checkout(self):
        from test_integrations import LocalEnvironment

        from integrations.pier_agent import AdaptiveAgent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent_workspace = root / "agent"
            verifier_workspace = root / "verifier"
            logs = root / "logs"
            agent_workspace.mkdir()
            verifier_workspace.mkdir()
            logs.mkdir()
            for workspace in (agent_workspace, verifier_workspace):
                subprocess.run(
                    ["git", "init", "-b", "main", str(workspace)],
                    check=True,
                    capture_output=True,
                )
                (workspace / "baseline.txt").write_text("base\n")
                subprocess.run(
                    ["git", "-C", str(workspace), "add", "baseline.txt"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(workspace),
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "commit",
                        "-m",
                        "base",
                    ],
                    check=True,
                    capture_output=True,
                )
            (agent_workspace / "answer").write_text("42\n")
            settings = root / "agent.toml"
            settings.write_text("")
            agent = AdaptiveAgent(
                logs_dir=logs,
                config=str(settings),
                workspace=str(agent_workspace),
                commit_patch=True,
            )
            asyncio.run(agent._commit(LocalEnvironment(agent_workspace)))
            base = subprocess.run(
                ["git", "-C", str(agent_workspace), "rev-parse", "HEAD^"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            patch = subprocess.run(
                ["git", "-C", str(agent_workspace), "diff", "--binary", base, "HEAD"],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertIn("new file mode", patch)
            patch_path = root / "model.patch"
            patch_path.write_text(patch)
            subprocess.run(
                ["git", "-C", str(verifier_workspace), "apply", "--binary", str(patch_path)],
                check=True,
                capture_output=True,
            )
            self.assertEqual((verifier_workspace / "answer").read_text(), "42\n")


@unittest.skipUnless(
    importlib.util.find_spec("pier") and importlib.util.find_spec("harbor"),
    "optional Pier and Harbor SDK environment required",
)
class RealGitPierTrial(unittest.TestCase):
    """Unmocked commit and separate-verifier trials over temporary Git repos."""

    payload = b"\x00\xff\x10binary\x80\n"

    def setUp(self):
        self.environment_patch = patch.dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True")
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.settings = self.root / "agent.toml"
        self.settings.write_text(
            '[model]\nname="fixture/toy"\napi_key_env="PIER_TOY_UNUSED_KEY"\n'
            '[run]\nmax_turns=3\nmax_seconds=30\nsupervision="off"\n'
        )
        self.events = []
        self.backends = []

    def backend_factory(self, **options):
        role = "verifier" if options.get("mounts_json") else "agent"
        backend = LocalTransport(self.root / role, options, self.events)
        backend.preserve_workspace = True
        self.backends.append(backend)
        self._init_git_workspace(backend.workspace)
        return backend

    def _init_git_workspace(self, workspace):
        run_git("init", "-b", "main", str(workspace))
        (workspace / "baseline.txt").write_text("base\n")
        run_git("-C", str(workspace), "add", "baseline.txt")
        run_git(
            "-C",
            str(workspace),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "base",
        )

    def _payload_command(self):
        encoded = self.payload.hex()
        script = (
            "from pathlib import Path; "
            f"p=Path('payload.bin'); p.write_bytes(bytes.fromhex('{encoded}')); "
            "p.chmod(0o755); Path('baseline.txt').write_text('changed\\n')"
        )
        return f"python3 -c {shlex.quote(script)}"

    def make_git_config(self, *, behavior="submit", collection="success", grading="success"):
        from pier.models.trial.config import TrialConfig

        task = self.root / f"git-{behavior}-{collection}-{grading}"
        (task / "environment").mkdir(parents=True)
        (task / "tests").mkdir()
        (task / "instruction.md").write_text(
            "Write the binary payload, verify it, and submit the task."
        )
        artifact = self.root / "agent/logs/artifacts/model.patch"
        if collection == "success":
            collect = f"git diff --binary HEAD^ HEAD > {shlex.quote(str(artifact))}"
        else:
            collect = "exit 17"
        test = self._verifier_script(grading, collection)
        (task / "tests/test.sh").write_text(test)
        (task / "task.toml").write_text(
            'version="1.0"\n[agent]\ntimeout_sec=30\n'
            '[verifier]\nenvironment_mode="separate"\ntimeout_sec=30\n'
            f"[[verifier.collect]]\ncommand={json.dumps(collect)}\n"
        )
        return TrialConfig(
            task={"path": task},
            trials_dir=self.root / "trials",
            trial_name=f"git-{behavior}-{collection}-{grading}",
            agent={
                "import_path": "integrations.pier_agent:AdaptiveAgent",
                "kwargs": {
                    "config": str(self.settings),
                    "commit_patch": True,
                },
            },
        )

    def _verifier_script(self, grading, collection):
        expected = self.payload.hex()
        if grading == "failure":
            return "#!/bin/sh\nset -eu\nexit 19\n"
        if collection == "failure":
            return (
                "#!/bin/sh\n"
                "set -eu\n"
                "if test -e ../logs/artifacts/model.patch; then exit 18; fi\n"
                "echo 0 > ../logs/verifier/reward.txt\n"
            )
        check_payload = (
            "from pathlib import Path; "
            f"assert Path('payload.bin').read_bytes().hex() == '{expected}'"
        )
        return (
            "#!/bin/sh\n"
            "set -eu\n"
            'test "$(cat baseline.txt)" = base\n'
            "test ! -e payload.bin\n"
            "git apply --binary ../logs/artifacts/model.patch\n"
            'test "$(cat baseline.txt)" = changed\n'
            "test -x payload.bin\n"
            f"python3 -c {shlex.quote(check_payload)}\n"
            "echo 1 > ../logs/verifier/reward.txt\n"
        )

    def _replies(self, behavior):
        calls = [
            ToolCall("write", "run_command", {"command": self._payload_command()}),
        ]
        if behavior == "commit-failure":
            calls.append(ToolCall("lock", "run_command", {"command": "touch .git/index.lock"}))
        if behavior == "submit":
            calls.append(
                ToolCall(
                    "verify",
                    "run_command",
                    {"command": "test -f payload.bin", "verify": True},
                )
            )
        if behavior in {"submit", "commit-failure"}:
            calls.append(ToolCall("submit", "submit", {"summary": "binary patch verified"}))
        return [
            ModelReply(
                {"role": "assistant", "content": None},
                (call,),
                Usage(6, 3),
            )
            for call in calls
        ]

    async def run_real_trial(self, config, behavior):
        from pier.trial.trial import Trial

        with (
            patch(
                "pier.trial.execution.EnvironmentFactory.create_environment_from_config",
                side_effect=self.backend_factory,
            ),
            patch(
                "integrations.harbor_agent.OpenAIModel",
                return_value=ScriptedModel(self._replies(behavior)),
            ),
        ):
            trial = await Trial.create(config)
            try:
                return trial, await trial.run()
            finally:
                for backend in self.backends:
                    await backend.stop()
                trial._close_logger_handler()
                shutil.rmtree(trial._agent.root, ignore_errors=True)

    def harness_result(self, trial):
        return json.loads((trial.trial_dir / "agent/harness/result.json").read_text())

    def test_real_trial_commits_binary_patch_and_grades_pristine_verifier(self):
        trial, result = asyncio.run(self.run_real_trial(self.make_git_config(), "submit"))
        self.assertIsNone(result.exception_info)
        self.assertEqual(result.verifier_result.rewards, {"reward": 1.0})
        self.assertEqual(self.harness_result(trial)["status"], "submitted")
        agent, verifier = self.backends
        commit_message = run_git(
            "-C", str(agent.workspace), "log", "-1", "--format=%s"
        ).stdout.strip()
        self.assertEqual(commit_message, "Submit task patch")
        patch_path = Path(verifier.env_paths.artifacts_dir) / "model.patch"
        expected_patch = run_git(
            "-C", str(agent.workspace), "diff", "--binary", "HEAD^", "HEAD"
        ).stdout.encode()
        self.assertEqual(
            hashlib.sha256(patch_path.read_bytes()).hexdigest(),
            hashlib.sha256(expected_patch).hexdigest(),
        )
        self.assertTrue((verifier.workspace / "payload.bin").exists())
        self.assertTrue((verifier.workspace / "payload.bin").stat().st_mode & 0o111)
        self.assertEqual(
            hashlib.sha256((verifier.workspace / "payload.bin").read_bytes()).hexdigest(),
            hashlib.sha256(self.payload).hexdigest(),
        )
        self.assertEqual(
            json.loads((trial.trial_dir / "agent/submission.json").read_text())["return_code"],
            0,
        )
        self.assertTrue(all(backend.stopped for backend in self.backends))
        self.assertTrue((trial.trial_dir / "result.json").exists())

    def test_real_trial_budget_exhaustion_still_records_committed_patch(self):
        self.settings.write_text(
            '[model]\nname="fixture/toy"\napi_key_env="PIER_TOY_UNUSED_KEY"\n'
            '[run]\nmax_turns=1\nmax_seconds=30\nsupervision="off"\n'
        )
        config = self.make_git_config(behavior="budget")
        trial, result = asyncio.run(self.run_real_trial(config, "budget"))
        self.assertIsNone(result.exception_info)
        self.assertEqual(self.harness_result(trial)["status"], "budget_exhausted")
        self.assertEqual(result.verifier_result.rewards, {"reward": 1.0})
        self.assertEqual(
            json.loads((trial.trial_dir / "agent/submission.json").read_text())["return_code"],
            0,
        )
        self.assertTrue((trial.trial_dir / "artifacts/model.patch").exists())

    def test_real_trial_commit_failure_is_reported_and_cleaned_up(self):
        trial, result = asyncio.run(
            self.run_real_trial(self.make_git_config(behavior="commit-failure"), "commit-failure")
        )
        self.assertIsNotNone(result.exception_info)
        self.assertIn("commit", result.exception_info.exception_message.lower())
        self.assertIsNone(result.verifier_result)
        receipt = json.loads((trial.trial_dir / "agent/submission.json").read_text())
        self.assertNotEqual(receipt["return_code"], 0)
        self.assertTrue(all(backend.stopped for backend in self.backends))

    def test_real_trial_collection_failure_is_recorded(self):
        trial, result = asyncio.run(
            self.run_real_trial(self.make_git_config(collection="failure"), "submit")
        )
        self.assertEqual(result.verifier_result.rewards, {"reward": 0.0})
        self.assertFalse((trial.trial_dir / "artifacts/model.patch").exists())
        self.assertTrue(all(backend.stopped for backend in self.backends))

    def test_real_trial_grader_failure_is_recorded(self):
        _, result = asyncio.run(
            self.run_real_trial(self.make_git_config(grading="failure"), "submit")
        )
        self.assertIsNone(result.verifier_result)
        self.assertIsNotNone(result.exception_info)
        self.assertTrue(all(backend.stopped for backend in self.backends))


if __name__ == "__main__":
    unittest.main()
