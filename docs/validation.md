# Local validation record

Validated on macOS with Python 3.12.13 on 2026-09-05. These results cover harness
behavior, not model quality or benchmark solve rates.

| Check | Result |
| --- | --- |
| Core pytest suite | 39 tests passed; optional SDK tests use a separate environment |
| Harbor 0.22.0 and Modal 1.5.5 contracts | 3 tests passed: external agent lifecycle, committed patch receipt, and mocked secret-safe server launch |
| Ruff lint and formatting | Passed |
| Strict mypy on runtime | Passed |
| Boolean-aware Radon complexity gate | Maximum 10 per function, no violations |
| Locked dependency synchronization | Passed offline |
| Wheel and source distribution | Built successfully |
| Documented offline repair demo | Failing reproduction, atomic edit, passing verification, successful submission |
| Trace inspection | Validated 25 ordered events and final result from the successful demo |

The test suite includes real local HTTP and Unix-socket servers, subprocess stdin,
timeouts, process-group cleanup, structured model parsing, trace redaction, stale
verification, supervisor bounds, invalid advice, stale signals within batches, and
SWE-bench export preserving the real Git index. The SDK tests do not provision cloud
resources. CI is configured for Python 3.11, 3.12, and 3.14 but has not run remotely.

Pending: live Docker execution because the local daemon is stopped; actual Modal GPU
startup and billing; selected-model tool parsing/context behavior; official SWE-bench,
Terminal-Bench and DeepSWE tasks; compatibility with a pinned Pier revision.

The successful demo trace is under `.agent-runs/demo-56880767/trace` on this checkout.
Run `uv run python scripts/demo.py` to create a fresh, independent trace. Fixture
token counts are synthetic and must not be used for cost or performance estimates.

No Modal credits were consumed and no Git remote or commit was created.
