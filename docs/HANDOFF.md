# Copy-paste handoff

Continue the coding-agent project at `/Users/adhithyasash1/new-coding-agent`.
Snapshot: 2026-09-06, finalized for the requested handoff near 80% five-hour usage.
Latest source, validation, process and export status below comes from main's latest
handoff update, not an independent live recheck. User requested a subagent-prepared
handoff for a new session near 80%; the handoff prompt has been delivered in
commentary and this document is ready. No new session or
monitor was created. Recheck active work before launching anything.

## Immediate continuation

1. Continue observing the existing gRPC development retry, job
   `tb21-dev-kv-store-grpc-06`, wrapper PID47117, log
   `R/harbor-retry-kv-store-grpc-06.log`. Do not launch a duplicate.
2. Nginx export completed with HTTP200 after pacing; root
   `603064e3-5115-5200-a01b-148a36464584`, grader feedback HTTP200.
   OpenSSL repeat and scheduling exports also succeeded; retain their receipts.
3. OpenSSL development benchmark retry has NOT started. Its duplicate LangSmith
   export did succeed; these are separate activities.
4. Session06 serving is active, launched under caffeinate wrapper PID38091;
   native06 passed. Launcher timeout is 3600 seconds. Check remaining lifetime
   and spending before deciding whether to start the OpenSSL development retry.
5. Preserve baseline results: 2 passes, 1 failure, 1 infrastructure outcome.
   Record development retries separately, finish the report, then verify owned
   resource shutdown and final billing. Last billing was $6.39 before session06.

## User goal and authority

Build a simple, robust coding harness with one adaptive worker and a bounded
read-only supervisor. Test a small diverse benchmark sample, fix real issues,
retain honest first-pass results, and deliver a detailed observability report.
User authorizes $30 total Modal usage; working ceiling $24. No push. Commit at
checkpoints. No em dash or agent co-author. Reproduce bugs E2E before fixes.
Do not change global AGENTS.md or generated files. Prefer low complexity and
subagents for independent work. Do not expose hidden benchmark tests or solutions
to the solver, and do not replace failed tasks after observing outcomes.

## Current source and architecture

Current source commit: `f086809` (reported by main). Earlier commits:
`9400da6` baseline, `0b523e6` CUDA serving fix, `d32397f` unused DeepGEMM warmup skip.
Earlier snapshot: branch main, no remote. Check `git status` before editing;
ongoing report/doc edits may be uncommitted. Do not overwrite main's work.
This finalization updates only this handoff; no source changes or commits.

`src/coding_agent/runtime.py` is the sequential worker and shared budget loop.
`context.py` trims whole exchanges, preserving task and durable notes.
`monitor.py` detects repeated actions/results with unchanged revision; `supervisor.py`
provides bounded evidence-citing advice with no execution privileges. Modes off,
shadow, on. Worker is the only writer. Successful revision-bound verification gates
submission, but the official grader determines correctness.

`execution.py`/`toolset.py` manage commands, atomic file edits and subprocesses.
`remote_server.py` plus `integrations/harbor_agent.py` run tools inside Harbor's
sandbox while model calls and authoritative traces stay on the host.
`model.py` handles native tools/reasoning, usage and bounded same-origin HTTP 303
result polling. `trace.py` writes ordered, flushed events and diagnostic checkpoints;
checkpoints are not automatic resume. LangSmith export is explicit and omits raw
artifacts. SWE patch export and DeepSWE committed-patch support exist but have not
been validated through their actual benchmark graders.

## Model and configuration

Qwen/Qwen3.8-27B-FP8, revision `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
Selected over Muse Glimmer 30B based on coding evidence; see docs/model-selection.md.
Published vendor scores are not this harness's results.

vLLM 0.28.0, Transformers 5.16.1, CUDA 13 devel image, one H100, eager execution,
text-only, prefix caching, qwen3_coder tool parser and qwen3 reasoning parser.
FlashInfer sampler disabled, GDN prefill Triton, DeepGEMM warmup skip. The old slim
image crash-looped without nvcc; those costs and logs are retained.

Run root `R=.agent-runs/live-20260905`. `R/agent.toml`: 131072 context,
16384 output, temperature 1, top_p .95, seed 0; 80 turns, 2M raw tokens,
840 seconds; model timeout 300, command timeout 120; supervision on, max3,
cooldown4, supervisor output4096. `R/controls-smoke.yaml`: one trial at a time,
no retries; agent900/setup60/verifier120/sandbox1200 seconds, build multiplier .15.
These shortened limits define a development smoke, not a leaderboard run.

## Current results and work in flight

- Native model/tool roundtrips 04, 05 and 06 passed.
- Repair smoke04 passed: 7 turns, 72.944s, 19534 input/860 output, no supervisor.
- OpenSSL first completed trial `tb21-openssl-smoke-02/openssl-selfsigned-cert__eYEX2Jf`:
  reward0,5/6 grader checks. 19 turns,836.323s,198648 input/8435 output,0 supervisor.
  Generated check_cert.py imported cryptography installed only in the worker's
  environment; grader could not import it. Actual deliverable replay with python -S
  reproduced this in `R/dependency-reproduction/result.json`.
- Local results for `tb21-remaining-baseline-05`: scheduling task
  `constraints-scheduling__FWWtaZb` and Nginx `nginx-request-logging__5bSwoVn`
  have reward1. gRPC `kv-store-grpc__B4TA8pY` has exception_info and no verifier
  reward (finished_at 2026-09-06T06:40:54.770560Z). Do not count it as a measured
  reward0 or a pass. Baseline total: 2 passes, 1 failure, 1 infrastructure outcome.
  Development retry `tb21-dev-kv-store-grpc-06` is running under wrapper PID47117;
  log `R/harbor-retry-kv-store-grpc-06.log`. No retry result is known yet.
- Preserve original baseline logs and source provenance. Main owns subsequent
  adapter/prompt changes and development retries. Do not modify source during an
  active trial, because per-run source hashes are collected from disk.
- OpenSSL development benchmark retry has NOT started. Keep any subsequent run
  labeled as development and preserve the original failure. No benchmark-specific fixes.
- Earlier lifecycle investigation evidence: `R/lifecycle-reproduction/`, subagent
  `01a0750d-532a-7611-8f4d-c9aaf7547b87`. This is historical context, not a claim
  that the investigation is still running. Consult source commit `f086809` and
  main's notes for the integrated adapter/prompt behavior.
- `docs/EVALUATION_REPORT.md` is an IN PROGRESS draft. Main execution owner must
  finalize task results, validations, costs, shutdown state and this handoff.
- SWE-bench four frozen candidates and DeepSWE remain unmeasured. See
  docs/sample-selection.md for pins and Python3.11 tool-runtime readiness limits.

## Secrets and observability

Credential file PATH ONLY: `/private/tmp/coding-agent-cloud-credentials.json`, mode0600.
Keys MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, LANGSMITH_API_KEY, AGENT_API_KEY.
Never print values or put them in arguments, docs, Git or task sandboxes.
`R/with_credentials.py KEY... -- command` privately exports only named keys to a child.

First OpenSSL trace is persisted in LangSmith: trace
`dba2de09-94a5-5323-8485-c314e49d78b3`, project
`119cbcff-e72d-4a1c-bbfd-21541f728280`. Grader reward0 was attached separately as
benchmark_reward feedback with HTTP200. Receipts are in R/langsmith-openssl-*.json.
Export remaining completed traces and attach their actual rewards without altering
local trace files. Grader outcome is separate from harness submission success.

Exporter fix implemented and exercised live by main: integrations/langsmith_export.py and
tests/test_langsmith_export.py. The reported duplicate returned HTTP409, disproving
the previous unconditional upsert claim. The stateful mocked HTTP reproduction
failed before the fix for root-only, partial, and complete prior ingestion.
The exporter retains one batch request on success. On batch409 it submits spans
individually; each singleton409 requires GET /runs/{id} to confirm id, trace_id,
dotted_order and parent_run_id. Missing children cannot be hidden by an existing
root. Existing spans are left unchanged; this is repeat export of finalized logs,
not an update mechanism for edited logs. Recovery is bounded, with no local marker
or automatic retry loop. Unreadable/mismatched conflicts fail safely; main can
retry later using the same IDs. New HTTP2xx ingestion remains queued, not verified
persistence. This trades additional requests on conflicts for simple recovery.

Latest live export status reported by main:
- OpenSSL repeat export succeeded for `dba2de09-94a5-5323-8485-c314e49d78b3`.
- Scheduling root `802e115a-77ce-506d-97d0-2a1ecfba5aea` exported;
  grader feedback returned HTTP200.
- Nginx initially hit HTTP429 midway through reconciliation. With helper
  `PacedTransport` applying one-second request delays, its retry completed HTTP200:
  root `603064e3-5115-5200-a01b-148a36464584`, grader feedback HTTP200.
  Retain pacing and receipts; recover partial ingestion with the same IDs.

Use `R/export_completed.py` with the private credential helper. Collect existing
retry results before starting another export. Verify persistence and actual grader
feedback separately; do not invent a gRPC reward. The script skips existing receipt
files. No claim is made here that every remote child has been independently checked.
Official contracts consulted: https://docs.langchain.com/langsmith/smith-api/runs/ingest-runs-batch-json
and https://docs.langchain.com/langsmith/smith-api/run/read-run.

## Live resources and spending

Last reported billing: $6.39 before session06. Session06 spend is additional;
billing may lag. Against the $24 working ceiling that left $17.61 before session06,
not a current remaining balance. Recheck before more launches.

Session06 app `ap-8JFsctTXtfRdSKYQiCMZq4` is active, launched under caffeinate
wrapper PID38091. Native06 passed. Created 2026-09-06 at 17:47:12 +0530
(12:17:12 UTC); launch script timeout3600 gives an approximate deadline of
13:17 UTC (18:47 +0530). Latest main-confirmed task counts: serving app1,
evaluation app1. Recheck remaining lifetime and owned resources before acting.
gRPC retry job `tb21-dev-kv-store-grpc-06` is running under wrapper PID47117,
log `R/harbor-retry-kv-store-grpc-06.log`. OpenSSL benchmark retry is not started.
Verify process identity before acting on either PID.

Earlier evaluation app ID: `ap-PMlnDl7NOv6q8IUzuMMVp9`; recheck current state.
Cache volume adaptive-agent-model-cache and secret adaptive-agent-inference are
persistent. No shutdown or zero-running-container confirmation is recorded yet.

Read status using the credential helper with only Modal token keys:
`.venv-eval/bin/python -m modal app list --json` and `modal container list --json`.
Stop ONLY owned inference apps with `modal app stop APP_ID --yes`; stop/cancel an
active Harbor job before removing its sandbox. Confirm zero running containers
and save final billing. Retain model cache and credential secret unless instructed
otherwise. A zero-task deployed app consumes no running GPU but is still a resource.

For an authorized fresh session after status/budget checks, re-copy source to
`R/inference/modal_inference.py`, then use `R/detach.py` around the credential helper
and `R/launch.py FRESH_LOG_NAME`. The detached client is necessary in this tool host;
ordinary background children were reaped. The launcher supplies public model settings
and a 3600-second deadline. Warm it with `R/smoke.py FRESH_LABEL` using only
AGENT_API_KEY. No duplicate inference app. Preserve all existing logs.

## Validation

Main environment `.venv`, optional evaluation SDKs `.venv-eval`.
Latest main-reported validation for source commit `f086809`:
- Core suite: 139 passed, 11 optional skips.
- SDK suite: 10 passed.
- Mypy, Ruff formatting and lint passed.
- Complexity check passed: 203 functions, maximum complexity 10.

Earlier scoped exporter validation: 74 passed, 1 Harbor-dependent skip in .venv;
scoped lint/format passed, recovery helper complexities 6 and 5. Coverage includes
repeat CLI export, copied directories, partial ingestion, mid-recovery failures,
singleton traces, workspace headers, malformed/mismatched reads and safe errors.
The later full-suite totals above supersede prior validation totals. This doc-only
finalization did not rerun tests. Native06 and OpenSSL repeat export passed, but
gRPC development retry remains pending. Nginx export and feedback completed HTTP200
after pacing. OpenSSL
benchmark retry has not started. Core E2E tests need local socket and temporary Git
permissions; sandbox-only failures were spurious. Use
`UV_CACHE_DIR=/private/tmp/coding-agent-uv`.

Commands: `.venv/bin/pytest -q`, `.venv/bin/ruff check .`,
`.venv/bin/ruff format --check .`, `.venv/bin/mypy`,
`.venv/bin/python scripts/check_complexity.py`, and
`.venv-eval/bin/python -m unittest discover -s tests -p test_integrations.py -v`.
Harbor needs `harbor[modal]==0.22.0`, Modal1.5.5, and PYTHONPATH set to repo root.
