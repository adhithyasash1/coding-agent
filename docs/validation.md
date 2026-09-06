# Validation record

Validated on macOS with Python 3.12.13 on 2026-09-06 at source checkpoint
`f086809`. Local tests establish harness behavior, not benchmark solve rates.

| Check | Result |
| --- | --- |
| Core pytest suite | 139 passed; 11 optional SDK checks skipped in the core environment |
| Harbor 0.22.0 / Modal 1.5.5 contracts | 10 tests passed in the separate SDK environment |
| Ruff lint and formatting | Passed; 46 files formatted |
| Strict mypy | Passed on 17 runtime files |
| Boolean-aware Radon complexity gate | 203 functions; maximum 10; no violations |
| Native Qwen tool round trip on Modal | Passed in sessions 04, 05 and 06 |
| Live repair smoke | Submitted after reproduction, fix and verification |
| Terminal-Bench development sample | Baseline: 2 passes, 1 failure, 1 ungraded infrastructure interruption |
| LangSmith duplicate export | Live re-export of the original OpenSSL trace succeeded after conflict recovery |

The SDK tests use the real adapter and Unix-socket tool server with a local transport
substitute. A real HTTP service survives adapter return and remains reachable to a
simulated grader, then disappears after teardown or finite lifetime expiry. Tests
also cover optional download failures, preserved primary errors, process deadlines,
model parsing, unknown usage, redaction, revision-bound verification, supervisor
bounds, partial trace ingestion, and temporary-index patch export.

The additional Harbor-dependent exporter test is skipped by core pytest and is not
part of the ten unittest SDK checks. Do not add skipped tests to passing counts.

Earlier validation also built the wheel/source distribution and ran the offline
reproduce-edit-verify-submit demo; those packaging checks were not repeated for this
runtime-only change. CI is configured for Python 3.11, 3.12 and 3.14 but has not run
remotely. Live local Docker execution, actual SWE-bench and DeepSWE graders, and
pinned Pier compatibility remain unvalidated.

See [EVALUATION_REPORT.md](EVALUATION_REPORT.md) for task outcomes, exact settings,
retries, spending, and remaining limitations. Private run artifacts live under
`.agent-runs/live-20260905`. No Git remote is configured and nothing has been pushed.
