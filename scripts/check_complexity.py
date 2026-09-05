"""Enforce Radon's boolean-aware cyclomatic limit as well as Ruff's C901 rule."""

import json
from pathlib import Path

from radon.complexity import cc_visit


def functions(blocks):
    for block in blocks:
        if hasattr(block, "methods"):
            yield from functions(block.methods)
        else:
            yield block
            yield from functions(block.closures)


def main() -> int:
    entries = []
    for directory in ("src/coding_agent", "integrations", "scripts"):
        for path in sorted(Path(directory).glob("*.py")):
            entries.extend(
                {
                    "file": str(path),
                    "function": block.name,
                    "line": block.lineno,
                    "complexity": block.complexity,
                }
                for block in functions(cc_visit(path.read_text()))
            )
    entries = list(
        {(entry["file"], entry["line"], entry["function"]): entry for entry in entries}.values()
    )
    violations = [entry for entry in entries if entry["complexity"] > 10]
    print(
        json.dumps(
            {
                "functions": len(entries),
                "max_complexity": max(entry["complexity"] for entry in entries),
                "violations": violations,
            },
            indent=2,
        )
    )
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
