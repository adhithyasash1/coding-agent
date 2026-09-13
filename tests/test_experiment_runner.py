from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from coding_agent.experiment import ExperimentError, launch, preflight

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/coding_agent/experiment.py"
SCRIPT = ROOT / "scripts/run_experiment.py"


def _command(marker: Path, text: str = "ok") -> list[str]:
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text({text!r})"
    return [sys.executable, "-c", code]


def _config(tmp_path: Path, *, ready: bool = True, expires: float | None = None) -> Path:
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "instance_id": "fake-instance-1",
                "authenticated": True,
                "generation_ready": ready,
                "expires_at": expires if expires is not None else time.time() + 100,
            }
        )
    )
    marker = tmp_path / "marker"
    config = {
        "experiment_id": "local-rehearsal",
        "owner": "test-owner",
        "workspace": str(tmp_path),
        "source_paths": [str(MODULE), str(SCRIPT)],
        "tasks": [{"id": "task-pin", "dataset": "dataset@sha256:abc"}],
        "image_pin": "image@sha256:def",
        "sdk_versions": {"python": "3.11", "runner": "1"},
        "resources": {"gpu": "local-fake", "rate_usd_per_hour": 0},
        "deadlines": {
            "setup_seconds": 2,
            "worker_seconds": 2,
            "grader_seconds": 2,
            "cleanup_seconds": 2,
        },
        "inference": {
            "provider": "local-fake",
            "instance_id": "fake-instance-1",
            "status_path": str(status),
            "startup_seconds": 1,
            "warmup_seconds": 1,
            "billing_lag_seconds": 1,
        },
        "budget_usd": 24,
        "reserve_usd": 2,
        "billing": {
            "consumed_usd": 0,
            "unreported_usd": 0,
            "authenticated": True,
            "attested_at": time.time(),
        },
        "lineage": {"parent_run_id": "parent-1"},
        "owner_root": str(tmp_path / "locks"),
        "commands": {
            "setup": _command(marker, "setup"),
            "worker": _command(marker, "worker"),
            "grader": _command(marker, "grader"),
            "cleanup": _command(marker, "cleanup"),
        },
    }
    config_path = tmp_path / "experiment.json"
    config_path.write_text(json.dumps(config))
    return config_path


def test_preflight_writes_manifest_without_starting_stages(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = tmp_path / "out"

    result = preflight(config, output)

    assert result["status"] == "ready"
    assert not (tmp_path / "marker").exists()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["pins"]["image"] == "image@sha256:def"
    assert manifest["inference"]["instance_id"] == "fake-instance-1"
    assert manifest["billing"]["estimated_lifetime_seconds"] == 44.0
    assert manifest["lineage"]["parent_run_id"] == "parent-1"


def test_launch_runs_owned_lifecycle_and_preserves_receipt(tmp_path: Path) -> None:
    config = _config(tmp_path)

    receipt = launch(config, tmp_path / "out")

    assert receipt["status"] == "succeeded"
    assert (tmp_path / "marker").read_text() == "cleanup"
    assert [item["name"] for item in receipt["stages"]] == [
        "setup",
        "worker",
        "grader",
        "cleanup",
    ]
    saved = json.loads((tmp_path / "out/receipt.json").read_text())
    assert saved["manifest_sha256"] == receipt["manifest_sha256"]
    assert not (tmp_path / "out/owner.lock").exists()


def test_launch_does_not_overwrite_existing_receipt(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = tmp_path / "out"
    first = launch(config, output)
    saved = (output / "receipt.json").read_text()

    second = launch(config, output)

    assert second["status"] == "admission_failed"
    assert (output / "receipt.json").read_text() == saved
    assert json.loads(saved)["run_id"] == first["run_id"]


def test_readiness_and_lifetime_are_checked_before_worker(tmp_path: Path) -> None:
    config = _config(tmp_path, ready=False)
    output = tmp_path / "out"

    receipt = launch(config, output)

    assert receipt["status"] == "admission_failed"
    assert receipt["stages"] == []
    assert not (tmp_path / "marker").exists()
    assert "generation is not ready" in str(receipt["primary_error"])


def test_launch_requires_the_configured_inference_instance(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    raw["inference"]["instance_id"] = "different-instance"
    config.write_text(json.dumps(raw))

    receipt = launch(config, tmp_path / "out")

    assert receipt["status"] == "admission_failed"
    assert "instance changed" in str(receipt["primary_error"])
    assert receipt["stages"] == []


def test_command_provider_runs_live_readiness_only_at_launch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    status = tmp_path / "status.json"
    marker = tmp_path / "readiness-called"
    code = (
        "from pathlib import Path; "
        f"Path({str(marker)!r}).write_text('called'); "
        f"print(Path({str(status)!r}).read_text())"
    )
    raw["inference"]["provider"] = "command"
    raw["inference"]["readiness_command"] = [sys.executable, "-c", code]
    config.write_text(json.dumps(raw))

    preflight(config, tmp_path / "out")
    assert not marker.exists()
    receipt = launch(config, tmp_path / "out")

    assert receipt["status"] == "succeeded"
    assert marker.read_text() == "called"


def test_stale_attestation_and_already_used_output_are_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "instance_id": "fake-instance-1",
                "authenticated": True,
                "generation_ready": True,
                "expires_at": time.time() + 100,
                "attested_at": time.time() - 100,
            }
        )
    )
    raw["inference"]["status_max_age_seconds"] = 1
    config.write_text(json.dumps(raw))
    output = tmp_path / "out"

    result = preflight(config, output)

    assert result["status"] == "rejected"
    assert "attestation is stale" in str(result["errors"])
    raw["inference"]["status_max_age_seconds"] = 60
    status.write_text(
        json.dumps(
            {
                "instance_id": "fake-instance-1",
                "authenticated": True,
                "generation_ready": True,
                "expires_at": time.time() + 100,
            }
        )
    )
    config.write_text(json.dumps(raw))
    with pytest.raises(ExperimentError):
        preflight(config, output)


def test_consumed_and_unreported_billing_counts_against_reserve(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    raw["billing"] = {"consumed_usd": 20, "unreported_usd": 3}
    raw["resources"]["rate_usd_per_hour"] = 1
    config.write_text(json.dumps(raw))

    result = preflight(config, tmp_path / "out")

    assert result["status"] == "rejected"
    assert "cost" in str(result["errors"])


@pytest.mark.parametrize(
    ("field", "value"),
    [("source_paths", []), ("tasks", []), ("deadlines", {"setup_seconds": 0})],
)
def test_required_inputs_and_deadlines_are_rejected(tmp_path: Path, field, value) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    if field == "deadlines":
        raw["deadlines"].update(value)
    else:
        raw[field] = value
    config.write_text(json.dumps(raw))

    result = preflight(config, tmp_path / "out")

    assert result["status"] == "admission_failed"
    assert (tmp_path / "out/receipt.json").exists()


def test_expired_lifecycle_and_billing_lag_are_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path, expires=time.time() + 1)
    raw = json.loads(config.read_text())
    raw["inference"]["billing_lag_seconds"] = 100000
    raw["resources"]["rate_usd_per_hour"] = 1
    config.write_text(json.dumps(raw))

    result = preflight(config, tmp_path / "out")

    assert result["status"] == "rejected"
    errors = " ".join(str(item) for item in result["errors"])
    assert "lifetime" in errors
    assert "cost" in errors


def test_setup_failure_still_runs_cleanup_and_keeps_primary_error(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    marker = tmp_path / "marker"
    raw["commands"]["setup"] = [sys.executable, "-c", "raise SystemExit(7)"]
    raw["commands"]["cleanup"] = _command(marker, "cleanup")
    config.write_text(json.dumps(raw))

    receipt = launch(config, tmp_path / "out")

    assert receipt["status"] == "failed"
    assert "code 7" in str(receipt["primary_error"])
    assert receipt["cleanup_error"] is None
    assert marker.read_text() == "cleanup"
    assert [item["name"] for item in receipt["stages"]] == ["setup", "cleanup"]


def test_configured_worker_exit_code_still_runs_grader(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    grader_marker = tmp_path / "grader-marker"
    raw["worker_success_codes"] = [0, 2]
    raw["commands"]["worker"] = [sys.executable, "-c", "raise SystemExit(2)"]
    raw["commands"]["grader"] = _command(grader_marker, "graded")
    config.write_text(json.dumps(raw))

    receipt = launch(config, tmp_path / "out")

    assert receipt["status"] == "succeeded"
    assert grader_marker.read_text() == "graded"
    assert receipt["results"]["worker_status"] == "succeeded"


def test_successful_stage_descendants_are_cleaned_after_lifecycle(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    pid_file = tmp_path / "child.pid"
    code = (
        "import subprocess, sys; "
        f"p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        f"open({str(pid_file)!r}, 'w').write(str(p.pid))"
    )
    raw["commands"]["setup"] = [sys.executable, "-c", code]
    config.write_text(json.dumps(raw))

    receipt = launch(config, tmp_path / "out")

    assert receipt["status"] == "succeeded"
    child_pid = int(pid_file.read_text())
    for _ in range(20):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("successful stage descendant survived cleanup")


def test_owner_lock_yields_terminal_record(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    lock_root = tmp_path / "locks"
    lock_root.mkdir(exist_ok=True)
    lock_name = hashlib.sha256(b"fake-instance-1").hexdigest()[:24]
    lock_path = lock_root / f"inference-{lock_name}.lock"
    lock_path.write_text("owner=other pid=1\n")

    receipt = launch(config, output)

    assert receipt["status"] == "admission_failed"
    assert "owner holds the lock" in str(receipt["primary_error"])
    assert not (output / "receipt.json").exists()
    assert list((output / "admission-rejections").glob("*.json"))
    lock_path.unlink()


def test_cli_supports_preflight_without_compute(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output = tmp_path / "out"

    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "preflight",
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0
    assert json.loads(process.stdout)["status"] == "ready"
    assert not (tmp_path / "marker").exists()


def test_stage_logs_redact_host_secret_values(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    raw = json.loads(config.read_text())
    raw["commands"]["setup"] = [sys.executable, "-c", "import os; print(os.environ['FAKE_TOKEN'])"]
    config.write_text(json.dumps(raw))
    secret = "local-secret-value-7c2a"
    monkeypatch.setenv("FAKE_TOKEN", secret)

    launch(config, tmp_path / "out")

    assert secret not in (tmp_path / "out/setup.log").read_text()
    assert "[REDACTED]" in (tmp_path / "out/setup.log").read_text()
