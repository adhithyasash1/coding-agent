# Copy-paste handoff

Continue the coding-agent project at `/Users/adhithyasash1/new-coding-agent`.
Snapshot: 2026-09-07 with session08 SWE batch active. Earlier sections retain
historical outcomes; the Immediate continuation section takes precedence. Recheck processes, Modal resources, billing, and saved results before
launching anything. Do not duplicate running evaluations.

## Immediate continuation - 2026-09-07 session 08

1. ACTIVE job `swe-dev-remaining-08`, wrapper PID92971, log
   `R/harbor-swe-remaining-08.log`. Three remaining frozen SWE IDs, sequential,
   unchanged existing source at `666dc37`. Astropy started and is inspecting code.
   Do not duplicate the job or edit live runtime source mid-batch.
2. ACTIVE inference app `ap-X36M2zlK7EiRwG0RXjVPCJ`, wrapper PID89962,
   log `R/inference-08.log`. Native08 tool smoke passed. One-hour launcher limit,
   roughly 14:08 UTC September 7. Check actual remaining lifetime before more work.
   Both wrappers use bounded caffeinate. Evaluation app is unchanged.
3. Latest saved billing `R/billing-session08-start.json`: credits $11.69,
   metered $13.49164059, free storage $1.80164059, billed $0. Working ceiling $24.
   Session08 usage after this snapshot is additional. Always credential-wrap reads.
4. Deadline weakness reproduced from Django worker trace: no remaining seconds in
   context; successful behavioral checks at ~710s lacked verify=true; test-runner
   troubleshooting exhausted840s. Candidate is ISOLATED under
   `R/deadline-candidate/`, NOT APPLIED. Read VALIDATION.md and patch there.
   66 selected tests passed in isolated copy; review and apply after batch, then
   full tests and a labeled development rerun. Keep current source frozen now.
5. Pier v0.3.1 is cloned at `R/pier-source`, exact commit
   `df89f994623a0a6a57229103b6fe910766693c30`. Its uv.lock SHA256 is
   `6261c632e80ee65e327c23f450c7cc5c38e34f2ee941d3ebe96ff72fc91f6de9`.
   `uv sync --frozen --no-dev` created separate `.venv-pier`: Pier0.3.1,
   Harbor0.5.0, Modal1.4.2. Existing13SDK tests pass there AND original .venv-eval.
   That alone does not prove Pier compatibility. New adapter/tests/docs are being
   prepared by subagent `01a07c01-01d0-72e2-a496-5ad5ec50e0f6`; inspect working tree
   and its result before committing. No paid DeepSWE task has run.
6. Main core suite at666dc37:140passed14optional skips. Candidate subagent
   `01a07bfb-393e-7572-b3dc-48020aa1f812` wrote Django analysis and isolated patch.
   Five-hour usage reached79%, weekly90%; continuation prompt delivered in chat.
7. Finish existing batch, preserve per-task first-pass/retry distinctions, export
   finalized traces with separate grader scores, reconcile billing, stop owned
   compute, and update report. Prior TB/SWE outcomes below remain unchanged.

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
