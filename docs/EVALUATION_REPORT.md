# Coding-agent evaluation report

Status: 2026-09-07 continuation complete. Baseline and the frozen session08
batch are preserved. Deadline, Pier, Terminal-Bench, SWE-bench, and one
DeepSWE smoke were exercised under development limits. Owned Modal apps are
stopped with zero running containers. These are not leaderboard scores.

## Results and what they mean

The frozen four-task Terminal-Bench 2.1 sample produced **two first-pass grader
passes, one first-pass grader failure, and one infrastructure interruption**.
Development retries of the failed and interrupted tasks both received reward 1.
A separately labeled SWE-bench Verified smoke of `django__django-9296` also
received reward 1 after the worker hit the 840-second wall. These are shortened
development smokes, not official suite scores or a general solve-rate estimate.

### Frozen first-pass Terminal-Bench 2.1

| Baseline task | Grader reward | Worker outcome | Turns | Worker seconds | Input / output tokens |
| --- | ---: | --- | ---: | ---: | ---: |
| openssl-selfsigned-cert | 0 | Submitted; own checks passed | 19 | 836.323 | 198,648 / 8,435 |
| constraints-scheduling | 1 | Submitted | 7 | 482.117 | 60,750 / 7,521 |
| nginx-request-logging | 1 | Submitted | 9 | 723.243 | 75,995 / 9,658 |
| kv-store-grpc | Not graded | Sandbox lost after model transport interruption | Not finalized | Not finalized | Partial trace only |

First-pass grading coverage is 3/4 attempted. The original OpenSSL failure and
gRPC interruption remain in the record. Do not replace them with later retries.

### Labeled development retries and SWE smoke

| Job | Task | Grader reward | Worker outcome | Turns | Worker seconds | Input / output tokens |
| --- | --- | ---: | --- | ---: | ---: | ---: |
| tb21-dev-kv-store-grpc-06 | kv-store-grpc | 1 | Submitted; service left running | 23 | 839.351 | 297,792 / 10,881 |
| tb21-dev-openssl-selfsigned-cert-06 | openssl-selfsigned-cert | 1 | Submitted; stdlib/OpenSSL only | 12 | 472.971 | 80,019 / 4,529 |
| swe-dev-django-9296-07 | django__django-9296 | 1 | budget_exhausted; no submit | 16 | 840.316 | 279,247 / 7,194 |

Completed trials report complete token accounting and **zero supervisor calls
or interventions**. Cached-token fields are zero because the endpoint did not
expose detailed prefix-cache accounting.

A separate invoice-repair smoke passed earlier (7 turns). Native tool-call
roundtrips 04, 05, 06, and 07 passed. Those validate protocol, not coding quality.

No paired ablation exists. OpenSSL's development pass followed a task-independent
portable-verification prompt; that is associated evidence, not a controlled proof
that the prompt caused the gain. The supervisor still has no live benefit evidence.

## Failure classes

**Grader / environment mismatch (OpenSSL first pass).** Reward 0, 5/6 checks.
The worker's `check_cert.py` imported `cryptography` installed only in the worker
environment. Replayed with `python -S` in `R/dependency-reproduction/result.json`.
This is not a hidden-test leak; it used the generated deliverable.

**Harness defect (gRPC development retry).** `edit_file` with `create=true` omitted
required `old` and returned `missing=['old']`. The worker recovered on the next
turn. Fixed later in `c70d93c` by making `old` optional and defaulting it to `""`.
Exact-match edits of existing files still require a nonempty unique `old`.

**Model reasoning (gRPC development retry).** Looked for `kv-store_pb2_grpc.py`
after protoc emitted `kv_store_pb2_grpc.py`; `chmod` after a passing `verify=true`;
stopped and restarted the server after verification. The prompt now tells the
worker to submit after a successful current-revision check without further
workspace mutations. Revision tracking is unchanged.

**Infrastructure (gRPC first pass).** Host sleep while cloud deadlines continued.
UTC timestamps jump; sandbox was gone at cleanup. Not a measured reward 0.

**Worker budget vs grader (SWE smoke).** The django trial hit the 840-second
worker wall without `submit` and without `verified_revision`. Harbor still graded
the workspace (budget exhaustion is a normal grading stop). Reward 1 therefore
measures the official grader on the leftover patch, not a completed harness
submission. Setup succeeded: the frozen django image already has Python 3.11.5,
so the private tool-interpreter sidecar was not needed for this candidate.

**DeepSWE before session09.** The earlier report correctly treated DeepSWE as
not ready while Pier was unpinned. Session09 later used the pinned Pier
environment and is recorded below; it produced no grader score because the
inference launcher expired during a model request.

## Model choice

Selected **Qwen/Qwen3.8-27B-FP8**, pinned to revision
`017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`. The supervisor uses the same model
in a separate context unless configured otherwise.

Published vendor scores (Terminal-Bench 2.1 73.0, DeepSWE 1.1 42.2) are other
harnesses. See [model-selection.md](model-selection.md).

## Implemented architecture

The worker is the sole writer. File and process tools run sequentially. Exact-match
edits are atomic. Commands have deadlines and process-group ownership.

Context management trims complete exchanges while preserving the task, verification
state, and durable notes. A deterministic monitor detects repeated actions/results
without a changed workspace revision. The read-only supervisor has no tools.

Submission requires a successful `verify=true` check on the current revision.
The official grader remains the correctness authority.

Harbor keeps model calls, credentials, and authoritative traces on the host. A
standard-library tool server runs in each disposable sandbox. If image `python3`
is older than 3.11, setup may install a private CPython under `/tmp/ca-*` via uv
and use it only for that tool server.

SWE-bench prediction export uses a temporary Git index. DeepSWE committed-patch
submission is implemented and locally tested, not grader-validated.

## Exact evaluation configuration

Unchanged from the TB smoke profile except the SWE job, which used
`R/controls-swe-smoke.yaml`: workspace `/testbed`, setup 180 seconds, environment
build multiplier 0.5. That longer build/setup is harness plumbing, not official
SWE-bench limits.

| Setting | Value |
| --- | --- |
| Model / revision | Qwen3.8-27B-FP8 / 017b9c7af6b5689d5dd426a76e0bc077eb5ca20a |
| Serving | vLLM 0.28.0; Transformers 5.16.1; CUDA 13.0.1 devel; one H100 |
| Parsers | qwen3_coder tools; qwen3 reasoning |
| Context / output | 131,072 / 16,384 |
| Sampling | Temperature 1.0; top_p .95; seed 0 |
| Worker limits | 80 turns; 2,000,000 tokens; 840 seconds |
| Supervisor | On; max 3; cooldown 4; repeat threshold 3; output 4,096 |
| SDK | Harbor 0.22.0 with Modal; Modal 1.5.5 |
| TB agent / setup / verifier / sandbox | 900 / 60 / 120 / 1,200 seconds |
| SWE smoke setup / build multiplier | 180 seconds / 0.5 |

Source checkpoints: `9400da6` baseline, `0b523e6` CUDA serving, `d32397f` warmup
skip, `f086809` service lifetime and portable verification prompt, `c70d93c`
create-only `edit_file` and private tool interpreter. TB development retries used
`f086809` / `f8ba1c8`. The SWE smoke used `c70d93c`.

## Frozen sample

Unchanged. See [sample-selection.md](sample-selection.md). Failed tasks were not
replaced. No hidden grader or gold-solution source was supplied to the solver.

## Latency and observability

OpenSSL first pass: 88.6% of 836.323s in model request/response intervals.
gRPC development retry: 723.154s model / 34.774s tools of 839.351s worker time.
OpenSSL development retry: 416.743s model / 14.149s tools of 472.971s.
SWE django smoke: 709.744s model / 42.052s tools of 840.316s, then Harbor graded.

LangSmith is optional export. Roots with HTTP 200 grader feedback:

| Trial | Trace | Reward |
| --- | --- | ---: |
| openssl baseline | `dba2de09-94a5-5323-8485-c314e49d78b3` | 0 |
| scheduling | `802e115a-77ce-506d-97d0-2a1ecfba5aea` | 1 |
| nginx | `603064e3-5115-5200-a01b-148a36464584` | 1 |
| gRPC development | `1cee6e81-85c0-58ba-a78e-3a4213b93954` | 1 |
| OpenSSL development | `6d4fc288-d425-56dc-bb21-dbde2c2a64b0` | 1 |
| SWE django smoke | `149a93a0-2303-50f4-8b9f-30d33d6c6640` | 1 |

The interrupted gRPC baseline has no harness `result.json` and was not
exported as a graded run.

## Evidence index

Paths relative to `.agent-runs/live-20260905`. Not committed.

| Evidence | Location |
| --- | --- |
| Aggregated outcomes | outcomes.json |
| Baseline OpenSSL | jobs/tb21-openssl-smoke-02/openssl-selfsigned-cert__eYEX2Jf |
| Baseline scheduling / nginx / interrupted gRPC | jobs/tb21-remaining-baseline-05/ |
| gRPC development retry | jobs/tb21-dev-kv-store-grpc-06/kv-store-grpc__JnJuEwU |
| OpenSSL development retry | jobs/tb21-dev-openssl-selfsigned-cert-06/openssl-selfsigned-cert__E6YQzV3 |
| SWE django smoke | jobs/swe-dev-django-9296-07/django__django-9296__3FAeVg5 |
| Session08 frozen SWE batch | jobs/swe-dev-remaining-08/ |
| Deadline Django | jobs/swe-dev-deadline-django-10/django__django-9296__FrUKMfQ |
| Terminal-Bench gRPC continuation | jobs/tb21-dev-kv-store-grpc-09/kv-store-grpc__ADhLTCm |
| DeepSWE label 02 | jobs/deepswe-dev-abs-module-cache-flags-02/abs-module-cache-flags__9Yu6hW8 |
| Astropy post-deadline check | jobs/swe-dev-deadline-astropy-11/astropy__astropy-7166__Yxfs9d2 |
| DeepSWE declaration | deepswe-predeclare.json |
| Final billing and shutdown | billing-final-session09.json, shutdown-session09.json |
| Django image python probe | swe-python-probe.json (Python 3.11.5) |
| Dependency / lifecycle reproductions | dependency-reproduction/, lifecycle-reproduction/ |
| Settings | agent.toml, controls-smoke.yaml, controls-swe-smoke.yaml, image-pins.json |
| Billing | billing-before.json, billing-pre-retry-20260906.json, billing-post-tb-retries-20260906.json, billing-final-20260906.json, billing-final-session09.json |
| LangSmith receipts | langsmith-receipts/ |

## Cost, validation, shutdown

Credentialed workspace (do not use ambient `modal billing` without
`with_credentials.py`; that profile is a different account):

**$11.32 credits consumed**, $0 billed, metered $12.80261309, free-storage
adjustment $1.48261309. Against the $24 working ceiling, about **$12.68** remains.
User authorization is $30 total.

H100 rate $3.95/hour. Session06 (`ap-8JFsctTXtfRdSKYQiCMZq4`) stopped 18:31 IST.
Session07 (`ap-k0ymAHULxsb1CVgMcNmztE`) stopped 19:29 IST. SWE python probe app
`ap-au1v3WVdyvSkR7fHRvGcsg` stopped after the CPU-only image check. Eval app
`ap-PMlnDl7NOv6q8IUzuMMVp9` is deployed with **zero tasks** (no running GPU).
Zero running containers confirmed after session07 stop. Cache volume and
credential secret retained.

Validation at `c70d93c`: ruff, mypy, complexity 205 functions max 10; targeted
pytest 25 passed including create-without-old; Harbor SDK tests 13 passed
including sidecar interpreter unit tests. Full pytest needs local git and
`/tmp` socket permissions; sandbox-only failures of those two tests are
spurious.

## Remaining work

The supervisor has no measured benefit because no paired equal-budget ablation
was purchased. Do not treat development retries as unseen first passes. The
external LangSmith export of the new traces is pending explicit approval for
the outbound model and workspace payload; all local traces and grader reports
remain available.

## Session 08 continuation - September 7, completed

The remaining frozen SWE instances completed sequentially in
`swe-dev-remaining-08` on unchanged source at `666dc37`. Astropy,
pytest, and scikit-learn each ended at the wall boundary as `model_error`, with
no verifier result. The job record reports three completed trials, three
errors, and 815,593 input plus 34,259 output tokens. Their original traces,
partial workspaces, interruptions, and unknown-usage reservations remain
untouched. This batch was not duplicated or edited during execution.

Worker-only Django trace analysis found that context exposed remaining tokens but
not wall time. Passing behavioral checks at approximately710s omitted verify=true;
runner troubleshooting then exhausted840s. An isolated candidate adds declining
remaining_seconds and one finalization reminder in the last min(120s,20%ofbudget).
It preserves explicit verification/submission and deadlines. The reproduced missing
field failed before the change; 66 selected tests passed afterward. The candidate
was then applied and measured in session09 below; evidence is in the private
deadline-candidate folder.

Pier 0.3.1 is pinned to `df89f994623a0a6a57229103b6fe910766693c30` in a
separate lockfile-created environment with Harbor 0.5.0. The adapter now has
separate write, verify, and submit fixture calls plus a real temporary Git
trial covering committed binary patch collection, pristine transfer, grading,
commit failure, collection failure, budget exhaustion, and cleanup. Pier's
original 13 SDK checks and eight Pier checks pass. DeepSWE task metadata at
dataset commit `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` parsed under this
environment and declared `abs-module-cache-flags` alphabetically before the
solver ran. The first launch failed before setup because the host import path
omitted `src`; fresh label 02 then ran with the corrected path.

## Session 09 continuation - September 7, completed

The isolated deadline candidate was applied at commit
`12abd2b3a4ce4659ea84ad0532423fe4b6086eef`. It adds declining
`remaining_seconds` to the model context and one finalization reminder in the
last `min(120 seconds, 20% of budget)`. The separate classification fix adds
`ModelDeadlineExceeded` for a global deadline, preserves the failure event and
unknown-token reservation, and ends the run as `budget_exhausted`. Earlier
timeouts, connection failures, and malformed responses remain `model_error`.
No automatic retries were added. Focused deadline/model/runtime tests passed
51/51 and the full core suite passed 150 tests with 22 optional skips.

The corrected Django development run `swe-dev-deadline-django-10` completed
with reward 1.0 after a `budget_exhausted` worker result at 840.626 seconds
(109,745 input and 5,432 output tokens). It emitted one reminder at 742.28
seconds, then preserved a global-deadline `model_failed` event with 90,252
unknown tokens reserved. The grader applied the patch and resolved the task.
The Terminal-Bench `kv-store-grpc` run `tb21-dev-kv-store-grpc-09` completed
with reward 1.0, 18 turns, 657.875 seconds, and 146,227 input plus 5,788
output tokens. The service remained running through the separate grading step.

The one DeepSWE task was predeclared with the pinned dataset, image,
no-network agent and verifier, separate verifier environment, 840-second
worker budget, and committed-patch collection. Label 01 is a preserved
preflight import failure. Label 02 reached 17 turns and 771.301 seconds with
296,528 input and 2,193 output tokens, then received HTTP 500 when the
inference launcher expired. Pier therefore recorded `model_error`, no commit
receipt, no verifier run, and no score. The local trajectory and partial
workspace remain preserved; no reference solution or hidden tests entered the
solver context.

The allowed Astropy post-deadline check `swe-dev-deadline-astropy-11` ended at
840.817 seconds as `budget_exhausted`, emitted one reminder at 751.818 seconds,
and was graded separately with reward 0.0. The patch applied successfully, but
`astropy/utils/tests/test_misc.py::test_inherit_docstrings` remained failing;
the other recorded checks passed. This confirms restored grading after a wall
deadline while also showing that deadline handling does not improve an
incomplete task patch by itself.

Final credentialed billing was $20.38 credits consumed and $22.18164059
metered, with $0 billed and $1.80164059 free-storage adjustment. Five inference
and evaluation app IDs were stopped, and a credentialed container listing was
empty after shutdown. Billing may report small usage lag. Local evidence is in
`billing-final-session09.json` and `shutdown-session09.json`.

The new local traces, partial traces, and grader reports are retained under
`.agent-runs/live-20260905/jobs`. The existing paced LangSmith exporter was
not run because the external-data review blocked sending model messages and
workspace-derived traces without explicit user approval. Existing receipts
were not modified, and no new remote trace or feedback receipt is claimed.
