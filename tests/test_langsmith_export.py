"""Offline export contract tests. All HTTP uses MockTransport and dummy credentials."""

import asyncio
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from coding_agent.trace import EventLog
from integrations import langsmith_export as exporter

FAKE_KEY = "dummy-export-key-for-local-tests"
BASE_TIME = datetime(2026, 9, 5, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Never inspect inherited credentials, and forbid accidental live HTTP."""
    monkeypatch.setenv("LANGSMITH_API_KEY", FAKE_KEY)
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGSMITH_WORKSPACE_ID", raising=False)
    original = httpx.Client

    def unexpected(request):
        pytest.fail("Unexpected network request without an explicit local mock")

    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"] == 30
        kwargs["transport"] = kwargs.get("transport") or httpx.MockTransport(unexpected)
        return original(**kwargs)

    monkeypatch.setattr(exporter.httpx, "Client", client)


def write_run(directory, *, status="submitted", tool_status="ok"):
    directory.mkdir(exist_ok=True)
    events = []

    def emit(kind, **data):
        offset = len(events)
        events.append(
            {
                **data,
                "id": offset + 1,
                "kind": kind,
                "elapsed_seconds": offset + 0.25,
                "timestamp": (BASE_TIME + timedelta(seconds=offset)).isoformat(),
            }
        )

    call = {"id": "call-1", "name": "run_command", "arguments": {"command": "pytest"}}
    usage = {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 3}
    emit(
        "run_started",
        task="Check the workspace",
        config={
            "model": {"name": "worker-model", "base_url": "omitted-private-config-url"},
            "supervisor_model": {"name": "supervisor-model"},
        },
    )
    emit(
        "model_request",
        role="worker",
        messages=[{"role": "user", "content": "Check"}],
        tools=[],
        max_output_tokens=100,
        estimated_input_tokens=12,
    )
    emit("model_transport", role="worker", attempt=1)
    emit(
        "model_response",
        role="worker",
        message={"role": "assistant", "content": "Testing"},
        usage=usage,
        finish_reason="tool_calls",
    )
    emit("tool_started", call=call)
    emit(
        "tool_result",
        call=call,
        result={
            "status": tool_status,
            "output": "bounded redacted observation",
            "exit_code": 0,
            "artifact": str(directory / "artifacts/raw.txt"),
            "revision": "abc",
            "metadata": {"verification": True, "raw_artifact": "not-for-export"},
        },
    )
    emit("model_request", role="supervisor", messages=[], tools=[], max_output_tokens=100)
    emit(
        "model_response",
        role="supervisor",
        message={"role": "assistant", "content": "Done"},
        usage=usage,
        finish_reason="stop",
    )
    result = {
        "status": status,
        "detail": "Completed",
        "turns": 1,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "cached_tokens": 6,
            "unknown_usage_reserved": 0,
            "complete": True,
        },
        "supervisor_calls": 1,
        "interventions": 0,
        "revision": "abc",
        "verified_revision": "abc",
        "grader_result": None,
    }
    emit("run_finished", result=result)
    persist(directory, events, result)
    artifacts = directory / "artifacts"
    artifacts.mkdir()
    (artifacts / "raw.txt").write_text("RAW-ARTIFACT-MUST-NEVER-BE-UPLOADED")
    return events, result


def persist(directory, events, result):
    (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (directory / "result.json").write_text(json.dumps(result))


def snapshot(directory):
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


@pytest.fixture
def exported_run(tmp_path):
    write_run(tmp_path)
    before = snapshot(tmp_path)
    received = []

    def ingest(request):
        assert str(request.url) == exporter.DEFAULT_ENDPOINT + "/runs/batch"
        assert request.method == "POST"
        assert request.headers["x-api-key"] == FAKE_KEY
        assert "x-tenant-id" not in request.headers
        payload = json.loads(request.content)
        assert list(payload) == ["post"]
        received.extend(payload["post"])
        return httpx.Response(202, json={})

    trace_id = exporter.export_run(
        tmp_path, api_key=FAKE_KEY, transport=httpx.MockTransport(ingest)
    )
    assert snapshot(tmp_path) == before
    return trace_id, received


def test_root_payload_and_immutable_source(exported_run):
    trace_id, received = exported_run
    root = received[0]
    assert trace_id == root["id"] == root["trace_id"]
    assert root["run_type"] == "chain"
    assert "parent_run_id" not in root
    assert root["inputs"] == {"task": "Check the workspace"}
    assert root["extra"]["metadata"]["usage"]["input_tokens"] == 20
    assert root["extra"]["metadata"]["status"] == "submitted"
    assert root["extra"]["metadata"]["elapsed_seconds"] == 8


def test_child_hierarchy(exported_run):
    _, received = exported_run
    root = received[0]
    for child in received[1:]:
        assert UUID(child["id"])
        assert child["trace_id"] == child["parent_run_id"] == root["id"]
        assert child["dotted_order"].startswith(root["dotted_order"] + ".")
        assert child["dotted_order"].endswith(child["id"])
        assert child["session_name"] == exporter.DEFAULT_PROJECT


def test_model_payload_usage_and_timing(exported_run):
    _, (_, worker, _, supervisor) = exported_run
    assert worker["run_type"] == supervisor["run_type"] == "llm"
    assert worker["start_time"] == (BASE_TIME + timedelta(seconds=1)).isoformat()
    assert worker["end_time"] == (BASE_TIME + timedelta(seconds=3)).isoformat()
    assert worker["extra"]["metadata"]["ls_model_name"] == "worker-model"
    assert supervisor["extra"]["metadata"]["ls_model_name"] == "supervisor-model"
    assert worker["outputs"]["usage_metadata"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_token_details": {"cache_read": 3},
    }


def test_tool_payload_excludes_artifacts_and_config(exported_run):
    _, received = exported_run
    tool = received[2]
    assert tool["run_type"] == "tool"
    assert tool["inputs"] == {"command": "pytest"}
    assert tool["outputs"]["output"] == "bounded redacted observation"
    assert tool["extra"]["metadata"]["artifact_omitted"] is True
    serialized = json.dumps(received)
    for excluded in ("RAW-ARTIFACT", "not-for-export", "omitted-private-config-url", FAKE_KEY):
        assert excluded not in serialized


@pytest.mark.parametrize("persisted_count", [1, 2, 4])
def test_retry_and_copied_directory_recover_duplicate_409(tmp_path, persisted_count):
    source, copied = tmp_path / "source", tmp_path / "copied"
    write_run(source)
    shutil.copytree(source, copied)
    stored = {}
    attempts = []

    def ingest(request):
        if request.method == "GET":
            span = stored.get(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json=span) if span else httpx.Response(404)
        posts = json.loads(request.content)["post"]
        attempts.append(posts)
        if len(attempts) == 1:
            stored.update({span["id"]: span for span in posts[:persisted_count]})
            raise httpx.ReadTimeout("server might have ingested; " + FAKE_KEY)
        if any(span["id"] in stored for span in posts):
            # Live duplicate export returned 409, not a successful upsert.
            # Do not assume any missing children in this batch were accepted.
            return httpx.Response(409, text=FAKE_KEY)
        stored.update({span["id"]: span for span in posts})
        return httpx.Response(202)

    transport = httpx.MockTransport(ingest)
    with pytest.raises(exporter.ExportError, match="transport failed"):
        exporter.export_run(source, api_key=FAKE_KEY, transport=transport)
    exporter.export_run(source, api_key=FAKE_KEY, transport=transport)
    exporter.export_run(copied, api_key=FAKE_KEY, transport=transport)
    assert attempts[0] == attempts[1]
    assert stored == {span["id"]: span for span in exporter.build_trace(source)}
    assert len(stored) == 4
    assert exporter.build_trace(source, "another-project")[0]["id"] not in stored


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.smith.langchain.com",
        "https://evil.example",
        "https://api.smith.langchain.com.evil",
        "https://api.smith.langchain.com@evil.example",
        "https://user:secret@api.smith.langchain.com",
        "https://api.smith.langchain.com:444",
        "https://api.smith.langchain.com/runs",
        "https://api.smith.langchain.com?key=secret",
        "https://api.smith.langchain.com#secret",
        "https://api.smith.langchain.com//",
        " https://api.smith.langchain.com",
        "https://api.smith.langchain.com\n",
    ],
)
def test_untrusted_credential_routes_rejected_before_network(tmp_path, endpoint):
    with pytest.raises(exporter.ExportError, match="trusted official") as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY, endpoint=endpoint)
    assert endpoint not in str(caught.value)
    assert FAKE_KEY not in str(caught.value)


def test_eu_endpoint_and_workspace_header(tmp_path):
    write_run(tmp_path)
    workspace = "cbf03831-d63d-447b-bf37-12c587bcbe99"

    def ingest(request):
        assert request.url.host == "eu.api.smith.langchain.com"
        assert request.url.path == "/runs/batch"
        assert request.headers["x-tenant-id"] == workspace
        assert json.loads(request.content)["post"][0]["session_name"] == "custom"
        return httpx.Response(202)

    exporter.export_run(
        tmp_path,
        api_key=FAKE_KEY,
        project="custom",
        workspace_id=workspace,
        endpoint="https://eu.api.smith.langchain.com/",
        transport=httpx.MockTransport(ingest),
    )


@pytest.mark.parametrize("code", [301, 307, 401, 403, 409, 413, 429, 500])
def test_http_failures_do_not_leak_or_follow_redirects(tmp_path, code):
    write_run(tmp_path)
    requests = []

    def ingest(request):
        requests.append(request)
        return httpx.Response(
            code, text=FAKE_KEY, headers={"location": "https://evil.example/" + FAKE_KEY}
        )

    with pytest.raises(exporter.ExportError, match=f"HTTP {code}") as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY, transport=httpx.MockTransport(ingest))
    assert len(requests) == (3 if code == 409 else 1)
    assert all(request.url.host == "api.smith.langchain.com" for request in requests)
    assert FAKE_KEY not in str(caught.value)
    assert "evil" not in str(caught.value)


@pytest.mark.parametrize("code", [301, 307, 401, 403, 404, 429, 500])
def test_conflict_requires_readable_persisted_span(tmp_path, code):
    write_run(tmp_path)

    def ingest(request):
        return httpx.Response(
            code if request.method == "GET" else 409,
            text=FAKE_KEY,
            headers={"location": "https://evil.example/" + FAKE_KEY},
        )

    with pytest.raises(exporter.ExportError, match=f"HTTP {code}") as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY, transport=httpx.MockTransport(ingest))
    assert FAKE_KEY not in str(caught.value)
    assert "evil" not in str(caught.value)


@pytest.mark.parametrize("root_only", [False, True])
def test_conflict_with_partial_batch_acceptance_and_workspace(tmp_path, root_only):
    events, result = write_run(tmp_path)
    if root_only:
        events[-1]["id"] = 2
        persist(tmp_path, [events[0], events[-1]], result)
    spans = exporter.build_trace(tmp_path)
    stored = {spans[-1]["id"]: spans[-1]}
    workspace = "cbf03831-d63d-447b-bf37-12c587bcbe99"
    requests = []

    def ingest(request):
        requests.append(request)
        assert request.url.host == "eu.api.smith.langchain.com"
        assert request.headers["x-api-key"] == FAKE_KEY
        assert request.headers["x-tenant-id"] == workspace
        if request.method == "GET":
            return httpx.Response(200, json=stored[request.url.path.rsplit("/", 1)[1]])
        posts = json.loads(request.content)["post"]
        conflict = any(span["id"] in stored for span in posts)
        # A conflicting batch may still accept some or all missing spans.
        stored.update({span["id"]: span for span in posts})
        return httpx.Response(409 if conflict else 202)

    assert (
        exporter.export_run(
            tmp_path,
            api_key=FAKE_KEY,
            endpoint="https://eu.api.smith.langchain.com",
            workspace_id=workspace,
            transport=httpx.MockTransport(ingest),
        )
        == spans[0]["id"]
    )
    assert stored == {span["id"]: span for span in spans}
    assert len(requests) == (2 if root_only else 1 + 2 * len(spans))


@pytest.mark.parametrize(
    "bad_field", ["json", "object", "id", "trace_id", "dotted_order", "parent_run_id"]
)
def test_conflict_requires_matching_span_identity(tmp_path, bad_field):
    write_run(tmp_path)
    root = exporter.build_trace(tmp_path)[0]

    def ingest(request):
        if request.method == "POST":
            return httpx.Response(409)
        if bad_field == "json":
            return httpx.Response(200, text=FAKE_KEY)
        if bad_field == "object":
            return httpx.Response(200, json=[FAKE_KEY])
        return httpx.Response(200, json={**root, bad_field: FAKE_KEY})

    with pytest.raises(exporter.ExportError, match="identity not confirmed") as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY, transport=httpx.MockTransport(ingest))
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize("failure", ["read_timeout", "write_timeout", "http_error"])
def test_failure_during_conflict_recovery_can_be_retried(tmp_path, failure):
    write_run(tmp_path)
    spans = exporter.build_trace(tmp_path)
    stored = {spans[0]["id"]: spans[0]}
    failed = False

    def ingest(request):
        nonlocal failed
        if request.method == "GET":
            if failure == "read_timeout" and not failed:
                failed = True
                raise httpx.ReadTimeout(FAKE_KEY)
            return httpx.Response(200, json=stored[request.url.path.rsplit("/", 1)[1]])
        posts = json.loads(request.content)["post"]
        if any(span["id"] in stored for span in posts):
            return httpx.Response(409)
        if not failed and posts[0]["id"] == spans[2]["id"]:
            failed = True
            if failure == "http_error":
                return httpx.Response(500, text=FAKE_KEY)
            stored.update({span["id"]: span for span in posts})
            raise httpx.ReadTimeout(FAKE_KEY)
        stored.update({span["id"]: span for span in posts})
        return httpx.Response(202)

    transport = httpx.MockTransport(ingest)
    with pytest.raises(exporter.ExportError) as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY, transport=transport)
    assert FAKE_KEY not in str(caught.value)
    assert exporter.export_run(tmp_path, api_key=FAKE_KEY, transport=transport) == spans[0]["id"]
    assert stored == {span["id"]: span for span in spans}


@pytest.mark.parametrize("key", ["", " ", "secret\nkey", "secret\rkey", "secret-\u2603"])
def test_invalid_api_key_rejected(tmp_path, key):
    with pytest.raises(exporter.ExportError, match="LANGSMITH_API_KEY"):
        exporter.export_run(tmp_path, api_key=key)


def test_invalid_workspace_does_not_echo_value(tmp_path):
    with pytest.raises(exporter.ExportError, match="must be a UUID") as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY, workspace_id=FAKE_KEY)
    assert FAKE_KEY not in str(caught.value)


@pytest.mark.parametrize(
    "status", ["budget_exhausted", "model_error", "runtime_error", "interrupted"]
)
def test_run_failure_status_and_tool_timeout(tmp_path, status):
    write_run(tmp_path, status=status, tool_status="timeout")
    root, _, tool, _ = exporter.build_trace(tmp_path)
    assert root["extra"]["metadata"]["status"] == status
    assert root["status"] == tool["status"] == "error"
    assert root["error"] and tool["error"]
    assert tool["extra"]["metadata"]["status"] == "timeout"


def test_failed_model_preserves_unknown_usage_without_inventing_tokens(tmp_path):
    events, result = write_run(tmp_path, status="model_error")
    events[3] = {
        **events[3],
        "kind": "model_failed",
        "error": "Redacted model failure",
        "unknown_usage_reserved": 112,
    }
    del events[3]["usage"]
    result["usage"].update(unknown_usage_reserved=112, complete=False)
    persist(tmp_path, events, result)
    root, worker, *_ = exporter.build_trace(tmp_path)
    assert root["extra"]["metadata"]["usage"]["complete"] is False
    assert worker["status"] == "error"
    assert worker["extra"]["metadata"]["unknown_usage_reserved"] == 112
    assert "usage_metadata" not in worker["outputs"]


def test_unpaired_operation_on_failed_finalized_run_is_incomplete(tmp_path):
    events, result = write_run(tmp_path, status="runtime_error")
    events[5]["kind"] = "runtime_error"
    persist(tmp_path, events, result)
    tool = exporter.build_trace(tmp_path)[2]
    assert tool["run_type"] == "tool"
    assert tool["status"] == "error"
    assert tool["extra"]["metadata"]["status"] == "incomplete"
    assert tool["extra"]["metadata"]["end_time_inferred"] is True


def test_wall_clock_jump_uses_monotonic_duration(tmp_path):
    events, result = write_run(tmp_path)
    events[3]["timestamp"] = (BASE_TIME - timedelta(days=1)).isoformat()
    persist(tmp_path, events, result)
    worker = exporter.build_trace(tmp_path)[1]
    assert worker["extra"]["metadata"]["elapsed_seconds"] == 2
    assert worker["end_time"] == (BASE_TIME + timedelta(seconds=3)).isoformat()


@pytest.mark.parametrize("corruption", ["truncated", "mismatch", "unfinished", "duplicate", "nan"])
def test_invalid_source_rejected_before_network(tmp_path, corruption):
    events, result = write_run(tmp_path)
    if corruption == "truncated":
        (tmp_path / "events.jsonl").write_text('{"secret":"' + FAKE_KEY)
    elif corruption == "mismatch":
        (tmp_path / "result.json").write_text('{"status":"' + FAKE_KEY + '"}')
    elif corruption == "unfinished":
        (tmp_path / "result.json").unlink()
    elif corruption == "duplicate":
        (tmp_path / "result.json").write_text('{"status":1,"status":2}')
    else:
        result["usage"]["input_tokens"] = float("nan")
        persist(tmp_path, events, result)
    with pytest.raises(exporter.ExportError, match="local run") as caught:
        exporter.export_run(tmp_path, api_key=FAKE_KEY)
    assert FAKE_KEY not in str(caught.value)


def test_cli_with_real_eventlog_and_mock_http(tmp_path, monkeypatch, capsys):
    with_log = EventLog(tmp_path)
    with_log.emit("run_started", task="Small task", config={})
    with_log.emit("model_request", role="worker", messages=[], tools=[])
    with_log.emit(
        "model_response",
        role="worker",
        message={"role": "assistant", "content": "done"},
        usage={"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0},
    )
    result = {"status": "submitted", "detail": "done"}
    with_log.emit("run_finished", result=result)
    with_log.finish(result)
    with_log.close()
    before = snapshot(tmp_path)
    original = exporter.httpx.Client
    payloads = []
    stored = {}

    def ingest(request):
        if request.method == "GET":
            return httpx.Response(200, json=stored[request.url.path.rsplit("/", 1)[1]])
        payload = json.loads(request.content)
        payloads.append(payload)
        if any(span["id"] in stored for span in payload["post"]):
            return httpx.Response(409)
        stored.update({span["id"]: span for span in payload["post"]})
        return httpx.Response(202)

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(ingest)
        return original(**kwargs)

    monkeypatch.setattr(exporter.httpx, "Client", client)
    assert exporter.main([str(tmp_path), "--project", "cli-test"]) == 0
    assert len(payloads[0]["post"]) == 2
    assert payloads[0]["post"][0]["session_name"] == "cli-test"
    assert "Trace queued for ingestion:" in capsys.readouterr().out
    assert exporter.main([str(tmp_path), "--project", "cli-test"]) == 0
    assert len(stored) == 2
    assert "Trace queued for ingestion:" in capsys.readouterr().out
    assert snapshot(tmp_path) == before


def test_cli_transport_error_is_sanitized(tmp_path, monkeypatch, capsys):
    write_run(tmp_path)

    def fail(**kwargs):
        raise httpx.ConnectError("authorization=" + FAKE_KEY)

    monkeypatch.setattr(exporter.httpx, "Client", fail)
    assert exporter.main([str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert "transport failed" in output.err
    assert FAKE_KEY not in output.out + output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize(
    "entry", [["-m", "integrations.langsmith_export"], ["integrations/langsmith_export.py"]]
)
def test_script_entry_point_requires_explicit_key(tmp_path, entry):
    process = subprocess.run(
        [sys.executable, *entry, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        env={"LANGSMITH_API_KEY": ""},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert process.returncode == 1
    assert "LANGSMITH_API_KEY" in process.stderr
    assert "Traceback" not in process.stderr


def _harbor_localsim_trace(tmp_path, monkeypatch):
    """Use the installed Harbor SDK, real adapter, RPC server and local shell tools."""
    pytest.importorskip("harbor")
    from harbor.models.agent.context import AgentContext
    from test_integrations import LocalEnvironment

    from coding_agent.model import ScriptedModel
    from coding_agent.types import ModelReply, ToolCall, Usage
    from integrations.harbor_agent import AdaptiveAgent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = tmp_path / "agent.toml"
    settings.write_text(
        '[model]\nname="harbor-fixture"\napi_key_env="EXPORT_TEST_MODEL_KEY"\n[run]\nmax_turns=3\n'
    )
    monkeypatch.setenv("EXPORT_TEST_MODEL_KEY", "")
    replies = [
        ModelReply(
            {"role": "assistant", "content": "Verify"},
            (ToolCall("1", "run_command", {"command": "true", "verify": True}),),
            Usage(10, 5),
        ),
        ModelReply(
            {"role": "assistant", "content": "Submit"},
            (ToolCall("2", "submit", {"summary": "verified"}),),
            Usage(10, 5),
        ),
    ]
    monkeypatch.setattr("integrations.harbor_agent.OpenAIModel", lambda _: ScriptedModel(replies))
    agent = AdaptiveAgent(logs_dir=tmp_path / "logs", config=str(settings))
    environment = LocalEnvironment(workspace)

    async def run():
        await agent.setup(environment)
        await agent.run("Verify the environment", environment, AgentContext())

    try:
        asyncio.run(run())
        assert agent.result["status"] == "submitted"
    finally:
        shutil.rmtree(agent.root, ignore_errors=True)
    return tmp_path / "logs/harness"


def _export_through_cli(run_dir, monkeypatch):
    original = exporter.httpx.Client
    payloads = []

    def ingest(request):
        payloads.append(json.loads(request.content)["post"])
        return httpx.Response(202)

    def client(**kwargs):
        kwargs["transport"] = httpx.MockTransport(ingest)
        return original(**kwargs)

    monkeypatch.setattr(exporter.httpx, "Client", client)
    assert exporter.main([str(run_dir)]) == 0
    return payloads[0]


def test_harbor_localsim_trace_exports_with_provenance(tmp_path, monkeypatch):
    run_dir = _harbor_localsim_trace(tmp_path, monkeypatch)
    events = exporter.read_events(run_dir / "events.jsonl")
    before = snapshot(run_dir)
    assert [e["kind"] for e in events[:2]] == ["environment", "run_started"]
    root, *children = _export_through_cli(run_dir, monkeypatch)
    metadata = root["extra"]["metadata"]
    assert metadata["envelope_events"] == events[:1]
    assert metadata["provenance"] == events[0]["provenance"]
    assert root["inputs"] == {"task": "Verify the environment"}
    assert [c["run_type"] for c in children] == ["llm", "tool", "llm", "tool"]
    assert children[0]["extra"]["metadata"]["ls_model_name"] == "harbor-fixture"
    assert snapshot(run_dir) == before


@pytest.fixture
def enveloped_run(tmp_path):
    events, result = write_run(tmp_path)
    for event in events:
        event["id"] += 2
        event["elapsed_seconds"] += 2
    envelope = [
        {
            "id": 1,
            "kind": "environment",
            "elapsed_seconds": 0.25,
            "timestamp": (BASE_TIME - timedelta(seconds=2)).isoformat(),
            "backend": "harbor",
            "workspace": "/workspace",
            "commit_patch": False,
            "provenance": {"source_sha256": "fixture-source-hash", "harness_version": "1"},
        },
        {
            "id": 2,
            "kind": "setup_finished",
            "elapsed_seconds": 1.25,
            "timestamp": (BASE_TIME - timedelta(seconds=1)).isoformat(),
            "detail": "local setup ready",
        },
    ]
    persist(tmp_path, envelope + events, result)
    return tmp_path, envelope


def test_envelope_preserves_full_provenance_and_source(enveloped_run):
    directory, envelope = enveloped_run
    before = snapshot(directory)
    root = exporter.build_trace(directory)[0]
    metadata = root["extra"]["metadata"]
    assert metadata["envelope_events"] == envelope
    assert metadata["provenance"] == envelope[0]["provenance"]
    assert metadata["backend"] == "harbor"
    assert root["inputs"] == {"task": "Check the workspace"}
    assert snapshot(directory) == before


def test_envelope_timing_and_ids_include_setup(enveloped_run):
    directory, envelope = enveloped_run
    spans = exporter.build_trace(directory)
    root, worker, *_ = spans
    assert root["start_time"] == envelope[0]["timestamp"]
    assert root["extra"]["metadata"]["elapsed_seconds"] == 10
    assert worker["start_time"] == (BASE_TIME + timedelta(seconds=1)).isoformat()
    assert worker["extra"]["metadata"]["elapsed_seconds"] == 2
    assert worker["parent_run_id"] == root["id"]
    copied = directory.parent / (directory.name + "-copied")
    shutil.copytree(directory, copied)
    assert exporter.build_trace(copied) == spans


@pytest.mark.parametrize("corruption", ["missing_start", "two_starts", "two_ends", "pre_run_tool"])
def test_envelope_still_requires_one_valid_run(enveloped_run, corruption):
    directory, _ = enveloped_run
    events = exporter.read_events(directory / "events.jsonl")
    changes = {
        "missing_start": (2, "setup_finished"),
        "two_starts": (0, "run_started"),
        "two_ends": (0, "run_finished"),
        "pre_run_tool": (0, "tool_started"),
    }
    index, kind = changes[corruption]
    events[index]["kind"] = kind
    persist(directory, events, events[-1]["result"])
    with pytest.raises(exporter.ExportError, match="local run"):
        exporter.export_run(directory, api_key=FAKE_KEY)
