# Adaptive coding agent

A fresh Python harness with one coding worker and an optional, bounded supervisor.
It is model-independent through an OpenAI-compatible endpoint. No model weights,
cloud credentials, or raw benchmark artifacts are included. Development measurements
and their limitations are recorded in [the evaluation report](docs/EVALUATION_REPORT.md).

## Start locally

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required.

```sh
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run mypy
uv run python scripts/check_complexity.py
```

The tests use scripted responses and a loopback HTTP server. They do not call paid
model APIs. Optional SDK tests run separately as described in [evaluations](docs/evaluations.md).

For a complete offline demonstration that reproduces and fixes a small bug:

```sh
uv run python scripts/demo.py
```

For a real task, copy `agent.example.toml`, select a model and endpoint, and set its
API key in your terminal. Keep the task checkout separate from this harness repository.

```sh
cp agent.example.toml agent.local.toml
export AGENT_API_KEY="$(python -c 'import getpass; print(getpass.getpass("Endpoint key: "))')"
uv run coding-agent run --config agent.local.toml \
  --task-file /absolute/path/to/task.txt \
  --workspace /absolute/path/to/task-checkout \
  --trace-dir /absolute/path/to/new-run-directory
```

The default backend runs tools in Docker with no network, two CPUs, 4 GiB RAM,
and a persistent workspace. Start Docker first. Use `--image` for an environment
with the task's dependencies, and `--network bridge` when network access is needed.
Pin the image by digest for measured evaluations. The default Python image is a
minimal smoke-test environment, not a universal benchmark image.

`--backend local` runs commands directly on the host. Use it for trusted fixtures
or inside a sandbox already supplied by an evaluation runner. File-tool containment
does not sandbox arbitrary shell commands. Interactive tools support pipe stdin;
programs that require a real PTY are not supported yet.

## Worker and supervisor

The worker can inspect files, search, edit atomically, run commands, and interact
with managed processes. `remember` preserves findings and failed hypotheses across
context trimming. `submit` records completion after a successful verification of
the current workspace revision. The official benchmark grader decides correctness.

The deterministic progress monitor notices repeated actions with the same result
and unchanged workspace. A worker can also call `request_help`.

| Mode in `[run]` | Behavior |
| --- | --- |
| `off` | Worker only; observations remain traceable |
| `shadow` | Default: record stuck signals without advisor calls |
| `on` | Consult a supervisor on eligible signals |

The supervisor receives a separate context with the task, notes, recent evidence,
previous advice, and remaining budget. It has no execution tools. Advice must cite
supplied event IDs and describe a concrete next action and expected progress.
The worker remains the sole writer. Calls have a cooldown and a default maximum
of three attempts. Invalid advice is logged and ignored. All model usage, including
supervision, draws from the same run budget.

## Inspect a run

```sh
uv run coding-agent inspect /absolute/path/to/run
uv run coding-agent inspect /absolute/path/to/run --events
```

Progress appears on stderr while the run executes; stdout contains the final JSON.
Every run has ordered, flushed JSONL events, full tool-output files, diagnostic
checkpoints, and a final result. See [trace semantics](docs/tracing.md) for redaction,
budgets, failure recovery, and the distinction between a checkpoint and safe resume.

## Live evaluation

Qwen3.8-27B FP8 is the [initial model choice](docs/model-selection.md).
The authorized $30 Modal allowance covers a small development sample and fixes,
with a $24 working ceiling. Model/tool and repair smoke tests have passed; graded
Terminal-Bench trials are recorded in [the report](docs/EVALUATION_REPORT.md).
See [Modal setup](docs/modal.md) and [the frozen sample](docs/sample-selection.md).
Docker transport
requires a running daemon; its CLI arguments and shared tool protocol are locally
tested, but container execution has not yet been validated on this machine.

The scope and remaining experiments are in [architecture](docs/architecture.md).
The project has no Git remote. All implementation is new; the old reference
checkout was deleted before this repository was initialized.
