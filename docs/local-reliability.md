# Local reliability milestone - September 9, 2026

The implementation improves completion mechanics locally. It does not establish a
benchmark score increase. No paid evaluation, external trace export, dependency
upgrade or push is part of this milestone. Historical trial directories, generated
outcomes and frozen configurations remain unchanged.

## Enabled correctness changes

The execution environment now reports its checkout, shell, available interpreters,
explicit task-interpreter selection and declared public test entry points. Discovery
uses at most six distinct executable candidates, one-second version probes, bounded
output and process-group cleanup. It does not install task dependencies or read
grader tests. Discovery is setup time, before the worker-solving clock. The private
tool-server interpreter is not automatically selected as the project interpreter.
An absent or ambiguous selection is reported to the worker. Local mode remains a
trusted host runner; Docker and the evaluator provide the actual sandbox boundary.

Configure task facts in a new experiment configuration, for example:

```toml
[environment]
task_interpreter = "/opt/project/bin/python"
test_entry_points = ["python -m pytest tests/public_tests.py"]
```

These are task facts and guidance, not an executable allowlist. Other tools such as
compilers and shell test scripts remain usable. Do not list hidden verifier entry
points here. Environment paths are paths inside the worker's execution environment.

`verify_command(argv, cwd?, timeout?)` invokes an argument vector without a shell.
It returns the executable, cwd, exit status, duration, output artifact ID and before/
after workspace revisions. Compatibility `run_command(command, verify=true)` uses
Bash with `errexit` and `pipefail`; missing Bash directs the worker to argv verification.
Ordinary commands still use `/bin/sh`. Output is captured before it is shortened.
Write, verify and submit are separate actions. Content or permission changes make
a previous receipt stale. A passing command establishes only that check's result.

Workers receive opaque artifact IDs. `read_artifact(artifact_id, offset?, limit?)`
reads only registered outputs from that tool-server trial, at most 65,536 bytes per
request. Host storage paths remain in local trace metadata. Credentials remain on
the host in evaluator mode; existing credential filtering and network controls remain.

Malformed and truncated model responses never execute tools. Valid provider usage
is retained even when the response is unusable. Only absent or invalid accounting
requires an unknown-usage reservation. Global model deadline expiry remains
`budget_exhausted`; earlier transport errors remain `model_error`. Existing 429
handling remains; no new retry or remote cancellation policy was added.

Worker status, explicit submission, source changes, collection/application, grader
status, reward and accounting completeness have separate evidence fields. Null grader
fields are not scores. Collection/cleanup errors do not replace the primary failure.
Host lifecycle receipts and worker traces remain separate from grader feedback.

## Experimental behavior

All new reasoning, generation, layout and progress policies retain legacy defaults.
See [independent profiles](../profiles/README.md). Reasoning effort is omitted from
model requests unless explicitly configured.

The time-aware planner caps work at 2,048 output tokens and finalization at 1,024.
It reserves 120 seconds during work and allows finalization to consume that reserve
once. It starts at 10 generated tokens/second and 15 seconds of request overhead.
After three responses it conservatively uses measured whole-request throughput and
residual overhead estimates. These are planning estimates, not server decode metrics
or wall-time guarantees. Each allowance logs inputs and phase. The hard deadline and
one-time final reminder continue to apply.

The alternate context layout retains static instructions and environment facts,
then complete exchanges, then changing state. It preserves model-specific reasoning
fields. The last three verification receipts persist as deterministic facts without
requiring `remember`; worker notes are attributed hypotheses. There are no summarizer
calls. Older complete exchanges may still be dropped to fit the context limit.

The adaptive detector proposes review after six read-only actions over at least
120 seconds without source or verification progress, or three identical normalized
diagnostic failures. Command, path and error differences remain meaningful. Only ISO
timestamps and conventional test-duration metadata are normalized. Signals alone do
not prove a stall. Experimental reminder and critic modes use identical triggers.
The critic has at most one call, 512 output tokens and at least 180 seconds remaining.

## Local evidence and its limits

The initial public CLI reproductions failed for masked verification pipelines,
failure followed by cleanup, missing artifact IDs and missing project-interpreter/
argv-verification support. They pass after the changes. A separate local HTTP plus
CLI reproduction showed that a truncated response could execute an edit; it now
ends as `model_error` without the edit and retains the returned usage. A declared
version probe that forks a child is now cleaned up.

The generated offline shadow replay is at
`.agent-runs/local-reliability-20260909/shadow-signals.json`. It reads original events
without changing them or querying a model:

| Saved trace | Tool actions | Candidate signals |
| --- | ---: | ---: |
| Frozen Astropy | 10 | 0 |
| Frozen pytest | 25 | 6 |
| Frozen scikit-learn | 24 | 6 |
| Astropy deadline retry | 7 | 0 |
| Successful constraints-scheduling control | 9 | 0 |
| Successful gRPC deadline retry control | 20 | 0 |

Signals can repeat in one episode. These counts are not stall precision or recall.
A synthetic legitimate long-diagnosis control intentionally also triggers review;
the detector cannot decide correctness. Local scripted comparisons establish equal
triggers and bounded intervention mechanics, not the usefulness of critic advice.

Pier validation now includes actual pinned `Trial.run` orchestration using local
environment transports, the inherited unmocked Git commit, real binary bytes and
text edits, executable permissions, configured collection, checksum-preserving
transfer, a pristine separate verifier checkout, grading and cleanup. Commit,
collection and grader failures and budget-exhausted workers are covered. The local
toy grader is not the DeepSWE task grader. Real Modal image builds and DeepSWE
committed-patch performance remain unvalidated in this milestone.

Use [validation](validation.md) for the final acceptance record and
[evaluation report](EVALUATION_REPORT.md) for historical paid results. Future paid
validation needs fresh credentialed billing, a full bounded lifecycle estimate plus
the $2 reserve below $24, exclusive ownership, fresh inference readiness and sufficient
inference lifetime. No saved readiness receipt grants standing permission to launch.

The finalization reserve is advisory token planning. Tool execution, verification,
context scans and supervisor work still share the hard deadline and can consume that
time. The detector can flag productive read-only diagnosis or repeated failures
separated by successful diagnostics; review candidates should be measured against
those controls before enabling interventions by default.
