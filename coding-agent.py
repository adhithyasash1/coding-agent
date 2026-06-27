#!/usr/bin/env python3
"""
coding-agent.py — a small, model-driven coding agent (an "agentic query loop").

A coding agent is a while-loop around an LLM that can call tools. The model
decides what to do; the harness around it stays in charge of safety and context:

    per turn:  assemble context -> COMPACT (if over budget) -> model call
               (with RECOVERY) -> dispatch tools -> DENY-FIRST permission gate ->
               execute -> check STOP conditions -> repeat

It implements the engineering pieces that make such a loop hold up on real repos:
  1. explicit STOP conditions      : no_tool_use | finish | max_turns | context_overflow | abort
  2. a token BUDGET + accounting   : the loop knows how big the context is
  3. COMPACTION shapers (graduated, cheapest-first):
        snip          -> truncate oversized tool outputs in place
        microcompact  -> elide old tool results, keep the decisions
        auto-compact  -> summarize old turns with one model call (last resort)
  4. DENY-FIRST permission gate    : ordered deny rules, then allow (incl. "no editing tests")
  5. RECOVERY / escalation         : max-output-token retries, reactive compaction,
                                     fallback model

It edits the working tree directly (no branch/rollback), so run it on a clean
git repo and review with `git diff` / `git checkout` afterward. Needs no
third-party dependencies — the Python standard library plus config.py.

Run (defaults to a local MLX server — see config.py; no env setup needed):
    python3 coding-agent.py --repo ./repo --task "fix the failing test"
    # use a cloud provider instead:
    export OPENAI_API_KEY=...  ; export OPENAI_BASE_URL=https://api.groq.com/openai/v1
    python3 coding-agent.py --repo ./repo --task "..." --model openai/gpt-oss-120b
    # force compaction to fire on a tiny repo:
    python3 coding-agent.py --repo ./repo --task "..." --context-budget 1200
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import config


# ============================================================================
# 0. model client — returns (message, finish_reason); supports max_tokens
# ============================================================================

class ModelError(Exception):
    def __init__(self, message: str, kind: str = "api"):
        super().__init__(message)
        self.kind = kind  # "memory" | "context" | "api"


def chat(messages: list[dict], model: str, tools: list[dict] | None, max_tokens: int) -> tuple[dict, str]:
    # Defaults target the local MLX server (config.py); env vars override for cloud.
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY") or config.DEFAULT_API_KEY
    base_url = os.environ.get("OPENAI_BASE_URL", config.DEFAULT_BASE_URL).rstrip("/")
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": config.DEFAULT_TEMPERATURE,
        "top_p": config.DEFAULT_TOP_P,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": config.MODEL_USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.MODEL_HTTP_TIMEOUT_SECONDS) as response:
            choice = json.loads(response.read().decode("utf-8"))["choices"][0]
            return choice["message"], choice.get("finish_reason", "stop")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else str(exc)
        low = body.lower()
        # A local server's memory guard ("too large for available memory") is a
        # different problem from a genuine context-length error — handle "memory" first.
        kind = "memory" if "memory" in low else "context" if "context" in low else "api"
        raise ModelError(f"HTTP {exc.code}: {body[:200]}", kind=kind) from exc
    except urllib.error.URLError as exc:
        raise ModelError(str(exc), kind="api") from exc


# ============================================================================
# 1. token accounting — the loop must know how scarce context is
# ============================================================================

def tokens_of(message: dict) -> int:
    """Rough estimate: ~4 chars per token. Good enough to drive compaction."""
    text = message.get("content") or ""
    for call in message.get("tool_calls") or []:
        text += (call.get("function") or {}).get("arguments", "")
    return len(text) // 4 + 4


def total_tokens(messages: list[dict]) -> int:
    return sum(tokens_of(m) for m in messages)


# ============================================================================
# 2. compaction shapers — graduated lazy-degradation, cheapest first
# ============================================================================

def shaper_snip(messages: list[dict], cap_tokens: int = 400) -> int:
    """Cheapest: truncate any single oversized tool output (keep head + tail)."""
    snipped = 0
    for m in messages:
        if m["role"] == "tool" and "[snipped" not in m["content"] and tokens_of(m) > cap_tokens:
            text = m["content"]
            keep = cap_tokens * 4 // 2
            m["content"] = f"{text[:keep]}\n...[snipped {len(text) - 2 * keep} chars]...\n{text[-keep:]}"
            snipped += 1
    return snipped


def shaper_microcompact(messages: list[dict], keep_recent: int = 4) -> int:
    """Mid: elide the *contents* of old tool results but keep the messages (and
    the assistant decisions) intact. Pairing stays valid because we don't remove
    anything — we just shrink it."""
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    elided = 0
    for i in tool_idx[:-keep_recent] if len(tool_idx) > keep_recent else []:
        if not messages[i]["content"].startswith("[elided"):
            messages[i]["content"] = "[elided older tool result to save context]"
            elided += 1
    return elided


def shaper_auto_compact(messages: list[dict], model: str, keep_recent_turns: int = 2) -> tuple[list[dict], bool]:
    """Last resort: summarize the old turns with ONE model call and replace them.

    We cut only at a turn boundary (an assistant message starts each turn), so the
    surviving suffix still begins cleanly and tool_call/tool pairing is preserved.
    """
    assistant_idx = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
    if len(assistant_idx) <= keep_recent_turns:
        return messages, False
    cut = assistant_idx[-keep_recent_turns]
    head = messages[1:cut]  # everything after the system prompt, before the kept turns
    if not head:
        return messages, False

    transcript = "\n".join(
        f"{m['role']}: {(m.get('content') or '')[:500]}"
        + ("".join(f" [calls {c['function']['name']}]" for c in m.get('tool_calls') or []))
        for m in head
    )
    try:
        summary_msg, _ = chat(
            [
                {"role": "system", "content": "You compress agent transcripts."},
                {"role": "user", "content":
                    "Summarize this coding-agent transcript into a compact memo. Preserve: "
                    "files read, edits applied, test results, and what still needs doing.\n\n" + transcript},
            ],
            model, tools=None, max_tokens=512,
        )
        summary = summary_msg.get("content") or "(summary unavailable)"
    except ModelError:
        # If even the summary call fails (e.g. memory guard), fall back to a cheap,
        # non-model drop rather than crashing the whole run.
        summary = "(older steps omitted to save context)"
    new = [messages[0], {"role": "user", "content": "[Earlier work summarized to save context]\n" + summary}]
    new += messages[cut:]
    return new, True


def shape_context(messages: list[dict], model: str, hard: int, soft: int) -> tuple[list[dict], list[str]]:
    """Run the ladder. Each rung only escalates if we're still over budget."""
    fired: list[str] = []
    if shaper_snip(messages):
        fired.append("snip")
    if total_tokens(messages) > soft and shaper_microcompact(messages):
        fired.append("microcompact")
    if total_tokens(messages) > hard:
        messages, did = shaper_auto_compact(messages, model)
        if did:
            fired.append("auto-compact")
    return messages, fired


# ============================================================================
# 3. deny-first permission gate (ordered denies, then allow)
# ============================================================================

KNOWN_TOOLS = {"list_files", "search_text", "read_file", "edit_file", "write_file", "run_command", "finish"}


def resolve_in_repo(repo: str, path: str) -> tuple[Path | None, str]:
    root = Path(repo).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        return None, ""
    return target, target.relative_to(root).as_posix()


def is_test_file(rel: str) -> bool:
    name = Path(rel).name
    return (
        rel.startswith("tests/") or "/tests/" in f"/{rel}"
        or name.startswith("test_") or name.endswith("_test.py")
        or ".spec." in name or ".test." in name
    )


def permission_gate(name: str, args: dict, repo: str) -> tuple[bool, str]:
    # DENY-FIRST: the first matching deny wins; only then do we allow known tools.
    if name not in KNOWN_TOOLS:
        return False, "unknown tool"

    if name in {"read_file", "edit_file", "write_file"}:
        target, rel = resolve_in_repo(repo, str(args.get("path", "")))
        if target is None:
            return False, "path escapes the repo root"
        if any(p in config.SKIPPED_DIRS for p in Path(rel).parts):
            return False, "path is in a protected/skipped dir (.git, etc.)"
        if Path(rel).suffix.lower() in config.BINARY_EXTENSIONS:
            return False, "refusing to touch a binary file"
        if name in {"edit_file", "write_file"} and is_test_file(rel):
            return False, "writing tests is forbidden (the agent must not grade itself)"

    if name == "run_command":
        command = str(args.get("command", "")).strip()
        if not command or any(tok in command for tok in config.SHELL_CHAIN_TOKENS):
            return False, "empty or shell-chained command"
        try:
            parts = shlex.split(command)
        except ValueError:
            return False, "could not parse the command"
        exe = Path(parts[0]).name
        if exe in config.NETWORK_COMMANDS or exe in {"pip", "pip3", "rm", "rmdir", "mv"}:
            return False, f"{exe} is not allowed"
        if exe not in config.ALLOWED_COMMANDS:
            return False, f"{exe} is not in the allowlist"

    return True, ""  # default allow for known, non-denied tools


# ============================================================================
# 4. tools
# ============================================================================

def tool_list_files(repo: str, **_: object) -> str:
    out = subprocess.run(["git", "ls-files"], cwd=repo, text=True, capture_output=True)
    files = [f for f in out.stdout.splitlines()
             if not any(p in config.SKIPPED_DIRS for p in Path(f).parts)]
    return "\n".join(files[:200]) or "(no tracked files)"


def tool_search_text(repo: str, query: str = "", **_: object) -> str:
    if not query:
        return "ERROR: empty query"
    out = subprocess.run(["git", "ls-files"], cwd=repo, text=True, capture_output=True)
    q = query.lower()
    matches: list[str] = []
    for rel in out.stdout.splitlines():
        if any(p in config.SKIPPED_DIRS for p in Path(rel).parts):
            continue
        if Path(rel).suffix.lower() in config.BINARY_EXTENSIONS:
            continue
        try:
            text = (Path(repo) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if q in line.lower():
                matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(matches) >= 50:
                    return "\n".join(matches) + "\n...(truncated at 50 matches)"
    return "\n".join(matches) or f"(no matches for {query!r})"


def tool_read_file(repo: str, path: str = "", **_: object) -> str:
    target, _rel = resolve_in_repo(repo, path)
    try:
        return target.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError as exc:
        return f"ERROR: {exc}"


def tool_write_file(repo: str, path: str = "", content: str = "", **_: object) -> str:
    target, _rel = resolve_in_repo(repo, path)
    if target is None:
        return "ERROR: path escapes the repo root"
    if target.exists():
        return "ERROR: file exists — use edit_file to modify it"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return f"ERROR: {exc}"
    return f"OK: created {path}"


def tool_edit_file(repo: str, path: str = "", old: str = "", new: str = "", **_: object) -> str:
    target, _rel = resolve_in_repo(repo, path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return f"ERROR: {exc}"
    count = text.count(old)
    if not old or count == 0:
        return "ERROR: 'old' text not found — read_file first and copy exact text"
    if count > 1:
        return f"ERROR: 'old' matched {count} places — add surrounding context to make it unique"
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"OK: edited {path}"


def tool_run_command(repo: str, command: str = "", **_: object) -> str:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # keep the repo free of __pycache__
    result = subprocess.run(shlex.split(command), cwd=repo, text=True, capture_output=True, timeout=60, env=env)
    return f"exit_code={result.returncode}\n{(result.stdout + result.stderr).strip()[:4000]}"


TOOLS = {"list_files": tool_list_files, "search_text": tool_search_text, "read_file": tool_read_file,
         "edit_file": tool_edit_file, "write_file": tool_write_file, "run_command": tool_run_command}

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "list_files", "description": "List the tracked files in the repo.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "search_text",
        "description": "Search all tracked text files for a substring; returns file:line: matches.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "edit_file",
        "description": "Replace an exact, unique snippet 'old' with 'new' in an EXISTING file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {"name": "write_file",
        "description": "Create a NEW file with the given content (use edit_file to change an existing file).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command",
        "description": "Run one allowlisted command (e.g. 'python3 -m unittest -q'). No pipes/chaining.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "finish", "description": "Call when the task is complete and verified.",
        "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}}},
]


def dispatch(name: str, args: dict, repo: str) -> str:
    allowed, reason = permission_gate(name, args, repo)
    if not allowed:
        return f"DENIED by permission gate: {reason}"
    try:
        return TOOLS[name](repo, **args)
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out"
    except TypeError as exc:
        return f"ERROR: bad arguments: {exc}"
    except Exception as exc:
        return f"ERROR: {exc}"


# ============================================================================
# 5. model call WITH RECOVERY (escalation -> reactive compaction -> fallback)
# ============================================================================

def model_call_with_recovery(
    messages: list[dict], primary: str, fallback: str | None,
) -> tuple[dict, str, list[dict]]:
    max_tokens = config.DEFAULT_MAX_OUTPUT_TOKENS
    cap = config.MAX_OUTPUT_TOKENS_CAP
    model = primary
    used_fallback = False
    reactive_done = False
    retried_same = False

    for _ in range(8):  # bounded total attempts across all recovery kinds
        try:
            msg, finish = chat(messages, model, TOOL_SCHEMAS, max_tokens)
        except ModelError as exc:
            if exc.kind == "memory" and max_tokens > config.MIN_OUTPUT_TOKENS:
                # The server reserves KV memory for max_tokens; shrink the ask, don't grow it.
                max_tokens = max(config.MIN_OUTPUT_TOKENS, max_tokens // 2)
                print(f"   ↳ recovery: server low on memory, backing off to max_tokens={max_tokens}")
                continue
            if exc.kind == "context" and not reactive_done:
                print("   ↳ recovery: reactive auto-compaction (context overflow)")
                messages, _ = shaper_auto_compact(messages, model, keep_recent_turns=1)
                reactive_done = True
                continue
            if not retried_same:  # transient blip: retry the same model once first
                print(f"   ↳ recovery: transient error, retrying {model}")
                retried_same = True
                continue
            if fallback and not used_fallback and model != fallback:
                print(f"   ↳ recovery: switching to fallback model {fallback}")
                model, used_fallback, retried_same = fallback, True, False
                continue
            raise
        if finish == "length" and max_tokens < cap:
            max_tokens = min(max_tokens * 2, cap)  # output truncated -> escalate
            print(f"   ↳ recovery: output truncated, retrying with max_tokens={max_tokens}")
            continue
        # Reasoning models can burn the whole budget "thinking" and return an empty
        # turn (no tool call, no text, finish="stop"). Treat that as recoverable.
        empty = not msg.get("tool_calls") and not (msg.get("content") or "").strip()
        if empty and max_tokens < cap:
            max_tokens = min(max_tokens * 2, cap)
            print(f"   ↳ recovery: empty reply (reasoning ran long), retrying with max_tokens={max_tokens}")
            continue
        return msg, finish, messages

    raise ModelError("exhausted recovery attempts", kind="api")


# ============================================================================
# 6. the agentic query loop
# ============================================================================

SYSTEM_PROMPT = (
    "You are a coding agent in a local git repo. Solve the task by calling tools "
    "one step at a time: explore with list_files / search_text / read_file, make a "
    "minimal change with edit_file (existing files) or write_file (new files), then "
    "run the tests with run_command to confirm. Change only what the task needs. "
    "When the task is complete AND verified by a command, call finish."
)


def short(value: object, limit: int = 60) -> str:
    return str(value).replace("\n", "\\n")[:limit]


def make_plan(task: str, repo: str, model: str) -> str:
    """Plan mode: one model call (no tools) that drafts a short numbered plan."""
    files = tool_list_files(repo)
    try:
        msg, _ = chat(
            [
                {"role": "system", "content": "You are a coding assistant. Reply with a short numbered plan only."},
                {"role": "user", "content":
                    f"Task: {task}\n\nRepo files:\n{files}\n\nWrite a 3-6 step plan. Plan only, no preamble."},
            ],
            model, tools=None, max_tokens=512,
        )
        return (msg.get("content") or "").strip()
    except ModelError:
        return ""


def run_agent(task: str, repo: str, model: str, fallback: str | None,
              max_turns: int, hard_budget: int, plan: str = "") -> str | None:
    repo = str(Path(repo).resolve())
    soft_budget = int(hard_budget * 0.6)
    nudges_left = 4  # some models narrate instead of calling a tool; nudge them back
    user_msg = task if not plan else f"Task: {task}\n\nFollow this plan:\n{plan}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]

    try:
        for turn in range(1, max_turns + 1):
            # --- step: assemble + COMPACT context (pre-model shapers) ---
            messages, fired = shape_context(messages, model, hard_budget, soft_budget)
            used = total_tokens(messages)
            tag = f" | compaction: {', '.join(fired)}" if fired else ""
            print(f"[turn {turn}] context ~{used}/{hard_budget} tok{tag}")

            # --- STOP: context overflow we couldn't shrink ---
            if used > hard_budget * 1.5:
                print("\n[stop: context_overflow] could not fit the task in budget")
                return None

            # --- step: model call (with recovery) ---
            msg, finish, messages = model_call_with_recovery(messages, model, fallback)
            calls = msg.get("tool_calls") or []

            # --- no tool call: nudge a narrating model, else STOP ---
            if not calls:
                text = (msg.get("content") or "").strip()
                if nudges_left > 0:
                    nudges_left -= 1
                    print("   ↳ nudge: got prose, not a tool call — asking for a tool call")
                    messages.append({"role": "assistant", "content": text or "(no content)"})
                    messages.append({"role": "user", "content":
                        "That was prose, not a tool call. Respond with exactly ONE tool call now "
                        "(list_files, search_text, read_file, edit_file, write_file, run_command, "
                        "or finish). Do not explain."})
                    continue
                print(f"\n[stop: no_tool_use]\n{text}")
                return msg.get("content")

            messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})

            # --- step: dispatch -> gate -> execute ---
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):  # models sometimes emit "null" / a list
                    args = {}

                # --- STOP: finish ---
                if name == "finish":
                    print(f"\n[stop: finish] {args.get('summary', '')}")
                    return args.get("summary")

                result = dispatch(name, args, repo)
                print(f"   {name}({', '.join(f'{k}={short(v,40)}' for k, v in args.items())}) -> {short(result, 90)}")
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})

        # --- STOP: max turns ---
        print(f"\n[stop: max_turns] reached {max_turns} turns without finishing")
        return None
    except KeyboardInterrupt:
        # --- STOP: abort ---
        print("\n[stop: abort] interrupted by user")
        return None


# ============================================================================
# 7. git safety net — isolate the run on a throwaway branch
# ============================================================================

def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)


def git_isolate(repo: str, task: str) -> tuple[str, str]:
    """Create a throwaway agent branch so the original branch stays untouched.
    Requires a clean tree (so the agent's diff is meaningful). Returns
    (original_branch, agent_branch)."""
    if _git(repo, "rev-parse", "--is-inside-work-tree").returncode != 0:
        raise SystemExit(f"{repo} is not a git repo")
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise SystemExit("working tree is dirty — commit or stash first, then re-run (or use --no-git)")
    original = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")[:32] or "task"
    branch = f"agent/{slug}-{time.strftime('%Y%m%d-%H%M%S')}"
    if _git(repo, "checkout", "-b", branch).returncode != 0:
        raise SystemExit(f"could not create agent branch {branch}")
    print(f"[git] isolated on branch {branch} (from {original})")
    return original, branch


def git_finalize(repo: str, original: str, branch: str, task: str) -> None:
    """Commit the agent's changes on the agent branch, or delete it if empty."""
    if not _git(repo, "status", "--porcelain").stdout.strip():
        _git(repo, "checkout", original)
        _git(repo, "branch", "-D", branch)
        print(f"\n[git] no changes — returned to {original}, removed empty {branch}")
        return
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=coding-agent", "-c", "user.email=agent@local",
         "commit", "-q", "-m", f"agent: {task[:60]}")
    print(f"\n[git] changes committed on {branch} (original {original} untouched)")
    print(f"  review:  git diff {original}..{branch}")
    print(f"  keep:    git checkout {original} && git merge {branch}")
    print(f"  discard: git checkout {original} && git branch -D {branch}")


# ============================================================================
# 8. CLI
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="An agentic query loop: ReAct + compaction + recovery.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL", config.DEFAULT_MODEL))
    parser.add_argument("--fallback-model", default="",
                        help="model to fall back to on errors; empty by default (one local model)")
    parser.add_argument("--max-turns", type=int, default=15)
    parser.add_argument("--context-budget", type=int, default=config.DEFAULT_CONTEXT_BUDGET,
                        help="soft token budget; lower it (e.g. 1200) to watch compaction fire")
    parser.add_argument("--plan", action="store_true", help="draft a short plan before acting")
    parser.add_argument("--no-git", action="store_true",
                        help="edit the working tree directly instead of isolating on an agent branch")
    args = parser.parse_args(argv)

    repo = str(Path(args.repo).expanduser())
    if not (Path(repo) / ".git").exists():
        raise SystemExit(f"{args.repo} is not a git repo (run on a clean repo so you can diff/revert)")

    original = branch = None
    if not args.no_git:
        original, branch = git_isolate(repo, args.task)
    try:
        plan = make_plan(args.task, repo, args.model) if args.plan else ""
        if plan:
            print(f"\n[plan]\n{plan}\n")
        run_agent(args.task, repo, args.model, args.fallback_model or None,
                  args.max_turns, args.context_budget, plan)
    finally:
        if branch:
            git_finalize(repo, original, branch, args.task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
