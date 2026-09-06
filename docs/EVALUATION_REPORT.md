# Coding-agent evaluation report

Status: IN PROGRESS - 2026-09-06. Baseline complete; fixes and targeted development
retries in progress. Final checks, billing and resource cleanup remain pending.

## Results and what they mean

The frozen four-task Terminal-Bench 2.1 sample produced **two passes, one failure,
and one infrastructure interruption without a grader result**. That is 2/3 graded
tasks passed, with grading coverage 3/4 attempted. The original failed and interrupted
attempts remain in the record. This small, purposefully selected development sample
uses shortened time limits and is not a leaderboard score or general solve-rate estimate.

| Baseline task | Grader reward | Worker outcome | Turns | Worker seconds | Input / output tokens |
| --- | ---: | --- | ---: | ---: | ---: |
| openssl-selfsigned-cert | 0 | Submitted; own checks passed | 19 | 836.323 | 198,648 / 8,435 |
| constraints-scheduling | 1 | Submitted | 7 | 482.117 | 60,750 / 7,521 |
| nginx-request-logging | 1 | Submitted | 9 | 723.243 | 75,995 / 9,658 |
| kv-store-grpc | Not graded | Sandbox lost after model transport interruption | Not finalized | Not finalized | Partial trace only |

Completed baseline tasks report complete token accounting and zero supervisor calls
or interventions. Reported cached tokens are zero, but the endpoint did not expose
detailed prefix-cache accounting; this does not prove caching was unused.

A separate invoice-repair smoke passed: 7 turns, 72.944 seconds, 19,534 input and
860 output tokens. It is not an official benchmark task. Native tool-call roundtrips
validated a structured `report_sum({"value":5})` call followed by a successful tool
response continuation. Tests of the model protocol are separate from coding quality.

Development retry outcomes: pending. SWE-bench and DeepSWE: not measured.
No paired ablation or live evidence of supervisor benefit exists yet.

## Model choice

Selected **Qwen/Qwen3.8-27B-FP8**, pinned to revision
`017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`. The supervisor uses the same model
in a separate context unless explicitly configured otherwise.

The initial research favored Qwen over Muse Glimmer 30B based on published coding
results: Terminal-Bench 2.1 73.0 versus 51.7, and Qwen's reported DeepSWE 1.1 42.2.
These are vendor results with their own harnesses, not our measurements.
[Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B),
[Muse model card](https://huggingface.co/meta-models/Muse-Glimmer-30B),
[Meta methodology](https://research.meta.ai/static/muse-glimmer-methodology).

QwenSWEBench is not SWE-bench Verified. Published SWE-bench Pro numbers use different
corrections and are not directly comparable. Muse remains an untested alternative;
we have not established which model is best under this harness. The full research
record and serving references are in [model-selection.md](model-selection.md).

Architecture references include [Wink](https://arxiv.org/abs/2602.17037),
[SupervisorAgent](https://arxiv.org/abs/2510.26585),
[Shepherd](https://arxiv.org/abs/2605.10913), and
[mini-SWE-agent](https://mini-swe-agent.com/latest/). They motivate a small worker
loop and selective advice. Their published performance is not a guarantee here.

## Implemented architecture

The worker is the sole writer. It calls file and process tools sequentially, with a
complete tool batch finishing before supervisor advice enters the conversation.
Exact-match edits are atomic. Commands have deadlines and process-group ownership;
background tools support polling, stdin and explicit stop.

Context management trims complete exchanges while preserving the task, current
verification state and durable notes. Its conservative UTF-8 byte estimate avoids
assuming a provider tokenizer, at the cost of earlier trimming than may be necessary.
Provider token usage remains authoritative for accounting.

A deterministic monitor detects repeated actions/results without a changed workspace
revision. It is not a general semantic detector of all unproductive behavior. The
read-only supervisor receives bounded evidence, can recommend the next step, and
has no tools, recursion or independent write privileges. Advice is validated and
subject to a cooldown and a shared token budget. Modes are off, shadow and on.

Submission requires a successful check attached to the current content-and-mode
revision. Subsequent edits invalidate that check. This prevents stale verification;
it does not establish that the worker's chosen check matches the grader, as the
OpenSSL failure demonstrates.

Harbor keeps model calls, credentials and authoritative traces on the host. A
standard-library tool server executes inside each disposable task sandbox over a
Unix socket. Model/provider credentials are not uploaded into the sandbox. Local
shell execution retains host-user access and is not a security boundary. Docker is
the default isolated CLI path, but live local Docker execution remains unvalidated.

SWE-bench patch export uses a temporary Git index. DeepSWE supports an optional
committed-patch submission inside a disposable checkout. These integrations have
local contract coverage, not actual benchmark scores. Some historical SWE images
need a separate Python 3.11+ tool interpreter before the adapter can run safely
without changing the benchmark's own test environment.

Checkpoints contain diagnostic state, not an automatic-resume protocol. Transport
failures may follow executed or billed work, so the harness does not blindly repeat
model calls or tool actions. Unknown model usage is reserved and reported separately.

## Exact evaluation configuration

| Setting | Value |
| --- | --- |
| Model / revision | Qwen3.8-27B-FP8 / 017b9c7af6b5689d5dd426a76e0bc077eb5ca20a |
| Serving | vLLM 0.28.0; Transformers 5.16.1; CUDA 13.0.1 devel image; Python 3.12 |
| Accelerator | One H100; FP8; one sequence; text-only; eager execution |
| Parsers | qwen3_coder tools; qwen3 reasoning |
| Context / output allowance | 131,072 / 16,384 tokens per worker request |
| Sampling | Temperature 1.0; top_p .95; seed 0; top_k 20 from pinned generation config |
| Thinking / cache | Thinking enabled; reasoning history retained; prefix caching enabled |
| Worker limits | 80 turns; 2,000,000 input+output tokens; 840 seconds |
| Model / command deadline | 300 / 120 seconds, bounded by remaining worker allowance |
| Supervisor | On; maximum 3 interventions; cooldown 4 turns; repeat threshold 3; output 4,096 |
| SDK versions | Harbor 0.22.0 with Modal extra; Modal 1.5.5 |
| Trial control | One concurrent trial; one attempt; no automatic retries |
| Agent / setup / verifier / sandbox limits | 900 / 60 / 120 / 1,200 seconds |
| Build multiplier | .15 |
| Task sandbox | CPU only; workspace /app; delete after grading |
| Serving exposure | Maximum one GPU container; 300-second idle scale-down; one-hour client deadline |

FlashInfer sampler is disabled, GDN prefill uses Triton, and unused DeepGEMM warmup
is skipped. The startup fix pins FlashInfer cubin/JIT cache 0.6.16.post3. The vLLM
server is checked for early child exit during readiness. These choices prioritize a
working bounded deployment; no controlled performance claim is made for warmup skip.

Source checkpoints are `9400da6` (baseline), `0b523e6` (CUDA serving fix),
`d32397f` (warmup skip), and `f086809` (service lifetime, finalization, portable
verification prompt and duplicate trace recovery). Each run records a runtime source hash, model settings,
platform and task checksum. The runtime hash does not cover the entire adapter and
serving configuration, so retain Git checkpoints and config snapshots as well.
The first OpenSSL runtime hash is
`a6a881e0291165aef91ef3d80aa92fc57d7644400c059aec250fb3926f20d9d0`.

## Frozen sample and lineage

The sample was selected for category coverage before outcomes were observed. Tasks
were not replaced after failures. The TB 2.1 source is pinned at
`7131e4375048a0e408a8fb404b5f499d726b695b` in
`harbor-framework/terminal-bench-2-1`. Local task image tags were replaced with
immutable OCI digests, retained in `image-pins.json`. Per-trial task checksums record
these local pins. The legacy Harbor registry does not contain `terminal-bench@2.1`;
the pinned Git checkout is the source used here.

Four SWE candidates remain frozen and unrun: `astropy__astropy-7166`,
`django__django-9296`, `pytest-dev__pytest-5262`, and
`scikit-learn__scikit-learn-11310`. Their source and registry pins are recorded in
[sample-selection.md](sample-selection.md). No hidden grader or gold-solution
source was used to construct fixes or supplied to the solver. Post-run grader
failure diagnostics were used to identify the observed dependency problem.

## Failures, reproductions and decisions

**OpenSSL dependency mismatch.** The grader completed normally with reward 0 and
5/6 checks passing. The generated check_cert.py imported a package available in the
worker's environment but unavailable to the grader's Python entry point. The actual
generated script was replayed with `python -S`, reproducing ModuleNotFoundError
without site packages. This host replay explains the dependency failure; it is not
another official score or proof of the grader's exact interpreter setup.

The prompt change is task-independent: prefer existing dependencies/standard tools,
make added dependencies reproducible in the intended runtime, verify exact entry
points, preserve failure exit codes, and invoke Bash explicitly for Bash syntax.
The original failure stays visible. A retry after this change is development data,
not a new unseen first pass or proof of general improvement.

**Service lifetime.** A real local HTTP server through the actual Harbor adapter
was reachable during worker verification but stopped before a simulated grader
ran after Agent.run returned. A control deferring remote cleanup kept it reachable,
and teardown then killed and reaped it. The fix gives Harbor's disposable sandbox
ownership through grading, while local tools retain cleanup on exit. Background
service deadlines and remote-server lifetime must cover grading and remain bounded.

**Interrupted gRPC trial.** UTC trace timestamps jump from 05:12:47 to 06:40:49,
while monotonic elapsed time advances only about 87 seconds. This is consistent with
host sleep while cloud deadlines continued. The final model call failed with unknown
usage; the sandbox no longer existed when cleanup and download ran. The baseline
has no grader result. Retry processes use a bounded host-idle-sleep inhibitor.
Failure finalization and optional artifact transfer now ensure secondary
cleanup/download failures cannot erase the original outcome.

**Duplicate LangSmith export.** Re-exporting a persisted trace returned HTTP 409,
contradicting the previous upsert assumption. Stable IDs alone do not establish
complete ingestion. The exporter now reconciles every conflicting
span, preserve existing spans, and submit missing ones. A root-only success marker
must not hide a partial trace. Live re-export validation passed. A subsequent HTTP 429 was handled by pacing archival requests; no trace was reported complete on that failure.

Earlier infrastructure failures are retained: obsolete vLLM flag, missing runtime
CUDA compiler, reaped background client, Harbor import configuration, and missing
Dockerfile parser dependency. Setup-only attempts have no task score. Their costs
are still part of account usage.

## Latency and observability

The first OpenSSL worker spent 741.048 seconds in model request/response intervals,
24.658 seconds in tool start/result intervals, and 70.617 seconds outside those
pairs. Model intervals account for 88.6% of its 836.323-second worker duration.
These host intervals include transport and provider wait; they are not GPU profiling.
The full trial lasted 896.553 seconds, including setup and grading.

Its local trace has 141 ordered events, including 19 model calls, 19 tool actions,
19 checkpoints and explicit transport stages. JSONL events are flushed and fsynced;
results/checkpoints are atomically published. Full outputs remain available beyond
context truncation. Structured data redacts known secrets, but arbitrary raw task
artifacts remain private and require inspection before sharing.

LangSmith is an explicit optional export, not a runtime dependency. The first
OpenSSL root `dba2de09-94a5-5323-8485-c314e49d78b3` was retrieved with HTTP 200
in project `119cbcff-e72d-4a1c-bbfd-21541f728280`. Its grader reward 0 was attached
separately as benchmark_reward feedback, also HTTP 200. A submitted harness result
is never substituted for the official reward. Scheduling and Nginx traces are also persisted with HTTP 200 grader feedback. Their roots are `802e115a-77ce-506d-97d0-2a1ecfba5aea` and `603064e3-5115-5200-a01b-148a36464584`.

## Evidence index

All following paths are relative to the private run root
`.agent-runs/live-20260905` in this checkout. They are not committed or public.

| Evidence | Location |
| --- | --- |
| Aggregated outcomes from persisted results | outcomes.json |
| Baseline OpenSSL trial | jobs/tb21-openssl-smoke-02/openssl-selfsigned-cert__eYEX2Jf |
| Baseline scheduling | jobs/tb21-remaining-baseline-05/constraints-scheduling__FWWtaZb |
| Baseline Nginx | jobs/tb21-remaining-baseline-05/nginx-request-logging__5bSwoVn |
| Interrupted gRPC | jobs/tb21-remaining-baseline-05/kv-store-grpc__B4TA8pY |
| Per-trial worker trace | agent/harness/events.jsonl, result.json, outputs/, artifacts/ |
| Grader outcome | Each trial result.json and verifier/reward.txt |
| Dependency reproduction | dependency-reproduction/result.json |
| Service lifecycle reproduction | lifecycle-reproduction/evidence.json |
| Exact settings and image digests | agent.toml, controls-smoke.yaml, image-pins.json |
| Model protocol smoke | model-smoke-04/05/06.json and model-roundtrip-04/05/06.json |
| Remote trace receipts | langsmith-openssl-*.json and langsmith-receipts/ |
| Startup history | inference.log, inference-02.log through inference-06.log |
| Billing snapshots | billing-before.json, billing-resume-20260906.json, billing-pre-retry-20260906.json |

## Cost, validation and remaining work

Before session 06, Modal reported **$6.39 credits consumed**, **$0 billed**, metered
usage $7.87261309, and free-storage adjustment $1.48261309. These reconcile to the
credit draw. Against a $24 working ceiling, the remaining allowance was $17.61;
user authorization is $30 total. Billing can lag. These account totals include
startup and development work and cannot establish exact per-task costs.

The recorded H100 planning rate is $0.001097/second, about $3.95/hour, plus CPU,
RAM and sandbox extras. One-hour sessions and one GPU limit exposure but are not
an account-wide dollar cap. Final billing and zero-running-container evidence are
pending. Persistent cache and named credential secret will be retained.

At checkpoint `f086809`, 139 core tests passed with 11 optional SDK skips;
10 SDK contract tests passed separately. Ruff lint/format and strict mypy passed.
The Boolean-aware Radon check covered 203 functions with maximum complexity 10. CI is configured
for Python 3.11, 3.12 and 3.14 but has not run remotely. Live local Docker and actual
SWE/DeepSWE graders remain unvalidated.

Next measurements should use paired equal-budget controls: worker-only, adaptive
worker, fixed reminders and bounded supervision, followed by a separately frozen
holdout. The present sample can expose concrete harness defects; it cannot support
a broad superiority claim or estimate how much the supervisor improves solve rate.
