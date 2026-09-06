# Copy-paste handoff

Continue the coding-agent project at `/Users/adhithyasash1/new-coding-agent`.
Snapshot: 2026-09-06 after TB development retries, harness fixes, and one SWE
smoke. Recheck processes, Modal resources, billing, and saved results before
launching anything. Do not duplicate running evaluations.

## Immediate continuation

1. Nothing owned is running. Inference apps `ap-8JFsctTXtfRdSKYQiCMZq4` and
   `ap-k0ymAHULxsb1CVgMcNmztE` are stopped. Probe app `ap-au1v3WVdyvSkR7fHRvGcsg`
   is stopped. Eval app `ap-PMlnDl7NOv6q8IUzuMMVp9` is deployed with zero tasks.
   Last container list was empty after session07 stop. Confirm before a new GPU.
2. Preserve first-pass TB results: OpenSSL reward 0, scheduling 1, nginx 1,
   gRPC NotFoundError with no grader reward. Development retries are separate:
   `tb21-dev-kv-store-grpc-06` reward 1, `tb21-dev-openssl-selfsigned-cert-06`
   reward 1. SWE smoke `swe-dev-django-9296-07` reward 1 after budget_exhausted.
3. LangSmith receipts exist for TB development retries and the SWE django smoke
   (`149a93a0-2303-50f4-8b9f-30d33d6c6640`, feedback HTTP 200). The interrupted
   gRPC baseline has no harness result and was not exported as a graded run.
4. Next paid work, if budget remains: remaining frozen SWE IDs, or a pinned
   Pier/DeepSWE smoke in a **separate** env. Do not assume the supervisor helps;
   it was never called. Do not replace frozen tasks after seeing outcomes.

## User goal and authority

Build a simple, robust coding harness with one adaptive worker and a bounded
read-only supervisor. Test a small diverse sample, fix real issues, keep honest
first-pass results, and keep a detailed observability report. $30 Modal
authorized; working ceiling $24. No push. Commit at checkpoints. Reproduce bugs
E2E before fixes. No hidden tests or gold solutions to the solver. No hardcoded
benchmark answers.

## Current source and architecture

Current source commit: `c70d93c` (create-only `edit_file`, private tool
interpreter). Docs commits include `f8ba1c8`. Earlier: `f086809` portable
verification prompt and export recovery, `d32397f` warmup skip, `0b523e6` CUDA
serving, `9400da6` baseline. Branch main, no remote.

Worker is the only writer. Harbor tool server runs in the sandbox; model calls
and traces stay on the host. If image `python3` is older than 3.11, setup may
install a private CPython under `/tmp/ca-*` with uv and use it only for the tool
server. Worker `run_command` still uses image `python3`. DeepSWE `commit_patch`
is local-only until Pier is pinned.

## Model and configuration

Qwen/Qwen3.8-27B-FP8, revision `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
vLLM 0.28.0, one H100, qwen3_coder and qwen3 parsers. Run root
`R=.agent-runs/live-20260905`. `R/agent.toml` unchanged: 80 turns, 2M tokens,
840 seconds, supervision on. TB jobs use `R/controls-smoke.yaml`. SWE smoke used
`R/controls-swe-smoke.yaml` (workspace `/testbed`, setup 180s, build multiplier
0.5). Those SWE timeouts are plumbing, not official limits.

## Results already on disk

- Native smokes 04-07 passed.
- TB baseline: 2 pass, 1 fail, 1 infrastructure.
- TB development: gRPC 23 turns, 297792/10881 tokens, reward 1, 0 supervisor.
  OpenSSL 12 turns, 80019/4529 tokens, reward 1, stdlib/OpenSSL, 0 supervisor.
- SWE django: 16 turns, 279247/7194 tokens, budget_exhausted, no
  `verified_revision`, grader reward 1. Image python 3.11.5 so sidecar unused.
- Outcomes: `R/outcomes.json`. Do not overwrite baseline trial directories.

## Secrets and observability

Credential file PATH ONLY: `/private/tmp/coding-agent-cloud-credentials.json`,
mode 0600. Keys MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, LANGSMITH_API_KEY,
AGENT_API_KEY. Use `R/with_credentials.py KEY... -- command`. Never print values.

LangSmith project `119cbcff-e72d-4a1c-bbfd-21541f728280`. Use
`R/export_completed.py` with PYTHONPATH set to the repo root. It skips existing
receipt files. PacedTransport is required. Ambient `modal billing` without the
credential helper hit a **different** workspace; always wrap billing reads.

## Live resources and spending

Last credentialed billing: **$11.32 credits**, metered $12.80261309, billed $0.
Working ceiling $24 leaves about **$12.68**. Authorization $30.

H100 $3.95/hour. Persistent cache `adaptive-agent-model-cache` and secret
`adaptive-agent-inference` are retained. Stop only owned inference with
`modal app stop APP_ID --yes`. Confirm zero running containers. A zero-task
deployed eval app is still a named resource but not a running GPU.

For a fresh session: copy `integrations/modal_inference.py` to
`R/inference/modal_inference.py`, then `R/detach.py` around the credential
helper and `R/launch.py FRESH_LOG_NAME`. Warm with `R/smoke.py FRESH_LABEL`.
No duplicate inference app. SWE launch helper: `R/run_swe.py LABEL`.
TB retry helper: `R/run_retry.py LABEL TASK`.

## Validation

`.venv` core tools; `.venv-eval` Harbor/Modal. At `c70d93c`: ruff, mypy,
complexity 205 / max 10; Harbor SDK 13 passed; create-without-old and sidecar
unit tests passed. Full pytest needs local socket and temporary Git permissions.
`UV_CACHE_DIR=/private/tmp/coding-agent-uv`.
