# Frozen official benchmark smoke sample

Selection v1, frozen 2026-09-05 before any agent execution. Four SWE-bench Verified instances and four Terminal-Bench 2.1 tasks. Model selected by the user: **Qwen3.8-27B-FP8**. This document does not select a model repository, weights revision, endpoint, or inference configuration.

Only this document was written. No task runs, image pulls/builds, cloud provisioning, credentials, runtime changes, or commits were performed. Research used public official registry metadata, task instructions, `task.toml`, Dockerfiles, and installed SDK source. No gold patches, solution scripts, hidden tests, trajectories, or benchmark results were inspected for selection. Broad initial search returned incidental performance snippets; none informed the choices and their linked results were not opened.

## Selection rule and scope

- SWE-bench: choose four different repository domains, then the lowest numeric instance ID in each repository whose official Harbor metadata says `<15 min fix`. The repositories are Astropy, Django, pytest, and scikit-learn. This gives scientific Python infrastructure, web framework APIs, developer tooling, and ML APIs without letting Django dominate the sample.
- Terminal-Bench: choose four distinct official categories, CPU-only single-container definitions, task-local or localhost work, expert estimates at most 20 minutes, and declared agent timeouts at most 1,200 seconds. Prefer image definitions with Python 3.11+ and avoid model downloads, GPUs, emulation, and large compilation jobs. Choices are purposeful coverage, not a random or representative sample.
- Freeze these IDs and revisions for subsequent model/harness comparisons. Do not replace failed or slow tasks after observing results. Record image/setup failures separately; a future manifest revision must explain any change using metadata or infrastructure evidence.
- These are **plausible short smoke tasks**, not demonstrated 20-minute solves or a leaderboard evaluation. Human estimates and timeout allowances are not measured agent durations. Selection and exposure to these instructions make this a development sample, not an unseen holdout.

## Pinned dataset references

| Benchmark | Official reference | Immutable source used here |
| --- | --- | --- |
| SWE-bench Verified | Legacy Harbor dataset `swebench-verified@1.0`, 500 instances; Hub name `swe-bench/swe-bench-verified` | [Official registry](https://raw.githubusercontent.com/laude-institute/harbor/5c364a538e0af19eb58a53fdb895d7c0f974cef5/registry.json); all four task directories point to `https://github.com/laude-institute/harbor-datasets.git` at `86723674f04e4209ac479d0fb75d9d9f44b4377e`, under `datasets/swebench-verified/` |
| Terminal-Bench 2.1 | Hub name `terminal-bench/terminal-bench-2-1`, 89 tasks | [Official dataset manifest](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/dataset.toml); `https://github.com/harbor-framework/terminal-bench-2-1.git` at `7131e4375048a0e408a8fb404b5f499d726b695b`, under `tasks/` |

The legacy registry inspected here contains `terminal-bench@2.0`, but **does not contain `terminal-bench@2.1`**. Do not use the tentative `terminal-bench@2.1` example in `docs/evaluations.md`. Use the pinned official Git source below. A Hub dataset name with `@latest` is not an immutable pin. The Git snapshots and Hub artifacts should not be assumed byte-identical without checking; the execution recipes below deliberately select the inspected Git snapshots.

## SWE-bench manifest

All four have category `debugging`, official difficulty `<15 min fix`, agent/verifier limits of 3,000 seconds each, and build limit 1,800 seconds. Each declares 1 CPU, `memory = '4G'`, and `storage = '10G'`; Harbor 0.22 accepts those legacy resource fields. Dockerfiles set `WORKDIR /testbed` and install uv 0.7.13. No GPU is requested.

| Frozen instance ID | Repository / instruction scope | Why a short task is plausible | Official instruction |
| --- | --- | --- | --- |
| `astropy__astropy-7166` | `astropy/astropy`: property docstrings are not inherited by `InheritDocstrings` | Narrow metaclass/descriptor behavior, explicitly identified in the issue | [Instruction](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/astropy__astropy-7166/instruction.md) |
| `django__django-9296` | `django/django`: make `Paginator` iterable over pages | Small public API extension; the official instruction itself gives an example | [Instruction](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/django__django-9296/instruction.md) |
| `pytest-dev__pytest-5262` | `pytest-dev/pytest`: captured text stream advertises binary mode | Localized stream compatibility issue with a public reproduction | [Instruction](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/pytest-dev__pytest-5262/instruction.md) |
| `scikit-learn__scikit-learn-11310` | `scikit-learn/scikit-learn`: expose estimator refit duration through `refit_time_` | Bounded timing/API addition with a small Iris example, no large training workload required | [Instruction](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/scikit-learn__scikit-learn-11310/instruction.md) |

Image references below are literal Dockerfile `FROM` values, **not resolved image digests**:

| Instance | Image | Setup sources |
| --- | --- | --- |
| `astropy__astropy-7166` | `swebench/sweb.eval.x86_64.astropy_1776_astropy-7166:latest` | [Metadata](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/astropy__astropy-7166/task.toml), [Dockerfile](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/astropy__astropy-7166/environment/Dockerfile) |
| `django__django-9296` | `swebench/sweb.eval.x86_64.django_1776_django-9296:latest` | [Metadata](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/django__django-9296/task.toml), [Dockerfile](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/django__django-9296/environment/Dockerfile) |
| `pytest-dev__pytest-5262` | `swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5262:latest` | [Metadata](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/pytest-dev__pytest-5262/task.toml), [Dockerfile](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/pytest-dev__pytest-5262/environment/Dockerfile) |
| `scikit-learn__scikit-learn-11310` | `swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-11310:latest` | [Metadata](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/scikit-learn__scikit-learn-11310/task.toml), [Dockerfile](https://github.com/laude-institute/harbor-datasets/blob/86723674f04e4209ac479d0fb75d9d9f44b4377e/datasets/swebench-verified/scikit-learn__scikit-learn-11310/environment/Dockerfile) |

**SWE-bench readiness limitation:** our `AdaptiveAgent.setup()` executes bare `python3` and requires version 3.11+. These historical SWE-bench Dockerfiles do not establish that requirement. uv installation alone does not install or select Python 3.11. Verify the task image's tool interpreter before execution; any separate tool-interpreter setup must preserve the repository's original test environment. No such image/setup change is supplied or made here. Repository base commits and OCI digests were not exposed by the inspected metadata and remain unresolved. The pinned Harbor task revision is not a substitute for either. These four are frozen candidates, not certified runnable with the current adapter.

## Terminal-Bench 2.1 manifest

All four declare 1 CPU, 2,048 MB memory, 10,240 MB storage, zero GPUs, internet allowed, a 600-second build timeout, and no MCP servers. Dockerfiles define one container with `WORKDIR /app`. Nginx and gRPC run on localhost inside that sandbox; no public service exposure or multi-container topology is needed.

| Frozen task ID | Official category / coverage | Expert minutes | Native agent / verifier seconds | Feasibility and setup |
| --- | --- | ---: | ---: | --- |
| `kv-store-grpc` | `software-engineering`: protobuf, RPC implementation, background service | 15 | 900 / 900 | Small in-memory service; Python 3.13 base. Instruction requires installing `grpcio==1.73.0` and `grpcio-tools==1.73.0`. PyPI access needed. |
| `nginx-request-logging` | `system-administration`: server configuration, logging, rate limits | 20 | 900 / 900 | Python 3.13 base with curl and requests; task requires installing Nginx and editing `/etc` and `/var`. Needs apt access and shell access beyond `/app`. |
| `openssl-selfsigned-cert` | `security`: certificate generation, permissions, verification | 20 | 900 / 900 | Python 3.13 base with OpenSSL installed; task outputs stay under `/app`. Most self-contained first smoke candidate. |
| `constraints-scheduling` | `personal-assistant`: calendar parsing, temporal constraints, ICS output | 15 | 1200 / 1200 | Ubuntu 24.04 installs distro `python3` and pip, expected Python 3.12; calendar inputs are copied into `/app`. Metadata also says `estimated_duration_sec = 1800`, so its timing evidence is mixed. |

Image names are `alexgshaw/<task-ID>:20251031` for these four. Date tags are still mutable; resolve and record actual image digests in a later execution record. Python compatibility above is inferred from Dockerfiles, not checked inside pulled images.

| Task | Official allowed sources | Package digest in pinned dataset manifest |
| --- | --- | --- |
| `kv-store-grpc` | [Instruction](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/kv-store-grpc/instruction.md), [metadata](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/kv-store-grpc/task.toml), [Dockerfile](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/kv-store-grpc/environment/Dockerfile) | `sha256:973c5d4c111fb61a344457936f1c36400acd2d9e44389e7b319586fe23a7a307` |
| `nginx-request-logging` | [Instruction](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/nginx-request-logging/instruction.md), [metadata](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/nginx-request-logging/task.toml), [Dockerfile](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/nginx-request-logging/environment/Dockerfile) | `sha256:9d1b8bebd989ea0bc8080c3b159480068caf183c5fd61385868b2574b206e097` |
| `openssl-selfsigned-cert` | [Instruction](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/openssl-selfsigned-cert/instruction.md), [metadata](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/openssl-selfsigned-cert/task.toml), [Dockerfile](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/openssl-selfsigned-cert/environment/Dockerfile) | `sha256:d4afa2bd2a9ba1420db8d6cfde42ffdb4873ae2d955c35014e8da94444c83302` |
| `constraints-scheduling` | [Instruction](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/constraints-scheduling/instruction.md), [metadata](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/constraints-scheduling/task.toml), [Dockerfile](https://github.com/harbor-framework/terminal-bench-2-1/blob/7131e4375048a0e408a8fb404b5f499d726b695b/tasks/constraints-scheduling/environment/Dockerfile) | `sha256:91dd6f87e1ee508328d5aafbd99a6bd1ccc47bc22671b4aaa4060bda492deb53` |

These are task package hashes, not Docker image hashes. The package forms are `terminal-bench/<task-ID>@sha256:<hash>`; they are recorded as provenance, while the commands below avoid Hub authentication by using official Git sources.

Before freeze, `cancel-async-tasks`, `log-summary-date-ranges`, and `sqlite-db-truncate` were considered but not retained: expert estimates were 120, 75, and 60 minutes. The first numeric Django task and four earlier scikit-learn tasks were skipped because their difficulty estimates were `15 min - 1 hour`. No run outcomes were used.

## Harbor 0.22.0 / Modal configuration

Installed distributions inspected: `harbor==0.22.0`, `modal==1.5.5` in `.venv-eval`. No installation or upgrade is needed for the documented CLI schema. `--env modal` selects Harbor's Modal sandbox backend; it does not host the chosen inference model. The existing adapter makes model requests from the host process and uploads its tool server into the sandbox.

For later execution, save the following control config as `/tmp/sample-controls.yaml`. That file was not created during this research. Before running, the separate `/Users/adhithyasash1/new-coding-agent/agent.local.toml` must have the selected model's actual served name, immutable weights revision, reachable endpoint, and appropriate token limits. Set its `[run].max_seconds` to **840** so it can finish and collect artifacts before Harbor's 900-second agent cap. That config was not read or changed here.

```yaml
n_attempts: 1
n_concurrent_trials: 1
retry:
  max_retries: 0
timeout_multiplier: 1.0
environment_build_timeout_multiplier: 0.15
environment:
  type: modal
  force_build: false
  delete: true
  kwargs:
    sandbox_timeout_secs: 1200
agents:
  - import_path: integrations.harbor_agent:AdaptiveAgent
    max_timeout_sec: 900
    override_setup_timeout_sec: 60
    kwargs:
      config: /Users/adhithyasash1/new-coding-agent/agent.local.toml
      workspace: /app
verifier:
  max_timeout_sec: 120
```

The warm-image target is 90 seconds environment startup/build + 60 seconds agent setup + 900 seconds agent execution + 120 seconds verification = **1,170 seconds**, leaving about 30 seconds for normal overhead. Task acquisition, queueing, teardown, and cancellation overhead are outside that arithmetic; this is not an end-to-end 20-minute guarantee. Modal's `sandbox_timeout_secs` caps sandbox lifetime, not image build cost or the full Harbor job. Cold builds may fail the short cap, especially SWE-bench. Record those as infrastructure/time-budget failures rather than demonstrated solver failures.

These smoke settings shorten official verifier limits and sometimes the agent limit. An incomplete verifier must be reported as incomplete grading, not a valid official failure. Keep grading enabled. Do not claim official benchmark scores from this altered timeout profile.

The commands below **print configuration and exit**. For a later authorized live run, remove only `--print-config` after image and inference readiness checks. Neither command was run live. Each selects exactly four task names; do not use `--n-tasks 4` as a substitute for the filters. `--include-task-name` / `-i` is the installed flag, not `--task-name`.

```sh
cd /Users/adhithyasash1/new-coding-agent
HARBOR_TELEMETRY=0 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv-eval/bin/harbor run --config /tmp/sample-controls.yaml \
  --repo 'https://github.com/harbor-framework/terminal-bench-2-1.git@7131e4375048a0e408a8fb404b5f499d726b695b' \
  --path tasks \
  --include-task-name kv-store-grpc \
  --include-task-name nginx-request-logging \
  --include-task-name openssl-selfsigned-cert \
  --include-task-name constraints-scheduling \
  --job-name sample-v1-tb21 --print-config
```

The SWE-bench command reuses the controls, overrides the workspace to `/testbed`, and scales its 1,800-second build allowance to 90 seconds. It selects the immutable official registry entry, whose four task source commits were checked against the manifest above:

```sh
cd /Users/adhithyasash1/new-coding-agent
HARBOR_TELEMETRY=0 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv-eval/bin/harbor run --config /tmp/sample-controls.yaml \
  --dataset 'swebench-verified@1.0' \
  --registry-url 'https://raw.githubusercontent.com/laude-institute/harbor/5c364a538e0af19eb58a53fdb895d7c0f974cef5/registry.json' \
  --include-task-name astropy__astropy-7166 \
  --include-task-name django__django-9296 \
  --include-task-name pytest-dev__pytest-5262 \
  --include-task-name scikit-learn__scikit-learn-11310 \
  --ak workspace=/testbed \
  --environment-build-timeout-multiplier 0.05 \
  --job-name sample-v1-swe-verified --print-config
```

Equivalent SWE Git selection, if legacy registry retrieval fails: replace `--dataset` and `--registry-url` with `--repo 'https://github.com/laude-institute/harbor-datasets.git@86723674f04e4209ac479d0fb75d9d9f44b4377e' --path datasets/swebench-verified`; retain the same filters, controls, workspace, and timeout override. Git task filters use bare directory names; Hub package task names include their organization prefix.

For the first paid smoke, use the Terminal-Bench command with only `--include-task-name openssl-selfsigned-cert` and a distinct job name. This is a predeclared launch order, not a change to the frozen four-task selection. Stop for billing/readiness review before expanding to the remaining IDs. Do not run oracle agents, upload results publicly, or pass model secrets into task environments.

## Budget, validation, and remaining limitations

The user's current research/testing allowance is **$30 total Modal spend**, superseding older $20 examples elsewhere in this repository. This research spent $0 on Modal. Concurrency one, zero retries, and time limits reduce exposure but do not enforce a dollar cap. Inference GPU startup, warm/idle time, failed builds, CPU/RAM, storage, and repeated comparisons all count toward the allowance. No current price, available account balance, or completed eight-task cost was checked. A separate execution owner must bound and track aggregate spend; this manifest does not authorize provisioning from this read-only task.

Direct sandbox DNS initially failed; public official GitHub reads succeeded through approved network access using unauthenticated requests. The legacy registry was accessible. The authenticated Hub package API was not queried and no credential store was opened. No dataset clone or archive was downloaded; only allowed public files were read into memory. Full task acquisition and grading are deferred to the isolated evaluator, with tests and reference solutions kept out of solver context.

Validation is limited to source inspection, public manifest membership, and offline Harbor CLI/config parsing. Both documented commands passed Harbor 0.22.0 CLI parsing with the exact documented YAML supplied in memory in place of the future config-file read; socket connections were blocked. The parsed configurations contained four explicit IDs each, Modal, the intended workspaces, and the specified time caps. A separate TB Git/filter `--print-config` invocation also passed without config-loader substitution. No runtime config file was created. `--print-config` returns before dataset acquisition and job execution, so it does not prove image availability, package resolution, model connectivity, permissions, verifier duration, or Modal startup. The adapter's Python requirement and SWE-bench image uncertainty are the main readiness risks. Nginx also requires shell operations outside the `/app` file-tool root; task-host filesystem access must remain confined to its disposable sandbox.

SDK references: installed `harbor/cli/jobs.py` confirms `--repo`, `--path`, `--dataset`, `--registry-url`, `--include-task-name`, `--ak`, and `--print-config`; `harbor/models/job/config.py` and `harbor/models/trial/config.py` define the YAML fields; `harbor/trial/trial.py` applies timeout caps; `harbor/environments/modal.py` implements `sandbox_timeout_secs`, prebuilt images, and Dockerfile builds. [Official Harbor eval documentation](https://www.harborframework.com/docs/run-jobs/run-evals), [official Terminal-Bench 2.1 repository](https://github.com/harbor-framework/terminal-bench-2-1), [official SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/), and [Modal Sandbox API](https://modal.com/docs/reference/modal.Sandbox#create) provide background; the installed 0.22.0 source is the authority for these commands.
