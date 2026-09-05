# Initial model selection - 2026-09-05

Select **Qwen/Qwen3.8-27B-FP8** for the worker and separately prompted supervisor.
This is a research-based starting choice, not a measured winner in our harness.

| Published evidence | Qwen3.8-27B | Muse Glimmer 30B |
| --- | --- | --- |
| Terminal-Bench 2.1 | 73.0 | 51.7 |
| DeepSWE 1.1 | 42.2 | Not reported in reviewed card |
| Native context tokens | 262,144 | 131,072 |

Sources: [Qwen model card](https://huggingface.co/Qwen/Qwen3.8-27B) and
[Meta model card](https://huggingface.co/meta-models/Muse-Glimmer-30B).
These external harness results are not our scores. Qwen's 79.0 QwenSWEBench result
is an in-house benchmark, not SWE-bench Verified. Qwen's 61.7 SWE-bench Pro uses
corrected tasks; Meta's 51.2 uses the original task set. They are not directly
comparable. [Meta methodology](https://research.meta.ai/static/muse-glimmer-methodology).

Muse remains a credible candidate for local tool use and failure recovery, with
compact quantized weights and a DFlash draft model. vLLM requires both dedicated
`muse_glimmer` parsers. Qwen uses `qwen3_coder` tools and `qwen3` reasoning.
[Muse recipe](https://recipes.vllm.ai/meta-models/Muse-Glimmer-30B),
[Qwen recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

## Initial settings

- FP8 weights revision: `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`.
- vLLM 0.28.0, Transformers 5.16.1; one H100, text-only, prefix caching.
- 131,072 context tokens; up to 16,384 output tokens per worker request.
- Thinking enabled with preserved history, temperature 1.0, top_p 0.95.
  The pinned generation config supplies top_k 20.
- Freeze a small diverse task list before outcomes. Retain first-pass results,
  distinguish infrastructure failures, and label reruns as development runs.

H100 GPU time is $0.001097/second, about $3.95/hour. CPU, RAM, task sandboxes,
and storage cost extra. Use an ephemeral session deadline, one GPU container,
sequential sandboxes, and a $24 working ceiling within the user's $30 authorization.
Budget against resource limits and wall time, then reconcile with billing, which
can lag. A CLI timeout is not an account-wide spending cap.
[Modal pricing](https://modal.com/pricing).
