"""Model usage accounting and model identity (PLAN §3.13, §5.1).

`Usage` is carried by every agentic `step.end`, `model.call`, `RunStepResult`, `ProcessResult` and `RunRecord`.
"""

from __future__ import annotations

from pydantic import BaseModel


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None          # None = unknown
    latency_ms: float = 0.0                # sum of per-call latencies
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        if self.cost_usd is None and other.cost_usd is None:
            cost = None
        else:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=cost,
            latency_ms=self.latency_ms + other.latency_ms,
            calls=self.calls + other.calls,
        )


class ModelInfo(BaseModel):
    provider: str
    model_id: str
    tier: str | None = None
    thinking: str | None = None
