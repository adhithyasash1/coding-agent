# coding-agent

## Architecture
![coding-agent architecture](coding-agent.png)

A small, readable coding agent for learning how agents are actually engineered.
It's a **model-driven tool-calling loop**: you give it a task, and the model
explores the repo, edits code, and runs the tests to fix a failing test —
deciding each step itself.

No agent framework, no third-party dependencies: just the Python standard
library plus `config.py`. One file you can read top to bottom: `coding-agent.py`.

## The one idea

An agent is a `while`-loop around an LLM that can call tools. Each turn:

```
assemble context → compact (if over budget) → model call (with recovery)
   → dispatch tool calls → deny-first permission gate → execute → check stop
```

The model chooses what to do (list files, read, edit, run tests); the order is
emergent, not scripted. The harness around it owns safety and context.

## What it implements

- **5 stop conditions:** `no_tool_use`, `finish`, `max_turns`, `context_overflow`, `abort`.
- **Token budget + compaction ladder** (cheapest first, only escalating when over budget):
  `snip` (truncate huge tool outputs) → `microcompact` (elide old results, keep the
  decisions) → `auto-compact` (summarize old turns with one model call). This is the
  "context is a scarce resource" idea.
- **Deny-first permission gate:** ordered deny rules, then allow. Blocks path escapes,
  `.git`, `pip`/network/`rm`, shell chaining — and **editing tests**, so the agent
  can't grade itself.
- **Recovery:** retry the same model on a transient error → raise `max_tokens` if
  output was truncated → **back off** `max_tokens` if the server's memory guard
  rejects the request → reactive compaction on context overflow → optional fallback
  model.
- **Git safety net:** by default the run is isolated on a throwaway `agent/<slug>`
  branch and the changes are committed there, leaving your original branch
  untouched (review, then merge or discard). `--no-git` edits the tree directly.
- **Plan mode:** `--plan` makes one up-front call to draft a short numbered plan,
  then the loop follows it.

Tools: `list_files`, `search_text`, `read_file`, `edit_file`, `write_file`, `run_command`, `finish`.

`config.py` holds shared, non-secret settings (provider defaults, generation params,
and the path/command allowlists the gate uses).

## Setup

No install step — just Python 3. The agent speaks the OpenAI-compatible API, and
**by default it talks to a local MLX server** (`config.py`), so with that server
running it works with no environment setup at all.

### Default: local model via MLX

Run a local MLX server (OpenAI-compatible) and the defaults in `config.py` point
at it:

```
base URL : http://localhost:8000/v1      # server host 0.0.0.0, port 8000
api key  : 1997
model    : NVIDIA-Nemotron-3-Nano-4B-OptiQ-4bit   # small + reliable at tool calls
```

Larger local models (e.g. `gemma-4-12b-*`) work too but are much slower per turn
and can trip the server's memory guard during the loop; pick them with `--model`
only if you have the headroom.

Change any of these in `config.py` (model, sampling temperature/top-p, context
budget, output-token budget, timeout). The model **must support OpenAI-style tool
calling** — this agent drives itself with native `tool_calls`.

Reasoning models can be inconsistent at this: they may "think" past the output
budget (the agent escalates `max_tokens` to recover) or narrate the tool name as
prose instead of emitting a tool call (the agent nudges, then stops). If a run
stalls with repeated `got prose, not a tool call` nudges, switch to a model that
reliably emits structured tool calls via `--model`.

### Optional: a cloud provider instead

Point it at OpenAI / Groq / etc. with env vars (these override the local defaults).
Copy `.env.example` to `.env` (gitignored) and load it with
`set -a && source .env && set +a`:

```sh
export OPENAI_API_KEY="..."                       # or LLM_API_KEY
export OPENAI_BASE_URL="https://api.groq.com/openai/v1"
export AGENT_MODEL="openai/gpt-oss-120b"           # or pass --model
```

Model choice matters: a strong tool-use model drives the loop reliably; weaker
models tend to hallucinate edits or finish early.

## Run

By default the agent **isolates the run on a throwaway `agent/<slug>` branch**
(requires a clean tree) and commits its changes there, leaving your original
branch untouched — review with `git diff`, then merge or delete the branch. Use
`--no-git` to edit the working tree directly instead.

```sh
python3 coding-agent.py --repo ./some_repo --task "fix the failing test"

# draft a plan first, then act:
python3 coding-agent.py --repo ./some_repo --task "..." --plan

# lower the budget to watch the compaction ladder fire on a tiny repo:
python3 coding-agent.py --repo ./some_repo --task "..." --context-budget 1200
```

Useful flags: `--model`, `--fallback-model`, `--max-turns`, `--context-budget`,
`--plan`, `--no-git`.
