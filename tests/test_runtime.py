import json
from dataclasses import replace
from pathlib import Path

import pytest

from coding_agent.config import Config, RunConfig
from coding_agent.model import ScriptedModel
from coding_agent.runtime import Agent
from coding_agent.toolset import WorkspaceTools
from coding_agent.trace import EventLog, read_events
from coding_agent.types import ModelReply, ToolCall, Usage


def reply(*calls):
    return ModelReply(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in calls
            ],
        },
        calls,
        Usage(10, 10),
        "tool_calls",
    )


def execute(tmp_path: Path, replies, *, settings=None, supervisor=None):
    workspace = tmp_path / "work"
    workspace.mkdir()
    log = EventLog(tmp_path / "trace")
    tools = WorkspaceTools(workspace, tmp_path / "outputs")
    agent = Agent(
        Config(run=settings or RunConfig()), ScriptedModel(replies), tools, log, supervisor
    )
    try:
        result = agent.run("Make a small correct change, reproduce bugs first.")
        return result, agent, read_events(log.directory / "events.jsonl")
    finally:
        log.close()


def test_verification_becomes_stale_after_edit(tmp_path):
    result, _, events = execute(
        tmp_path,
        [
            reply(ToolCall("1", "run_command", {"command": "true", "verify": True})),
            reply(ToolCall("2", "edit_file", {"path": "x", "old": "", "new": "y", "create": True})),
            reply(ToolCall("3", "submit", {"summary": "done"})),
        ],
        settings=RunConfig(max_turns=3),
    )
    assert result["status"] == "budget_exhausted"
    results = [event for event in events if event["kind"] == "tool_result"]
    assert results[-1]["result"]["status"] == "error"


class Advisor:
    def __init__(self, *, malformed=False):
        self.malformed = malformed

    def complete(self, messages, tools, max_tokens):
        assert tools == []
        packet = json.loads(messages[-1]["content"])
        advice = {
            "action": "replan",
            "diagnosis": "Repeated inspection",
            "evidence_ids": packet["signal"]["event_ids"],
            "next_action": "Try a focused reproduction",
            "expected_progress": "A new failure",
        }
        return ModelReply(
            {"role": "assistant", "content": "invalid" if self.malformed else json.dumps(advice)},
            usage=Usage(10, 10),
        )


def test_supervision_bounded_and_multi_call_order_valid(tmp_path):
    replies = [
        reply(
            ToolCall(str(i), "request_help", {"reason": "stuck"}),
            ToolCall(f"b{i}", "list_files", {}),
        )
        for i in range(5)
    ]
    result, agent, events = execute(
        tmp_path,
        replies,
        settings=RunConfig(
            max_turns=5, supervision="on", max_interventions=2, supervisor_cooldown=1
        ),
        supervisor=Advisor(),
    )
    assert result["supervisor_calls"] == 2
    assert result["interventions"] == 2
    assert result["usage"]["input_tokens"] == 70
    assert any(event["kind"] == "supervisor_advice" for event in events)
    for group in agent.context.groups:
        assert group[1]["role"] == group[2]["role"] == "tool"


@pytest.mark.parametrize("mode", ["shadow", "off"])
def test_shadow_and_off_do_not_call_model_for_supervision(tmp_path, mode):
    result, _, events = execute(
        tmp_path,
        [reply(ToolCall(str(i), "list_files", {})) for i in range(4)],
        settings=RunConfig(max_turns=4, supervision=mode),
    )
    assert result["supervisor_calls"] == 0
    assert any(e["kind"] == "progress_signal" for e in events)


def test_malformed_supervisor_fails_open_with_call_limit(tmp_path):
    result, _, events = execute(
        tmp_path,
        [reply(ToolCall(str(i), "request_help", {})) for i in range(3)],
        settings=RunConfig(max_turns=3, supervision="on", max_interventions=1),
        supervisor=Advisor(malformed=True),
    )
    assert result["supervisor_calls"] == 1
    assert result["interventions"] == 0
    assert any(e["kind"] == "supervisor_failed" for e in events)


def test_model_error_is_recorded_and_reserves_unknown_usage(tmp_path):
    result, _, events = execute(tmp_path, [])
    assert result["status"] == "model_error"
    assert not result["usage"]["complete"]
    assert result["usage"]["unknown_usage_reserved"] > 0
    assert any(e["kind"] == "model_failed" for e in events)


def test_no_side_effects_after_submission_in_batch(tmp_path):
    result, _, _ = execute(
        tmp_path,
        [
            reply(
                ToolCall("1", "submit", {"summary": "done"}),
                ToolCall("2", "run_command", {"command": "touch unexpected"}),
            )
        ],
        settings=replace(RunConfig(), require_verification=False),
    )
    assert result["status"] == "submitted"
    assert not (tmp_path / "work/unexpected").exists()


def test_supervisor_does_not_advise_on_a_revision_changed_by_batch(tmp_path):
    result, _, events = execute(
        tmp_path,
        [
            reply(ToolCall("1", "list_files", {})),
            reply(
                ToolCall("2", "list_files", {}),
                ToolCall(
                    "3", "edit_file", {"path": "new", "old": "", "new": "progress", "create": True}
                ),
            ),
        ],
        settings=RunConfig(max_turns=2, supervision="on", repeat_threshold=2),
        supervisor=Advisor(),
    )
    assert result["supervisor_calls"] == 0
    assert any(e["kind"] == "supervisor_skipped" for e in events)


def test_nonstring_supervisor_action_is_ignored(tmp_path):
    advisor = ScriptedModel([ModelReply({"role": "assistant", "content": '{"action":[]}'})])
    result, _, events = execute(
        tmp_path,
        [reply(ToolCall("1", "request_help", {}))],
        settings=RunConfig(max_turns=1, supervision="on"),
        supervisor=advisor,
    )
    assert result["status"] == "budget_exhausted"
    assert any(e["kind"] == "supervisor_failed" for e in events)


@pytest.mark.parametrize("revision_fails", [False, True])
def test_cleanup_failure_preserves_model_failure_and_final_trace(tmp_path, revision_fails):
    class BrokenCleanup(WorkspaceTools):
        cleanup_attempted = False

        def close(self):
            super().close()
            self.cleanup_attempted = True
            raise RuntimeError("remoteRPCNotFound")

        def revision(self):
            if self.cleanup_attempted and revision_fails:
                raise RuntimeError("sandbox disappeared")
            return super().revision()

    work = tmp_path / "work"
    work.mkdir()
    log = EventLog(tmp_path / "trace")
    tools = BrokenCleanup(work, tmp_path / "outputs")
    try:
        result = Agent(Config(), ScriptedModel([]), tools, log).run("Fail the model")
    finally:
        log.close()
    assert result["status"] == "model_error"
    assert result["detail"] == "Scripted model has no replies remaining"
    assert json.loads((log.directory / "result.json").read_text()) == result
    events = read_events(log.directory / "events.jsonl")
    kinds = [event["kind"] for event in events]
    assert "model_failed" in kinds
    assert "cleanup_failed" in kinds
    assert "cleanup_finished" not in kinds
    assert kinds[-1] == "run_finished"
    assert ("revision_failed" in kinds) == revision_fails
    assert bool(result["revision"]) != revision_fails


@pytest.mark.parametrize("failure", ["cleanup", "revision"])
def test_finalization_failure_does_not_report_submission_success(tmp_path, failure):
    class BrokenFinalization(WorkspaceTools):
        closed = False

        def close(self):
            super().close()
            self.closed = True
            if failure == "cleanup":
                raise RuntimeError("cannot stop service")

        def revision(self):
            if self.closed and failure == "revision":
                raise RuntimeError("cannot read revision")
            return super().revision()

    work = tmp_path / "work"
    work.mkdir()
    log = EventLog(tmp_path / "trace")
    tools = BrokenFinalization(work, tmp_path / "outputs")
    model = ScriptedModel(
        [
            reply(ToolCall("verify", "run_command", {"command": "true", "verify": True})),
            reply(ToolCall("submit", "submit", {"summary": "done"})),
        ]
    )
    try:
        result = Agent(Config(), model, tools, log).run("Submit")
    finally:
        log.close()
    assert result["status"] == "runtime_error"
    assert "failed" in result["detail"]
    assert json.loads((log.directory / "result.json").read_text()) == result
    assert read_events(log.directory / "events.jsonl")[-1]["kind"] == "run_finished"
