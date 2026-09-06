"""Explicit, offline-log-to-LangSmith export using only the core httpx dependency.

Run from a checkout with the coding_agent package installed:
    python -m integrations.langsmith_export RUN_DIR --project coding-agent-development

Only main() reads LANGSMITH_API_KEY, LANGSMITH_ENDPOINT and LANGSMITH_WORKSPACE_ID.
Importing this module or building a trace never sends data. Only events.jsonl and
result.json are read; artifact references are never opened and no files are written.
Repeated exports reuse the same IDs, including after copying the source directory.

REST contract checked against official docs on 2026-09-06:
https://docs.langchain.com/langsmith/run-data-format
https://docs.langchain.com/langsmith/log-llm-trace
https://docs.langchain.com/langsmith/smith-api/runs/ingest-runs-batch-json
https://docs.langchain.com/langsmith/smith-api/run/read-run
Batch POST can return 409. Resolve conflicts per span, confirming existing IDs
and submitting missing spans individually; a persisted root is not a full trace.
An HTTP success means queued for ingestion, not independently verified persistence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx

from coding_agent.trace import read_events

DEFAULT_PROJECT = "coding-agent-development"
DEFAULT_ENDPOINT = "https://api.smith.langchain.com"
TRUSTED_ENDPOINTS = frozenset({DEFAULT_ENDPOINT, "https://eu.api.smith.langchain.com"})
_NAMESPACE = uuid5(NAMESPACE_URL, "coding-agent/eventlog/langsmith/v1")
_START_KINDS = {"model_request", "tool_started"}
_END_KINDS = {"model_response", "model_failed", "tool_result"}
Json = dict[str, Any]


class ExportError(Exception):
    """Safe-to-display failure without request, response, or credential contents."""


def validate_endpoint(endpoint: str) -> str:
    """Accept only exact official HTTPS origins, with an optional trailing slash.

    No arbitrary hosts, userinfo, ports, paths, queries, or fragments can route the
    API key elsewhere. Redirects and environment proxy configuration are disabled.
    """
    normalized = endpoint.removesuffix("/")
    if normalized not in TRUSTED_ENDPOINTS:
        raise ExportError("LANGSMITH_ENDPOINT must be a trusted official US or EU HTTPS origin")
    return normalized


def _object(pairs: list[tuple[str, Any]]) -> Json:
    result: Json = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _read_source(directory: Path) -> tuple[list[Json], Json]:
    events = read_events(directory / "events.jsonl")
    result = json.loads((directory / "result.json").read_bytes(), object_pairs_hook=_object)
    # Also rejects non-finite result values before a network client is created.
    json.dumps(result, allow_nan=False)
    if not events or not isinstance(result, dict):
        raise ValueError("Missing run")
    if events[-1]["kind"] != "run_finished":
        raise ValueError("Run is not finalized")
    if events[-1].get("result") != result or not isinstance(result.get("status"), str):
        raise ValueError("Inconsistent run result")
    return events, result


def _run_start(events: list[Json]) -> int:
    starts = [index for index, event in enumerate(events) if event["kind"] == "run_started"]
    finishes = [event for event in events if event["kind"] == "run_finished"]
    if len(starts) != 1 or len(finishes) != 1:
        raise ValueError("Expected exactly one agent run")
    index = starts[0]
    if any(event["kind"] in _START_KINDS | _END_KINDS for event in events[:index]):
        raise ValueError("Model or tool operation outside the agent run")
    return index


def _pick(value: Json, *keys: str) -> Json:
    return {key: value[key] for key in keys if key in value}


def _time(event: Json, first: Json) -> datetime:
    # Wall clocks can jump. Preserve the first UTC anchor and measured durations.
    start = datetime.fromisoformat(first["timestamp"])
    return start + timedelta(seconds=event["elapsed_seconds"] - first["elapsed_seconds"])


def _span(start: Json, end: Json, root: Json | None, first: Json, project: str) -> Json:
    identity = json.dumps([project, first], sort_keys=True, separators=(",", ":"))
    trace_id = str(uuid5(_NAMESPACE, hashlib.sha256(identity.encode()).hexdigest()))
    span_id = str(uuid5(UUID(trace_id), str(start["id"]))) if root else trace_id
    start_time = _time(start, first)
    order = start_time.strftime("%Y%m%dT%H%M%S%fZ") + span_id
    result: Json = {
        "id": span_id,
        "trace_id": trace_id,
        "session_name": project,
        "name": "coding-agent",
        "run_type": "chain",
        "start_time": start_time.isoformat(),
        "end_time": _time(end, first).isoformat(),
        "dotted_order": order,
        "inputs": {},
        "outputs": {},
        "extra": {
            "metadata": {
                "source": "coding-agent.EventLog",
                "start_event_id": start["id"],
                "end_event_id": end["id"],
                "elapsed_seconds": end["elapsed_seconds"] - start["elapsed_seconds"],
                "source_start_timestamp": start["timestamp"],
                "source_end_timestamp": end["timestamp"],
            }
        },
    }
    if root:
        result["parent_run_id"] = root["id"]
        result["dotted_order"] = root["dotted_order"] + "." + order
    return result


def _status(span: Json, status: str, *, success: bool) -> None:
    span["extra"]["metadata"]["status"] = status
    span["status"] = "success" if success else "error"
    if not success:
        # Preserve detailed redacted outcomes in outputs, not in exporter errors.
        span["error"] = "Local operation did not complete successfully"


def _usage(usage: Json) -> Json:
    counts = _pick(usage, "input_tokens", "output_tokens")
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("Invalid token counts")
    if len(counts) == 2:
        counts["total_tokens"] = counts["input_tokens"] + counts["output_tokens"]
    cached = usage.get("cached_tokens")
    if cached is not None:
        if type(cached) is not int or cached < 0:
            raise ValueError("Invalid cache count")
        counts["input_token_details"] = {"cache_read": cached}
    return counts


def _root(events: list[Json], result: Json, project: str, run_start: int) -> Json:
    first = events[0]
    root = _span(first, events[-1], None, first, project)
    root["inputs"] = _pick(events[run_start], "task")
    root["outputs"] = _pick(result, "status", "detail", "revision", "verified_revision")
    root["extra"]["metadata"].update(
        _pick(result, "turns", "usage", "supervisor_calls", "interventions", "grader_result")
    )
    root["extra"]["metadata"]["event_count"] = len(events)
    _envelope_metadata(root["extra"]["metadata"], events[:run_start])
    _status(root, result["status"], success=result["status"] == "submitted")
    return root


def _envelope_metadata(metadata: Json, envelope: list[Json]) -> None:
    if not envelope:
        return
    # Keep every leading record, including its original ID and timestamps. These
    # are already-redacted log records; artifact references are never opened.
    metadata["envelope_events"] = envelope
    for event in envelope:
        if event["kind"] == "environment":
            metadata.update(_pick(event, "backend", "workspace", "provenance", "commit_patch"))


def _model_span(span: Json, start: Json, end: Json, config: Json) -> None:
    role = start["role"]
    profile = config.get("supervisor_model") if role == "supervisor" else None
    profile = profile or config.get("model", {})
    span.update(name=f"{role} model", run_type="llm")
    span["inputs"] = _pick(start, "messages", "tools", "max_output_tokens")
    metadata = span["extra"]["metadata"]
    metadata.update(_pick(start, "role", "estimated_input_tokens"))
    if profile.get("name"):
        metadata["ls_model_name"] = profile["name"]
    # Config only identifies a model and OpenAI-compatible URL, not its provider.
    # Do not guess ls_provider or export configuration URLs / API-key references.
    metadata.update(_pick(end, "finish_reason", "unknown_usage_reserved"))
    if end["kind"] == "model_response":
        span["outputs"] = {"messages": [end["message"]]}
        if "usage" in end:
            span["outputs"]["usage_metadata"] = _usage(end["usage"])
        _status(span, "ok", success=True)
    else:
        span["outputs"] = _pick(end, "error")
        _status(span, "model_failed", success=False)


def _tool_span(span: Json, start: Json, end: Json) -> None:
    call = start["call"]
    result = end["result"]
    span.update(name=call["name"], run_type="tool", inputs=call["arguments"])
    # Output is the bounded, redacted EventLog observation, never artifact bytes.
    span["outputs"] = _pick(result, "status", "output", "exit_code", "revision")
    metadata = span["extra"]["metadata"]
    metadata["tool_call_id"] = call["id"]
    metadata["artifact_omitted"] = bool(result.get("artifact"))
    metadata["tool_metadata"] = _pick(
        result.get("metadata", {}), "verification", "truncated", "duration_seconds"
    )
    _status(span, result["status"], success=result["status"] in {"ok", "running"})


def _pair_key(event: Json) -> tuple[str, str]:
    if event["kind"].startswith("model_"):
        return "model", event["role"]
    return "tool", event["call"]["id"]


def _pairs(events: list[Json]) -> list[tuple[Json, Json | None]]:
    pending: dict[tuple[str, str], Json] = {}
    pairs: list[tuple[Json, Json | None]] = []
    for event in events:
        kind = event["kind"]
        if kind in _START_KINDS:
            key = _pair_key(event)
            if key in pending:
                raise ValueError("Overlapping operations with the same identity")
            pending[key] = event
        elif kind in _END_KINDS:
            start = pending.pop(_pair_key(event))
            pairs.append((start, event))
    pairs.extend((event, None) for event in pending.values())
    return sorted(pairs, key=lambda pair: pair[0]["id"])


def _build_trace(events: list[Json], result: Json, project: str) -> list[Json]:
    run_start = _run_start(events)
    root = _root(events, result, project, run_start)
    spans = [root]
    for start, end in _pairs(events):
        span = _span(start, end or events[-1], root, events[0], project)
        if end is None:
            span["name"] = "incomplete " + start["kind"]
            span["run_type"] = "llm" if start["kind"] == "model_request" else "tool"
            span["extra"]["metadata"]["end_time_inferred"] = True
            _status(span, "incomplete", success=False)
        elif start["kind"] == "model_request":
            _model_span(span, start, end, events[run_start].get("config", {}))
        else:
            _tool_span(span, start, end)
        spans.append(span)
    return spans


def build_trace(run_dir: Path, project: str = DEFAULT_PROJECT) -> list[Json]:
    """Build one finalized trace offline. No credentials, network, or disk writes.

    Model and tool spans are siblings: model calls finish before their tools start.
    Leading environment/setup records remain in root metadata; the first record
    anchors the trace identity and time, while run_started supplies task/config.
    Missing operation ends on a finalized failed run are marked incomplete, with
    the run end as an explicit inferred upper bound. Unfinalized logs are rejected.
    """
    if not isinstance(project, str) or not project.strip():
        raise ExportError("Project must be a nonempty name")
    try:
        events, result = _read_source(Path(run_dir))
        return _build_trace(events, result, project)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ExportError("Cannot export: invalid, inconsistent, or unfinished local run") from None


def _headers(api_key: str, workspace_id: str | None) -> dict[str, str]:
    if not api_key or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise ExportError("LANGSMITH_API_KEY must be a nonempty printable credential")
    headers = {"x-api-key": api_key}
    if workspace_id is not None:
        try:
            headers["x-tenant-id"] = str(UUID(workspace_id))
        except ValueError:
            raise ExportError("LANGSMITH_WORKSPACE_ID must be a UUID") from None
    return headers


def _confirm_duplicate(
    client: httpx.Client, endpoint: str, headers: dict[str, str], span: Json
) -> None:
    response = client.get(endpoint + "/runs/" + span["id"], headers=headers)
    if response.status_code != 200:
        raise ExportError(
            "LangSmith export conflict (HTTP 409) could not be confirmed "
            f"(HTTP {response.status_code}); retrying uses the same trace IDs"
        )
    try:
        existing = response.json()
    except ValueError:
        existing = None
    identity = ("id", "trace_id", "dotted_order", "parent_run_id")
    if not isinstance(existing, dict) or any(
        existing.get(key) != span.get(key) for key in identity
    ):
        raise ExportError(
            "LangSmith export conflict (HTTP 409): stored span identity not confirmed"
        )


def _ingest(
    client: httpx.Client, endpoint: str, headers: dict[str, str], spans: list[Json]
) -> None:
    response = client.post(endpoint + "/runs/batch", headers=headers, json={"post": spans})
    if response.is_success:
        return
    if response.status_code != 409:
        # Never echo body, URL, headers, or exception text, even for redirects.
        raise ExportError(f"LangSmith export was not accepted (HTTP {response.status_code})")
    if len(spans) == 1:
        _confirm_duplicate(client, endpoint, headers, spans[0])
        return
    # Batch conflicts say nothing about which spans were accepted. Bounded
    # singleton attempts also handle partial ingestion and concurrent exporters.
    for span in spans:
        _ingest(client, endpoint, headers, [span])


def export_run(
    run_dir: Path,
    *,
    api_key: str,
    project: str = DEFAULT_PROJECT,
    endpoint: str = DEFAULT_ENDPOINT,
    workspace_id: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Explicitly queue one trace; a MockTransport allows entirely local testing.

    Reuses project + first-event-derived root IDs and event-derived child IDs.
    On 409, each span must be accepted or confirmed remotely with matching trace
    identity. Existing spans are left unchanged, not updated from edited logs.
    An unreadable conflict fails safely for a later explicit retry. No local
    marker can hide partial ingestion, and no automatic retry loop is used.
    """
    endpoint = validate_endpoint(endpoint)
    headers = _headers(api_key, workspace_id)
    spans = build_trace(run_dir, project)
    try:
        with httpx.Client(
            timeout=30, follow_redirects=False, trust_env=False, transport=transport
        ) as client:
            _ingest(client, endpoint, headers, spans)
    except (httpx.HTTPError, OSError, ValueError):
        raise ExportError(
            "LangSmith export transport failed; retrying uses the same trace IDs"
        ) from None
    return str(spans[0]["id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Finalized EventLog directory")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    args = parser.parse_args(argv)
    try:
        trace_id = export_run(
            args.run_dir,
            project=args.project,
            api_key=os.environ.get("LANGSMITH_API_KEY", ""),
            endpoint=os.environ.get("LANGSMITH_ENDPOINT", DEFAULT_ENDPOINT),
            workspace_id=os.environ.get("LANGSMITH_WORKSPACE_ID"),
        )
    except ExportError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Trace queued for ingestion: {trace_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
