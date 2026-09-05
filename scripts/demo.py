"""Run a complete, deterministic bug-repair example with no model credentials."""

import json
import shlex
import subprocess
import sys
import uuid
from pathlib import Path


def message(name: str, arguments: dict[str, object], identity: int) -> dict[str, object]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": str(identity),
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def main() -> int:
    root = Path(".agent-runs") / ("demo-" + uuid.uuid4().hex[:8])
    work = root / "workspace"
    work.mkdir(parents=True)
    (work / "invoice.py").write_text("def total(price, count):\n    return price + count\n")
    (work / "test_invoice.py").write_text(
        "import unittest\nfrom invoice import total\n\n"
        "class InvoiceTest(unittest.TestCase):\n"
        "    def test_multiple_items(self):\n        self.assertEqual(total(4, 3), 12)\n"
    )
    task = root / "task.txt"
    task.write_text(
        "Fix invoice totals for multiple items. Reproduce the failure and verify the fix."
    )
    fixture = root / "fixture.json"
    # Same-size edits within one second can reuse timestamp-based .pyc files.
    # Keep this fast deterministic demo independent of bytecode-cache timing.
    verify_command = shlex.join([sys.executable, "-B", "-m", "unittest", "-v"])
    fixture.write_text(
        json.dumps(
            [
                message("run_command", {"command": verify_command}, 1),
                message(
                    "edit_file",
                    {"path": "invoice.py", "old": "price + count", "new": "price * count"},
                    2,
                ),
                message(
                    "run_command",
                    {"command": verify_command, "verify": True},
                    3,
                ),
                message(
                    "submit",
                    {"summary": "Reproduced the bug, fixed addition, and verified the result."},
                    4,
                ),
            ]
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "coding_agent",
            "run",
            "--backend",
            "local",
            "--workspace",
            str(work),
            "--task-file",
            str(task),
            "--fixture",
            str(fixture),
            "--trace-dir",
            str(root / "trace"),
        ]
    )
    print(f"Inspect: coding-agent inspect {(root / 'trace').resolve()}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
