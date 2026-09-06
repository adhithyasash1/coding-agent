# Modal setup and the $30 development evaluation

Qwen3.8-27B FP8 has passed live model/tool and repair smoke tests on one H100.
See [the evaluation report](EVALUATION_REPORT.md) for graded outcomes, spending,
and resource state. Keep inference separate from CPU task sandboxes. Local traces
work without LangSmith credentials.

Create an isolated environment and log in when ready:

```sh
uv venv .venv-modal
uv pip install --python .venv-modal/bin/python 'modal==1.5.5'
.venv-modal/bin/modal setup
.venv-modal/bin/modal token info
```

`modal token set` is the interactive alternative for an existing token ID and secret.
Do not paste credentials into chat or commit them.

Create a separate endpoint key through an interactive Python prompt:

```sh
.venv-modal/bin/python integrations/create_modal_secret.py
```

This creates **adaptive-agent-inference** containing `VLLM_API_KEY`, using a private
temporary JSON file that is removed afterward. It refuses to overwrite an existing
named secret. Account tokens and the endpoint key are separate credentials.
The server reads `VLLM_API_KEY` from its
environment, so it never places the key in logged command arguments.

After selecting compatible weights and hardware, fill these values in your terminal:

```sh
export AGENT_MODEL_NAME='organization/selected-model'
export AGENT_MODEL_REVISION='exact-model-commit'
export AGENT_TOOL_PARSER='parser-for-selected-model'
export AGENT_GPU='selected-single-gpu-type'
export AGENT_CONTEXT_TOKENS='32768'
# Set AGENT_REASONING_PARSER only if the model needs one.
.venv-modal/bin/modal serve --timeout 900 --timestamps integrations/modal_inference.py
```

Run this in the foreground for the first test. The definition allows one container,
one concurrent request, no permanently warm replica, and a 300-second idle scaledown.

## Container image

The image starts from `nvidia/cuda:13.0.1-devel-ubuntu22.04` with Python 3.12, not
`debian_slim`. vLLM 0.28.0 wheels depend on torch 2.13.0 built for CUDA 13, and
FlashInfer compiles some kernels at first use with `nvcc`. On 2026-09-05 a
`debian_slim` image loaded the 27 GiB model four times and died each time with
`Could not find nvcc and default cuda_home='/usr/local/cuda' doesn't exist`, because
the pending client request made Modal start a fresh container after every failure.
The devel base supplies the toolkit; `flashinfer-cubin` and `flashinfer-jit-cache`
(CUDA 13.0 index) carry precompiled kernels; `VLLM_USE_FLASHINFER_SAMPLER=0` and
`--gdn-prefill-backend triton` remove the two compile paths that the log named.
Watch the first boot log and stop the app on the first `exited during startup`
line; do not leave a client request pending against a failing image.
`VLLM_DEEP_GEMM_WARMUP=skip` avoids warming unused DeepGEMM shapes on the selected
dense CUTLASS FP8 path. The option was checked against vLLM 0.28.0 source.
Copy the displayed development endpoint into `model.base_url`, adding `/v1`, and set
the same endpoint key as `AGENT_API_KEY` in the terminal running the harness.
Keep the model name and revision in the config aligned with the service.

## Spend policy

The harness token limit is not a dollar limit for a rented GPU. Cold starts, image
builds, CPU/RAM, storage, idle time, and failed requests can consume credits.
Before the first live session, check current pricing for the chosen GPU and estimate
`hourly rate * session seconds / 3600`, then add CPU/RAM, startup and storage margin.
Start with a small smoke session targeting at most $3-$5, concurrency one, and keep
the remaining credit for diagnosing failures. Stop and inspect billing after that
session before increasing the task count. The current working ceiling is $24 within
the user's $30 allowance. That allowance is not enough to assume a
complete run across three benchmarks.

The 900-second serve session and container limits reduce exposure but do not implement
an account-wide hard spending cap. Do not run a permanent `modal deploy` yet.
Confirm the app has stopped in the dashboard after the foreground session ends.

Validated with Modal SDK 1.5.5: local contract tests plus live GPU startup, model
loading, native tool parsing, endpoint authentication, and a billing read. These checks
do not establish general model quality; use the report's measured task outcomes.

References: [Modal inference example](https://modal.com/docs/examples/vllm_inference),
[serve CLI](https://modal.com/docs/cli/latest/serve),
[web server API](https://modal.com/docs/sdk/py/latest/web_server), and
[vLLM environment variables](https://docs.vllm.ai/en/latest/configuration/env_vars/).
