"""Non-secret configuration for the coding agent."""

# --- model provider ----------------------------------------------------------
# DEFAULT: a local MLX server (OpenAI-compatible API). With it running, the agent
# works with no environment setup at all. To use a cloud provider instead, set
# OPENAI_BASE_URL, OPENAI_API_KEY (or LLM_API_KEY), and AGENT_MODEL / --model.
DEFAULT_BASE_URL = "http://localhost:8000/v1"   # MLX server (host 0.0.0.0, port 8000)
DEFAULT_API_KEY = "1997"                         # local MLX server key (not a cloud secret)
# A small, tool-reliable local model. Larger models (e.g. gemma-4-12b-*) are
# slower and can trip the server's memory guard during the agent loop.
DEFAULT_MODEL = "NVIDIA-Nemotron-3-Nano-4B-OptiQ-4bit"

# generation params — a local coding model; keep it near-deterministic
DEFAULT_TEMPERATURE = 0.0      # 0 = deterministic (best for code); raise toward 2.0 for variety
DEFAULT_TOP_P = 0.95           # nucleus sampling threshold
DEFAULT_CONTEXT_BUDGET = 24000  # soft token budget for compaction (model window is ~32k)
# Per-call output budget. Kept modest: a tool call or patch rarely needs more, and
# large values make the local server reserve more KV memory (which can trip its
# memory guard). Recovery escalates a little for length, and backs off on memory.
DEFAULT_MAX_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS_CAP = 4096
MIN_OUTPUT_TOKENS = 512   # floor when backing off from a memory-guard rejection

MODEL_HTTP_TIMEOUT_SECONDS = 120  # local models can be slow to first token
# Some providers (e.g. Groq, behind Cloudflare) reject the default urllib
# User-Agent with HTTP 403, so we send our own.
MODEL_USER_AGENT = "local-coding-agent/1.0"

# --- directories the agent never lists, reads, or edits ---
SKIPPED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    "coverage",
}

# --- file types the agent refuses to read or edit ---
BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".tgz",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
}

# --- run_command gateway ---
ALLOWED_COMMANDS = {"python", "python3", "pytest", "ruff", "uv", "poetry", "npm", "pnpm", "yarn"}
NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "ftp", "nc", "telnet"}
SHELL_CHAIN_TOKENS = ("&&", "||", ";", "|", "`", "$(", "\n", ">", "<")
