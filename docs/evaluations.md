# Evaluation adapters and measurement

The official graders remain separate from the agent. The worker and supervisor
receive the task and allowed task workspace, never hidden tests or reference solutions.
Graded development trials and their limits are recorded in
[the evaluation report](EVALUATION_REPORT.md); they are not full-suite scores.

## Harbor and Terminal-Bench

The external adapter implements `harbor.agents.base.BaseAgent`. Model calls and the
authoritative trace writer run in the host evaluation process. A small tool server
runs in the task environment via `BaseEnvironment.exec`; it retains managed process
state between calls. The adapter uploads Python source requiring Python 3.11+. If the task image's
`python3` is older, setup installs a private CPython under the adapter's `/tmp/ca-*`
tree with the image's `uv` and uses that interpreter only for the tool server.
Worker commands keep the image `python3` so the benchmark test environment is unchanged.

```sh
uv venv .venv-eval
uv pip install --python .venv-eval/bin/python -e . 'harbor[modal]==0.22.0' 'modal==1.5.5'
.venv-eval/bin/python -m unittest discover -s tests -p test_integrations.py -v
.venv-eval/bin/harbor run \
  --agent integrations.harbor_agent:AdaptiveAgent \
  --ak config=agent.local.toml --n-concurrent 1 --max-retries 0 --print-config
```

Set `PYTHONPATH` to this checkout when launching Harbor so it can import the external
adapter. Use the exact Git reference and task filters in
[the frozen sample](sample-selection.md). The inspected legacy registry does not
contain `terminal-bench@2.1`. Keep the verifier enabled, distinguish shortened smoke
limits from official limits, and review spending before expanding the task count.

`--ak workspace=/path/in/task/container` overrides the environment's working directory.
Do not use `/` as the workspace. Set this to the actual checkout. Tool artifacts are
downloaded under each trial's `harness/outputs`; the authoritative trace is in
`harness/events.jsonl`. Harbor receives total reported input/output/cache usage.
Budget exhaustion is a normal grading stop so a valid partial patch can still be
graded. Model and infrastructure errors remain errors. Native ATIF and automatic
resume capabilities are not advertised.

Local validation uses the real Harbor 0.22.0 SDK with a local environment simulator
and real tool-server subprocesses. It checks setup, runtime, artifact download, and
usage propagation. Official task images, full job orchestration, and Modal task
environments remain to be validated. The simulator is not evidence of benchmark
performance or of complete sandbox-provider compatibility.

## Datacurve DeepSWE

Use the Datacurve DeepSWE benchmark, distinct from the older DeepSWE training/model
project. DeepSWE 1.1 grades a committed patch in a separate pristine container.
The adapter's explicit `commit_patch=true` option commits task changes at submission
or budget exhaustion, with hooks and signing disabled. This is intended only inside
the disposable benchmark checkout. It does not commit this harness repository.
The adapter saves the commit command's outcome in `submission.json` beside the
trial trace. A commit/export failure remains an infrastructure failure.

Pier is a Harbor fork and must use its own environment. Pin a reviewed Pier commit
before the first run; do not install Harbor and Pier into the same environment.
The adapter relies on their shared external-agent/environment interfaces. Its
compatibility with the chosen Pier revision and the committed-patch grader is pending
an actual DeepSWE smoke task. Verify environment network policies and resource support
for each task before paying for a full job. Do not assume every multi-container task
is supported by the selected Modal backend.

## SWE-bench

Run the worker in an environment built for the chosen SWE-bench instance. Export
predictions with a JSON manifest containing one entry per task:

```json
[
  {
    "instance_id": "the-official-instance-id",
    "workspace": "/absolute/path/to/task-checkout",
    "base_commit": "the-official-task-base-commit",
    "model_name_or_path": "our-pinned-harness-and-model-label"
  }
]
```

```sh
uv run python scripts/export_swe_predictions.py manifest.json predictions.jsonl
```

The exporter includes untracked, nonignored files through a temporary Git index and
does not change the real index. It refuses to overwrite an existing output. Feed
the predictions to the official SWE-bench grading environment, with the dataset and
grader revision pinned. An export is not a benchmark run. Keep the official report
beside the task traces without exposing it to the solver.

## Experiment records

Record task IDs/version, repository base commits, image digests, harness source hash,
model weights revision, sampling, supervision setting, token/time limits, actual
reported tokens, unknown-usage reservations, provider timings and billing. Keep
development and holdout task lists fixed and disclose any overlap.

Compare repeated paired runs at equal total allowance. Include worker-only and
fixed-reminder controls before crediting the supervisor. Report official solve rate
separately for each benchmark, alongside regressions, stop reasons, infrastructure
errors, latency, cost, and uncertainty. The initial $30 allowance is for smoke testing,
not enough evidence to claim a reliable score improvement.

References: [Harbor agent API](https://www.harborframework.com/docs/agents),
[DeepSWE](https://github.com/datacurve-ai/deep-swe),
[Pier](https://github.com/datacurve-ai/pier), and
[SWE-bench evaluation](https://www.swebench.com/SWE-bench/guides/evaluation/).

## Services and post-run artifacts

Harbor grades after the worker returns. Its tool transport therefore detaches at
worker completion, leaving services under sandbox ownership. The remote-server
lifetime defaults to the worker allowance plus 720 seconds, measured from setup;
override `service_lifetime_sec` through adapter kwargs for longer verifier windows.
The sandbox timeout must also cover setup, worker execution and grading. An omitted
`start_process` timeout uses the remaining service lifetime. Explicit timeouts keep
their normal worker-budget cap. Local CLI cleanup remains immediate.

If the sandbox disappears, optional artifact downloads record errors separately and
do not overwrite the host's primary failure or finalized trace. Keep a host running
for the duration of client-driven cloud trials: host sleep does not pause cloud
sandbox deadlines. Use bounded sleep inhibition on laptops during a paid run.
