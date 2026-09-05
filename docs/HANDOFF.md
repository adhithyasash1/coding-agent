# Copy-paste context handoff

Continue the scratch-built coding-agent project in `/Users/adhithyasash1/new-coding-agent`. Snapshot: 2026-09-05, based on repository source/docs, public live-run metadata/logs, and the user's latest status. **Live process state expires: recheck ownership, processes, app status, endpoint readiness, and remaining spend before launching anything.** This document is context, not evidence that a service is still running.

## Goal, authority, and ownership

- Build and validate a robust, model-independent coding harness: one adaptive worker plus a bounded supervisor. Run honest, small official benchmark evaluations, diagnose failures from traces, and compare supervision fairly. This is a new implementation; old/reference harness scores are not its results.
- User authorizes up to **$30 total Modal credit consumption**, including inference, cold starts, failed launches/builds, sandboxes, CPU/RAM, idle time, and storage. Use a **$24 working ceiling** to leave margin. Older README/modal docs mentioning $20 or no cloud launches are stale. A one-hour CLI timeout is not a dollar cap; reconcile aggregate spend with billing, which can lag. Do not assume the entire allowance remains.
- Other agents own task selection (`docs/sample-selection.md`) and the LangSmith exporter (`integrations/langsmith_export.py`, related tests). Coordinate before overlapping edits. The main agent owns the final report. This handoff writer owns **only `docs/HANDOFF.md`**, makes no cloud calls/mutations, and refreshes this file on request. Do not interpret this document as permission for the handoff writer to launch work.
- Preserve concurrent/uncommitted work. The inspected Git status showed the project files untracked; do not reset, clean, or assume a committed baseline. README reports no Git remote.
- Follow user instructions: no em dash, no automatic agent co-author, no manual CHANGELOG/generated-file edits. Favor quality, simplicity, robustness, scalability, and maintainability. Reproduce bugs end-to-end as a user would before fixing; repair observed UI defects, lint/test failures, and flakiness within assigned ownership. Personal configuration changes require the separate evidence and explicit-approval process; do not change AGENTS.md here.

## Secrets: metadata only

Secret file PATH ONLY: `/private/tmp/coding-agent-cloud-credentials.json`; required mode **0600**; keys **MODAL_TOKEN_ID**, **MODAL_TOKEN_SECRET**, **LANGSMITH_API_KEY**, **AGENT_API_KEY**. No secret values belong in prompts, docs, logs, arguments, commits, traces, or task sandboxes. This handoff did not read the file or any private credential contents. Public helper source references it; do not print it. Future authorized execution must load credentials privately into only the process that needs them. Commands below assume that secure environment setup has already occurred; they deliberately contain no secret-loading snippet.

## Architecture and code map

- `src/coding_agent/runtime.py`: sequential worker loop, complete tool-call batches, shared finite budgets. `context.py`: preserve task/notes and trim whole exchanges. `monitor.py`: deterministic repeated-action/no-progress signals. `supervisor.py`: separate evidence-based advisory context, no execution privileges, cooldown and bounded calls (default three), stale/invalid advice ignored. Worker is the sole writer. Modes: `off`, default `shadow`, `on`.
- `toolset.py`, `execution.py`, `sandbox.py`, `remote_server.py`, `tool_server.py`: exact atomic edits, revision-bound verification, command/process deadlines, stdin/polling, cleanup, Docker and external sandbox transport. `submit` requires successful verification of the current content/mode revision; it does not establish benchmark correctness.
- `model.py`, `config.py`, `types.py`: OpenAI-compatible endpoint, model/revision and sampling, typed contracts, usage/budgets. Worker and supervisor share the allowance. `trace.py`, `provenance.py`, `cli.py`: durable local evidence and inspection.
- `integrations/harbor_agent.py`: host-side model/trace with tool server in task sandbox; optional committed-patch submission for DeepSWE. `scripts/export_swe_predictions.py`: prediction export using a temporary Git index. `integrations/modal_inference.py`: single-GPU inference, separate from CPU task sandboxes.
- No safe automatic resume, PTY, recursive supervisors, candidate branching, or training. Checkpoints are diagnostic; inspect incomplete operations and workspace state before starting a fresh run.

## Model and live inference snapshot

Selected after comparing Muse Glimmer 30B: **Qwen/Qwen3.8-27B-FP8**, immutable revision **017b9c7af6b5689d5dd426a76e0bc077eb5ca20a**, for worker and separately prompted supervisor. See `docs/model-selection.md` for published evidence and comparability caveats. Published numbers are not our measurements.

Settings: vLLM **0.28.0**, Transformers **5.16.1**, one H100, text-only, prefix caching, eager execution, one sequence/request; context **131072**, worker output up to **16384**; thinking/history preserved, temperature 1.0, top_p 0.95, pinned generation config top_k 20. Environment:

```sh
cd /Users/adhithyasash1/new-coding-agent
export AGENT_MODEL_NAME='Qwen/Qwen3.8-27B-FP8'
export AGENT_MODEL_REVISION='017b9c7af6b5689d5dd426a76e0bc077eb5ca20a'
export AGENT_TOOL_PARSER='qwen3_coder'
export AGENT_REASONING_PARSER='qwen3'
export AGENT_GPU='H100'
export AGENT_CONTEXT_TOKENS='131072'
```

Public run root: `.agent-runs/live-20260905/`.

- `session.json`: created `2026-09-05T17:28:14.499262+00:00`; 3600-second session, $24/$30 ceilings, recorded H100 rate $0.001097/sec (about $3.95/hour, excluding extras). `billing-before.json` is only a historical baseline, not current spend or balance.
- First launch `inference.log`, app `ap-oZy1ckQqDhzwR5vOznuFRV`: vLLM rejected `--disable-log-requests`; log confirms CLI stop. That obsolete argument was removed. Current source also detects a crashed child promptly instead of waiting the full startup timeout. Verify the fix against the real cold boot before declaring success.
- **Second launch was reported active by the user**, log `inference-02.log`, app **ap-Ooqify4gtlimR5loaC5SrE**. Public log confirms app/web-function creation, not model readiness. Dashboard: <https://modal.com/apps/sashiradhithya/main/ap-Ooqify4gtlimR5loaC5SrE>. Dev endpoint: <https://sashiradhithya--adaptive-coding-agent-inference-serve-dev.modal.run>; client base URL adds `/v1`.
- `launch.py` executes the isolated snapshot `inference/modal_inference.py` using `.venv-eval/bin/python -m modal serve ... --timeout 3600`. It exclusively creates `inference-02.log`; rerunning it unchanged fails if that file exists. Preserve logs, check for an existing live app, and use a fresh launch record if a later launch is needed. Verify snapshot/source agreement.
- `agent.toml` is the live config path (contents not inspected for this handoff). `smoke.py` checks authenticated model listing, native `report_sum` tool-call arguments, then the tool-result round trip. Intended outputs: `models.json`, `model-smoke-02.json`, `model-roundtrip-02.json`. Their success is **not established** by this snapshot.
- Inference app name `adaptive-coding-agent-inference`; volume `adaptive-agent-model-cache` mounted at `/root/.cache/huggingface`; named secret `adaptive-agent-inference` exposes `VLLM_API_KEY` to the inference container. Max one container, min zero, 30-second scaledown, function/startup timeout 600 seconds, CPU max 4, RAM max 65536 MiB. Persistent volume storage can continue after app shutdown. No benchmark sandboxes have been confirmed here.

For the execution owner only, after checking current state/budget, an eventual fresh foreground launch uses:

```sh
.venv-eval/bin/python -m modal serve .agent-runs/live-20260905/inference/modal_inference.py --timeout 3600
```

Shutdown: Ctrl-C in the owning foreground serve session, or explicitly stop the verified live app. Installed Modal 1.5.5 CLI source supports these commands (not executed by this handoff writer):

```sh
.venv-eval/bin/modal app list --json
.venv-eval/bin/modal app stop ap-Ooqify4gtlimR5loaC5SrE
.venv-eval/bin/modal app list --json
```

Verify shutdown in the dashboard and account for any separately created evaluation sandboxes. Do not delete the cache volume or secret merely to stop compute, and do not start a permanent deployment.

## Local commands and validation status

Python 3.11+; local validation previously used Python 3.12.13. Core environment `.venv`; evaluation SDK environment `.venv-eval` contains Harbor **0.22.0** and Modal **1.5.5**. Keep a future pinned Pier install separate from Harbor. From the repository root:

```sh
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/check_complexity.py
.venv-eval/bin/python -m unittest discover -s tests -p test_integrations.py -v
uv run python scripts/demo.py
```

Latest **user-reported** core result: **87 passed, 4 skipped**. Latest four SDK tests are **pending**, including the failed-inference-child startup regression; their presence is not a passing result. Older `docs/validation.md` counts (39 core / 3 SDK) are stale. Historical lint/format, strict mypy, Radon maximum complexity 10, packaging, offline reproduction/edit/verify/submit demo, HTTP/subprocess/transport/trace tests passed as documented; revalidate affected checks after concurrent changes. No tests were rerun by the handoff writer. Local Docker daemon/live container validation was previously unavailable; recheck before use.

## Remaining execution and evaluation work

1. Reconcile live app ownership, cold-boot progress, budget, and shutdown deadline. Finish native model/tool round-trip validation and an actual reproduce-fix-verify smoke task before benchmarks. Preserve first failed launch evidence and label reruns. Run the four SDK tests against the latest changes.
2. Read the latest `docs/sample-selection.md`, owned by the selection agent, for exact pinned commands and readiness caveats. Frozen SWE-bench Verified IDs: `astropy__astropy-7166`, `django__django-9296`, `pytest-dev__pytest-5262`, `scikit-learn__scikit-learn-11310`. Frozen Terminal-Bench 2.1 IDs: `kv-store-grpc`, `nginx-request-logging`, `openssl-selfsigned-cert`, `constraints-scheduling`. First paid benchmark smoke is predeclared as `openssl-selfsigned-cert` only; inspect billing before expanding.
3. TB Git revision `7131e4375048a0e408a8fb404b5f499d726b695b`; SWE task source `86723674f04e4209ac479d0fb75d9d9f44b4377e`, registry `5c364a538e0af19eb58a53fdb895d7c0f974cef5`. Do not use the stale `terminal-bench@2.1` legacy registry example. Use explicit `--include-task-name` filters; `--print-config` proves parsing only. `/tmp/sample-controls.yaml` in that document is a proposed path, not confirmed created. Align its model-config path with the actual live config.
4. Concurrency one, zero automatic trial retries, bounded disposable Modal sandboxes. Documented smoke controls use 840-second harness, 900-second agent, 120-second verifier, 1200-second sandbox caps. These altered limits cannot produce an unqualified official leaderboard score. Resolve image digests and repository base commits; confirm Python 3.11+ for the tool server while preserving task test environments, especially historical SWE images. Nginx needs sandbox-local access beyond `/app`. Validate actual setup, grading and cleanup, not just CLI parsing.
5. DeepSWE means Datacurve DeepSWE 1.1, not the training project. Pin/review Pier in its own environment and verify committed-patch grading, receipt/export success, and backend task compatibility before claims.
6. **No benchmarks have run yet. No measured solve rates or fake scores.** Keep hidden tests, gold patches and reference solutions out of solver context. Keep first-pass outcomes, infrastructure failures, incomplete grading, development reruns, and benchmark families separate. A submitted patch, synthetic demo token count, or tool-call smoke is not benchmark success.
7. Compare off/shadow/on under equal task/model/revision/seed/environment/total budgets; count supervision costs. Include minimal/adaptive baselines and fixed-reminder control before attributing gains to reasoning. Use paired repeats, uncertainty, correctness/regressions, stop reasons, tokens, wall time and infrastructure failures. Freeze settings before a separate holdout. The eight selected tasks are purposeful development coverage, not representative performance evidence.

## Evidence, tracing, and report locations

Read `docs/architecture.md`, `docs/evaluations.md`, `docs/tracing.md`, `docs/model-selection.md`, `docs/sample-selection.md`, and `docs/validation.md`, applying the freshness caveats above. Local traces are authoritative: `events.jsonl`, `outputs/`, `artifacts/checkpoint-NNNN.json`, `result.json` with separate grader result. Inspect with `uv run coding-agent inspect /absolute/path/to/run --events`. Historical offline demo: `.agent-runs/demo-56880767/trace`. Harbor trial traces/artifacts live under each trial's `harness/`, with `submission.json` for committed-patch receipts. Preserve full raw evidence privately; outputs can contain task data.

LangSmith exporter and tests exist but are concurrently owned; confirm their final status with that agent. Intended invocation is `uv run python -m integrations.langsmith_export /absolute/path/to/finalized-run --project PROJECT`, using privately supplied credentials. Do not infer an upload or report exists from source presence. `docs/tracing.md` may still describe the exporter as future work. No final evaluation report path or live LangSmith URL was established by this handoff; the **main agent owns the final report** and should record verified locations/results there. Do not manufacture missing results or links.
