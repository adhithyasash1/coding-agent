# Trace and recovery contract

Local traces are the source of truth. LangSmith is not required and no telemetry
is uploaded automatically. A future exporter can use the ordered event IDs to
create parent/child runs without making cloud tracing a runtime dependency.

Each new run directory contains:

| File | Contents |
| --- | --- |
| `events.jsonl` | Ordered IDs, UTC timestamps, monotonic offsets, configuration, model messages and usage, tool starts/results, stuck signals, advice, context trimming, failures, and final status |
| `outputs/` | Full combined command stdout/stderr and full file-tool results, preserved beyond context truncation |
| `artifacts/checkpoint-NNNN.json` | Task, configuration, workspace revision, notes, usage, recent observations, supervisor state at a complete turn boundary |
| `result.json` | Status, detail, token accounting, turns, interventions, elapsed time, revision, verification revision, and a separate initially-null grader result |

CLI environment events include Python/platform information and a SHA-256 hash of
the harness source, so runs can be identified even before the first Git commit.
Configuration records model name, declared revision, sampling parameters, endpoint,
and limits. A declared model revision must match the deployed weights; the HTTP
client cannot independently verify an endpoint operator's configuration.

Events are flushed and fsynced before execution continues. Results and checkpoints
are published atomically without overwriting earlier evidence. A fresh run directory
is required. `inspect` rejects missing, reordered, or malformed IDs and truncated
records. This is integrity validation, not a cryptographic tamper-proof ledger.

Structured events and checkpoints redact known secret keys and the configured
model credential values. Raw task-output artifacts retain task data and may contain
secrets present in the task itself. Keep run directories private, inspect them before
sharing, and do not put live secrets in task repositories. Model/provider/Modal/
LangSmith credential environment entries are removed from local command environments.
Docker and the external Harbor adapter do not send model keys into the task sandbox.
Local mode still has the host user's filesystem access.

## Budgets and stop reasons

`max_tokens` counts all reported input plus output, including cached input. Cached
tokens are reported separately and are not subtracted. Supervisor requests share
the budget. Before a request, a conservative UTF-8 byte estimate reserves input
space and limits requested output. Context is bounded by trimming whole exchanges;
the original task, system instructions, durable notes, and current verification
state remain. Provider-specific tokenizers should replace the conservative estimate
before optimizing long-context throughput. Different tokenizer conventions can
still invalidate an estimated bound, which is why provider usage is checked again.

A request that fails without reliable usage reserves its estimated input plus full
output allowance as `unknown_usage_reserved`. Reported tokens remain distinct from
this reservation and `usage.complete` becomes false. A transport failure is not
automatically retried, since it may have consumed inference. HTTP 429 rate-limit
refusals have bounded retries; each attempt and delay is traced. Tool actions are
never blindly retried.

Wall time is checked between operations, and request and command timeouts are bounded
by the remaining allowance. HTTP timeouts are transport timeouts, not an account
billing cap. Cleanup can take additional time. Use an independent cloud session
deadline and the Modal billing dashboard for live spending.

| Status | CLI code | Meaning |
| --- | --- | --- |
| `submitted` | 0 | Worker submitted; this does not imply grader success |
| `budget_exhausted` | 2 | Turn, time, input/context, or token allowance ended |
| `model_error` | 3 | Endpoint, response format, or usage accounting failed |
| `runtime_error` | 4 | Configuration, sandbox, tool infrastructure, or runtime failed |
| `interrupted` | 130 | User interrupted the worker |

## Recovery

Checkpoints are diagnostic snapshots, not permission to repeat an interrupted
command. Automatic resume, rollback, and trajectory branching are intentionally not
implemented. A process may have changed external state even when a tool result was
never received. Inspect the workspace and last `tool_started`/`tool_result` pair,
then start a new run with explicit recovered context. Preserve the original trace.

Verification binds to a content-and-mode hash, excluding Git internals and common
runtime caches. A successful `verify=true` command counts only when it leaves this
revision unchanged. This records evidence; it cannot determine whether a test was
meaningful. Background processes are killed on cleanup. Revision scanning currently
hashes workspace files in full; large repositories may warrant measured caching.
