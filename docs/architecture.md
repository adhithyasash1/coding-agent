# Implementation and experiments

The runtime is deliberately sequential. A small worker loop owns the model history,
budgets, and tools. Context management, progress monitoring, advice validation,
process execution, and trace persistence are independent modules with typed contracts.
Ruff enforces C901 and a separate Radon check includes boolean branches with a maximum
cyclomatic complexity of 10 per function. Simpler ordinary functions are preferred.

The worker executes a complete tool-call batch before any advice is added to the
conversation. A signal from an earlier revision is discarded. There is one supervisor
context, no recursive supervisors, no competing writers, and no supervisor execution
privileges. With no separate profile, the supervisor uses the same model through a
fresh context. A stronger supervisor is a separate experiment.

## Implemented first version

- Configurable model endpoint, weights revision metadata, sampling, and finite budgets.
- Typed tool results, atomic exact edits, content-and-mode verification, process
  deadlines, incremental polling, stdin, and process-group cleanup.
- Persistent task and worker-written notes with bounded whole-exchange context trimming.
- Deterministic repeat detection, explicit help requests, shadow mode, bounded advice.
- Durable local evidence, progress output, diagnostic checkpoints, and trace inspection.
- Credential-free Docker tool transport and external Harbor tool transport.
- Optional single-GPU Modal inference definition with model selection deferred.
- SWE-bench prediction export and optional DeepSWE committed-patch submission.

## Validation before claiming an improvement

First run a small fixed smoke task with supervision off, then shadow, then on.
Hold the worker model, revision, task version, seed, environment, and total allowance
constant. Count supervisor calls within that allowance. Inspect whether interventions
were useful and whether they interrupted productive work.

On a development split, compare the minimal worker, adaptive context/monitoring,
and supervised worker. Add a fixed reminder under identical triggers before attributing
a gain to supervisor reasoning. Run repeated paired trials and report confidence
intervals alongside correctness, regressions, stop reason, token use, wall time, and
infrastructure failures. Never equate intervention recovery with task correctness.

Freeze settings before running a holdout, preferably with repository separation.
Keep SWE-bench, Terminal-Bench, and DeepSWE scores separate and pin versions.
The legacy repository's reported scores do not measure this implementation.

## Deliberately deferred

Safe automatic resume, PTY execution, asynchronous observation, candidate branching,
learned stuck detection, automatic semantic summarization, persistent cross-task
memory, online prompt updates, and training are outside the first version. The
supervisor's initial evidence comes from the recent trace packet; additional bounded
read-only retrieval can be added if live traces show it is needed.

Research informing this design: [Wink](https://arxiv.org/abs/2602.17037),
[SupervisorAgent](https://arxiv.org/abs/2510.26585),
[Shepherd](https://arxiv.org/abs/2605.10913), and
[mini-SWE-agent](https://mini-swe-agent.com/latest/). Their published results are
motivation, not a performance guarantee for this harness.
