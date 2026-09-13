"""Local agent-loop deadline regression with scripted latency and real workspace tools."""

import json
import shlex
import sys
import time
from types import SimpleNamespace

import pytest
from test_runtime import reply

from coding_agent import runtime
from coding_agent.config import Config, RunConfig
from coding_agent.model import ScriptedModel
from coding_agent.runtime import Agent
from coding_agent.toolset import WorkspaceTools
from coding_agent.trace import EventLog, read_events
from coding_agent.types import ToolCall


def run_script(tmp_path, monkeypatch, steps, *, max_seconds=840):
    clock = SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class TimedModel(ScriptedModel):
        def complete(self, messages, tools, max_tokens):
            clock.now += next(delays)
            return super().complete(messages, tools, max_tokens)

    delays = iter(delay for delay, _ in steps)
    work = tmp_path / "work"
    work.mkdir()
    (work / "toy.py").write_text("def pages():\n    return [1, 2]\n")
    log = EventLog(tmp_path / "trace")
    tools = WorkspaceTools(work, tmp_path / "outputs")
    model = TimedModel([response for _, response in steps])
    agent = Agent(
        Config(run=RunConfig(max_seconds=max_seconds, max_turns=len(steps), supervision="off")),
        model,
        tools,
        log,
    )
    try:
        result = agent.run("Check toy.pages() returns [1, 2], then submit.")
    finally:
        log.close()
    events = read_events(log.directory / "events.jsonl")
    requests = [e for e in events if e["kind"] == "model_request"]
    states = [json.loads(e["messages"][2]["content"].splitlines()[1]) for e in requests]
    return result, events, states


def check(call_id, *, verify=False):
    command = shlex.join(
        [sys.executable, "-B", "-c", "from toy import pages; assert pages() == [1, 2]"]
    )
    arguments = {"command": command}
    if verify:
        arguments["verify"] = True
    return ToolCall(call_id, "run_command", arguments)


def test_deadline_context_declines_and_reminder_is_once(tmp_path, monkeypatch):
    result, events, states = run_script(
        tmp_path,
        monkeypatch,
        [
            (199, reply(ToolCall("inspect", "read_file", {"path": "toy.py"}))),
            (511, reply(check("behavior"))),
            (10, reply(ToolCall("remember", "remember", {"notes": "Behavior passes."}))),
            (10, reply(check("verify", verify=True))),
            (10, reply(ToolCall("submit", "submit", {"summary": "Toy behavior verified."}))),
        ],
    )
    assert [s["remaining_seconds"] for s in states] == pytest.approx([840, 641, 130, 120, 110])
    assert [i for i, s in enumerate(states) if "finalization_reminder" in s] == [3]
    reminders = [e for e in events if e["kind"] == "finalization_reminder"]
    assert len(reminders) == 1
    assert reminders[0]["remaining_seconds"] == pytest.approx(120)
    assert reminders[0]["reserve_seconds"] == 120
    assert reminders[0]["turn"] == 4
    assert "verify=true" in reminders[0]["message"]
    assert "preserve failing exit codes" in reminders[0]["message"]
    assert states[2]["verified_revision"] is None  # Plain passing check earns no credit.
    assert result["status"] == "submitted"
    assert result["verified_revision"] == result["revision"]
    assert result["supervisor_calls"] == 0
    assert [e["call"]["id"] for e in events if e["kind"] == "tool_started"] == [
        "inspect",
        "behavior",
        "remember",
        "verify",
        "submit",
    ]


@pytest.mark.parametrize("max_seconds,reserve", [(60, 12), (840, 120)])
def test_reserve_scales_and_does_not_autoverify_or_submit(
    tmp_path, monkeypatch, max_seconds, reserve
):
    result, events, states = run_script(
        tmp_path,
        monkeypatch,
        [
            (max_seconds - reserve, reply(check("plain-pass"))),
            (1, reply(ToolCall("premature", "submit", {"summary": "Not verified"}))),
            (1, reply(ToolCall("notes", "remember", {"notes": "Still unverified"}))),
        ],
        max_seconds=max_seconds,
    )
    assert "finalization_reminder" not in states[0]
    assert [s["remaining_seconds"] for s in states] == pytest.approx(
        [
            max_seconds,
            reserve,
            reserve - 1,
        ]
    )
    assert sum("finalization_reminder" in s for s in states) == 1
    reminders = [e for e in events if e["kind"] == "finalization_reminder"]
    assert len(reminders) == 1
    assert reminders[0]["reserve_seconds"] == reserve
    assert result["status"] == "budget_exhausted"
    assert result["verified_revision"] is None
    denied = next(
        e for e in events if e["kind"] == "tool_result" and e["call"]["id"] == "premature"
    )
    assert denied["result"]["status"] == "error"
    assert "verify=true" in denied["result"]["output"]


def test_reminder_does_not_allow_stale_verification(tmp_path, monkeypatch):
    result, events, states = run_script(
        tmp_path,
        monkeypatch,
        [
            (720, reply(check("verify", verify=True))),
            (
                1,
                reply(
                    ToolCall(
                        "edit",
                        "edit_file",
                        {
                            "path": "toy.py",
                            "old": "[1, 2]",
                            "new": "[1, 3]",
                        },
                    )
                ),
            ),
            (1, reply(ToolCall("submit", "submit", {"summary": "Stale check"}))),
        ],
    )
    assert "finalization_reminder" in states[1]
    assert result["status"] == "budget_exhausted"
    assert result["verified_revision"] != result["revision"]
    assert [e for e in events if e["kind"] == "tool_result"][-1]["result"]["status"] == "error"


@pytest.mark.parametrize(
    "late_call",
    [
        ToolCall("late-edit", "edit_file", {"path": "late", "old": "", "new": "x", "create": True}),
        ToolCall("late-submit", "submit", {"summary": "Too late"}),
    ],
)
@pytest.mark.parametrize("delay", [120, 121])
def test_reminder_never_allows_tools_at_or_after_deadline(tmp_path, monkeypatch, late_call, delay):
    result, events, states = run_script(
        tmp_path,
        monkeypatch,
        [
            (720, reply(check("verify", verify=True))),
            (delay, reply(late_call)),
        ],
    )
    assert "finalization_reminder" in states[1]
    assert result["status"] == "budget_exhausted"
    assert result["detail"] == "wall time limit reached"
    assert [e["call"]["id"] for e in events if e["kind"] == "tool_started"] == ["verify"]
    assert not (tmp_path / "work/late").exists()
