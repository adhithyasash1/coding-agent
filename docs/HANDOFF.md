# Copy-paste handoff

Continue the coding-agent project at `/Users/adhithyasash1/new-coding-agent`.
Snapshot: 2026-09-07 after session09 shutdown. Earlier sections retain
historical outcomes; the Immediate continuation section records the completed
work. Modal resources, billing, saved results, and local traces were rechecked.
Do not launch another paid evaluation without a new budget review.

## Immediate continuation - 2026-09-07 session 09

1. The frozen `swe-dev-remaining-08` job completed unchanged at source
   `666dc37`. Astropy, pytest, and scikit-learn each ended as `model_error` at
   the 840-second wall with no verifier result. Preserve these original trial
   directories and partial traces; do not replace them with retries.
2. The deadline candidate and classification fix are committed at
   `12abd2b3a4ce4659ea84ad0532423fe4b6086eef`. Global deadline failures now
   become `budget_exhausted` after preserving failure and unknown-token evidence.
   Earlier transport and response failures remain `model_error`.
3. Django label `swe-dev-deadline-django-10` graded reward 1.0 after wall
   exhaustion. Terminal-Bench label `tb21-dev-kv-store-grpc-09` graded reward
   1.0 and preserved its running service through grading. Astropy label
   `swe-dev-deadline-astropy-11` graded reward 0.0; its patch applied, but
   `test_inherit_docstrings` still failed.
4. Pier 0.3.1 is pinned at `R/pier-source` commit
   `df89f994623a0a6a57229103b6fe910766693c30` with uv.lock SHA256
   `6261c632e80ee65e327c23f450c7cc5c38e34f2ee941d3ebe96ff72fc91f6de9`.
   The separate `.venv-pier` uses Harbor 0.5.0 and Modal 1.4.2. Eight Pier
   adapter checks and the original 13 Harbor SDK checks pass.
5. DeepSWE task `abs-module-cache-flags` was predeclared from dataset commit
   `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea`. Label 01 failed before setup due
   a host import path; label 02 ran under the corrected path but the inference
   launcher expired during a model request, so Pier recorded `model_error` with
   no verifier score or commit receipt. Keep both labels and the local partial
   workspace as evidence. No hidden tests or reference solution were supplied
   to the solver.
6. Final credentialed billing is $20.38 credits consumed, $22.18164059 metered,
   $0 billed, with $1.80164059 free storage. The final container listing is
   empty. Inference apps and both deployed evaluation apps are stopped. Billing
   can lag slightly; see `R/billing-final-session09.json` and
   `R/shutdown-session09.json`.
7. The paced LangSmith exporter remains pending explicit user approval because
   it sends model messages and workspace-derived traces to an external service.
   Local traces and grader reports are preserved. Do not launch another paid
   job until export approval and a fresh reserve check are complete.

## User goal and authority

Build a simple, robust coding harness with one adaptive worker and a bounded
read-only supervisor. Test a small diverse sample, fix real issues, keep honest
first-pass results, and keep a detailed observability report. $30 Modal
authorized; working ceiling $24. No push. Commit at checkpoints. Reproduce bugs
E2E before fixes. No hidden tests or gold solutions to the solver. No hardcoded
benchmark answers.

## Current source and architecture

Current source commit: `12abd2b` (deadline context, global deadline
classification, create-only `edit_file`, private tool interpreter, and Pier
adapter validation). Earlier: `c70d93c` create-only `edit_file` and private tool
interpreter; `f086809` portable verification prompt and export recovery,
`d32397f` warmup skip, `0b523e6` CUDA serving, `9400da6` baseline. Branch main,
no remote.

Worker is the only writer. Harbor tool server runs in the sandbox; model calls
and traces stay on the host. If image `python3` is older than 3.11, setup may
install a private CPython under `/tmp/ca-*` with uv and use it only for the tool
server. Worker `run_command` still uses image `python3`. DeepSWE
`commit_patch` is validated locally through the pinned Pier adapter, but the
paid smoke did not reach committed-patch grading.

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
- Session08 frozen SWE batch: three 840-second `model_error` outcomes with no
  verifier result; original traces and unknown reservations retained.
- Deadline Django: reward 1.0 after `budget_exhausted`; one reminder and a
  preserved global-deadline failure event.
- Terminal-Bench gRPC retry: reward 1.0, service preserved through grading.
- Astropy post-deadline check: reward 0.0; patch applied, one F2P test failed.
- DeepSWE `abs-module-cache-flags`: label 01 preflight import failure; label
  02 model_error at inference expiry, no score or commit receipt.
- Outcomes: `R/outcomes.json`. Do not overwrite baseline trial directories.

## Secrets and observability

Credential file PATH ONLY: `/private/tmp/coding-agent-cloud-credentials.json`,
mode 0600. Keys MODAL_TOKEN_ID, MODAL_TOKEN_SECRET, LANGSMITH_API_KEY,
AGENT_API_KEY. Use `R/with_credentials.py KEY... -- command`. Never print values.

LangSmith project `119cbcff-e72d-4a1c-bbfd-21541f728280`. The existing paced
exporter skips existing receipt files, but the new export is pending explicit
approval because it sends model messages and workspace-derived traces to an
external service. Ambient `modal billing` without the credential helper hit a
**different** workspace; always wrap billing reads.

## Live resources and spending

Final credentialed billing: **$20.38 credits**, metered $22.18164059, billed $0,
with $1.80164059 free-storage adjustment. The $24 working ceiling leaves
about **$3.62**. Billing may report a small lag.

H100 $3.95/hour. Persistent cache `adaptive-agent-model-cache` and secret
`adaptive-agent-inference` are retained. All owned inference and evaluation
apps are stopped, and the final credentialed container listing is empty.

For a fresh session: copy `integrations/modal_inference.py` to
`R/inference/modal_inference.py`, then `R/detach.py` around the credential
helper and `R/launch.py FRESH_LOG_NAME`. Warm with `R/smoke.py FRESH_LABEL`.
No duplicate inference app. SWE launch helper: `R/run_swe.py LABEL`.
TB retry helper: `R/run_retry.py LABEL TASK`.

## Validation

`.venv` core tools; `.venv-eval` Harbor/Modal; `.venv-pier` Pier 0.3.1,
Harbor 0.5.0, Modal 1.4.2. At `12abd2b`: focused deadline/model/runtime tests
51 passed, full core pytest 150 passed with 22 optional skips, ruff and mypy
passed, Pier checks 8 passed, original Harbor SDK checks 13 passed, and
`git diff --check` passed. The isolated deadline candidate's selected tests
also passed. Full local checks used the required socket and temporary Git
permissions. `UV_CACHE_DIR=/private/tmp/coding-agent-uv`.
