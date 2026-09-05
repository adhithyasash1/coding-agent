import json
from pathlib import Path

import pytest

from coding_agent.context import Context, ContextOverflow, estimate_tokens
from coding_agent.trace import EventLog, read_events


def test_context_drops_whole_exchanges_and_preserves_task_notes():
    context = Context("system", "original task", memory="hypothesis A failed; inspect B")
    for i in range(10):
        context.add(
            [
                {"role": "assistant", "content": "x" * 1000},
                {"role": "tool", "tool_call_id": str(i), "content": "result"},
            ]
        )
    messages = context.build([], 4000, 1000)
    assert context.dropped_groups > 0
    assert messages[1]["content"] == "original task"
    assert "hypothesis A failed" in messages[2]["content"]
    assert estimate_tokens(messages) < 3000
    assert messages[3]["role"] == "assistant"


def test_oversize_task_fails_before_model_request():
    with pytest.raises(ContextOverflow):
        Context("sys", "x" * 10000).build([], 4000, 1000)


def test_trace_redaction_durability_and_no_overwrite(tmp_path):
    log = EventLog(tmp_path, ("super-secret",))
    log.emit("example", api_key="hidden", nested={"text": "contains super-secret"})
    artifact = log.artifact("checkpoint.json", json.dumps({"password": "hidden"}))
    log.finish({"status": "done"})
    log.close()
    text = (tmp_path / "events.jsonl").read_text()
    assert "super-secret" not in text and "hidden" not in text
    assert read_events(tmp_path / "events.jsonl")[0]["id"] == 1
    assert "hidden" not in Path(artifact).read_text()
    with pytest.raises(FileExistsError):
        EventLog(tmp_path)


def test_truncated_trace_is_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"id":1')
    with pytest.raises(ValueError, match="truncated"):
        read_events(path)
