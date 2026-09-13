"""Opt-in token planning. Allowances never replace the global wall deadline."""

from dataclasses import dataclass, field
from statistics import median
from typing import Any


@dataclass
class GenerationPlanner:
    finalizing: bool = False
    samples: list[tuple[int, float]] = field(default_factory=list)

    def observe(self, output_tokens: int, duration: float) -> None:
        if output_tokens > 0 and duration > 0:
            self.samples.append((output_tokens, duration))
            self.samples = self.samples[-12:]

    def allowance(self, remaining: float, tokens: int, cap: int) -> tuple[int, dict[str, Any]]:
        # Whole-request throughput deliberately includes queue, prefill and polling.
        # Until transport-specific decomposition exists, never infer those components.
        rate, overhead = 10.0, 15.0
        if len(self.samples) >= 3:
            rate = min(rate, median(n / seconds for n, seconds in self.samples))
            # Residual beyond the conservative decode estimate is observable request
            # overhead, not a claim about any individual server-side component.
            overhead = max(
                overhead, median(max(0, seconds - n / 10.0) for n, seconds in self.samples)
            )
        work = max(0, int((remaining - 120.0 - overhead) * rate))
        if min(work, tokens, cap) < 128:
            self.finalizing = True
        available_time = remaining if self.finalizing else remaining - 120.0
        limit = min(
            cap,
            1024 if self.finalizing else 2048,
            max(0, tokens),
            max(0, int((available_time - overhead) * rate)),
        )
        return limit, {
            "policy": "time_aware",
            "phase": "finalization" if self.finalizing else "work",
            "remaining_seconds": remaining,
            "remaining_output_tokens": tokens,
            "reserve_seconds": 120.0,
            "tokens_per_second": rate,
            "request_overhead_seconds": overhead,
            "estimate_kind": "conservative whole-request throughput and residual overhead",
            "samples": len(self.samples),
            "allowance": limit,
        }
