"""Runner admission failures through its public command entry point."""

import json
import subprocess
import sys

from test_experiment_runner import SCRIPT, _config


def test_cli_rejects_nonfinite_string_expiry(tmp_path):
    config = _config(tmp_path)
    status = tmp_path / "status.json"
    raw = json.loads(status.read_text())
    raw["expires_at"] = "nan"
    status.write_text(json.dumps(raw))
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "preflight",
            "--config",
            str(config),
            "--output",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert (tmp_path / "out/receipt.json").exists()


def test_cli_missing_source_preserves_preworker_evidence(tmp_path):
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    raw["source_paths"] = [str(tmp_path / "missing.py")]
    config.write_text(json.dumps(raw))
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "preflight",
            "--config",
            str(config),
            "--output",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    receipt = json.loads((tmp_path / "out/receipt.json").read_text())
    assert receipt["status"] == "admission_failed"
    assert "source" in receipt["primary_error"]
