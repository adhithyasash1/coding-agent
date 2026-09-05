"""Ephemeral, single-GPU inference. No resources start when importing this module."""

import os
import socket
import subprocess
import time

import modal


def settings() -> dict[str, str]:
    required = ("AGENT_MODEL_NAME", "AGENT_MODEL_REVISION", "AGENT_TOOL_PARSER", "AGENT_GPU")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError(f"Select model settings before serving: {', '.join(missing)}")
    return {key: os.environ[key] for key in required} | {
        "AGENT_CONTEXT_TOKENS": os.environ.get("AGENT_CONTEXT_TOKENS", "32768"),
        "AGENT_REASONING_PARSER": os.environ.get("AGENT_REASONING_PARSER", ""),
    }


def server_command(config: dict[str, str]) -> list[str]:
    command = [
        "vllm",
        "serve",
        config["AGENT_MODEL_NAME"],
        "--revision",
        config["AGENT_MODEL_REVISION"],
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--max-model-len",
        config["AGENT_CONTEXT_TOKENS"],
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        config["AGENT_TOOL_PARSER"],
        "--max-num-seqs",
        "1",
        "--enforce-eager",
        "--language-model-only",
        "--enable-prefix-caching",
        "--generation-config",
        "auto",
        # Qwen3.5 GDN prefill defaults to a FlashInfer kernel that is JIT-compiled at
        # first use. The Triton kernel needs no compiler and starts immediately.
        "--gdn-prefill-backend",
        "triton",
    ]
    if config["AGENT_REASONING_PARSER"]:
        command += ["--reasoning-parser", config["AGENT_REASONING_PARSER"]]
    return command


def wait_for_server(process: subprocess.Popen, timeout: float = 600) -> None:
    """Fail promptly on a crashed child instead of paying through the startup timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"Inference process exited during startup: {code}")
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=1):
                return
        except OSError:
            time.sleep(1)
    process.terminate()
    raise TimeoutError("Inference server did not become ready")


config = settings()
app = modal.App("adaptive-coding-agent-inference")
# vLLM 0.28.0 wheels target CUDA 13 and FlashInfer compiles some kernels at runtime.
# A debian_slim image has no nvcc, so the engine died during startup. The CUDA devel
# base supplies the toolkit and the FlashInfer cache packages avoid most compilation.
FLASHINFER = "0.6.16.post3"
image = (
    modal.Image.from_registry("nvidia/cuda:13.0.1-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(
        "vllm==0.28.0",
        "transformers==5.16.1",
        f"flashinfer-cubin=={FLASHINFER}",
        extra_index_url="https://flashinfer.ai/whl/",
    )
    .uv_pip_install(
        f"flashinfer-jit-cache=={FLASHINFER}", index_url="https://flashinfer.ai/whl/cu130"
    )
    # The FlashInfer sampler is another JIT path; the PyTorch sampler is fine at one sequence.
    .env(config | {"VLLM_USE_FLASHINFER_SAMPLER": "0"})
)
cache = modal.Volume.from_name("adaptive-agent-model-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=config["AGENT_GPU"],
    cpu=(4, 4),
    memory=(16384, 65536),
    max_containers=1,
    min_containers=0,
    # Cold starts load 27 GiB; a short window would scale to zero during long tool commands.
    scaledown_window=300,
    timeout=600,
    volumes={"/root/.cache/huggingface": cache},
    secrets=[modal.Secret.from_name("adaptive-agent-inference")],
)
@modal.concurrent(max_inputs=1)
@modal.web_server(8000, startup_timeout=600)
def serve() -> None:
    if not os.environ.get("VLLM_API_KEY"):
        raise ValueError("Modal secret must contain VLLM_API_KEY")
    # vLLM reads VLLM_API_KEY from the environment. It never appears in argv.
    command = server_command(config)
    print(
        {
            "event": "inference_start",
            "model": config["AGENT_MODEL_NAME"],
            "revision": config["AGENT_MODEL_REVISION"],
            "gpu": config["AGENT_GPU"],
        },
        flush=True,
    )
    wait_for_server(subprocess.Popen(command))
