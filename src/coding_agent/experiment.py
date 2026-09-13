"""Small, auditable runner for local experiment lifecycle rehearsals.

The runner deliberately has no cloud SDK.  A provider status file is read for
admission, while stage commands are ordinary local processes.  This makes the
preflight and lifecycle rules testable without starting paid compute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from coding_agent.execution import sanitized_environment


class ExperimentError(RuntimeError):
    """A configuration or lifecycle admission error."""


@dataclass(frozen=True)
class Inference:
    provider: str
    instance_id: str
    status_path: Path
    startup_seconds: float
    warmup_seconds: float
    billing_lag_seconds: float
    status_max_age_seconds: float
    readiness_command: tuple[str, ...] | None
    readiness_timeout_seconds: float


@dataclass(frozen=True)
class Spec:
    config_path: Path
    workspace: Path
    experiment_id: str
    owner: str
    source_paths: tuple[Path, ...]
    tasks: tuple[Mapping[str, str], ...]
    image_pin: str
    sdk_versions: Mapping[str, str]
    resources: Mapping[str, object]
    deadlines: Mapping[str, float]
    inference: Inference
    budget_usd: float
    reserve_usd: float
    consumed_usd: float
    unreported_usd: float
    billing_authenticated: bool
    billing_attested_at: float
    lineage: Mapping[str, object]
    commands: Mapping[str, tuple[str, ...]]
    owner_root: Path
    worker_success_codes: frozenset[int]


@dataclass(frozen=True)
class Readiness:
    instance_id: str
    authenticated: bool
    generation_ready: bool
    expires_at: float
    attested_at: float


@dataclass(frozen=True)
class StageResult:
    name: str
    status: str
    started_at: float
    ended_at: float
    exit_code: int | None
    error: str | None


def _terminal(error: str, status: str = "admission_failed") -> dict[str, object]:
    return {
        "schema": 1,
        "run_id": uuid.uuid4().hex,
        "status": status,
        "finished_at": time.time(),
        "stages": [],
        "primary_error": error,
        "cleanup_error": None,
    }


def _dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentError(f"{label} must be a non-empty string")
    return value


def _number(value: object, label: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentError(f"{label} must be a number")
    if not math.isfinite(float(value)) or value < 0:
        raise ExperimentError(f"{label} must be non-negative")
    return float(value)


def _path(base: Path, value: object, label: str) -> Path:
    return (base / _text(value, label)).resolve()


def _command(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ExperimentError(f"{label} must be a non-empty argv list")
    return tuple(value)


def _parse_inference(raw: dict[str, object], base: Path) -> Inference:
    readiness = raw.get("readiness_command")
    readiness_command = (
        None if readiness is None else _command(readiness, "inference.readiness_command")
    )
    return Inference(
        provider=_text(raw.get("provider", ""), "inference.provider"),
        instance_id=_text(raw.get("instance_id", ""), "inference.instance_id"),
        status_path=_path(base, raw.get("status_path"), "inference.status_path"),
        startup_seconds=_number(raw.get("startup_seconds"), "inference.startup_seconds", 0),
        warmup_seconds=_number(raw.get("warmup_seconds"), "inference.warmup_seconds", 0),
        billing_lag_seconds=_number(
            raw.get("billing_lag_seconds"), "inference.billing_lag_seconds", 0
        ),
        status_max_age_seconds=_number(
            raw.get("status_max_age_seconds"), "inference.status_max_age_seconds", 60
        ),
        readiness_command=readiness_command,
        readiness_timeout_seconds=_number(
            raw.get("readiness_timeout_seconds"), "inference.readiness_timeout_seconds", 5
        ),
    )


def _parse_sources(raw: dict[str, object], workspace: Path) -> tuple[Path, ...]:
    source_values = raw.get("source_paths", [])
    if not isinstance(source_values, list):
        raise ExperimentError("source_paths must be a list")
    if not source_values:
        raise ExperimentError("source_paths must not be empty")
    return tuple(_path(workspace, item, "source_paths item") for item in source_values)


def _parse_tasks(raw: dict[str, object]) -> tuple[Mapping[str, str], ...]:
    task_values = raw.get("tasks", [])
    if not isinstance(task_values, list) or any(not isinstance(item, dict) for item in task_values):
        raise ExperimentError("tasks must be a list of objects")
    if not task_values:
        raise ExperimentError("tasks must not be empty")
    for index, task in enumerate(task_values):
        if not isinstance(task.get("id"), str) or not task["id"].strip():
            raise ExperimentError(f"tasks[{index}].id must be non-empty")
    return tuple(
        {str(key): _text(item, f"tasks[{index}].{key}") for key, item in task.items()}
        for index, task in enumerate(task_values)
    )


def _parse_deadlines(raw: dict[str, object]) -> Mapping[str, float]:
    raw_values = _dict(raw.get("deadlines", {}), "deadlines")
    values = {
        name: _number(raw_values.get(name), f"deadlines.{name}")
        for name in ("setup_seconds", "worker_seconds", "grader_seconds", "cleanup_seconds")
    }
    if any(value <= 0 for value in values.values()):
        raise ExperimentError("stage deadlines must be positive")
    return values


def _parse_commands(raw: dict[str, object]) -> Mapping[str, tuple[str, ...]]:
    values = _dict(raw.get("commands", {}), "commands")
    return {
        name: _command(values.get(name), f"commands.{name}")
        for name in ("setup", "worker", "grader", "cleanup")
    }


def _parse_exit_codes(raw: dict[str, object]) -> frozenset[int]:
    values = raw.get("worker_success_codes", [0])
    if (
        not isinstance(values, list)
        or not values
        or any(type(value) is not int for value in values)
    ):
        raise ExperimentError("worker_success_codes must be a non-empty integer list")
    if any(value < 0 for value in values):
        raise ExperimentError("worker_success_codes must be non-negative")
    return frozenset(values)


def load_spec(config_path: Path) -> Spec:
    """Load and type-check a JSON runner configuration."""
    config_path = config_path.resolve()
    try:
        raw = _dict(json.loads(config_path.read_text()), "config")
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read config: {exc}") from exc
    base = config_path.parent
    workspace = _path(base, raw.get("workspace", "."), "workspace")
    source_paths = _parse_sources(raw, workspace)
    tasks = _parse_tasks(raw)
    deadlines = _parse_deadlines(raw)
    inference = _parse_inference(_dict(raw.get("inference", {}), "inference"), base)
    commands = _parse_commands(raw)
    sdk_raw = _dict(raw.get("sdk_versions", {}), "sdk_versions")
    sdk_versions = {key: _text(value, f"sdk_versions.{key}") for key, value in sdk_raw.items()}
    resources = _dict(raw.get("resources", {}), "resources")
    billing = _dict(raw.get("billing", {}), "billing")
    lineage = _dict(raw.get("lineage", {}), "lineage")
    return Spec(
        config_path=config_path,
        workspace=workspace,
        experiment_id=_text(raw.get("experiment_id", ""), "experiment_id"),
        owner=_text(raw.get("owner", ""), "owner"),
        source_paths=source_paths,
        tasks=tasks,
        image_pin=_text(raw.get("image_pin", ""), "image_pin"),
        sdk_versions=sdk_versions,
        resources=resources,
        deadlines=deadlines,
        inference=inference,
        budget_usd=_number(raw.get("budget_usd"), "budget_usd", 24),
        reserve_usd=_number(raw.get("reserve_usd"), "reserve_usd", 2),
        consumed_usd=_number(billing.get("consumed_usd"), "billing.consumed_usd", 0),
        unreported_usd=_number(billing.get("unreported_usd"), "billing.unreported_usd", 0),
        billing_authenticated=billing.get("authenticated") is True,
        billing_attested_at=_number(billing.get("attested_at"), "billing.attested_at", 0),
        lineage=lineage,
        commands=commands,
        owner_root=_path(
            base, raw.get("owner_root", "/tmp/coding-agent-experiments"), "owner_root"
        ),
        worker_success_codes=_parse_exit_codes(raw),
    )


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(str(child.relative_to(path)).encode())
            digest.update(child.read_bytes())
    else:
        raise ExperimentError(f"source path does not exist: {path}")
    return digest.hexdigest()


def _source_label(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def _status_object(inference: Inference, live: bool) -> tuple[dict[str, object], float]:
    if live:
        if inference.readiness_command is None:
            raise ExperimentError("command provider requires a launch-time readiness command")
        try:
            process = subprocess.Popen(
                inference.readiness_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
            try:
                output, _ = process.communicate(timeout=inference.readiness_timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                raise ExperimentError("live inference readiness command timed out") from exc
            finally:
                _stop_process(process)
            if process.returncode != 0:
                raise ExperimentError("live inference readiness command failed")
            return _dict(json.loads(output), "live status"), time.time()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ExperimentError(f"cannot read live inference status: {exc}") from exc
    try:
        raw = _dict(json.loads(inference.status_path.read_text()), "status")
        modified_at = inference.status_path.stat().st_mtime
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read inference status: {exc}") from exc
    return raw, modified_at


def _readiness(inference: Inference, *, live: bool = False) -> Readiness:
    raw, modified_at = _status_object(inference, live)
    expires = raw.get("expires_at")
    if isinstance(expires, str):
        try:
            expires_value = _number(float(expires), "status.expires_at")
        except ValueError as exc:
            raise ExperimentError("status.expires_at must be a Unix timestamp") from exc
    else:
        expires_value = _number(expires, "status.expires_at")
    attested = raw.get("attested_at", modified_at)
    attested_value = _number(attested, "status.attested_at")
    authenticated = raw.get("authenticated") is True
    generation_ready = raw.get("generation_ready") is True
    return Readiness(
        instance_id=_text(raw.get("instance_id", ""), "status.instance_id"),
        authenticated=authenticated,
        generation_ready=generation_ready,
        expires_at=expires_value,
        attested_at=attested_value,
    )


def _lifetime(spec: Spec) -> float:
    readiness = (
        spec.inference.readiness_timeout_seconds if spec.inference.provider == "command" else 0
    )
    # Bound process termination/output-drain grace as well as stage deadlines.
    grace = 8 * len(spec.deadlines) + 1
    return (
        spec.inference.startup_seconds
        + spec.inference.warmup_seconds
        + sum(spec.deadlines.values())
        + spec.inference.billing_lag_seconds
        + readiness
        + grace
    )


def _cost(spec: Spec) -> float:
    rate = _number(spec.resources.get("rate_usd_per_hour"), "resources.rate_usd_per_hour", 0)
    hourly = rate * _lifetime(spec) / 3600
    fixed = sum(
        _number(spec.resources.get(key), f"resources.{key}", 0)
        for key in (
            "fixed_usd",
            "build_usd",
            "startup_usd",
            "warmup_usd",
            "fixed_cost_usd",
            "build_cost_usd",
            "startup_cost_usd",
        )
    )
    return hourly + fixed


def _reserve(spec: Spec) -> float:
    return max(2.0, spec.reserve_usd)


def _provider_errors(spec: Spec) -> list[str]:
    errors: list[str] = []
    if spec.inference.provider not in {"local-fake", "command"}:
        errors.append("provider must be local-fake or command")
    if spec.inference.provider == "command" and spec.inference.readiness_command is None:
        errors.append("command provider requires a readiness command")
    return errors


def _identity_errors(spec: Spec, readiness: Readiness) -> list[str]:
    errors: list[str] = []
    if readiness.instance_id != spec.inference.instance_id:
        errors.append("inference instance changed")
    if not readiness.authenticated:
        errors.append("inference authentication is not ready")
    if not readiness.generation_ready:
        errors.append("inference generation is not ready")
    return errors


def _attestation_errors(spec: Spec, readiness: Readiness, now: float) -> list[str]:
    errors: list[str] = []
    if readiness.expires_at <= now:
        errors.append("inference lifetime has expired")
    if readiness.expires_at - now < _lifetime(spec):
        errors.append("inference lifetime is shorter than setup, worker, grader, cleanup, and lag")
    if now - readiness.attested_at > spec.inference.status_max_age_seconds:
        errors.append("inference readiness attestation is stale")
    if readiness.attested_at > now + 5:
        errors.append("inference readiness attestation is from the future")
    return errors


def _billing_errors(spec: Spec, now: float) -> list[str]:
    errors: list[str] = []
    if not spec.billing_authenticated:
        errors.append("billing snapshot is not authenticated")
    if now - spec.billing_attested_at > spec.inference.status_max_age_seconds:
        errors.append("billing snapshot is stale")
    if spec.billing_attested_at > now + 5:
        errors.append("billing snapshot is from the future")
    return errors


def _readiness_errors(spec: Spec, readiness: Readiness, now: float) -> list[str]:
    return (
        _provider_errors(spec)
        + _identity_errors(spec, readiness)
        + _attestation_errors(spec, readiness, now)
        + _billing_errors(spec, now)
    )


def _budget_errors(spec: Spec) -> list[str]:
    errors: list[str] = []
    if spec.budget_usd > 24:
        errors.append("budget exceeds the hard $24 cap")
    if _cost(spec) + spec.consumed_usd + spec.unreported_usd + _reserve(spec) > min(
        spec.budget_usd, 24
    ):
        errors.append("estimated full cost plus reserve exceeds budget")
    return errors


def _admission(spec: Spec, readiness: Readiness, now: float) -> list[str]:
    return _readiness_errors(spec, readiness, now) + _budget_errors(spec)


def _manifest(spec: Spec, readiness: Readiness, created_at: float) -> dict[str, object]:
    source_hashes = {
        _source_label(path, spec.workspace): _digest(path) for path in spec.source_paths
    }
    return {
        "schema": 1,
        "run_id": uuid.uuid4().hex,
        "created_at": created_at,
        "experiment_id": spec.experiment_id,
        "owner": spec.owner,
        "config_sha256": hashlib.sha256(spec.config_path.read_bytes()).hexdigest(),
        "source_sha256": source_hashes,
        "pins": {"tasks": list(spec.tasks), "image": spec.image_pin},
        "sdk_versions": dict(spec.sdk_versions),
        "resources": dict(spec.resources),
        "deadlines": dict(spec.deadlines),
        "inference": {
            "provider": spec.inference.provider,
            "instance_id": readiness.instance_id,
            "expires_at": readiness.expires_at,
            "attested_at": readiness.attested_at,
            "authenticated": readiness.authenticated,
            "generation_ready": readiness.generation_ready,
        },
        "billing": {
            "estimated_lifetime_seconds": _lifetime(spec),
            "estimated_cost_usd": _cost(spec),
            "consumed_usd": spec.consumed_usd,
            "unreported_usd": spec.unreported_usd,
            "projected_total_usd": _cost(spec) + spec.consumed_usd + spec.unreported_usd,
            "reserve_usd": _reserve(spec),
            "budget_usd": spec.budget_usd,
            "authenticated": spec.billing_authenticated,
            "attested_at": spec.billing_attested_at,
        },
        "lineage": dict(spec.lineage),
    }


def _manifest_hash(manifest: Mapping[str, object]) -> str:
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    return hashlib.sha256(json.dumps(unsigned, sort_keys=True).encode()).hexdigest()


def _write_json(path: Path, value: Mapping[str, object], *, exclusive: bool = False) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if exclusive:
        with path.open("x") as handle:
            handle.write(payload)
    else:
        path.write_text(payload)


def _write_rejection(output_dir: Path, receipt: Mapping[str, object]) -> None:
    rejection_dir = output_dir / "admission-rejections"
    rejection_dir.mkdir(parents=True, exist_ok=True)
    _write_json(rejection_dir / f"{receipt['run_id']}.json", receipt, exclusive=True)


def _persist_terminal(
    output_dir: Path, error: str, status: str = "admission_failed"
) -> dict[str, object]:
    receipt = _terminal(error, status)
    _write_json(output_dir / "receipt.json", receipt, exclusive=True)
    return receipt


def preflight(config_path: Path, output_dir: Path) -> dict[str, object]:
    """Validate admission and write a manifest without starting any process."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "receipt.json").exists():
        return _terminal("output already contains a terminal receipt")
    if (output_dir / "manifest.json").exists() or (output_dir / "preflight.json").exists():
        raise ExperimentError("output already contains a preflight result")
    try:
        spec = load_spec(config_path)
        readiness = _readiness(spec.inference)
        now = time.time()
        manifest = _manifest(spec, readiness, now)
    except KeyboardInterrupt:
        return _persist_terminal(output_dir, "preflight interrupted", "interrupted")
    except ExperimentError as exc:
        return _persist_terminal(output_dir, str(exc))
    manifest_hash = _manifest_hash(manifest)
    manifest["manifest_sha256"] = manifest_hash
    _write_json(output_dir / "manifest.json", manifest, exclusive=True)
    errors = _admission(spec, readiness, now)
    result: dict[str, object] = {
        "schema": 1,
        "status": "ready" if not errors else "rejected",
        "checked_at": now,
        "manifest_sha256": manifest_hash,
        "errors": errors,
    }
    _write_json(output_dir / "preflight.json", result, exclusive=True)
    return result


def _lock(spec: Spec) -> tuple[tuple[Path, ...], tuple[int, ...]]:
    lock_root = spec.owner_root
    lock_root.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(spec.inference.instance_id.encode()).hexdigest()[:24]
    paths = (lock_root / "evaluation.lock", lock_root / f"inference-{identity}.lock")
    descriptors: list[int] = []
    try:
        for path in paths:
            descriptors.append(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError as exc:
        for descriptor in descriptors:
            os.close(descriptor)
        for path in paths[: len(descriptors)]:
            path.unlink(missing_ok=True)
        raise ExperimentError("another experiment owner holds the lock") from exc
    for descriptor in descriptors:
        os.write(descriptor, f"owner={spec.owner} pid={os.getpid()}\n".encode())
    return paths, tuple(descriptors)


def _unlock(lock_paths: Sequence[Path], descriptors: Sequence[int]) -> None:
    for descriptor in descriptors:
        os.close(descriptor)
    for path in lock_paths:
        path.unlink(missing_ok=True)


def _redact(text: str, environment: Mapping[str, str]) -> str:
    safe = sanitized_environment(environment)
    secret_values = [value for key, value in environment.items() if key not in safe and value]
    for value in sorted(secret_values, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


def _stop_process(process: subprocess.Popen[str]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=1)


def _collect(process: subprocess.Popen[str]) -> str:
    try:
        output, _ = process.communicate(timeout=2)
        return output
    except subprocess.TimeoutExpired:
        _stop_process(process)
        try:
            output, _ = process.communicate(timeout=2)
            return output
        except subprocess.TimeoutExpired:
            return "[process output collection timed out]"


def _stage(
    spec: Spec,
    name: str,
    output_dir: Path,
    groups: list[int],
) -> StageResult:
    started = time.time()
    log_path = output_dir / f"{name}.log"
    environment = dict(os.environ)
    process: subprocess.Popen[str] | None = None
    status, exit_code, error = "failed", None, None
    try:
        process = subprocess.Popen(
            spec.commands[name],
            cwd=spec.workspace,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        groups.append(process.pid)
        try:
            output, _ = process.communicate(timeout=spec.deadlines[f"{name}_seconds"])
        except subprocess.TimeoutExpired:
            _stop_process(process)
            output = _collect(process)
            status, error = "timeout", f"{name} exceeded deadline"
        else:
            exit_code = process.returncode
            accepted = spec.worker_success_codes if name == "worker" else frozenset({0})
            status = "succeeded" if exit_code in accepted else "failed"
            error = None if status == "succeeded" else f"{name} exited with code {exit_code}"
        log_path.write_text(_redact(output, environment))
        log_path.chmod(0o600)
    except KeyboardInterrupt:
        if process is not None:
            _stop_process(process)
            output = _collect(process)
        else:
            output = ""
        status, error = "interrupted", f"{name} interrupted"
        log_path.write_text(_redact(output + "\n" + error, environment))
        log_path.chmod(0o600)
    except (OSError, ValueError) as exc:
        error = str(exc)
        log_path.write_text(_redact(error, environment))
        log_path.chmod(0o600)
    return StageResult(name, status, started, time.time(), exit_code, error)


def _cleanup_groups(groups: Sequence[int]) -> None:
    for process_group in groups:
        with suppress(ProcessLookupError):
            os.killpg(process_group, signal.SIGTERM)
    time.sleep(0.05)
    for process_group in groups:
        with suppress(ProcessLookupError):
            os.killpg(process_group, signal.SIGKILL)


def _receipt(
    manifest: Mapping[str, object],
    status: str,
    stages: Sequence[StageResult],
    primary_error: str | None,
    cleanup_error: str | None,
) -> dict[str, object]:
    return {
        "schema": 1,
        "run_id": manifest["run_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "status": status,
        "finished_at": time.time(),
        "stages": [_stage_record(stage) for stage in stages],
        "primary_error": primary_error,
        "cleanup_error": cleanup_error,
        "results": _result_fields(stages),
        "timing": _timing(stages),
        "provenance": _provenance(manifest),
        "billing": cast(Mapping[str, object], manifest["billing"]),
        "lineage": cast(Mapping[str, object], manifest["lineage"]),
    }


def _stage_record(stage: StageResult) -> dict[str, object]:
    return {
        "name": stage.name,
        "status": stage.status,
        "started_at": stage.started_at,
        "ended_at": stage.ended_at,
        "exit_code": stage.exit_code,
        "error": stage.error,
    }


def _result_fields(stages: Sequence[StageResult]) -> dict[str, object]:
    return {
        "worker_status": next((stage.status for stage in stages if stage.name == "worker"), None),
        "submission_status": None,
        "collection_status": None,
        "grader_status": next((stage.status for stage in stages if stage.name == "grader"), None),
        "reward": None,
        "accounting_status": None,
    }


def _timing(stages: Sequence[StageResult]) -> dict[str, float | None]:
    return {
        "worker_time_seconds": _duration(stages, "worker"),
        "total_time_seconds": _total_duration(stages),
        "submission_time_seconds": None,
        "collection_time_seconds": None,
        "grader_time_seconds": _duration(stages, "grader"),
        "reward_time_seconds": None,
        "accounting_time_seconds": None,
    }


def _duration(stages: Sequence[StageResult], name: str) -> float | None:
    stage = next((item for item in stages if item.name == name), None)
    return stage.ended_at - stage.started_at if stage else None


def _total_duration(stages: Sequence[StageResult]) -> float:
    if not stages:
        return 0
    return max(stage.ended_at for stage in stages) - min(stage.started_at for stage in stages)


def _provenance(manifest: Mapping[str, object]) -> dict[str, object]:
    return {
        key: manifest[key]
        for key in (
            "config_sha256",
            "source_sha256",
            "pins",
            "sdk_versions",
            "resources",
            "deadlines",
            "inference",
        )
    }


def _run_stages(spec: Spec, output_dir: Path, manifest: Mapping[str, object]) -> dict[str, object]:
    stages: list[StageResult] = []
    groups: list[int] = []
    primary_error: str | None = None
    status = "succeeded"
    try:
        for name in ("setup", "worker", "grader"):
            result = _stage(spec, name, output_dir, groups)
            stages.append(result)
            if result.status != "succeeded":
                primary_error = result.error
                status = "interrupted" if result.status == "interrupted" else "failed"
                break
    except KeyboardInterrupt:
        primary_error, status = "interrupted", "interrupted"
    except BaseException as exc:
        primary_error, status = str(exc), "failed"
    return _finish_stages(spec, output_dir, manifest, groups, stages, primary_error, status)


def _finish_stages(
    spec: Spec,
    output_dir: Path,
    manifest: Mapping[str, object],
    groups: list[int],
    stages: list[StageResult],
    primary_error: str | None,
    status: str,
) -> dict[str, object]:
    cleanup = _stage(spec, "cleanup", output_dir, groups)
    stages.append(cleanup)
    cleanup_error = cleanup.error
    if primary_error is None and cleanup.status != "succeeded":
        primary_error, status = cleanup_error, "failed"
    try:
        _cleanup_groups(groups)
    except (OSError, KeyboardInterrupt) as exc:
        cleanup_error = f"{cleanup_error or ''} process cleanup failed: {type(exc).__name__}"
        if primary_error is None:
            primary_error, status = cleanup_error, "failed"
    return _receipt(manifest, status, stages, primary_error, cleanup_error)


def _launch_admission(
    spec: Spec, config_path: Path, output_dir: Path
) -> tuple[dict[str, object], dict[str, object], list[str]]:
    if not (output_dir / "manifest.json").exists():
        preflight(config_path, output_dir)
    manifest = _dict(json.loads((output_dir / "manifest.json").read_text()), "manifest")
    if not (output_dir / "preflight.json").exists():
        raise ExperimentError("manifest exists without preflight result")
    preflight_result = _dict(json.loads((output_dir / "preflight.json").read_text()), "preflight")
    current_hash = hashlib.sha256(spec.config_path.read_bytes()).hexdigest()
    if manifest.get("config_sha256") != current_hash:
        raise ExperimentError("config changed after preflight")
    expected_sources = {
        _source_label(path, spec.workspace): _digest(path) for path in spec.source_paths
    }
    if manifest.get("source_sha256") != expected_sources:
        raise ExperimentError("source changed after preflight")
    if manifest.get("manifest_sha256") != _manifest_hash(manifest):
        raise ExperimentError("manifest integrity check failed")
    readiness = _readiness(spec.inference, live=spec.inference.provider == "command")
    now = time.time()
    errors = _admission(spec, readiness, now)
    _write_json(
        output_dir / "launch-admission.json",
        {
            "checked_at": now,
            "readiness": asdict(readiness),
            "errors": errors,
            "manifest_sha256": manifest["manifest_sha256"],
        },
        exclusive=True,
    )
    return manifest, preflight_result, errors


def launch(config_path: Path, output_dir: Path) -> dict[str, object]:
    """Run the four local lifecycle stages after a fresh readiness check."""
    started = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "receipt.json").exists():
        return _terminal("output already contains a terminal receipt")
    try:
        spec = load_spec(config_path)
    except ExperimentError as exc:
        receipt = _terminal(str(exc))
        _write_json(output_dir / "receipt.json", receipt, exclusive=True)
        return receipt
    try:
        lock_paths, descriptors = _lock(spec)
    except ExperimentError as exc:
        receipt = _terminal(str(exc))
        _write_rejection(output_dir, receipt)
        return receipt
    try:
        try:
            manifest, preflight_result, errors = _launch_admission(spec, config_path, output_dir)
            if preflight_result.get("status") != "ready" or errors:
                prior_errors = preflight_result.get("errors", [])
                if not isinstance(prior_errors, list):
                    prior_errors = [prior_errors]
                message = "; ".join([str(item) for item in prior_errors] + errors)
                receipt = _receipt(manifest, "admission_failed", [], message, None)
            else:
                receipt = _run_stages(spec, output_dir, manifest)
        except KeyboardInterrupt:
            receipt = _terminal("launch interrupted", "interrupted")
        except (ExperimentError, OSError, json.JSONDecodeError) as exc:
            receipt = _terminal(str(exc))
        return _finish_launch(output_dir, receipt, started)
    finally:
        _unlock(lock_paths, descriptors)


def _finish_launch(
    output_dir: Path, receipt: dict[str, object], started: float
) -> dict[str, object]:
    receipt["launch_elapsed_seconds"] = time.time() - started
    if (output_dir / "receipt.json").exists():
        return _dict(json.loads((output_dir / "receipt.json").read_text()), "terminal receipt")
    _write_json(output_dir / "receipt.json", receipt, exclusive=True)
    return receipt


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Run a local, receipt-producing experiment rehearsal"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("preflight", "launch"):
        subparser = subparsers.add_parser(action)
        subparser.add_argument("--config", type=Path, required=True)
        subparser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (
            preflight(args.config, args.output)
            if args.action == "preflight"
            else launch(args.config, args.output)
        )
    except ExperimentError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") in ("ready", "succeeded") else 1


def main() -> int:
    return _cli()


if __name__ == "__main__":
    sys.exit(main())
