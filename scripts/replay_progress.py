"""Replay read-only shadow signals over saved events; never query a model or grader."""

import argparse
import json
from dataclasses import asdict, fields
from pathlib import Path

from coding_agent.monitor import AdaptiveProgressMonitor
from coding_agent.types import ToolCall, ToolResult


def replay(path: Path) -> dict:
    monitor = AdaptiveProgressMonitor()
    signals = []
    actions = 0
    partial = False
    for line in path.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            partial = True
            break
        if event.get("kind") != "tool_result":
            continue
        actions += 1
        result = ToolResult(
            **{
                f.name: event["result"][f.name]
                for f in fields(ToolResult)
                if f.name in event["result"]
            }
        )
        signal = monitor.observe(
            ToolCall(**event["call"]), result, event["id"], now=event["elapsed_seconds"]
        )
        if signal:
            signals.append(asdict(signal))
    return {"trace": str(path), "actions": actions, "partial": partial, "signals": signals}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = {
        "mode": "offline shadow",
        "model_calls": 0,
        "interpretation": "Review candidates, not demonstrated stalls or intervention gains.",
        "traces": [replay(path) for path in args.traces],
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
