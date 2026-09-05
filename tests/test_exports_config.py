import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from coding_agent.config import load_config


@pytest.mark.parametrize(
    "setting",
    [
        "max_turns=true",
        "max_tokens=1.5",
        "max_seconds=nan",
        "command_timeout=inf",
        "require_verification='false'",
    ],
)
def test_invalid_toml_limits_fail_early(tmp_path, setting):
    path = tmp_path / "config.toml"
    path.write_text("[run]\n" + setting)
    with pytest.raises(ValueError):
        load_config(path)


def test_swe_export_includes_untracked_files_and_preserves_real_index(tmp_path):
    work = tmp_path / "work"
    work.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(work), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    (work / "original").write_text("before")
    git("add", ".")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@localhost",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "fixture",
    )
    base = git("rev-parse", "HEAD")
    index_before = (work / ".git/index").read_bytes()
    (work / "original").write_text("after")
    (work / "new-file").write_text("created")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "workspace": str(work),
                    "base_commit": base,
                    "instance_id": "fixture-1",
                    "model_name_or_path": "fixture",
                }
            ]
        )
    )
    output = tmp_path / "predictions.jsonl"
    script = Path(__file__).resolve().parents[1] / "scripts/export_swe_predictions.py"
    subprocess.run([sys.executable, str(script), str(manifest), str(output)], check=True)
    assert (work / ".git/index").read_bytes() == index_before
    prediction = json.loads(output.read_text())
    assert "new-file" in prediction["model_patch"]
    assert "+after" in prediction["model_patch"]


def test_modal_secret_creation_keeps_key_out_of_argv_and_removes_tempfile():
    from integrations.create_modal_secret import create_secret

    paths = []

    def fake_run(command, **kwargs):
        assert "dummy-secret" not in str(command)
        file = Path(command[-1])
        assert file.stat().st_mode & 0o777 == 0o600
        assert json.loads(file.read_text()) == {"VLLM_API_KEY": "dummy-secret"}
        paths.append(file)

    with patch("integrations.create_modal_secret.subprocess.run", side_effect=fake_run):
        create_secret("dummy-secret")
    assert not paths[0].exists()
