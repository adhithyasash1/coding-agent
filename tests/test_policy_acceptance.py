"""Local acceptance for explicitly selected policies and their compatibility defaults."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cli_e2e import call, run_fixture
from test_runtime import Advisor, execute, reply

from coding_agent.config import Config, ModelConfig, RunConfig, load_config
from coding_agent.context import Context
from coding_agent.generation import GenerationPlanner
from coding_agent.monitor import AdaptiveProgressMonitor, diagnostic_fingerprint
from coding_agent.types import ToolCall, ToolResult


def test_legacy_defaults_and_invalid_policy():
    config = Config()
    assert config.model.reasoning_effort is None
    assert config.run.generation_policy == "legacy"
    assert config.run.context_layout == "legacy"
    assert config.run.progress_policy == "legacy"
    with pytest.raises(ValueError):
        RunConfig(generation_policy="automatic")
    with pytest.raises(ValueError):
        ModelConfig(reasoning_effort="")


@pytest.mark.parametrize(
    "remaining,expected", [(840, 2048), (120, 1024), (60, 450), (15, 0), (0, 0)]
)
def test_generation_allowance_boundaries(remaining, expected):
    planner = GenerationPlanner()
    allowance, evidence = planner.allowance(remaining, 10000, 4096)
    assert allowance == expected
    assert evidence["reserve_seconds"] == 120
    assert evidence["request_overhead_seconds"] == 15


def test_finalization_consumes_reserve_once_and_tiny_token_budget():
    planner = GenerationPlanner()
    assert planner.allowance(130, 10000, 4096)[0] == 1024
    assert planner.finalizing
    assert planner.allowance(100, 10000, 4096)[0] == 850
    assert planner.allowance(99, 7, 4096)[0] == 7
    assert planner.allowance(99, -1, 4096)[0] <= 0


def test_slow_measurements_reduce_allowance():
    planner = GenerationPlanner()
    for _ in range(3):
        planner.observe(200, 50)
    allowance, evidence = planner.allowance(200, 10000, 4096)
    assert allowance < 650
    assert evidence["tokens_per_second"] == 4


def test_cli_opt_in_generation_cap_and_explicit_submission(tmp_path):
    process = run_fixture(
        tmp_path,
        [
            call("verify_command", {"argv": ["/bin/sh", "-c", "true"]}),
            call("submit", {"summary": "Explicit check passed"}, "2"),
        ],
        '[run]\ngeneration_policy="time_aware"\ncontext_layout="history_first"\nmax_seconds=120\n',
    )
    assert process.returncode == 0, process.stderr
    events = [
        json.loads(line) for line in (tmp_path / "trace/events.jsonl").read_text().splitlines()
    ]
    allowances = [e for e in events if e["kind"] == "generation_allowance"]
    assert allowances and all(e["allowance"] <= 1024 for e in allowances)
    requests = [e for e in events if e["kind"] == "model_request"]
    assert requests[0]["messages"][:3] == requests[1]["messages"][:3]
    state = requests[1]["messages"][-1]["content"]
    assert "verification_receipt" in state


def test_history_layout_retains_complete_exchanges_and_reasoning():
    context = Context("instructions", "task", layout="history_first", environment="python choices")
    exchange = [
        {"role": "assistant", "reasoning_content": "reason", "tool_calls": [{"id": "x"}]},
        {"role": "tool", "tool_call_id": "x", "content": "receipt"},
    ]
    context.add(exchange)
    context.memory = "remaining=300"
    first = context.build([], 10000, 1024)
    context.memory = "remaining=200"
    second = context.build([], 10000, 1024)
    assert first[:-1] == second[:-1]
    assert first[3:5] == exchange
    context.add([{"role": "assistant", "content": "x" * 2000}])
    compact = context.build([], 2600, 1024)
    assert context.dropped_groups == 2
    assert all(m.get("role") != "tool" for m in compact)


def test_shadow_read_trigger_and_productive_diagnosis_control(monkeypatch):
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr("coding_agent.monitor.time", SimpleNamespace(monotonic=lambda: clock.now))
    monitor = AdaptiveProgressMonitor()
    signals = []
    for index in range(6):
        clock.now = index * 24
        signals.append(
            monitor.observe(
                ToolCall(str(index), "read_file", {"path": f"file{index}"}),
                ToolResult("ok", "new evidence", revision="same"),
                index,
            )
        )
    assert all(signal is None for signal in signals[:-1])
    assert signals[-1] is not None  # Legitimate long diagnosis is only a review candidate.
    changed = monitor.observe(
        ToolCall("edit", "edit_file", {}), ToolResult("ok", "edited", revision="new"), 6
    )
    assert changed is None
    clock.now += 150
    assert (
        monitor.observe(ToolCall("r", "read_file", {}), ToolResult("ok", "file", revision="new"), 7)
        is None
    )


def test_diagnostic_normalization_preserves_meaning():
    call1 = ToolCall("a", "run_command", {"command": "pytest test_a.py"})
    result1 = ToolResult(
        "error", "AssertionError in test_a.py:42\n1 failed in 1.23s", 1, revision="r"
    )
    result2 = ToolResult(
        "error", "AssertionError in test_a.py:42\n1 failed in 4.56s", 1, revision="r"
    )
    assert diagnostic_fingerprint(call1, result1) == diagnostic_fingerprint(call1, result2)
    call2 = ToolCall("b", "run_command", {"command": "pytest test_b.py"})
    assert diagnostic_fingerprint(call1, result1) != diagnostic_fingerprint(call2, result2)
    monitor = AdaptiveProgressMonitor()
    assert monitor.observe(call1, result1, 1) is None
    assert monitor.observe(call1, result2, 2) is None
    assert monitor.observe(call1, result1, 3) is not None


@pytest.mark.parametrize("recovery,calls", [("reminder", 0), ("critic", 1)])
def test_identical_diagnostic_triggers_bound_reminder_and_critic(tmp_path, recovery, calls):
    result, _, events = execute(
        tmp_path,
        [reply(ToolCall(str(i), "run_command", {"command": "false"})) for i in range(6)],
        settings=RunConfig(
            max_turns=6,
            max_seconds=840,
            supervision="on",
            progress_policy="adaptive",
            recovery_policy=recovery,
            supervisor_cooldown=1,
        ),
        supervisor=Advisor(),
    )
    assert result["supervisor_calls"] == calls
    assert result["interventions"] == 1
    signals = [e for e in events if e["kind"] == "progress_signal"]
    assert signals[0]["reason"] == "candidate: repeated diagnostic failure"
    requests = [e for e in events if e["kind"] == "model_request" and e["role"] == "supervisor"]
    assert len(requests) == calls
    assert all(e["max_output_tokens"] <= 512 and e["tools"] == [] for e in requests)


def test_no_experimental_critic_under_180_seconds(tmp_path):
    result, _, events = execute(
        tmp_path,
        [reply(ToolCall(str(i), "request_help", {})) for i in range(3)],
        settings=RunConfig(
            max_turns=3, max_seconds=179, supervision="on", progress_policy="adaptive"
        ),
        supervisor=Advisor(),
    )
    assert result["supervisor_calls"] == 0
    assert any(e["kind"] == "progress_signal" for e in events)


def test_frozen_profiles_change_independent_mechanisms():
    from dataclasses import asdict

    profiles = Path(__file__).resolve().parents[1] / "profiles"
    baseline = asdict(load_config(profiles / "legacy.toml"))
    for name, section, field in [
        ("reasoning-xhigh", "model", "reasoning_effort"),
        ("reasoning-medium", "model", "reasoning_effort"),
        ("generation-time-aware", "run", "generation_policy"),
        ("context-history-first", "run", "context_layout"),
        ("progress-critic", "run", "progress_policy"),
    ]:
        current = asdict(load_config(profiles / f"{name}.toml"))
        assert current[section][field] != baseline[section][field]
        current[section][field] = baseline[section][field]
        assert current == baseline
    reminder = asdict(load_config(profiles / "progress-reminder.toml"))
    critic = asdict(load_config(profiles / "progress-critic.toml"))
    reminder["run"]["recovery_policy"] = critic["run"]["recovery_policy"]
    assert reminder == critic


def test_time_aware_planning_never_allows_late_tools(tmp_path, monkeypatch):
    from coding_agent import runtime
    from coding_agent.model import ScriptedModel
    from coding_agent.runtime import Agent
    from coding_agent.toolset import WorkspaceTools
    from coding_agent.trace import EventLog, read_events

    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock.now))

    class SlowModel(ScriptedModel):
        def complete(self, messages, tools, max_tokens):
            clock.now += 730 if clock.now == 0 else 110
            return super().complete(messages, tools, max_tokens)

    work = tmp_path / "work"
    work.mkdir()
    tools = WorkspaceTools(work, tmp_path / "outputs")
    model = SlowModel(
        [
            reply(ToolCall("read", "list_files", {})),
            reply(ToolCall("late", "edit_file", {"path": "late", "new": "x", "create": True})),
        ]
    )
    log = EventLog(tmp_path / "trace")
    try:
        result = Agent(
            Config(run=RunConfig(max_seconds=840, generation_policy="time_aware")),
            model,
            tools,
            log,
        ).run("Make progress")
    finally:
        log.close()
    assert result["status"] == "budget_exhausted"
    assert not (work / "late").exists()
    events = read_events(log.directory / "events.jsonl")
    allowances = [e["allowance"] for e in events if e["kind"] == "generation_allowance"]
    assert allowances == [2048, 950]
    assert result["model_wait_seconds"] == 840
